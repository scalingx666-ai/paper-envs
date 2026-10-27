"""Karotte mirror of the Harbor task flashrt-agent-harness-guiding-agents-dep-718171."""

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
_DELIVERABLE = 'answer.json'
_INSTRUCTION = "# Pipeline Deployment Optimizer\n\nYou are given a small dataflow pipeline and must simulate a measurement-gated deployment optimizer. All data is in `pipeline.json` in the current directory. Write a deterministic Python 3.12 program using only the standard library. It must read `pipeline.json` and write `answer.json`.\n\n## Input\n\n`pipeline.json` contains:\n- `devices`: list of objects `{id, capacity}`. `capacity` is positive.\n- `stages`: list of stage objects. Each has `id`, `base_latency_ms`, `work`, `state_key` (string or null), `deps` (stage ids that must run before this stage), and `candidates`. Each candidate has `name`, `placement`, `shards`, and `chunk`. Every stage has exactly one candidate named `ref`.\n- `weights`: object with numeric `latency` and `throughput`.\n- `min_gain`: float.\n- `max_passes`: positive integer.\n\n## 1. Intermediate representation\n\nBuild the dependency DAG from each stage's `deps`. Produce `topological_order` using Kahn's algorithm: start with stages that have no remaining dependencies; whenever several are ready, choose the lexicographically smallest stage id. If a cycle exists, raise an error.\n\nGroup stages with a non-null `state_key` into state scopes. For each key, list its stage ids in topological order. Output `state_scopes` as a list of objects sorted by `key` lexicographically, for example `[{'key': 'kv', 'stages': ['s3', 's4']}]`.\n\n## 2. Plan validity\n\nA plan assigns one candidate to every stage. A plan is valid if every stage with a non-null `state_key` uses a candidate with `shards == 1`, and all stages sharing the same non-null `state_key` use the same `placement`. Dependencies may cross devices. The baseline plan uses the candidate named `ref` for every stage.\n\n## 3. Metrics for a plan\n\nFor each stage `s` with chosen candidate `c`, compute `stage_latency = base_latency_ms / c.shards + 0.8 * (c.chunk - 1)`.\n\nFor each dependency edge `u -> v` where `u` is in `v.deps`, if `placement(u) != placement(v)`, add `3.0` ms.\n\n`latency_ms` is the sum of all stage latencies plus all cross-device edge costs.\n\nFor each device `d`, let `load[d]` be the sum over stages placed on `d` of `work / (c.shards * c.chunk)`. Let `max_ratio = max(load[d] / capacity[d])` over all devices. If `max_ratio <= 1e-12`, set `throughput = 0.0`; otherwise `throughput = 1000.0 / max_ratio`.\n\n`objective = weights.latency * latency_ms - weights.throughput * throughput`. Lower objective is better.\n\n## 4. Measurement-gated coordinate descent\n\nStart from the baseline plan. Repeat for at most `max_passes` passes. In each pass, visit stages in `topological_order`.\n\nFor the current stage:\n- Consider every candidate for that stage in lexicographic order of `name`, skipping the candidate already selected for that stage.\n- Form a trial plan by replacing only this stage's candidate. If the trial plan is invalid, ignore that candidate.\n- Compute its metrics. Let `gain = (current_objective - trial_objective) / max(abs(current_objective), 1e-9)`.\n- Among candidates with `gain > min_gain` and `trial_objective < best_so_far - 1e-12`, select the one with the smallest trial objective. Strict inequalities are required: a tie or a gain exactly equal to `min_gain` is not accepted.\n- If a candidate is selected, update the current plan and current metrics before moving to the next stage. Otherwise leave the stage unchanged.\n\nA pass counts as changed if at least one stage's candidate was updated. Stop early when a pass makes no change. `passes_used` is the number of passes that made at least one change.\n\n## 5. Deliverable\n\nWrite `answer.json` with these keys (extra keys are allowed):\n\n{\n  'topological_order': ['s1', 's2'],\n  'state_scopes': [{'key': 'kv', 'stages': ['s3', 's4']}],\n  'final_plan': ['s1:ref', 's2:shard2'],\n  'accepted': ['s2:shard2'],\n  'metrics': {\n    'latency_ms': 112.6,\n    'throughput': 1000.0,\n    'objective': 12.6\n  },\n  'passes_used': 2\n}\n\n`final_plan` is a list in `topological_order`; each entry is `'<stage_id>:<candidate_name>'`. `accepted` is the list of final-plan entries whose candidate name is not `ref`, in `topological_order`. Round `metrics.latency_ms`, `metrics.throughput`, and `metrics.objective` to 3 decimal places. `passes_used` is an integer.\n\nUse only Python standard library. Do not use the network. The program must be deterministic.\n\nWork offline using only the Python 3.12 standard library. Do not use the network.\n\nWork in /workdir. The graded file is `answer.json` in that directory.\n"


@final
class PaperTask(Task):
    id = 'flashrt-agent-harness-guiding-agents-dep-718171'

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
        return {}

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
