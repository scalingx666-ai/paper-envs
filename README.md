# paper-envs

Turn selected papers from `paper-radar` into **evaluable agent environments** in
Harbor format, mirror them into **Karotte** tasks, and evaluate those
environments with **MiniMax Code** as the agent under test.

The repository has two quality tiers. Generated tasks under `envs/` are
demo-grade: they use public fixtures and exact-output grading, and they exist to
exercise the pipeline. Hand-authored tasks under `benchmarks/` are
benchmark-grade: held-out data, a separate no-network verifier, calibrated
conjunctive gates, and a verified separation between correct and defective
policies.

The `paper-radar/` directory is vendored so the survey CSV, the crawler source,
and its tests travel with this repository. It is upstream code, not part of the
generation pipeline:

```
paper-envs/
  config.py, papers.py, llm.py, prompts.py   # generation pipeline
  harbor.py, karotte_mirror.py, mcode.py     # validators and evaluators
  pipeline.py, dashboard.py, run.sh
  envs/<slug>/                               # generated demo tasks
  benchmarks/<slug>/                         # hand-authored benchmark-grade tasks
  karotte_env/                               # Karotte task mirror
  paper-radar/                               # vendored survey + crawler
  runs/                                      # local run artifacts (gitignored)
```

## Quality tiers

| Tier | Location | Data | Grading | Intended use |
|:--|:--|:--|:--|:--|
| demo | `envs/<slug>/` | one public fixture | exact match against `golden.json` | pipeline smoke test, prompt debugging |
| benchmark | `benchmarks/<slug>/` | public dev set + hidden holdout | calibrated thresholds over hidden workloads, binary reward | measuring agent capability |

Demo reward is the fraction of checks that match the golden output. A demo can
be solved by copying the fixture or by pattern-matching the expected file, so a
high demo score is not evidence of generalization. Benchmark tasks never expose
the holdout, grade only the delivered artifact, and include at least one
independent correct implementation and several comparators that fail.

### Benchmark pilot: `disagg-kv-retention`

`benchmarks/disagg-kv-retention/` is the reference for the benchmark tier. It
asks for an online KV-cache retention policy for a disaggregated LLM serving
pool, following Liu et al., SYSTOR '26. The agent gets five public workloads,
the simulator, the cost model, and an LRU starter; the verifier runs the
submitted policy as an unprivileged child over eight hidden workloads and
applies five calibrated performance gates plus contract and determinism checks.

| Submission | Reward | Evidence |
|:--|:--|:--|
| reference (decayed LFU) | 1 | 8/8 tests, geomean 0.378, total 0.439, p95 0.448 |
| independent CTLFU | 1 | 8/8 tests, geomean 0.380, total 0.441, p95 0.429 |
| independent SLRU | 1 | 8/8 tests at the boundary (0.388 / 0.456 / 0.470) |
| starter LRU | 0 | fails geomean, total, and p95 gates |
| hidden-trace cheat | 0 | contract violation + performance failure |
| empty submission | 0 | missing artifact |

The task README records the full comparator table, threshold calibration, and
anti-cheat design. Reproduce the verifier locally with:

```bash
cd benchmarks/disagg-kv-retention
docker build -t kvret-env:v3 environment
docker build -t kvret-tests:v4 tests
docker run --rm kvret-env:v3 sh -c \
  'python starter/check_policy.py --policy starter/policy.py'
# grade a policy: mount it at /app/policy.py in the tests image
docker run --rm -v "$PWD/policy.py:/app/policy.py:ro" kvret-tests:v4 \
  bash /tests/test.sh
```

The benchmark README explains why this paper met the bar; the next section
records the bar itself.

## Benchmark selection criteria

A paper is eligible for benchmark-grade authoring only if all of the following
hold:

1. **A decision, not a summary.** The paper defines an online decision an agent
   can implement (admit/evict, schedule, route, rank, calibrate) with a
   machine-checkable interface.
2. **A measurable objective.** The decision has a scalar cost or quality metric
   that can be computed by a simulator or a deterministic checker, without
   human judgement.
3. **A held-out split.** Workloads or instances can be generated that differ
   structurally from the public development set, so memorization does not pass.
4. **A non-trivial baseline.** A standard textbook policy leaves a measurable
   gap that a better design can close, and the gap is stable across seeds.
5. **Independent solvability.** At least one implementation written without
   access to the hidden split clears every gate; a gate only the reference can
   hit is rejected as too tight.
6. **Adversarial safety.** The verifier can isolate the submitted artifact,
   withhold the holdout, resist copying, and detect malformed outputs.

Papers that only describe an architecture, report a leaderboard result without
an implementable decision, or depend on proprietary data stay demo-grade. The
six generated tasks in `envs/` are retained as pipeline fixtures, not as
benchmarks.


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

- Generated demo tasks run their reference solution twice; non-determinism
  fails validation. Positive check (reference output) must score 1.0; negative
  check (empty workspace) must score 0.0.
- Generated demo tasks are offline, stdlib-only, and finish in under 60 seconds.
  Benchmark tasks give the agent open network access, but the graded artifact is
  stdlib-only and the verifier runs with no network.
- The benchmark pilot was built and graded with Docker 29.8 in Linux
  containers; generated tasks are validated with the local sandbox rather than
  a Harbor image build.
- Benchmark rewards are binary and conjunctive; demo rewards are the fraction
  of matching checks. The two tiers are not comparable.
- MCode evaluation is a real solve attempt, not a simulation; its reward can be
  lower than the reference. Failures are kept and shown in the dashboard.
