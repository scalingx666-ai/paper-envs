"""Build a standalone dashboard for generated paper environments.

The page is intentionally dependency-free: one HTML file with inline CSS/JS and
the dataset embedded as JSON, so it can be opened offline or served over
127.0.0.1.
"""

from __future__ import annotations

import json
import time
import tomllib
from pathlib import Path

from config import (
    DASHBOARD_HTML,
    DASHBOARD_JSON,
    ENVS_DIR,
    KAROTTE_DIR,
    ROOT,
    RUNS_DIR,
    TOPIC_ACCENTS,
    TOPICS,
)

_TOPIC_ORDER = list(TOPICS)


def _read_json(path: Path, default=None):
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return default


def _uri(path: Path) -> str:
    try:
        return path.resolve().relative_to(ROOT).as_posix()
    except ValueError:
        return path.resolve().as_uri()


def _tree(root: Path, limit: int = 40) -> list[str]:
    entries = []
    for path in sorted(root.rglob("*")):
        if not path.is_file():
            continue
        rel = path.relative_to(root).as_posix()
        if "/__pycache__/" in f"/{rel}" or rel.endswith(".pyc"):
            continue
        entries.append(rel)
        if len(entries) >= limit:
            break
    return entries


def collect() -> dict:
    papers = []
    if ENVS_DIR.is_dir():
        for env_dir in sorted(ENVS_DIR.iterdir()):
            if env_dir.name.startswith("_") or not (env_dir / "task.toml").is_file():
                continue
            paper = _read_json(env_dir / "tests" / "paper.json", {}) or {}
            meta = _read_json(env_dir / "tests" / "env_meta.json", {}) or {}
            golden = _read_json(env_dir / "tests" / "golden.json", {}) or {}
            solution = ""
            solution_path = env_dir / "tests" / "solution" / "solve.py"
            if solution_path.is_file():
                solution = solution_path.read_text(encoding="utf-8", errors="replace")
            instruction = ""
            instr_path = env_dir / "instruction.md"
            if instr_path.is_file():
                instruction = instr_path.read_text(encoding="utf-8", errors="replace")
            verifier = ""
            verifier_path = env_dir / "tests" / "test.sh"
            if verifier_path.is_file():
                verifier = verifier_path.read_text(encoding="utf-8", errors="replace")
            task_toml = {}
            try:
                task_toml = tomllib.loads((env_dir / "task.toml").read_text(encoding="utf-8"))
            except (OSError, tomllib.TOMLDecodeError):
                pass

            topic = paper.get("topic") or "other"
            validation = meta.get("validation") or {}
            critic = meta.get("critic") or {}
            mcode = meta.get("mcode")
            papers.append(
                {
                    "slug": env_dir.name,
                    "topic": topic,
                    "topic_name": TOPICS.get(topic, topic),
                    "accent": TOPIC_ACCENTS.get(topic, "#94a3b8"),
                    "arxiv_id": paper.get("arxiv_id", ""),
                    "arxiv_base": paper.get("arxiv_base", ""),
                    "title": paper.get("title", env_dir.name),
                    "abstract": paper.get("abstract", ""),
                    "authors": paper.get("authors", []),
                    "score": paper.get("score", 0),
                    "url": paper.get("url", ""),
                    "published": paper.get("published", ""),
                    "categories": paper.get("categories", []),
                    "task_title": meta.get("task_title") or task_toml.get("tool", {}).get("name", ""),
                    "one_liner": meta.get("one_liner") or task_toml.get("tool", {}).get("description", ""),
                    "difficulty": meta.get("difficulty", "medium"),
                    "estimated_minutes": meta.get("estimated_minutes"),
                    "concepts": meta.get("paper_concepts", []),
                    "deliverable": meta.get("deliverable", {}),
                    "graded_paths": meta.get("graded_paths", []),
                    "tolerances": meta.get("tolerances", {}),
                    "starter_files": meta.get("starter_files", []),
                    "design_rationale": meta.get("design_rationale", ""),
                    "verification_notes": meta.get("verification_notes", ""),
                    "instruction": instruction,
                    "verifier": verifier,
                    "solution": solution,
                    "golden": golden,
                    "validation": validation,
                    "critic": critic,
                    "critiques": meta.get("critiques", []),
                    "mcode": mcode,
                    "timings": meta.get("timings", {}),
                    "model": meta.get("model"),
                    "files": _tree(env_dir),
                    "paths": {
                        "env": _uri(env_dir),
                        "instruction": _uri(env_dir / "instruction.md"),
                        "task_toml": _uri(env_dir / "task.toml"),
                        "test_sh": _uri(env_dir / "tests" / "test.sh"),
                        "solution": _uri(solution_path),
                        "paper": _uri(env_dir / "tests" / "paper.json"),
                        "relative": f"envs/{env_dir.name}/",
                    },
                    "harbor": {
                        "id": task_toml.get("task", {}).get("id", env_dir.name),
                        "verifier": task_toml.get("verifier", {}).get("script", ""),
                        "rule": task_toml.get("verifier", {}).get("rule", ""),
                        "cpus": task_toml.get("resources", {}).get("cpus"),
                        "memory_mb": task_toml.get("resources", {}).get("memory_mb"),
                        "agent": task_toml.get("agent", {}).get("type", ""),
                    },
                }
            )

    papers.sort(
        key=lambda item: (
            _TOPIC_ORDER.index(item["topic"]) if item["topic"] in _TOPIC_ORDER else 99,
            -float(item["score"] or 0),
            item["slug"],
        )
    )

    mcode_runs = [p for p in papers if p.get("mcode")]
    mcode_pass = [p for p in mcode_runs if p["mcode"].get("reward") == 1.0]
    critics = [p["critic"]["score"] for p in papers if p.get("critic", {}).get("score") is not None]
    by_topic = []
    for topic in _TOPIC_ORDER:
        items = [p for p in papers if p["topic"] == topic]
        by_topic.append(
            {
                "id": topic,
                "name": TOPICS[topic],
                "accent": TOPIC_ACCENTS[topic],
                "count": len(items),
                "mcode_pass": len(
                    [p for p in items if p.get("mcode") and p["mcode"].get("reward") == 1.0]
                ),
            }
        )

    karotte_tasks = []
    tasks_dir = KAROTTE_DIR / "src" / "environment" / "tasks"
    if tasks_dir.is_dir():
        for task_dir in sorted(tasks_dir.iterdir()):
            if task_dir.is_dir() and not task_dir.name.startswith("_"):
                karotte_tasks.append(task_dir.name)

    latest_run = {}
    summaries = sorted(
        RUNS_DIR.glob("*/summary.json"), key=lambda path: path.stat().st_mtime, reverse=True
    )
    if summaries:
        summary = _read_json(summaries[0], {}) or {}
        latest_run = {
            "run_id": summary.get("run_id"),
            "requested": summary.get("requested"),
            "succeeded": summary.get("succeeded"),
            "failed": summary.get("failed"),
            "llm_calls": summary.get("llm_calls"),
            "llm_usage": summary.get("llm_usage"),
            "mcode_runs": summary.get("mcode_runs"),
            "mcode_pass": summary.get("mcode_pass"),
        }

    return {
        "generated_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "model": (papers[0].get("model") if papers else None) or {"name": "tierflow_pro", "effort": "max"},
        "summary": {
            "papers": len(papers),
            "topics": len([t for t in by_topic if t["count"]]),
            "checks": sum(len(p["graded_paths"]) for p in papers),
            "validated": len([p for p in papers if p.get("validation", {}).get("ok")]),
            "avg_critic": round(sum(critics) / len(critics), 2) if critics else 0.0,
            "mcode_runs": len(mcode_runs),
            "mcode_pass": len(mcode_pass),
            "karotte_tasks": len(karotte_tasks),
        },
        "run": latest_run,
        "topics": by_topic,
        "papers": papers,
        "karotte": {
            "path": _uri(KAROTTE_DIR),
            "tasks": karotte_tasks,
        },
    }


HTML = r"""<!doctype html>
<html lang="zh-CN">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Paper → Agent Environment · Harbor × Karotte</title>
<style>
  :root {
    --bg: #070b12;
    --panel: #0d1420;
    --panel-2: #111b2b;
    --line: #1d2a3d;
    --text: #e6edf7;
    --muted: #8ea0b8;
    --dim: #5f738d;
    --good: #34d399;
    --warn: #fbbf24;
    --bad: #fb7185;
    --accent: #5eead4;
    --radius: 16px;
    --mono: ui-monospace, SFMono-Regular, Menlo, Consolas, monospace;
  }
  * { box-sizing: border-box; }
  html, body { margin: 0; background: var(--bg); color: var(--text); }
  body {
    font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", "PingFang SC", "Hiragino Sans GB", "Microsoft YaHei", sans-serif;
    line-height: 1.55;
    background-image:
      radial-gradient(900px 500px at 12% -10%, rgba(94,234,212,.10), transparent 60%),
      radial-gradient(900px 500px at 88% 0%, rgba(192,132,252,.10), transparent 60%),
      linear-gradient(180deg, #070b12 0%, #070b12 60%, #060a10 100%);
    min-height: 100vh;
  }
  a { color: var(--accent); text-decoration: none; }
  a:hover { text-decoration: underline; }
  .wrap { max-width: 1440px; margin: 0 auto; padding: 34px 28px 80px; }
  header.hero { display: flex; flex-direction: column; gap: 18px; margin-bottom: 28px; }
  .eyebrow { color: var(--accent); font-size: 12px; letter-spacing: .18em; text-transform: uppercase; font-weight: 700; }
  h1 { font-size: clamp(28px, 4vw, 46px); line-height: 1.12; margin: 0; letter-spacing: -.02em; }
  h1 .grad {
    background: linear-gradient(100deg, #5eead4 0%, #7dd3fc 40%, #c084fc 80%);
    -webkit-background-clip: text; background-clip: text; color: transparent;
  }
  .sub { color: var(--muted); max-width: 940px; font-size: 15px; }
  .pipeline {
    display: flex; flex-wrap: wrap; gap: 8px; align-items: center;
    margin-top: 6px; font-size: 12px; color: var(--muted);
  }
  .pipeline .node {
    border: 1px solid var(--line); background: rgba(17,27,43,.7);
    padding: 6px 11px; border-radius: 999px; display: inline-flex; gap: 7px; align-items: center;
  }
  .pipeline .node b { color: var(--text); font-weight: 650; }
  .pipeline .arrow { color: var(--dim); }
  .stats { display: grid; grid-template-columns: repeat(6, minmax(0,1fr)); gap: 12px; margin: 8px 0 6px; }
  .stat {
    border: 1px solid var(--line); background: linear-gradient(180deg, rgba(17,27,43,.92), rgba(13,20,32,.92));
    border-radius: 14px; padding: 14px 16px; position: relative; overflow: hidden;
  }
  .stat::after { content: ""; position: absolute; inset: 0 0 auto 0; height: 2px; background: var(--accent); opacity: .55; }
  .stat .k { color: var(--muted); font-size: 11px; letter-spacing: .08em; text-transform: uppercase; }
  .stat .v { font-size: 26px; font-weight: 750; margin-top: 3px; letter-spacing: -.02em; }
  .stat .v small { font-size: 12px; color: var(--dim); font-weight: 500; }
  .toolbar {
    display: flex; flex-wrap: wrap; gap: 10px; align-items: center;
    margin: 22px 0 8px; padding: 12px; border: 1px solid var(--line);
    background: rgba(13,20,32,.75); border-radius: 14px; position: sticky; top: 10px; z-index: 20;
    backdrop-filter: blur(12px);
  }
  .search {
    flex: 1 1 260px; min-width: 220px; display: flex; align-items: center; gap: 8px;
    border: 1px solid var(--line); background: #0a111c; border-radius: 10px; padding: 9px 12px;
  }
  .search input { flex: 1; background: transparent; border: 0; outline: 0; color: var(--text); font-size: 14px; }
  .search svg { opacity: .55; }
  select {
    background: #0a111c; color: var(--text); border: 1px solid var(--line);
    border-radius: 10px; padding: 9px 12px; font-size: 13px; outline: none;
  }
  .chips { display: flex; flex-wrap: wrap; gap: 8px; }
  .chip {
    border: 1px solid var(--line); background: #0a111c; color: var(--muted);
    padding: 7px 12px; border-radius: 999px; font-size: 12px; cursor: pointer;
    transition: .15s ease; user-select: none;
  }
  .chip:hover { border-color: #2c405a; color: var(--text); }
  .chip.active { color: #04110f; background: var(--accent); border-color: var(--accent); font-weight: 700; }
  .section { margin-top: 30px; scroll-margin-top: 90px; }
  .section-head { display: flex; align-items: baseline; gap: 12px; margin: 0 0 12px; }
  .section-head h2 { margin: 0; font-size: 20px; letter-spacing: -.01em; }
  .section-head .count { font-size: 12px; color: var(--dim); font-family: var(--mono); }
  .section-head .rule { flex: 1; height: 1px; background: linear-gradient(90deg, var(--line), transparent); }
  .grid { display: grid; grid-template-columns: repeat(auto-fill, minmax(340px, 1fr)); gap: 14px; }
  .card {
    border: 1px solid var(--line); border-radius: var(--radius);
    background: linear-gradient(180deg, rgba(17,27,43,.92), rgba(11,17,27,.96));
    padding: 18px 18px 15px; cursor: pointer; position: relative; overflow: hidden;
    transition: transform .16s ease, border-color .16s ease, box-shadow .16s ease;
    display: flex; flex-direction: column; gap: 10px; min-height: 250px;
  }
  .card::before {
    content: ""; position: absolute; left: 0; top: 0; bottom: 0; width: 3px;
    background: var(--accent); opacity: .9;
  }
  .card:hover { transform: translateY(-2px); border-color: #2e4560; box-shadow: 0 14px 40px rgba(0,0,0,.35); }
  .card .top { display: flex; align-items: center; justify-content: space-between; gap: 8px; }
  .arxiv { font-family: var(--mono); font-size: 11.5px; color: var(--dim); }
  .badge {
    font-size: 10.5px; font-weight: 700; letter-spacing: .06em; text-transform: uppercase;
    border-radius: 999px; padding: 4px 8px; border: 1px solid var(--line); color: var(--muted);
  }
  .badge.good { color: var(--good); border-color: rgba(52,211,153,.35); background: rgba(52,211,153,.08); }
  .badge.warn { color: var(--warn); border-color: rgba(251,191,36,.35); background: rgba(251,191,36,.08); }
  .badge.bad { color: var(--bad); border-color: rgba(251,113,133,.35); background: rgba(251,113,133,.08); }
  .card h3 { margin: 0; font-size: 15.5px; line-height: 1.38; letter-spacing: -.005em; }
  .card .paper-title { color: var(--muted); font-size: 12.5px; display: -webkit-box; -webkit-line-clamp: 2; -webkit-box-orient: vertical; overflow: hidden; }
  .card .goal { color: #c6d4e6; font-size: 13px; display: -webkit-box; -webkit-line-clamp: 3; -webkit-box-orient: vertical; overflow: hidden; min-height: 58px; }
  .concepts { display: flex; flex-wrap: wrap; gap: 6px; }
  .concept { font-size: 10.5px; color: #9fb4cc; background: rgba(125,211,252,.07); border: 1px solid rgba(125,211,252,.16); padding: 3px 7px; border-radius: 999px; }
  .metrics { display: grid; grid-template-columns: repeat(3, 1fr); gap: 6px; margin-top: auto; padding-top: 10px; border-top: 1px dashed var(--line); }
  .metric { display: flex; flex-direction: column; gap: 2px; }
  .metric .k { font-size: 10px; color: var(--dim); letter-spacing: .05em; text-transform: uppercase; }
  .metric .v { font-family: var(--mono); font-size: 15px; font-weight: 700; }
  .metric .v.good { color: var(--good); }
  .metric .v.warn { color: var(--warn); }
  .metric .v.bad { color: var(--bad); }
  .empty { color: var(--dim); padding: 30px; border: 1px dashed var(--line); border-radius: 14px; text-align: center; }
  footer { margin-top: 60px; color: var(--dim); font-size: 12px; border-top: 1px solid var(--line); padding-top: 18px; display: flex; justify-content: space-between; gap: 12px; flex-wrap: wrap; }
  .modal-backdrop {
    position: fixed; inset: 0; background: rgba(3,6,10,.72); backdrop-filter: blur(6px);
    display: none; z-index: 100; padding: 28px;
  }
  .modal-backdrop.open { display: flex; align-items: flex-start; justify-content: center; }
  .modal {
    width: min(1100px, 100%); max-height: calc(100vh - 56px); overflow: auto;
    background: #0b121d; border: 1px solid #22344b; border-radius: 20px;
    box-shadow: 0 30px 90px rgba(0,0,0,.6); position: relative;
  }
  .modal-head { padding: 24px 26px 16px; border-bottom: 1px solid var(--line); position: sticky; top: 0; background: #0b121d; z-index: 3; }
  .modal-head .row { display: flex; gap: 12px; align-items: flex-start; justify-content: space-between; }
  .modal-head h2 { margin: 4px 0 6px; font-size: 22px; line-height: 1.25; }
  .modal-head .paper { color: var(--muted); font-size: 13px; }
  .close {
    border: 1px solid var(--line); background: #101a29; color: var(--muted);
    width: 34px; height: 34px; border-radius: 10px; cursor: pointer; font-size: 18px; line-height: 1;
    flex: none;
  }
  .close:hover { color: var(--text); border-color: #35506e; }
  .modal-badges { display: flex; flex-wrap: wrap; gap: 7px; margin-top: 12px; }
  .tabs { display: flex; gap: 6px; padding: 12px 26px 0; border-bottom: 1px solid var(--line); flex-wrap: wrap; }
  .tab {
    border: 0; background: transparent; color: var(--muted); padding: 9px 12px; cursor: pointer;
    font-size: 13px; border-bottom: 2px solid transparent; margin-bottom: -1px;
  }
  .tab.active { color: var(--text); border-bottom-color: var(--accent); }
  .tab:hover { color: var(--text); }
  .modal-body { padding: 20px 26px 30px; }
  .pane { display: none; }
  .pane.active { display: block; }
  .kv { display: grid; grid-template-columns: 150px 1fr; gap: 8px 14px; font-size: 13px; }
  .kv .k { color: var(--dim); }
  .kv .v { color: #c9d7e8; word-break: break-word; }
  .box { border: 1px solid var(--line); border-radius: 12px; background: #0a111c; padding: 14px 16px; margin-top: 14px; }
  .box h4 { margin: 0 0 8px; font-size: 12px; color: var(--muted); letter-spacing: .08em; text-transform: uppercase; }
  pre {
    margin: 0; white-space: pre-wrap; word-break: break-word; font-family: var(--mono);
    font-size: 12px; line-height: 1.6; color: #c5d5e8; max-height: 460px; overflow: auto;
  }
  code { font-family: var(--mono); font-size: 12px; }
  .checks { width: 100%; border-collapse: collapse; font-size: 12.5px; }
  .checks th, .checks td { text-align: left; padding: 8px 10px; border-bottom: 1px solid var(--line); vertical-align: top; }
  .checks th { color: var(--dim); font-weight: 600; font-size: 11px; text-transform: uppercase; letter-spacing: .06em; }
  .checks td.path { font-family: var(--mono); color: var(--accent); white-space: nowrap; }
  .checks td.val { font-family: var(--mono); color: #c9d7e8; word-break: break-word; }
  .timeline { display: flex; flex-direction: column; gap: 0; margin-top: 6px; }
  .step { display: grid; grid-template-columns: 26px 1fr; gap: 12px; }
  .step .dot-wrap { display: flex; flex-direction: column; align-items: center; }
  .step .dot { width: 10px; height: 10px; border-radius: 50%; background: var(--accent); margin-top: 6px; box-shadow: 0 0 0 4px rgba(94,234,212,.12); }
  .step .line { width: 1px; flex: 1; background: var(--line); }
  .step:last-child .line { display: none; }
  .step .content { padding-bottom: 18px; }
  .step .title { font-weight: 650; font-size: 13.5px; }
  .step .meta { color: var(--muted); font-size: 12px; }
  .issues { margin: 8px 0 0; padding-left: 18px; color: #d6c3c7; font-size: 13px; }
  .issues li { margin: 4px 0; }
  .strengths { margin: 8px 0 0; padding-left: 18px; color: #bfe4d2; font-size: 13px; }
  .files { font-family: var(--mono); font-size: 12px; color: #a9bdd5; display: grid; gap: 3px; }
  .bar { height: 6px; border-radius: 999px; background: #162235; overflow: hidden; margin-top: 8px; }
  .bar > i { display: block; height: 100%; background: linear-gradient(90deg, #34d399, #5eead4); }
  .muted { color: var(--muted); }
  .mono { font-family: var(--mono); }
  .pill-row { display: flex; flex-wrap: wrap; gap: 6px; }
  .pill { font-size: 11px; border: 1px solid var(--line); border-radius: 999px; padding: 3px 8px; color: var(--muted); }
  .toast {
    position: fixed; right: 22px; bottom: 22px; background: #102030; border: 1px solid #2c4562;
    color: var(--text); padding: 10px 14px; border-radius: 10px; font-size: 12.5px;
    opacity: 0; transform: translateY(8px); transition: .2s ease; pointer-events: none; z-index: 200;
  }
  .toast.show { opacity: 1; transform: translateY(0); }
  @media (max-width: 1050px) { .stats { grid-template-columns: repeat(3, 1fr); } }
  @media (max-width: 640px) {
    .wrap { padding: 22px 14px 60px; }
    .stats { grid-template-columns: repeat(2, 1fr); }
    .grid { grid-template-columns: 1fr; }
    .modal-backdrop { padding: 0; }
    .modal { max-height: 100vh; border-radius: 0; }
    .kv { grid-template-columns: 1fr; gap: 2px 0; }
    .kv .k { margin-top: 7px; }
  }
</style>
</head>
<body>
<div class="wrap">
  <header class="hero">
    <div class="eyebrow">paper-radar × harbor × karotte × mcode</div>
    <h1>从论文到<span class="grad">可评测的 Agent 环境</span></h1>
    <div class="sub">
      每一个 Harbor 任务都由一篇论文的核心机制生成：离线、确定性、可执行。Karotte 镜像同一套
      instruction / fixtures / golden，MiniMax Code 以 <span class="mono">tierflow_pro · effort=max</span>
      作为被测智能体实际解题，再由 verifier 给出 reward。
    </div>
    <div class="pipeline" id="pipeline"></div>
    <div class="stats" id="stats"></div>
  </header>

  <div class="toolbar">
    <label class="search">
      <svg width="15" height="15" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><circle cx="11" cy="11" r="7"/><path d="m20 20-3.5-3.5"/></svg>
      <input id="q" type="search" placeholder="搜索论文标题、arXiv ID、任务概念…">
    </label>
    <select id="sort">
      <option value="topic">按分类排序</option>
      <option value="critic">按评审分排序</option>
      <option value="mcode">按 MCode reward 排序</option>
      <option value="score">按 paper 相关度排序</option>
    </select>
    <div class="chips" id="chips"></div>
  </div>

  <main id="sections"></main>

  <footer>
    <span>Harbor: task.toml · instruction.md · environment/Dockerfile · tests/test.sh · reward.txt</span>
    <span id="generated"></span>
  </footer>
</div>

<div class="modal-backdrop" id="backdrop">
  <div class="modal" role="dialog" aria-modal="true" aria-labelledby="modal-title">
    <div class="modal-head">
      <div class="row">
        <div>
          <div class="eyebrow" id="modal-topic"></div>
          <h2 id="modal-title"></h2>
          <div class="paper" id="modal-paper"></div>
          <div class="modal-badges" id="modal-badges"></div>
        </div>
        <button class="close" id="close" aria-label="关闭">×</button>
      </div>
    </div>
    <div class="tabs" id="tabs">
      <button class="tab active" data-pane="overview">概览</button>
      <button class="tab" data-pane="instruction">Instruction</button>
      <button class="tab" data-pane="verifier">Verifier</button>
      <button class="tab" data-pane="pipeline">Pipeline / MCode</button>
      <button class="tab" data-pane="files">文件与参考解</button>
    </div>
    <div class="modal-body">
      <div class="pane active" id="pane-overview"></div>
      <div class="pane" id="pane-instruction"></div>
      <div class="pane" id="pane-verifier"></div>
      <div class="pane" id="pane-pipeline"></div>
      <div class="pane" id="pane-files"></div>
    </div>
  </div>
</div>
<div class="toast" id="toast"></div>

<script>
const DATA = __DATA__;
const state = { topic: "all", q: "", sort: "topic" };
const $ = (sel) => document.querySelector(sel);
const esc = (v) => String(v ?? "").replace(/[&<>"']/g, (c) => ({"&":"&amp;","<":"&lt;",">":"&gt;","\"":"&quot;","'":"&#39;"}[c]));
const fmt = (v, digits = 2) => (v === null || v === undefined || v === "" ? "—" : (typeof v === "number" ? v.toFixed(digits) : v));
const pct = (v) => Math.round((Number(v) || 0) * 100);

function clsFor(v, good, warn) {
  if (v === null || v === undefined) return "";
  if (v >= good) return "good";
  if (v >= warn) return "warn";
  return "bad";
}

function renderHeader() {
  const s = DATA.summary;
  const nodes = [
    ["Planner", "paper → task"],
    ["Builder", "Harbor render"],
    ["Validator", "ref 1.0 / empty 0.0"],
    ["Critic", "fidelity review"],
    ["MCode", "solve + reward"],
  ];
  $("#pipeline").innerHTML = nodes.map((n, i) =>
    `<span class="node"><b>${esc(n[0])}</b>${esc(n[1])}</span>` + (i < nodes.length - 1 ? `<span class="arrow">→</span>` : "")
  ).join("");

  const stats = [
    ["Papers", s.papers, "篇已生成"],
    ["Categories", s.topics, "个主题"],
    ["Graded checks", s.checks, "条断言"],
    ["Validated", `${s.validated}/${s.papers}`, "Harbor 本地验证"],
    ["Avg critic", fmt(s.avg_critic, 1), "/ 10"],
    ["MCode pass", `${s.mcode_pass}/${s.mcode_runs}`, "reward = 1.0"],
  ];
  $("#stats").innerHTML = stats.map(([k, v, small]) =>
    `<div class="stat"><div class="k">${esc(k)}</div><div class="v">${esc(v)} <small>${esc(small)}</small></div></div>`
  ).join("");
  const model = DATA.model?.name || "tierflow_pro";
  const effort = DATA.model?.effort || "max";
  $("#generated").textContent = `generated ${DATA.generated_at} · ${model} · effort=${effort} · ${DATA.summary.karotte_tasks} Karotte tasks`;
}

function renderChips() {
  const items = [{ id: "all", name: "全部", count: DATA.summary.papers }].concat(DATA.topics.filter(t => t.count));
  $("#chips").innerHTML = items.map(t =>
    `<span class="chip ${state.topic === t.id ? "active" : ""}" data-topic="${esc(t.id)}">${esc(t.name)} <span class="mono">${t.count}</span></span>`
  ).join("");
  $("#chips").querySelectorAll(".chip").forEach(chip => {
    chip.addEventListener("click", () => { state.topic = chip.dataset.topic; renderChips(); renderSections(); });
  });
}

function visiblePapers() {
  let items = DATA.papers.filter(p => {
    if (state.topic !== "all" && p.topic !== state.topic) return false;
    if (!state.q) return true;
    const hay = [p.title, p.arxiv_id, p.task_title, p.one_liner, p.abstract, (p.concepts || []).join(" ")].join(" ").toLowerCase();
    return hay.includes(state.q.toLowerCase());
  });
  if (state.sort === "critic") items = items.slice().sort((a, b) => (b.critic?.score || 0) - (a.critic?.score || 0));
  if (state.sort === "mcode") items = items.slice().sort((a, b) => (b.mcode?.reward || 0) - (a.mcode?.reward || 0));
  if (state.sort === "score") items = items.slice().sort((a, b) => (b.score || 0) - (a.score || 0));
  return items;
}

function cardHTML(p) {
  const critic = p.critic?.score;
  const mcode = p.mcode;
  const mcodeReward = mcode ? mcode.reward : null;
  const validation = p.validation?.ok;
  const badges = [
    `<span class="badge ${validation ? "good" : "bad"}">${validation ? "validated" : "unvalidated"}</span>`,
    `<span class="badge">${esc(p.difficulty)}</span>`,
  ].join("");
  return `
    <article class="card" style="--accent:${esc(p.accent)}" data-slug="${esc(p.slug)}">
      <div class="top">
        <span class="arxiv">${esc(p.arxiv_id)}</span>
        <span class="pill-row">${badges}</span>
      </div>
      <h3>${esc(p.task_title)}</h3>
      <div class="paper-title">${esc(p.title)}</div>
      <div class="goal">${esc(p.one_liner)}</div>
      <div class="concepts">${(p.concepts || []).slice(0, 4).map(c => `<span class="concept">${esc(c)}</span>`).join("")}</div>
      <div class="metrics">
        <div class="metric"><span class="k">paper score</span><span class="v">${fmt(p.score)}</span></div>
        <div class="metric"><span class="k">critic</span><span class="v ${clsFor(critic, 8, 6)}">${critic == null ? "—" : fmt(critic, 1)}</span></div>
        <div class="metric"><span class="k">mcode reward</span><span class="v ${mcodeReward == null ? "" : (mcodeReward === 1 ? "good" : (mcodeReward ? "warn" : "bad"))}">${mcodeReward == null ? "—" : fmt(mcodeReward, 2)}</span></div>
      </div>
    </article>`;
}

function renderSections() {
  const items = visiblePapers();
  const groups = [];
  const order = DATA.topics.map(t => t.id);
  for (const t of order) {
    const list = items.filter(p => p.topic === t);
    if (list.length) groups.push({ id: t, name: DATA.topics.find(x => x.id === t)?.name || t, list });
  }
  if (!groups.length) {
    $("#sections").innerHTML = `<div class="empty">没有匹配的论文环境。调整搜索或分类。</div>`;
    return;
  }
  $("#sections").innerHTML = groups.map(g => `
    <section class="section" id="topic-${esc(g.id)}">
      <div class="section-head">
        <h2>${esc(g.name)}</h2>
        <span class="count">${g.list.length} env${g.list.length > 1 ? "s" : ""}</span>
        <span class="rule"></span>
      </div>
      <div class="grid">${g.list.map(cardHTML).join("")}</div>
    </section>
  `).join("");
  document.querySelectorAll(".card").forEach(card => {
    card.addEventListener("click", () => openModal(card.dataset.slug));
  });
}

function kv(rows) {
  return `<div class="kv">${rows.map(([k, v]) => `<div class="k">${esc(k)}</div><div class="v">${v}</div>`).join("")}</div>`;
}

function checksTable(golden) {
  const expected = golden?.expected || {};
  const graded = golden?.graded_paths || Object.keys(expected);
  const tols = golden?.tolerances || {};
  if (!graded.length) return `<div class="muted">没有 golden 检查。</div>`;
  return `<table class="checks"><thead><tr><th>path</th><th>expected</th><th>tolerance</th></tr></thead><tbody>${
    graded.map(path => {
      let val = expected;
      for (const part of String(path).split(".")) {
        if (val == null) break;
        val = Array.isArray(val) ? val[Number(part)] : val[part];
      }
      return `<tr><td class="path">${esc(path)}</td><td class="val">${esc(JSON.stringify(val))}</td><td class="val">${esc(tols[path] ?? "exact")}</td></tr>`;
    }).join("")
  }</tbody></table>`;
}

function openModal(slug) {
  const p = DATA.papers.find(x => x.slug === slug);
  if (!p) return;
  $("#modal-topic").textContent = p.topic_name;
  $("#modal-title").textContent = p.task_title;
  $("#modal-paper").innerHTML = `<a href="${esc(p.url)}" target="_blank" rel="noreferrer">${esc(p.arxiv_id)}</a> · ${esc(p.title)}`;
  const critic = p.critic?.score;
  const mcode = p.mcode;
  $("#modal-badges").innerHTML = [
    `<span class="badge ${p.validation?.ok ? "good" : "bad"}">${p.validation?.ok ? "Harbor validated" : "validation failed"}</span>`,
    `<span class="badge">${esc(p.difficulty)} · ${esc(p.estimated_minutes || "?")} min</span>`,
    `<span class="badge ${clsFor(critic, 8, 6)}">critic ${critic == null ? "—" : fmt(critic, 1)}/10</span>`,
    `<span class="badge ${mcode?.reward === 1 ? "good" : (mcode ? "warn" : "")}">MCode ${mcode ? fmt(mcode.reward, 2) : "not run"}</span>`,
    `<span class="badge">${p.graded_paths.length} checks</span>`,
  ].join("");

  const v = p.validation || {};
  $("#pane-overview").innerHTML = `
    ${kv([
      ["Source paper", `<a href="${esc(p.url)}" target="_blank" rel="noreferrer">${esc(p.title)}</a>`],
      ["Authors", esc((p.authors || []).join(", "))],
      ["Published", esc(p.published) + " · " + esc((p.categories || []).join(", "))],
      ["Paper score", esc(p.score)],
      ["Task title", esc(p.task_title)],
      ["One-liner", esc(p.one_liner)],
      ["Deliverable", `<code>${esc(p.deliverable?.path || "")}</code>`],
      ["Schema", `<pre>${esc(p.deliverable?.schema || "")}</pre>`],
      ["Starter files", `<span class="mono">${esc((p.starter_files || []).join(", ") || "(none)")}</span>`],
      ["Harbor", `id=${esc(p.harbor?.id)} · verifier=${esc(p.harbor?.verifier)} · ${esc(p.harbor?.rule)} · ${esc(p.harbor?.cpus)} cpu / ${esc(p.harbor?.memory_mb)} MB`],
    ])}
    <div class="box"><h4>论文摘要</h4><pre>${esc(p.abstract)}</pre></div>
    <div class="box"><h4>为什么这个任务能代表论文</h4><pre>${esc(p.design_rationale || p.one_liner)}</pre></div>
    <div class="box"><h4>本地验证</h4>
      ${kv([
        ["Harbor layout", v.ok ? "通过" : "失败"],
        ["参考解 reward", `${fmt(v.positive_reward, 2)}`],
        ["空工作区 reward", `${fmt(v.negative_reward, 2)}`],
        ["参考解确定性", `${v.reference_runs || 0} 次运行`],
        ["校验点", `${v.checks || p.graded_paths.length}`],
      ])}
    </div>`;

  $("#pane-instruction").innerHTML = `<div class="box"><h4>instruction.md</h4><pre>${esc(p.instruction)}</pre></div>`;
  $("#pane-verifier").innerHTML = `
    <div class="box"><h4>tests/test.sh</h4><pre>${esc(p.verifier)}</pre></div>
    <div class="box"><h4>tests/golden.json · 评分点</h4>${checksTable(p.golden)}</div>
    <div class="box"><h4>验证说明</h4><pre>${esc(p.verification_notes || "reward = passed_checks / total_checks；deliverable 缺失或非法 JSON 时为 0。")}</pre></div>`;

  const timings = p.timings || {};
  const steps = [
    ["Planner", `tierflow_pro · effort=max · ${fmt(timings.plan_s, 1)}s`, "把论文机制收敛成一个确定性任务，产出 instruction、fixtures、参考解"],
    ["Builder", `${p.starter_files.length} fixture(s) · ${p.graded_paths.length} checks`, `写入 Harbor 布局，并运行参考解两次验证确定性`],
    ["Validator", `ref ${fmt(v.positive_reward, 2)} / empty ${fmt(v.negative_reward, 2)}`, `静态布局、Python 编译、正负样本全部通过才算有效环境`],
    ["Critic", `${critic == null ? "—" : fmt(critic, 1)}/10 · ${esc(p.critic?.verdict || "n/a")}`, "从论文保真度、指令清晰度、verifier 强度三个维度复核"],
    ["MCode", mcode ? `${esc(mcode.status)} · reward ${fmt(mcode.reward, 2)} · ${esc(mcode.elapsed || "?")}s` : "未运行", "MiniMax Code 在环境里真实解题，再跑同一个 verifier"],
  ];
  $("#pane-pipeline").innerHTML = `
    <div class="timeline">${steps.map(([t, m, d]) => `
      <div class="step"><div class="dot-wrap"><span class="dot"></span><span class="line"></span></div>
        <div class="content"><div class="title">${esc(t)}</div><div class="meta">${esc(m)}</div><div class="muted">${esc(d)}</div></div>
      </div>`).join("")}</div>
    <div class="box"><h4>评审细节</h4>
      ${kv([
        ["Overall", `${fmt(critic, 1)}/10 · ${esc(p.critic?.verdict || "n/a")}`],
        ["Paper fidelity", fmt(p.critic?.paper_fidelity, 1)],
        ["Clarity", fmt(p.critic?.clarity, 1)],
        ["Verifier strength", fmt(p.critic?.verifier_strength, 1)],
      ])}
      ${(p.critic?.strengths || []).length ? `<h4 style="margin-top:14px">Strengths</h4><ul class="strengths">${p.critic.strengths.map(s => `<li>${esc(s)}</li>`).join("")}</ul>` : ""}
      ${(p.critic?.issues || []).length ? `<h4 style="margin-top:14px">Issues</h4><ul class="issues">${p.critic.issues.map(s => `<li>${esc(s)}</li>`).join("")}</ul>` : ""}
    </div>
    <div class="box"><h4>MCode 评测</h4>
      ${mcode ? kv([
        ["Status", esc(mcode.status)],
        ["Reward", fmt(mcode.reward, 2)],
        ["Elapsed", `${esc(mcode.elapsed || "—")}s`],
        ["Model", `<span class="mono">${esc(JSON.stringify(mcode.model || "tierflow_pro"))}</span>`],
        ["Usage", `<span class="mono">${esc(JSON.stringify(mcode.usage || {}))}</span>`],
        ["Deliverable written", mcode.deliverable_written ? "yes" : "no"],
        ["Answer", `<pre>${esc(mcode.answer_excerpt || mcode.output || mcode.error || "")}</pre>`],
      ]) : `<div class="muted">这次 run 未启用 MCode 评测。</div>`}
    </div>`;

  $("#pane-files").innerHTML = `
    <div class="box"><h4>Harbor 文件树</h4><div class="files">${p.files.map(f => `<div>${esc(f)}</div>`).join("")}</div></div>
    <div class="box"><h4>路径</h4>${kv([
      ["environment", `<span class="mono">${esc(p.paths.env)}</span>`],
      ["instruction", `<a href="${esc(p.paths.relative)}instruction.md" target="_blank">${esc(p.paths.instruction)}</a>`],
      ["task.toml", `<a href="${esc(p.paths.relative)}task.toml" target="_blank">${esc(p.paths.task_toml)}</a>`],
      ["verifier", `<a href="${esc(p.paths.relative)}tests/test.sh" target="_blank">${esc(p.paths.test_sh)}</a>`],
      ["paper.json", `<a href="${esc(p.paths.relative)}tests/paper.json" target="_blank">${esc(p.paths.paper)}</a>`],
    ])}</div>
    <div class="box"><h4>参考解 tests/solution/solve.py</h4><pre>${esc(p.solution)}</pre></div>`;

  document.querySelectorAll(".tab").forEach(t => t.classList.toggle("active", t.dataset.pane === "overview"));
  document.querySelectorAll(".pane").forEach(pane => pane.classList.toggle("active", pane.id === "pane-overview"));
  $("#backdrop").classList.add("open");
  document.body.style.overflow = "hidden";
}

function closeModal() {
  $("#backdrop").classList.remove("open");
  document.body.style.overflow = "";
}

document.addEventListener("DOMContentLoaded", () => {
  renderHeader();
  renderChips();
  renderSections();
  $("#q").addEventListener("input", (e) => { state.q = e.target.value.trim(); renderSections(); });
  $("#sort").addEventListener("change", (e) => { state.sort = e.target.value; renderSections(); });
  $("#close").addEventListener("click", closeModal);
  $("#backdrop").addEventListener("click", (e) => { if (e.target === $("#backdrop")) closeModal(); });
  $("#tabs").addEventListener("click", (e) => {
    const tab = e.target.closest(".tab");
    if (!tab) return;
    document.querySelectorAll(".tab").forEach(t => t.classList.toggle("active", t === tab));
    document.querySelectorAll(".pane").forEach(pane => pane.classList.toggle("active", pane.id === "pane-" + tab.dataset.pane));
  });
  document.addEventListener("keydown", (e) => { if (e.key === "Escape") closeModal(); });
});
</script>
</body>
</html>
"""


def build_dashboard() -> Path:
    data = collect()
    payload = json.dumps(data, ensure_ascii=False, default=str).replace("</", "<\\/")
    html = HTML.replace("__DATA__", payload)
    DASHBOARD_HTML.write_text(html, encoding="utf-8")
    DASHBOARD_JSON.write_text(
        json.dumps(data, ensure_ascii=False, indent=2, default=str) + "\n",
        encoding="utf-8",
    )
    return DASHBOARD_HTML


if __name__ == "__main__":
    print(build_dashboard())
