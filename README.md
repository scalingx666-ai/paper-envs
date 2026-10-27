# paper-envs

Turn selected papers from `paper-radar` into **evaluable agent environments** in
Harbor format, mirror them into **Karotte** tasks, and evaluate those
environments with **MiniMax Code** as the agent under test.

The `paper-radar/` directory is vendored so the survey CSV, the crawler source,
and its tests travel with this repository. It is upstream code, not part of the
generation pipeline:

```
paper-envs/
  config.py, papers.py, llm.py, prompts.py   # generation pipeline
  harbor.py, karotte_mirror.py, mcode.py     # validators and evaluators
  pipeline.py, dashboard.py, run.sh
  envs/<slug>/                               # generated Harbor tasks
  karotte_env/                               # Karotte task mirror
  paper-radar/                               # vendored survey + crawler
  runs/                                      # local run artifacts (gitignored)
```

```
paper-radar survey CSV
        │
        ▼
  Planner (tierflow_pro · effort=max)
        │  instruction + fixtures + reference solution + graded paths
        ▼
  Builder ──► Harbor task dir ──► Validator (reference 1.0 / empty 0.0)
        │                              │
        │                              ▼
        │                          Critic (fidelity / clarity / verifier)
        │                              │
        │                              ▼
        └──────────────► Karotte task mirror
                                       │
                                       ▼
                         MCode evaluator ──► verifier reward
                                       │
                                       ▼
                                dashboard.html
```

## Layout produced per paper

```
envs/<slug>/
  task.toml                  # [tool] [task] [resources] [agent] [verifier]
  instruction.md             # self-contained, offline task statement
  environment/Dockerfile     # python:3.12-slim, WORKDIR /workspace
  <starter files...>         # copied into the agent workspace
  tests/test.sh              # Harbor verifier entrypoint (cwd = workspace)
  tests/verify.py            # compares deliverable with golden.json
  tests/golden.json          # expected output from the reference solution
  tests/solution/solve.py    # reference solution
  tests/paper.json           # provenance
  tests/env_meta.json        # plan, checks, critic, MCode, timings
```

Starter files live at the task root because `harbor-mlx` copies every root entry
except `task.toml`, `instruction.md`, `tests/`, and `environment/` into the
workspace. The reference solution and golden output stay under `tests/` so they
are never exposed to the solver.

The verifier writes `reward.txt` as `passed_checks / total_checks`; a missing or
invalid deliverable scores `0.0`.

## Karotte mirror

`karotte_env/` is a real `karotte create-env` project. Each paper becomes one
task package:

```
karotte_env/src/environment/tasks/<slug>/
  __init__.py                # Task + Step, ExecutableJudge
  scoring_script.py          # reads golden.json, writes {"score": ...}
  golden.json
  fixtures/<starter files>
```

`pre_hook` copies the fixtures into the student workdir; `pre_scoring_hook`
kills student processes; the judge grades the deliverable in place. Karotte is
container-first, so a full `karotte run` requires Docker/podman. Task discovery
and the scoring script work without it:

```bash
cd karotte_env
uv run karotte tasks list
python3 src/environment/tasks/<slug>/scoring_script.py <answer.json> score.json
```

## Usage

```bash
# Python 3.11+ is required for tomllib; this machine uses python3.13.
./run.sh --dry-run --limit-per-topic 2

# One environment per topic, skip MCode
./run.sh --limit-per-topic 1 --no-mcode --run-id demo --force

# Full pipeline, MCode solves the first two environments
./run.sh --limit-per-topic 2 --mcode-limit 2 --run-id full

# Only specific papers
./run.sh --only arxiv:2610.09426,arxiv:2607.18171 --limit-per-topic 1
```

Options:

| flag | meaning |
|:--|:--|
| `--limit-per-topic N` | take the top N ranked selected papers per topic |
| `--only IDS` | comma-separated arXiv ids or slugs |
| `--revisions N` | allow N critic-driven plan revisions (default 1) |
| `--critic-threshold X` | revise below this score (default 6.0) |
| `--mcode / --no-mcode` | run MiniMax Code evaluation (default on) |
| `--mcode-limit N` | evaluate at most N environments (0 = all) |
| `--no-karotte` | skip the Karotte mirror |
| `--force` | replace an existing environment (old one is moved to `_stale/`) |

Outputs:

- `envs/<slug>/` — Harbor environments
- `karotte_env/src/environment/tasks/<slug>/` — Karotte tasks
- `runs/<run-id>/` — plan, validation, critiques, MCode workspace, summary
- `dashboard.html` + `dashboard_data.json` — offline visualization

Serve the dashboard locally:

```bash
python3 -m http.server 8765 --bind 127.0.0.1
# http://127.0.0.1:8765/dashboard.html
```

## Model and credentials

Defaults: `https://tierflow.cn/v1`, model `tierflow_pro`, `reasoning_effort=max`.

The key is resolved at runtime from, in order:

1. `PAPER_ENV_API_KEY` / `TIERFLOW_API_KEY`
2. `paper-envs/.env.local`
3. `~/.minimax/config.yaml` (`custom_provider.tierflow.options.apiKey`)

No key is written to the repository, logs, dashboard, or generated tasks.

Evaluate an already generated environment without regenerating it:

```bash
python3 mcode.py envs/<slug>
```

This runs the same solve → verifier flow, writes `tests/mcode/mcode_result.json`,
and updates `tests/env_meta.json` (rerun `dashboard.py` afterwards).

MCode runs with `MINIMAX_DATA_DIR=.mcode-data/` so user sessions are untouched.
The provider config is copied once from `~/.minimax/config.yaml` into that
directory (mode 700 / 600); it is gitignored.

Each evaluation runs in a fresh temporary workspace outside the repository under
macOS `sandbox-exec`, with `envs/`, `runs/`, `karotte_env/` and `.mcode-data/`
read/write denied. The verifier runs after the sandbox exits, so the solver
cannot read `golden.json`, the reference solution, or earlier answers.

## Paper selection

Input is the paper-radar one-year survey CSV. `papers.py` keeps `selected=1`
rows, ranks within each topic by `score` plus implementation-oriented title
bonuses and penalties for surveys/very short abstracts, then takes the top N.
Six topics: agent-harness, rsi, agent-environment, post-training, rl-infra,
inference-infra.

## Guarantees and limits

- The reference solution runs twice; non-determinism fails validation.
- Positive check (reference output) must score 1.0; negative check (empty
  workspace) must score 0.0.
- Tasks are offline, stdlib-only, and finish in under 60 seconds.
- Docker is not available on this machine, so Harbor image builds and Karotte
  container runs are not executed here. Harbor validation uses a local sandbox
  with the same workspace/verifier contract.
- MCode evaluation is a real solve attempt, not a simulation; its reward can be
  lower than the reference. Failures are kept and shown in the dashboard.
