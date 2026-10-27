"""Mirror each Harbor task as a Karotte task package.

The generated Karotte task reuses the same starter fixtures, instruction and
golden output as the Harbor environment. Only the runtime adapter differs:
Karotte drives the agent through its own Step/Judge API and grades the file the
agent leaves in the student workdir.
"""

from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path

from config import KAROTTE_DIR

SCORING_SCRIPT = r'''#!/usr/bin/env python3
"""Karotte judge: compare the student deliverable with golden.json."""

from __future__ import annotations

import json
import math
import sys
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
    submission = Path(sys.argv[1])
    output = Path(sys.argv[2])
    golden = json.loads((Path(__file__).resolve().parent / "golden.json").read_text(encoding="utf-8"))
    expected = golden["expected"]
    graded = golden.get("graded_paths") or list(expected)
    tolerances = golden.get("tolerances") or {}
    details = []
    passed = 0

    if not submission.is_file():
        details.append({"path": "<deliverable>", "ok": False, "error": "missing deliverable"})
        actual = None
    else:
        try:
            actual = json.loads(submission.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, UnicodeDecodeError) as exc:
            details.append({"path": "<deliverable>", "ok": False, "error": "invalid JSON: " + str(exc)})
            actual = None

    if actual is not None:
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

    score = round(passed / len(graded), 4) if actual is not None and graded else 0.0
    output.write_text(
        json.dumps({"score": score, "metadata": {"checks": details}}, ensure_ascii=False),
        encoding="utf-8",
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
'''


TASK_TEMPLATE = '''"""Karotte mirror of the Harbor task {slug}."""

from __future__ import annotations

import shutil
import sys
from pathlib import Path
from typing import final

from karotte import Step, Task, kill_processes
from karotte.judges import ExecutableJudge

from environment import STUDENT_UID
from environment.paths import STUDENT_WORKDIR
from environment.system_prompts import get_system_prompt

_HERE = Path(__file__).resolve().parent
_DELIVERABLE = {deliverable!r}
_INSTRUCTION = {instruction!r}


@final
class PaperTask(Task):
    id = {slug!r}

    @property
    def system_prompt(self) -> str:
        return get_system_prompt(self.config.model, self.config.extra_config)

    def pre_hook(self):
        fixtures = _HERE / "fixtures"
        if fixtures.is_dir():
            for source in fixtures.rglob("*"):
                if not source.is_file():
                    continue
                target = STUDENT_WORKDIR / source.relative_to(fixtures)
                target.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(source, target)
        (STUDENT_WORKDIR / _DELIVERABLE).unlink(missing_ok=True)
        return {{}}

    @property
    def steps(self):
        return [SolveStep(config=self.config)]

    @property
    def tools(self):
        return ["bash", "view_lines_in_file", "replace_in_file"]


class SolveStep(Step):
    @property
    def instructions(self) -> str:
        return _INSTRUCTION

    @property
    def judge(self):
        return ExecutableJudge(
            [
                sys.executable,
                str(_HERE / "scoring_script.py"),
                str(STUDENT_WORKDIR / _DELIVERABLE),
                "score_output.txt",
            ]
        )

    def pre_scoring_hook(self):
        kill_processes(STUDENT_UID)
'''


def ensure_scaffold(env_dir: Path = KAROTTE_DIR) -> None:
    if (env_dir / "src" / "environment" / "tasks").is_dir():
        return
    if env_dir.exists():
        raise RuntimeError(f"{env_dir} exists but is not a Karotte environment")
    subprocess.run(
        ["karotte", "create-env", str(env_dir), "--template", "default"],
        check=True,
        capture_output=True,
        text=True,
    )


def retire_example_task(env_dir: Path = KAROTTE_DIR) -> None:
    example = env_dir / "src" / "environment" / "tasks" / "example_task"
    if not example.exists():
        return
    stale = env_dir / "_stale" / "example_task"
    stale.parent.mkdir(parents=True, exist_ok=True)
    if stale.exists():
        shutil.rmtree(stale)
    shutil.move(str(example), str(stale))


def add_task(
    env_dir: Path,
    paper: dict,
    plan: dict,
    golden_path: Path,
) -> Path:
    tasks_dir = env_dir / "src" / "environment" / "tasks"
    task_dir = tasks_dir / paper["slug"]
    if task_dir.exists():
        raise FileExistsError(f"Karotte task already exists: {task_dir}")
    task_dir.mkdir(parents=True)

    instruction = plan["instruction_md"].rstrip()
    if plan["deliverable"]["path"] not in instruction:
        instruction += f"\n\nWrite the deliverable to `{plan['deliverable']['path']}`.\n"
    if "work in" not in instruction.lower():
        instruction += (
            f"\n\nWork in {STUDENT_WORKDIR_TOKEN}. The graded file is "
            f"`{plan['deliverable']['path']}` in that directory.\n"
        )

    task_dir.joinpath("__init__.py").write_text(
        TASK_TEMPLATE.format(
            slug=paper["slug"],
            deliverable=plan["deliverable"]["path"],
            instruction=instruction,
        ),
        encoding="utf-8",
    )
    task_dir.joinpath("scoring_script.py").write_text(SCORING_SCRIPT, encoding="utf-8")
    shutil.copy2(golden_path, task_dir / "golden.json")

    fixtures = task_dir / "fixtures"
    for item in plan["starter_files"]:
        target = fixtures / item["path"]
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(item["content"], encoding="utf-8")

    task_dir.joinpath("README.md").write_text(
        f"# {plan['task_title']}\n\n"
        f"Harbor mirror: `envs/{paper['slug']}/`.\n\n"
        f"Source paper: `{paper['arxiv_id']}` — {paper['title']}\n\n"
        "Run inside the Karotte container:\n\n"
        "```bash\n"
        f"karotte run --task {paper['slug']} --model <model>\n"
        "```\n",
        encoding="utf-8",
    )
    return task_dir


# Rendered literally at runtime by Karotte; kept out of the f-string above.
STUDENT_WORKDIR_TOKEN = "/workdir"
