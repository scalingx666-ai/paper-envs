"""Run MiniMax Code headless as the environment-solving agent.

MCode is launched with an isolated ``MINIMAX_DATA_DIR`` so the pipeline never
mutates the user's real ``~/.minimax`` sessions. The provider config is copied
once (mode 700 directory / 600 file) from the user config; the key is never
read, logged, or embedded by this module.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import tempfile
import time
from pathlib import Path

from config import (
    ENVS_DIR,
    KAROTTE_DIR,
    MCODE_DATA_DIR,
    MCODE_EFFORT,
    MCODE_MODEL,
    MCODE_PERMISSION,
    MCODE_ROOT,
    ROOT,
    RUNS_DIR,
)
from harbor import prepare_workspace, run_verifier


def ensure_data_dir() -> Path:
    MCODE_DATA_DIR.mkdir(parents=True, exist_ok=True)
    MCODE_DATA_DIR.chmod(0o700)
    config = MCODE_DATA_DIR / "config.yaml"
    if not config.exists():
        source = Path.home() / ".minimax" / "config.yaml"
        if not source.exists():
            raise RuntimeError(
                "MCode provider config missing: run `mcode` once and add the "
                "tierflow BYOK provider, or set MINIMAX_DATA_DIR to an existing profile"
            )
        shutil.copy2(source, config)
        config.chmod(0o600)
    return MCODE_DATA_DIR


def _parse_stdout(stdout: str) -> dict:
    for line in reversed(stdout.splitlines()):
        line = line.strip()
        if not line.startswith("{"):
            continue
        try:
            data = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(data, dict) and data.get("type") == "exec.result":
            return data
    return {}


def run_mcode(
    workspace: Path,
    prompt: str,
    *,
    timeout: str = "3m",
    max_steps: int = 12,
    output_dir: Path,
    data_dir: Path | None = None,
    sandbox_deny: list[Path] | None = None,
) -> dict:
    ensure_data_dir()
    workspace = Path(workspace).resolve()
    output_dir = Path(output_dir).resolve()
    output_dir.mkdir(parents=True, exist_ok=True)
    answer_path = output_dir / "mcode_answer.md"
    diagnostics = output_dir / "diagnostics"
    cli = MCODE_ROOT / "dist" / "cli.js"
    if not cli.exists():
        raise RuntimeError(f"MCode CLI not found: {cli}")

    command = [
        "node",
        str(cli),
        "exec",
        "--cwd",
        str(workspace),
        "--model",
        MCODE_MODEL,
        "--effort",
        MCODE_EFFORT,
        "--permission",
        MCODE_PERMISSION,
        "--prompt-mode",
        "coding",
        "--timeout",
        timeout,
        "--max-steps",
        str(max_steps),
        "--output-format",
        "json",
        "--diagnostics-dir",
        str(diagnostics),
        "-o",
        str(answer_path),
        prompt,
    ]
    if sandbox_deny:
        # The "smart" policy only auto-approves tool use; it does not stop reads
        # outside the workspace, so grading data must be denied at the OS level.
        if not Path("/usr/bin/sandbox-exec").exists():
            raise RuntimeError("sandbox-exec is required to isolate MCode evaluation")
        denied = [Path(path).resolve() for path in sandbox_deny]
        for path in denied:
            if workspace == path or path in workspace.parents:
                raise RuntimeError(f"workspace is inside a sandbox-denied path: {path}")
        profile = output_dir / "sandbox.sb"
        rules = "\n".join(
            f"  (subpath {json.dumps(str(path))})" for path in denied
        )
        profile.write_text(
            "(version 1)\n(allow default)\n(deny file-read* file-write*\n"
            f"{rules})\n",
            encoding="utf-8",
        )
        command = ["/usr/bin/sandbox-exec", "-f", str(profile)] + command

    env = os.environ.copy()
    env["MINIMAX_DATA_DIR"] = str(Path(data_dir).resolve() if data_dir else MCODE_DATA_DIR)
    env.pop("PAPER_ENV_API_KEY", None)

    started = time.time()
    try:
        proc = subprocess.run(
            command,
            capture_output=True,
            text=True,
            timeout=_seconds(timeout) + 90,
            env=env,
        )
    except subprocess.TimeoutExpired as exc:
        return {
            "status": "timeout",
            "elapsed": round(time.time() - started, 1),
            "error": f"mcode process timed out after {timeout}",
            "stdout": (exc.stdout or "")[-2000:] if isinstance(exc.stdout, str) else "",
        }
    elapsed = round(time.time() - started, 1)
    data = _parse_stdout(proc.stdout or "")
    result = {
        "status": data.get("status") or ("failed" if proc.returncode else "unknown"),
        "elapsed": elapsed,
        "duration_ms": data.get("durationMs"),
        "model": data.get("model"),
        "usage": data.get("usage"),
        "run_id": data.get("runId"),
        "session_id": data.get("sessionId"),
        "output": (data.get("output") or "")[-4000:],
        "stdout": (proc.stdout or "")[-2000:] if not data else "",
        "stderr": (proc.stderr or "")[-3000:],
        "returncode": proc.returncode,
    }
    if answer_path.exists():
        result["answer_excerpt"] = answer_path.read_text(
            encoding="utf-8", errors="replace"
        )[:2000]
    if not data and proc.returncode != 0:
        result["error"] = (proc.stderr or proc.stdout or "mcode failed").strip()[-1500:]
    return result


def _seconds(value: str) -> int:
    unit = value[-1].lower()
    number = float(value[:-1] or 1)
    if unit == "s":
        return int(number)
    if unit == "m":
        return int(number * 60)
    if unit == "h":
        return int(number * 3600)
    return int(float(value))


def _isolated_data_dir(root: Path) -> Path:
    ensure_data_dir()
    target = root / "minimax-data"
    target.mkdir(mode=0o700)
    config = target / "config.yaml"
    shutil.copy2(MCODE_DATA_DIR / "config.yaml", config)
    config.chmod(0o600)
    return target


def _repo_relative(path: Path) -> str:
    try:
        return Path(path).resolve().relative_to(ROOT).as_posix()
    except ValueError:
        return str(path)


def _move_aside(path: Path, run_dir: Path) -> None:
    if not path.exists():
        return
    stale = run_dir / "_stale"
    stale.mkdir(parents=True, exist_ok=True)
    shutil.move(str(path), str(stale / f"{path.name}-{time.strftime('%Y%m%d-%H%M%S')}"))


def evaluate_environment(
    env_dir: Path,
    instruction: str,
    deliverable: str,
    out_dir: Path,
    *,
    timeout: str = "3m",
    max_steps: int = 12,
) -> dict:
    env_dir = Path(env_dir).resolve()
    run_dir = Path(out_dir).resolve()
    run_dir.mkdir(parents=True, exist_ok=True)
    solve_root = Path(tempfile.mkdtemp(prefix="paper-env-solve-"))
    workspace = solve_root / "workspace"
    prepare_workspace(env_dir, workspace)

    prompt = (
        f"{instruction.rstrip()}\n\n"
        "You are being evaluated automatically. Work only inside the current "
        "directory, use the tools available to you, and actually create the "
        f"deliverable at `{deliverable}`. Do not ask questions. When the file is "
        "written, reply with a one-line summary and stop."
    )
    try:
        result = run_mcode(
            workspace,
            prompt,
            timeout=timeout,
            max_steps=max_steps,
            output_dir=solve_root,
            data_dir=_isolated_data_dir(solve_root),
            sandbox_deny=[ENVS_DIR, RUNS_DIR, KAROTTE_DIR, MCODE_DATA_DIR],
        )
        verifier = run_verifier(env_dir, workspace)
        final_workspace = run_dir / "workspace"
        _move_aside(final_workspace, run_dir)
        shutil.copytree(workspace, final_workspace)
        for name in ("mcode_answer.md", "diagnostics"):
            source = solve_root / name
            if source.exists():
                target = run_dir / name
                _move_aside(target, run_dir)
                shutil.move(str(source), str(target))
    finally:
        shutil.rmtree(solve_root, ignore_errors=True)

    record = {
        "status": result.get("status"),
        "reward": verifier["reward"],
        "verifier": verifier,
        "elapsed": result.get("elapsed"),
        "duration_ms": result.get("duration_ms"),
        "model": result.get("model"),
        "usage": result.get("usage"),
        "run_id": result.get("run_id"),
        "session_id": result.get("session_id"),
        "error": result.get("error"),
        "output": result.get("output", "")[-2000:],
        "answer_excerpt": result.get("answer_excerpt", ""),
        "workspace": _repo_relative(final_workspace),
        "deliverable_written": (final_workspace / deliverable).is_file(),
        "sandboxed": True,
    }
    (run_dir / "mcode_result.json").write_text(
        json.dumps(record, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    return record


def evaluate_env_dir(
    env_dir: Path | str,
    out_dir: Path | str | None = None,
    *,
    timeout: str = "4m",
    max_steps: int = 16,
) -> dict:
    env_dir = Path(env_dir).resolve()
    meta_path = env_dir / "tests" / "env_meta.json"
    meta = json.loads(meta_path.read_text(encoding="utf-8"))
    instruction = (env_dir / "instruction.md").read_text(encoding="utf-8")
    out = Path(out_dir) if out_dir else env_dir / "tests" / "mcode"
    record = evaluate_environment(
        env_dir,
        instruction,
        meta["deliverable"]["path"],
        out,
        timeout=timeout,
        max_steps=max_steps,
    )
    meta["mcode"] = record
    meta_path.write_text(
        json.dumps(meta, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    return record


def main(argv: list[str] | None = None) -> int:
    import argparse

    parser = argparse.ArgumentParser(description="Run MiniMax Code against one generated environment")
    parser.add_argument("env", help="path to envs/<slug>")
    parser.add_argument("--out", help="output dir (default: <env>/tests/mcode)")
    parser.add_argument("--timeout", default="4m")
    parser.add_argument("--max-steps", type=int, default=16)
    args = parser.parse_args(argv)

    record = evaluate_env_dir(
        args.env, args.out, timeout=args.timeout, max_steps=args.max_steps
    )
    print(
        f"{Path(args.env).name}: status={record['status']} "
        f"reward={record['reward']} elapsed={record['elapsed']}s"
    )
    return 0 if record["reward"] > 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
