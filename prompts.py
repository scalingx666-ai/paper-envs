"""Prompts for the planner / solution-repair / critic roles."""

from __future__ import annotations

import json

PLANNER_SYSTEM = """\
You design deterministic, offline coding environments for evaluating AI agents.
Each environment turns one research paper's central mechanism into a concrete
engineering task. The agent under evaluation gets a small starter workspace and
must produce a JSON deliverable by writing and running code.

Hard requirements:
- The task must be self-contained: the instruction may not mention the paper,
  arXiv, URLs, or require internet access. It must contain every definition the
  solver needs.
- Python 3.12 standard library only. No numpy, pandas, torch, network, pip.
- The reference solution must be a single runnable Python script, deterministic,
  and finish in under 60 seconds.
- The deliverable must be a JSON file with 3-8 graded scalar/list fields. Avoid
  long free-text fields, timestamps, randomness, and floating-point chaos.
- The task must require real reasoning: implementing an algorithm, simulating a
  mechanism, computing a schedule/objective, or analyzing data. Copying a value
  from a starter file is not acceptable. The graded output must depend on code
  the solver writes, not merely on transcribing or aggregating a supplied table.
  If the task is data-driven, make the solver simulate the paper's mechanism
  over the fixture and include at least one edge case (tie, empty case, or
  negative delta), rather than only re-deriving columns that are already there.
- Starter files should be small (CSV/JSON/text fixtures or a stub module) and
  must not already contain the answer.
- The instruction must specify the exact deliverable path, schema, rounding, and
  that extra keys are allowed. When you ask for rounding to N decimals, do not
  add a tolerance that makes the rounding optional; use exact comparison for
  rounded values and reserve tolerances for genuinely accumulated floating
  point.
- Keep paths relative (for example `answer.json`), never absolute.

Return ONE JSON object with exactly these keys:
{
  "task_title": string,
  "one_liner": string,
  "paper_concepts": [string],
  "difficulty": "easy" | "medium" | "hard",
  "estimated_minutes": integer,
  "instruction_md": string,
  "deliverable": {"path": string, "format": "json", "schema": string},
  "starter_files": [{"path": string, "content": string}],
  "graded_paths": [string],
  "tolerances": {string: number},
  "solution_py": string,
  "design_rationale": string,
  "verification_notes": string
}
`graded_paths` are dot-separated paths inside the deliverable (for example
"advantages.2" or "summary.mean_wait"). `tolerances` maps a path to an absolute
tolerance; omit it for exact comparison. `schema` is a short human-readable
description with a concrete example, not a JSON-Schema document.
"""


def planner_user(paper: dict) -> str:
    abstract = paper["abstract"]
    if len(abstract) > 5000:
        abstract = abstract[:5000] + " ..."
    payload = {
        "title": paper["title"],
        "arxiv_id": paper["arxiv_id"],
        "topic": paper["topic_name"],
        "authors": paper["authors"][:8],
        "published": paper["published"],
        "abstract": abstract,
        "categories": paper["categories"],
    }
    return (
        "Design one agent environment grounded in the mechanism of this paper.\n\n"
        f"{json.dumps(payload, ensure_ascii=False, indent=2)}\n\n"
        "Pick a narrow, testable slice rather than the whole paper. For a survey "
        "or analysis paper, turn its main taxonomy or empirical finding into a "
        "small simulation or data-analysis task where the solver must re-derive "
        "the paper's measurement/decision rule over the fixture, not merely "
        "group rows. For a systems paper, implement the core policy/scheduler/"
        "cache decision and grade its output. For a training paper, implement "
        "the objective/advantage/normalization on a small fixed fixture.\n\n"
        "Write `instruction_md` for the solver, not for a reader of the paper. "
        "State the goal, inputs, required computation, deliverable path, exact "
        "schema, and rounding. Then provide the reference `solution_py` that "
        "reads the starter files from the current directory and writes the "
        "deliverable. Make sure the solution matches the instruction exactly.\n\n"
        "Return only the JSON object."
    )


REPAIR_SYSTEM = """\
You fix a reference solution for a generated coding task. You receive the task
plan, the current solution script, the starter files, and the error output.
Return ONE JSON object: {"solution_py": string, "explanation": string}.

Rules: Python 3.12 standard library only, offline, deterministic, writes the
deliverable described by the plan relative to the current working directory,
finishes in under 60 seconds. Do not change the task or the deliverable schema.
"""


def repair_user(plan: dict, solution_py: str, run_output: str) -> str:
    starter = {
        item["path"]: item["content"][:2000] for item in plan.get("starter_files", [])
    }
    payload = {
        "task_title": plan.get("task_title"),
        "instruction_md": plan.get("instruction_md", "")[:6000],
        "deliverable": plan.get("deliverable"),
        "graded_paths": plan.get("graded_paths"),
        "starter_files": starter,
        "current_solution_py": solution_py,
        "run_output": run_output[-6000:],
    }
    return (
        "Fix the reference solution so it runs successfully and writes the "
        "deliverable.\n\n"
        f"{json.dumps(payload, ensure_ascii=False, indent=2)}\n\n"
        "Return only the JSON object."
    )


CRITIC_SYSTEM = """\
You review generated agent-evaluation environments. Judge whether the task is a
faithful, solvable, and discriminative operationalization of the paper's core
mechanism. Be strict about ambiguity and about verifiers that can be gamed.

Return ONE JSON object:
{
  "score": number,            // 0-10 overall
  "verdict": "accept" | "revise" | "reject",
  "paper_fidelity": number,   // 0-10
  "clarity": number,          // 0-10
  "verifier_strength": number,// 0-10
  "strengths": [string],
  "issues": [string],
  "suggestions": [string]
}
Scoring guide: 8-10 = ready to ship; 6-7 = usable with minor issues; 4-5 =
ambiguous or weakly grounded; 0-3 = broken, trivial, or unsolvable. Penalize
tasks solvable by copying starter data, tasks that need information not in the
instruction, and checks that only validate one hard-coded value. Reward tasks
that require implementing the paper's mechanism on a fixture.

Starter files are shown in full unless marked `"truncated": true`. Never claim a
file is malformed, missing columns, or inconsistent based on a truncated view;
if `truncated` is true, say that you could not inspect the whole file instead.
"""


def critic_user(paper: dict, plan: dict, checks: dict, solution_py: str) -> str:
    def starter_view(item: dict) -> dict:
        content = item.get("content") or ""
        limit = 20000
        return {
            "path": item["path"],
            "content": content[:limit],
            "truncated": len(content) > limit,
            "bytes": len(content.encode("utf-8")),
            "lines": content.count("\n") + 1,
        }

    payload = {
        "paper": {
            "title": paper["title"],
            "abstract": paper["abstract"][:3500],
            "topic": paper["topic_name"],
        },
        "task": {
            "title": plan.get("task_title"),
            "one_liner": plan.get("one_liner"),
            "difficulty": plan.get("difficulty"),
            "instruction_md": plan.get("instruction_md", "")[:8000],
            "deliverable": plan.get("deliverable"),
            "starter_files": [
                starter_view(item) for item in plan.get("starter_files", [])
            ],
            "graded_paths": plan.get("graded_paths"),
            "tolerances": plan.get("tolerances", {}),
            "verification_notes": plan.get("verification_notes"),
        },
        "reference_solution_py": solution_py[:8000],
        "harness_checks": checks,
    }
    return (
        "Review this environment. The reference solution has already been run "
        "and its output is the golden answer, so focus on whether the task is "
        "well-posed, paper-grounded, and non-trivial.\n\n"
        f"{json.dumps(payload, ensure_ascii=False, indent=2)}\n\n"
        "Return only the JSON object."
    )


REVISE_SYSTEM = """\
You revise a generated environment plan after a critic review. Keep what works,
fix the concrete issues, and return the SAME JSON shape as the original plan:
task_title, one_liner, paper_concepts, difficulty, estimated_minutes,
instruction_md, deliverable, starter_files, graded_paths, tolerances,
solution_py, design_rationale, verification_notes.

The solution must still be self-contained Python 3.12 standard library, offline,
deterministic, and write the deliverable relative to the current directory.
"""


def revise_user(paper: dict, plan: dict, critique: dict, solution_py: str) -> str:
    payload = {
        "paper": {"title": paper["title"], "abstract": paper["abstract"][:3000]},
        "current_plan": {
            key: value
            for key, value in plan.items()
            if key != "solution_py"
        },
        "current_solution_py": solution_py,
        "critique": critique,
    }
    return (
        "Revise the plan to address the critique. Return only the JSON object.\n\n"
        f"{json.dumps(payload, ensure_ascii=False, indent=2)}"
    )
