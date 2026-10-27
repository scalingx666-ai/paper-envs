"""Render and validate Harbor-format task directories from a paper plan.

Layout produced per paper (Harbor-native, plus repo metadata under tests/):

    <slug>/
      task.toml
      instruction.md
      environment/Dockerfile
      <starter files...>            # copied into the agent workspace
      tests/test.sh                 # verifier entrypoint (cwd = workspace)
      tests/verify.py               # compares deliverable against golden.json
      tests/golden.json             # expected output, produced by the reference
      tests/solution/solve.py       # reference solution
      tests/paper.json              # provenance
      tests/env_meta.json           # generation / validation record

Keeping starter files at the task root matches harbor-mlx's workspace
preparation; keeping solution/golden under tests/ prevents them from being
copied into the agent workspace.
"""

from __future__ import annotations

import json
import math
import os
import re
import shutil
import subprocess
import sys
import tempfile
import time
import tomllib
from contextlib import contextmanager
from pathlib import Path
from typing import Any

RESERVED_ROOT = {
    "task.toml",
    "instruction.md",
    "tests",
    "environment",
    "reward.txt",
    "reward.json",
    "verification.json",
    "final_answer.txt",
    "__pycache__",
    ".DS_Store",
}

MAX_STARTER_FILES = 24
MAX_STARTER_BYTES = 200_000
MAX_GRADED_PATHS = 16


class PlanError(ValueError):
    pass


def _safe_relpath(value: Any, field: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise PlanError(f"{field} must be a non-empty relative path")
    path = value.strip().replace("\\", "/")
    if path.startswith("/") or re.match(r"^[A-Za-z]:", path):
        raise PlanError(f"{field} must be relative: {value!r}")
    parts = [part for part in path.split("/") if part not in ("", ".")]
    if any(part == ".." for part in parts):
        raise PlanError(f"{field} may not escape the workspace: {value!r}")
    if not parts:
        raise PlanError(f"{field} is empty")
    return "/".join(parts)


def _strip_code_fence(text: Any) -> str:
    if not isinstance(text, str):
        raise PlanError("expected a string")
    value = text.strip()
    match = re.match(r"^```(?:python|json|markdown|md)?\s*\n(.*)\n```$", value, re.S)
    if match:
        return match.group(1).strip()
    return value


def normalize_plan(raw: Any) -> dict:
    if not isinstance(raw, dict):
        raise PlanError("plan must be a JSON object")

    def need(key: str) -> Any:
        if key not in raw:
            raise PlanError(f"plan missing {key!r}")
        return raw[key]

    title = str(need("task_title")).strip()
    one_liner = str(need("one_liner")).strip()
    if not title or not one_liner:
        raise PlanError("task_title and one_liner must be non-empty")

    difficulty = str(need("difficulty")).strip().lower()
    if difficulty not in ("easy", "medium", "hard"):
        difficulty = "medium"

    instruction = _strip_code_fence(need("instruction_md"))
    if len(instruction) < 200:
        raise PlanError(f"instruction_md too short ({len(instruction)} chars)")

    deliverable = need("deliverable")
    if not isinstance(deliverable, dict):
        raise PlanError("deliverable must be an object")
    deliverable_path = _safe_relpath(deliverable.get("path"), "deliverable.path")
    if not deliverable_path.endswith(".json"):
        raise PlanError("deliverable.path must end with .json")
    if deliverable_path.split("/")[0] in RESERVED_ROOT:
        raise PlanError(f"deliverable.path uses reserved name: {deliverable_path}")

    starters_raw = need("starter_files")
    if not isinstance(starters_raw, list) or len(starters_raw) > MAX_STARTER_FILES:
        raise PlanError("starter_files must be a list of at most 24 files")
    starters: list[dict[str, str]] = []
    seen: set[str] = set()
    for item in starters_raw:
        if not isinstance(item, dict):
            raise PlanError("each starter file must be an object")
        path = _safe_relpath(item.get("path"), "starter_files.path")
        if path.split("/")[0] in RESERVED_ROOT:
            raise PlanError(f"starter file uses reserved name: {path}")
        content = item.get("content")
        if not isinstance(content, str):
            raise PlanError(f"starter file {path} content must be a string")
        if len(content.encode("utf-8")) > MAX_STARTER_BYTES:
            raise PlanError(f"starter file {path} is too large")
        if path in seen:
            raise PlanError(f"duplicate starter file: {path}")
        if path == deliverable_path:
            raise PlanError("deliverable.path collides with a starter file")
        seen.add(path)
        starters.append({"path": path, "content": content})

    graded = need("graded_paths")
    if not isinstance(graded, list) or not (1 <= len(graded) <= MAX_GRADED_PATHS):
        raise PlanError("graded_paths must contain 1-16 paths")
    graded_paths = []
    for value in graded:
        if not isinstance(value, str) or not value.strip():
            raise PlanError("graded_paths entries must be non-empty strings")
        path = value.strip()
        if path.startswith("$."):
            path = path[2:]
        if path.startswith("$"):
            path = path[1:].lstrip(".")
        if path.startswith(deliverable_path + "."):
            path = path[len(deliverable_path) + 1 :]
        graded_paths.append(path)
    graded_paths = list(dict.fromkeys(graded_paths))

    tolerances_raw = raw.get("tolerances") or {}
    if not isinstance(tolerances_raw, dict):
        raise PlanError("tolerances must be an object")
    tolerances: dict[str, float] = {}
    for key, value in tolerances_raw.items():
        key = str(key).lstrip("$").lstrip(".")
        try:
            tolerance = float(value)
        except (TypeError, ValueError) as exc:
            raise PlanError(f"tolerance for {key} is not a number") from exc
        if tolerance < 0 or not math.isfinite(tolerance):
            raise PlanError(f"tolerance for {key} must be finite and >= 0")
        tolerances[str(key)] = tolerance

    solution = _strip_code_fence(need("solution_py"))
    if len(solution) < 80:
        raise PlanError("solution_py is too short to be a real script")
    if "```" in solution:
        raise PlanError("solution_py still contains a code fence")

    if deliverable_path not in instruction:
        schema = deliverable.get("schema") or "a JSON object with the graded fields"
        instruction += (
            f"\n\n## Deliverable\n\nWrite a JSON file at `{deliverable_path}`. "
            f"It must match this schema:\n\n{schema}\n\nExtra keys are allowed. "
            "Work offline using only the Python standard library."
        )
    if "offline" not in instruction.lower():
        instruction += (
            "\n\nWork offline using only the Python 3.12 standard library. "
            "Do not use the network."
        )

    paper_concepts = [
        str(item).strip() for item in (raw.get("paper_concepts") or []) if str(item).strip()
    ]

    return {
        "task_title": title,
        "one_liner": one_liner,
        "paper_concepts": paper_concepts[:10],
        "difficulty": difficulty,
        "estimated_minutes": int(raw.get("estimated_minutes") or 25),
        "instruction_md": instruction.strip() + "\n",
        "deliverable": {
            "path": deliverable_path,
            "format": "json",
            "schema": str(deliverable.get("schema") or "").strip(),
        },
        "starter_files": starters,
        "graded_paths": graded_paths,
        "tolerances": tolerances,
        "solution_py": solution + "\n",
        "design_rationale": str(raw.get("design_rationale") or "").strip(),
        "verification_notes": str(raw.get("verification_notes") or "").strip(),
    }


VERIFY_PY = r'''#!/usr/bin/env python3
"""Harbor verifier: compare the agent deliverable against golden.json."""

from __future__ import annotations

import argparse
import json
import math
from pathlib import Path


def get_path(obj, dotted):
    current = obj
    walked = []
    for part in dotted.split("."):
        walked.append(part)
        if isinstance(current, list):
            try:
                index = int(part)
            except ValueError as exc:
                raise KeyError(".".join(walked) + " expects a list index") from exc
            if index < 0 or index >= len(current):
                raise KeyError(".".join(walked) + " is out of range")
            current = current[index]
        elif isinstance(current, dict):
            if part not in current:
                raise KeyError(".".join(walked) + " is missing")
            current = current[part]
        else:
            raise KeyError(".".join(walked) + " cannot be indexed")
    return current


def compare(actual, expected, tolerance):
    if isinstance(expected, bool):
        return isinstance(actual, bool) and actual == expected
    if isinstance(expected, (int, float)):
        return (
            isinstance(actual, (int, float))
            and not isinstance(actual, bool)
            and math.isfinite(float(actual))
            and math.isclose(float(actual), float(expected), rel_tol=1e-9, abs_tol=tolerance)
        )
    if isinstance(expected, list):
        if not isinstance(actual, list) or len(actual) != len(expected):
            return False
        return all(compare(a, e, tolerance) for a, e in zip(actual, expected))
    if isinstance(expected, dict):
        if not isinstance(actual, dict) or set(actual) != set(expected):
            return False
        return all(compare(actual[key], value, tolerance) for key, value in expected.items())
    return actual == expected


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--workspace", default=".")
    parser.add_argument("--tests", default=str(Path(__file__).resolve().parent))
    parser.add_argument("--reward", default=None)
    args = parser.parse_args()

    workspace = Path(args.workspace).resolve()
    tests = Path(args.tests).resolve()
    reward_path = Path(args.reward) if args.reward else workspace / "reward.txt"

    golden = json.loads((tests / "golden.json").read_text(encoding="utf-8"))
    deliverable_path = workspace / golden["deliverable"]
    details = []
    reward = 0.0

    if not deliverable_path.is_file():
        details.append({"path": "<deliverable>", "ok": False, "error": "missing " + golden["deliverable"]})
    else:
        try:
            actual = json.loads(deliverable_path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, UnicodeDecodeError) as exc:
            details.append({"path": "<deliverable>", "ok": False, "error": "invalid JSON: " + str(exc)})
            actual = None
        if actual is not None:
            expected = golden["expected"]
            graded = golden.get("graded_paths") or list(expected)
            tolerances = golden.get("tolerances") or {}
            passed = 0
            for path in graded:
                tolerance = float(tolerances.get(path, 0.0))
                try:
                    want = get_path(expected, path)
                    got = get_path(actual, path)
                    ok = compare(got, want, tolerance)
                    details.append({"path": path, "ok": ok, "expected": want, "actual": got})
                except KeyError as exc:
                    details.append({"path": path, "ok": False, "error": str(exc)})
                passed += 1 if details[-1]["ok"] else 0
            reward = round(passed / len(graded), 4) if graded else 0.0

    reward_path.write_text(f"{reward:.4f}\n", encoding="utf-8")
    (workspace / "verification.json").write_text(
        json.dumps({"reward": reward, "checks": details}, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    print(f"reward={reward:.4f} checks={sum(1 for d in details if d['ok'])}/{len(details)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
'''


TEST_SH = r'''#!/usr/bin/env bash
# Harbor verifier entrypoint. Harbor runs this with cwd = agent workspace.
set -u
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
WORKSPACE="$PWD"
exec python3 "$SCRIPT_DIR/verify.py" --workspace "$WORKSPACE" --tests "$SCRIPT_DIR" --reward "$WORKSPACE/reward.txt"
'''


DOCKERFILE = """FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \\
    PYTHONUNBUFFERED=1
WORKDIR /workspace
CMD ["bash"]
"""


def _toml_str(value: str) -> str:
    return json.dumps(" ".join(str(value).split()), ensure_ascii=False)


def render_task_toml(paper: dict, plan: dict) -> str:
    tags = ["paper-env", paper["topic"], paper["arxiv_id"]]
    tag_text = ", ".join(_toml_str(tag) for tag in tags)
    return (
        "[tool]\n"
        f"id = {_toml_str(paper['slug'])}\n"
        f"name = {_toml_str(plan['task_title'])}\n"
        f"description = {_toml_str(plan['one_liner'])}\n"
        f"tags = [{tag_text}]\n"
        "\n"
        "[task]\n"
        f"id = {_toml_str(paper['slug'])}\n"
        "\n"
        "[resources]\n"
        "cpus = 1\n"
        "memory_mb = 1024\n"
        "\n"
        "[agent]\n"
        'type = "react"\n'
        "\n"
        "[verifier]\n"
        'script = "tests/test.sh"\n'
        'rule = "reward_txt"\n'
    )


def _write(path: Path, text: str, mode: int | None = None) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    if mode is not None:
        path.chmod(mode)


def write_env(paper: dict, plan: dict, out_dir: Path) -> dict:
    if out_dir.exists():
        raise FileExistsError(f"environment already exists: {out_dir}")
    out_dir.mkdir(parents=True)

    _write(out_dir / "task.toml", render_task_toml(paper, plan))
    _write(out_dir / "instruction.md", plan["instruction_md"])
    _write(out_dir / "environment" / "Dockerfile", DOCKERFILE)
    _write(out_dir / "tests" / "test.sh", TEST_SH, mode=0o755)
    _write(out_dir / "tests" / "verify.py", VERIFY_PY, mode=0o755)
    _write(out_dir / "tests" / "solution" / "solve.py", plan["solution_py"], mode=0o755)
    _write(
        out_dir / "tests" / "paper.json",
        json.dumps(paper, ensure_ascii=False, indent=2) + "\n",
    )

    for item in plan["starter_files"]:
        _write(out_dir / item["path"], item["content"])

    meta = {
        "slug": paper["slug"],
        "arxiv_id": paper["arxiv_id"],
        "topic": paper["topic"],
        "task_title": plan["task_title"],
        "one_liner": plan["one_liner"],
        "difficulty": plan["difficulty"],
        "estimated_minutes": plan["estimated_minutes"],
        "paper_concepts": plan["paper_concepts"],
        "deliverable": plan["deliverable"],
        "graded_paths": plan["graded_paths"],
        "tolerances": plan["tolerances"],
        "design_rationale": plan["design_rationale"],
        "verification_notes": plan["verification_notes"],
        "starter_files": [item["path"] for item in plan["starter_files"]],
        "generated_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
    }
    _write(
        out_dir / "tests" / "env_meta.json",
        json.dumps(meta, ensure_ascii=False, indent=2) + "\n",
    )
    return meta


def prepare_workspace(env_dir: Path, dest: Path) -> None:
    """Reproduce harbor-mlx's workspace copy: starters at root, tests excluded."""
    dest.mkdir(parents=True, exist_ok=True)
    for item in env_dir.iterdir():
        if item.name in RESERVED_ROOT:
            continue
        target = dest / item.name
        if item.is_dir():
            shutil.copytree(item, target, dirs_exist_ok=True)
        else:
            shutil.copy2(item, target)


def _run_python(script: Path, cwd: Path, timeout: float) -> subprocess.CompletedProcess:
    env = {
        "PATH": os.environ.get("PATH", "/usr/bin:/bin"),
        "HOME": str(cwd),
        "PYTHONDONTWRITEBYTECODE": "1",
        "PYTHONUNBUFFERED": "1",
    }
    return subprocess.run(
        [sys.executable, "-I", str(script)],
        cwd=cwd,
        capture_output=True,
        text=True,
        timeout=timeout,
        env=env,
    )


def run_reference(
    env_dir: Path, plan: dict, *, timeout: float = 90.0, runs: int = 2
) -> tuple[dict, list[str]]:
    """Run the reference solution twice in fresh workspaces and return golden."""
    deliverable = plan["deliverable"]["path"]
    outputs: list[dict] = []
    problems: list[str] = []
    for index in range(runs):
        with tempfile.TemporaryDirectory(prefix="paper-env-ref-") as tmp:
            workspace = Path(tmp) / "workspace"
            prepare_workspace(env_dir, workspace)
            solution_copy = workspace / "_reference_solution.py"
            solution_copy.write_text(plan["solution_py"], encoding="utf-8")
            try:
                proc = _run_python(solution_copy, workspace, timeout)
            except subprocess.TimeoutExpired:
                problems.append(f"reference run {index + 1} timed out after {timeout}s")
                break
            output = (proc.stdout or "") + (proc.stderr or "")
            if proc.returncode != 0:
                problems.append(
                    f"reference run {index + 1} exited {proc.returncode}:\n{output[-4000:]}"
                )
                break
            target = workspace / deliverable
            if not target.is_file():
                problems.append(
                    f"reference run {index + 1} did not write {deliverable}:\n{output[-2000:]}"
                )
                break
            try:
                outputs.append(json.loads(target.read_text(encoding="utf-8")))
            except (json.JSONDecodeError, UnicodeDecodeError) as exc:
                problems.append(f"reference run {index + 1} wrote invalid JSON: {exc}")
                break
    if problems:
        return {}, problems
    if len(outputs) > 1 and outputs[0] != outputs[1]:
        return {}, ["reference solution is not deterministic across two runs"]
    return outputs[0], []


def write_golden(env_dir: Path, plan: dict, expected: dict) -> None:
    golden = {
        "deliverable": plan["deliverable"]["path"],
        "graded_paths": plan["graded_paths"],
        "tolerances": plan["tolerances"],
        "expected": expected,
    }
    _write(
        env_dir / "tests" / "golden.json",
        json.dumps(golden, ensure_ascii=False, indent=2) + "\n",
    )


def run_verifier(env_dir: Path, workspace: Path, *, timeout: float = 120.0) -> dict:
    script = (Path(env_dir) / "tests" / "test.sh").resolve()
    workspace = Path(workspace).resolve()
    started = time.time()
    try:
        proc = subprocess.run(
            ["bash", str(script)],
            cwd=workspace,
            capture_output=True,
            text=True,
            timeout=timeout,
        )
    except subprocess.TimeoutExpired:
        return {"reward": 0.0, "error": f"verifier timed out after {timeout}s", "elapsed": timeout}
    reward_path = workspace / "reward.txt"
    reward = 0.0
    error = None
    if reward_path.is_file():
        try:
            reward = float(reward_path.read_text(encoding="utf-8").strip())
        except ValueError:
            error = "reward.txt is not a float"
    else:
        error = "reward.txt missing"
    if proc.returncode != 0 and error is None:
        error = f"verifier exited {proc.returncode}"
    return {
        "reward": reward,
        "error": error,
        "elapsed": round(time.time() - started, 2),
        "stdout": ((proc.stdout or "") + (proc.stderr or ""))[-3000:],
    }


def validate_env(env_dir: Path, plan: dict) -> dict:
    problems: list[str] = []

    toml_path = env_dir / "task.toml"
    try:
        meta = tomllib.loads(toml_path.read_text(encoding="utf-8"))
        if not meta.get("task", {}).get("id"):
            problems.append("task.toml missing [task].id")
        if meta.get("verifier", {}).get("script") != "tests/test.sh":
            problems.append("task.toml [verifier].script must be tests/test.sh")
        if meta.get("verifier", {}).get("rule") != "reward_txt":
            problems.append("task.toml [verifier].rule must be reward_txt")
    except Exception as exc:
        problems.append(f"task.toml parse error: {exc}")

    for rel in [
        "instruction.md",
        "environment/Dockerfile",
        "tests/test.sh",
        "tests/verify.py",
        "tests/solution/solve.py",
        "tests/paper.json",
        "tests/env_meta.json",
    ]:
        if not (env_dir / rel).is_file():
            problems.append(f"missing {rel}")

    instruction = (env_dir / "instruction.md").read_text(encoding="utf-8")
    if plan["deliverable"]["path"] not in instruction:
        problems.append("instruction does not mention the deliverable path")
    if re.search(r"/Users/|https?://", instruction):
        problems.append("instruction contains an absolute path or URL")

    for rel in ["tests/verify.py", "tests/solution/solve.py"]:
        try:
            compile((env_dir / rel).read_text(encoding="utf-8"), rel, "exec")
        except SyntaxError as exc:
            problems.append(f"{rel} does not compile: {exc}")

    if problems:
        return {"ok": False, "problems": problems}

    expected, ref_problems = run_reference(env_dir, plan)
    if ref_problems:
        return {"ok": False, "problems": ref_problems, "static": "ok"}

    write_golden(env_dir, plan, expected)

    with _solution_workspace(env_dir, plan) as solution_ws:
        positive = run_verifier(env_dir, solution_ws)
    with _fresh_workspace(env_dir) as empty_ws:
        negative = run_verifier(env_dir, empty_ws)
    if positive["reward"] != 1.0:
        problems.append(
            f"positive check failed: reward={positive['reward']} error={positive['error']}"
        )
    if negative["reward"] != 0.0:
        problems.append(
            f"negative check failed: empty workspace scored {negative['reward']}"
        )

    record = {
        "ok": not problems,
        "problems": problems,
        "static": "ok",
        "checks": len(plan["graded_paths"]),
        "positive_reward": positive["reward"],
        "negative_reward": negative["reward"],
        "reference_runs": 2,
    }
    meta_path = env_dir / "tests" / "env_meta.json"
    meta = json.loads(meta_path.read_text(encoding="utf-8"))
    meta["validation"] = record
    meta_path.write_text(json.dumps(meta, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return record


@contextmanager
def _fresh_workspace(env_dir: Path):
    with tempfile.TemporaryDirectory(prefix="paper-env-neg-") as tmp:
        workspace = Path(tmp) / "workspace"
        prepare_workspace(env_dir, workspace)
        yield workspace


@contextmanager
def _solution_workspace(env_dir: Path, plan: dict, *, timeout: float = 90.0):
    with tempfile.TemporaryDirectory(prefix="paper-env-pos-") as tmp:
        workspace = Path(tmp) / "workspace"
        prepare_workspace(env_dir, workspace)
        solution_copy = workspace / "_reference_solution.py"
        solution_copy.write_text(plan["solution_py"], encoding="utf-8")
        proc = _run_python(solution_copy, workspace, timeout)
        solution_copy.unlink(missing_ok=True)
        if proc.returncode != 0:
            output = ((proc.stdout or "") + (proc.stderr or ""))[-2000:]
            raise RuntimeError(f"reference solution failed in positive check:\n{output}")
        yield workspace
