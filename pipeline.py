"""Paper -> Harbor/Karotte agent-environment pipeline.

Stages per paper:
  1. Planner (tierflow_pro, effort=max) designs a deterministic offline task.
  2. Builder renders the Harbor task and runs the reference solution twice.
  3. Validator checks layout, positive reward (1.0) and negative reward (0.0).
  4. Critic reviews paper fidelity / clarity / verifier strength.
  5. Optional revision when the critic score is below the threshold.
  6. MCode evaluator solves the environment end-to-end and reports reward.
"""

from __future__ import annotations

import argparse
import fcntl
import json
import shutil
import sys
import time
import traceback
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from config import ENVS_DIR, KAROTTE_DIR, RUNS_DIR, SURVEY_CSV, Settings, load_settings, topic_name
from harbor import PlanError, normalize_plan, validate_env, write_env
from karotte_mirror import add_task as add_karotte_task
from karotte_mirror import ensure_scaffold, retire_example_task
from llm import LLMClient, LLMError
from mcode import evaluate_environment
from papers import Paper, load_papers
from prompts import (
    CRITIC_SYSTEM,
    PLANNER_SYSTEM,
    REPAIR_SYSTEM,
    REVISE_SYSTEM,
    critic_user,
    planner_user,
    repair_user,
    revise_user,
)


def log(message: str) -> None:
    stamp = time.strftime("%H:%M:%S")
    print(f"[{stamp}] {message}", flush=True)


@contextmanager
def _slug_lock(slug: str):
    lock_dir = RUNS_DIR / ".locks"
    lock_dir.mkdir(parents=True, exist_ok=True)
    handle = (lock_dir / f"{slug}.lock").open("w")
    try:
        fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError as exc:
        handle.close()
        raise RuntimeError(f"another pipeline is already generating {slug}") from exc
    try:
        yield
    finally:
        fcntl.flock(handle, fcntl.LOCK_UN)
        handle.close()


def _write_json(path: Path, data: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(data, ensure_ascii=False, indent=2, default=str) + "\n",
        encoding="utf-8",
    )


def _move_aside(path: Path, label: str) -> Path | None:
    if not path.exists():
        return None
    stale = path.parent / "_stale"
    stale.mkdir(parents=True, exist_ok=True)
    stamp = time.strftime("%Y%m%d-%H%M%S")
    dest = stale / f"{label}-{stamp}"
    suffix = 1
    while dest.exists():
        dest = stale / f"{label}-{stamp}-{suffix}"
        suffix += 1
    shutil.move(str(path), str(dest))
    return dest


def _generate_plan(
    client: LLMClient, paper: Paper, feedback: str = ""
) -> tuple[dict, dict]:
    user = planner_user(paper.to_dict()) + feedback
    raw = client.chat_json(PLANNER_SYSTEM, user, max_tokens=60000)
    try:
        return normalize_plan(raw), raw
    except PlanError as exc:
        retry = (
            f"\n\nYour previous plan failed validation: {exc}. "
            "Return a complete corrected JSON object with every required key."
        )
        raw2 = client.chat_json(PLANNER_SYSTEM, user + retry, max_tokens=60000)
        return normalize_plan(raw2), raw2


def _repair_solution(
    client: LLMClient, plan: dict, problems: list[str]
) -> dict:
    raw = client.chat_json(
        REPAIR_SYSTEM,
        repair_user(plan, plan["solution_py"], "\n".join(problems)),
        max_tokens=30000,
    )
    solution = raw.get("solution_py") if isinstance(raw, dict) else None
    if not isinstance(solution, str) or len(solution.strip()) < 80:
        raise LLMError("solution repair did not return a usable solution_py")
    solution = solution.strip()
    if solution.startswith("```"):
        solution = solution.split("\n", 1)[-1]
        if solution.rstrip().endswith("```"):
            solution = solution.rstrip()[:-3]
    updated = dict(plan)
    updated["solution_py"] = solution.strip() + "\n"
    return updated


def _paper_dict(paper: Paper | dict) -> dict:
    return paper.to_dict() if hasattr(paper, "to_dict") else paper


def _critique(client: LLMClient, paper: Paper | dict, plan: dict, validation: dict) -> dict:
    checks = {
        "deliverable": plan["deliverable"],
        "graded_paths": plan["graded_paths"],
        "tolerances": plan["tolerances"],
        "validation": validation,
    }
    raw = client.chat_json(
        CRITIC_SYSTEM,
        critic_user(_paper_dict(paper), plan, checks, plan["solution_py"]),
        max_tokens=30000,
    )
    if not isinstance(raw, dict):
        raise LLMError("critic did not return a JSON object")
    score = raw.get("score", 0)
    try:
        raw["score"] = max(0.0, min(10.0, float(score)))
    except (TypeError, ValueError):
        raw["score"] = 0.0
    return raw


def _revise_plan(
    client: LLMClient, paper: Paper, plan: dict, critique: dict
) -> dict:
    raw = client.chat_json(
        REVISE_SYSTEM,
        revise_user(paper.to_dict(), plan, critique, plan["solution_py"]),
        max_tokens=60000,
    )
    return normalize_plan(raw)


def _build_and_validate(
    client: LLMClient,
    paper: Paper,
    plan: dict,
    staging: Path,
    run_dir: Path,
    *,
    max_attempts: int = 3,
) -> tuple[dict, dict, list[dict]]:
    attempts: list[dict] = []
    for attempt in range(1, max_attempts + 1):
        if staging.exists():
            _move_aside(staging, f"staging-{paper.slug}-{attempt}")
        write_env(paper.to_dict(), plan, staging)
        log(f"  validating attempt {attempt}/{max_attempts}")
        validation = validate_env(staging, plan)
        attempts.append({"attempt": attempt, "validation": validation})
        if validation["ok"]:
            return plan, validation, attempts

        problems = validation.get("problems") or []
        log(f"  validation failed: {problems[0][:180] if problems else 'unknown'}")
        if attempt >= max_attempts:
            break

        reference_broken = any(
            p.startswith("reference run") or "not deterministic" in p for p in problems
        )
        if reference_broken:
            try:
                plan = _repair_solution(client, plan, problems)
                log("  applied reference-solution repair")
            except (LLMError, PlanError) as exc:
                log(f"  solution repair failed: {exc}")
                break
        else:
            try:
                plan, _ = _generate_plan(
                    client,
                    paper,
                    "\n\nYour previous plan failed local validation with these "
                    f"problems:\n- " + "\n- ".join(problems[:6]) + "\n"
                    "Return a corrected full JSON object.",
                )
                log("  regenerated plan after validation failure")
            except (LLMError, PlanError) as exc:
                log(f"  plan regeneration failed: {exc}")
                break

    raise RuntimeError(
        "environment failed local validation: "
        + "; ".join((attempts[-1]["validation"].get("problems") or ["unknown"])[:4])
    )


def process_paper(
    client: LLMClient,
    paper: Paper,
    run_dir: Path,
    *,
    settings: Settings,
    run_mcode: bool,
    revisions: int,
    critic_threshold: float,
    force: bool,
    build_karotte: bool,
) -> dict:
    started = time.time()
    slug = paper.slug
    paper_dir = run_dir / slug
    paper_dir.mkdir(parents=True, exist_ok=True)
    staging = run_dir / "staging" / slug
    record: dict[str, Any] = {
        "slug": slug,
        "arxiv_id": paper.arxiv_id,
        "title": paper.title,
        "topic": paper.topic,
        "topic_name": topic_name(paper.topic),
        "started_at": datetime.now(timezone.utc).isoformat(),
        "timings": {},
    }
    log(f"[{paper.topic}] {paper.arxiv_id} {paper.title[:80]}")

    env_dir = ENVS_DIR / slug
    if env_dir.exists() and not force:
        raise RuntimeError(f"environment already exists (use --force): {env_dir}")

    t0 = time.time()
    plan, raw_plan = _generate_plan(client, paper)
    record["timings"]["plan_s"] = round(time.time() - t0, 1)
    _write_json(paper_dir / "plan.json", plan)
    _write_json(paper_dir / "plan_raw.json", raw_plan)
    log(
        f"  plan: {plan['task_title']} ({plan['difficulty']}, "
        f"{len(plan['graded_paths'])} checks)"
    )

    t0 = time.time()
    plan, validation, attempts = _build_and_validate(
        client, paper, plan, staging, run_dir
    )
    record["timings"]["build_validate_s"] = round(time.time() - t0, 1)
    record["validation_attempts"] = attempts
    _write_json(paper_dir / "validation.json", validation)
    log(
        f"  validated: reference={validation['positive_reward']} "
        f"empty={validation['negative_reward']} in {record['timings']['build_validate_s']}s"
    )

    t0 = time.time()
    critique = _critique(client, paper, plan, validation)
    record["timings"]["critic_s"] = round(time.time() - t0, 1)
    critiques = [critique]
    log(f"  critic: {critique['score']}/10 — {critique.get('verdict')}")

    for revision in range(revisions):
        if critique["score"] >= critic_threshold:
            break
        log(f"  revising plan (score {critique['score']} < {critic_threshold})")
        t0 = time.time()
        revised = _revise_plan(client, paper, plan, critique)
        candidate_plan, candidate_validation, candidate_attempts = _build_and_validate(
            client, paper, revised, staging, run_dir
        )
        candidate_critique = _critique(client, paper, candidate_plan, candidate_validation)
        record["timings"][f"revision_{revision + 1}_s"] = round(time.time() - t0, 1)
        critiques.append(candidate_critique)
        log(
            f"  revised critic: {candidate_critique['score']}/10 — "
            f"{candidate_critique.get('verdict')}"
        )
        plan, validation = candidate_plan, candidate_validation
        record["validation_attempts"] += candidate_attempts
        critique = candidate_critique

    _write_json(paper_dir / "critiques.json", critiques)

    # Publish.
    if env_dir.exists():
        _move_aside(env_dir, slug)
    ENVS_DIR.mkdir(parents=True, exist_ok=True)
    shutil.move(str(staging), str(env_dir))
    log(f"  published {env_dir.relative_to(ENVS_DIR.parent)}")

    karotte_task = None
    if build_karotte:
        try:
            ensure_scaffold()
            retire_example_task()
            existing = KAROTTE_DIR / "src" / "environment" / "tasks" / slug
            if existing.exists():
                _move_aside(existing, slug)
            karotte_task = add_karotte_task(
                KAROTTE_DIR, paper.to_dict(), plan, env_dir / "tests" / "golden.json"
            )
            log(f"  karotte task {karotte_task.name}")
        except Exception as exc:
            record["karotte_error"] = str(exc)
            log(f"  karotte mirror failed: {exc}")

    mcode_record = None
    if run_mcode:
        log("  running MCode evaluator (effort=max) ...")
        t0 = time.time()
        try:
            mcode_record = evaluate_environment(
                env_dir,
                plan["instruction_md"],
                plan["deliverable"]["path"],
                paper_dir / "mcode",
                timeout="4m",
                max_steps=16,
            )
            log(
                f"  mcode: status={mcode_record['status']} "
                f"reward={mcode_record['reward']} in {mcode_record['elapsed']}s"
            )
        except Exception as exc:
            mcode_record = {"status": "error", "reward": 0.0, "error": str(exc)}
            log(f"  mcode failed: {exc}")
        record["timings"]["mcode_s"] = round(time.time() - t0, 1)

    meta_path = env_dir / "tests" / "env_meta.json"
    meta = json.loads(meta_path.read_text(encoding="utf-8"))
    meta["validation"] = validation
    meta["critic"] = critique
    meta["critiques"] = critiques
    meta["mcode"] = mcode_record
    meta["timings"] = record["timings"]
    meta["model"] = {"name": settings.model, "effort": settings.effort}
    _write_json(meta_path, meta)

    record.update(
        {
            "env_path": str(env_dir),
            "karotte_task": str(karotte_task) if karotte_task else None,
            "plan": plan,
            "validation": validation,
            "critic": critique,
            "critiques": critiques,
            "mcode": mcode_record,
            "finished_at": datetime.now(timezone.utc).isoformat(),
            "ok": True,
        }
    )
    _write_json(paper_dir / "record.json", record)
    log(
        f"  done in {round(time.time() - started, 1)}s "
        f"(critic {critique['score']}/10, mcode reward {mcode_record['reward'] if mcode_record else 'n/a'})"
    )
    return record


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--csv", default=None, help="survey CSV path")
    parser.add_argument("--limit-per-topic", type=int, default=1)
    parser.add_argument("--only", default="", help="comma-separated arXiv ids or slugs")
    parser.add_argument("--run-id", default=None)
    parser.add_argument("--force", action="store_true", help="replace existing environments")
    parser.add_argument("--revisions", type=int, default=1)
    parser.add_argument("--critic-threshold", type=float, default=6.0)
    parser.add_argument("--mcode", dest="mcode", action="store_true", default=True)
    parser.add_argument("--no-mcode", dest="mcode", action="store_false")
    parser.add_argument("--mcode-limit", type=int, default=2, help="0 = all environments")
    parser.add_argument("--no-karotte", dest="karotte", action="store_false", default=True)
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args(argv)

    settings = load_settings()
    only = [item for item in args.only.split(",") if item.strip()]
    csv_path = args.csv or str(SURVEY_CSV)
    papers = load_papers(
        csv_path,
        limit_per_topic=args.limit_per_topic,
        only=only or None,
    )
    if not papers:
        print("no papers selected", file=sys.stderr)
        return 2

    if args.dry_run:
        for paper in papers:
            print(f"{paper.topic:18} {paper.arxiv_id:18} {paper.slug:50} {paper.title[:70]}")
        return 0

    if not settings.has_key():
        print(
            "no API key found. Set PAPER_ENV_API_KEY or create paper-envs/.env.local.",
            file=sys.stderr,
        )
        return 2

    run_id = args.run_id or datetime.now().strftime("%Y%m%d-%H%M%S")
    run_dir = RUNS_DIR / run_id
    run_dir.mkdir(parents=True, exist_ok=True)
    client = LLMClient(settings)

    _write_json(
        run_dir / "run.json",
        {
            "run_id": run_id,
            "started_at": datetime.now(timezone.utc).isoformat(),
            "papers": [paper.to_dict() for paper in papers],
            "model": {"name": settings.model, "effort": settings.effort},
            "mcode": {"model": "custom_provider:tierflow/tierflow_pro", "effort": "max"},
            "limit_per_topic": args.limit_per_topic,
        },
    )

    results: list[dict] = []
    failures: list[dict] = []
    mcode_budget = args.mcode_limit if args.mcode_limit > 0 else len(papers)
    mcode_used = 0
    for paper in papers:
        use_mcode = args.mcode and mcode_used < mcode_budget
        try:
            with _slug_lock(paper.slug):
                record = process_paper(
                    client,
                    paper,
                    run_dir,
                    settings=settings,
                    run_mcode=use_mcode,
                    revisions=args.revisions,
                    critic_threshold=args.critic_threshold,
                    force=args.force,
                    build_karotte=args.karotte,
                )
            if use_mcode:
                mcode_used += 1
            results.append(record)
        except Exception as exc:
            failures.append(
                {
                    "slug": paper.slug,
                    "arxiv_id": paper.arxiv_id,
                    "title": paper.title,
                    "error": f"{type(exc).__name__}: {exc}",
                    "traceback": traceback.format_exc()[-4000:],
                }
            )
            log(f"  FAILED: {type(exc).__name__}: {exc}")

    summary = {
        "run_id": run_id,
        "finished_at": datetime.now(timezone.utc).isoformat(),
        "requested": len(papers),
        "succeeded": len(results),
        "failed": len(failures),
        "llm_calls": client.calls,
        "llm_usage": client.usage_totals,
        "avg_critic": round(
            sum(item["critic"]["score"] for item in results) / len(results), 2
        )
        if results
        else 0.0,
        "mcode_runs": len([item for item in results if item.get("mcode")]),
        "mcode_pass": len(
            [
                item
                for item in results
                if item.get("mcode") and item["mcode"].get("reward") == 1.0
            ]
        ),
        "results": results,
        "failures": failures,
    }
    _write_json(run_dir / "summary.json", summary)
    log(
        f"run {run_id}: {len(results)} ok, {len(failures)} failed, "
        f"llm calls={client.calls}, usage={client.usage_totals}"
    )

    try:
        from dashboard import build_dashboard

        path = build_dashboard()
        log(f"dashboard: {path}")
    except Exception as exc:
        log(f"dashboard failed: {type(exc).__name__}: {exc}")

    return 0 if not failures else 1


if __name__ == "__main__":
    raise SystemExit(main())
