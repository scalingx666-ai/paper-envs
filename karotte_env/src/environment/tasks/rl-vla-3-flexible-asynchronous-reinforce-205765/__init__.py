"""Karotte mirror of the Harbor task rl-vla-3-flexible-asynchronous-reinforce-205765."""

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
_INSTRUCTION = "# Asynchronous rollout-segment scheduling\n\nYou are given a deterministic model of an asynchronous training pipeline with independent environment shards, a batched policy-inference server, and a minibatch trainer. Implement the discrete-event simulation described below. Use only the Python 3.12 standard library. Read files from the current directory and write `answer.json`.\n\n## Inputs\n`config.json` keys: `env_count` (E), `chunk_steps` (H), `max_inference_batch` (B_inf), `inference_base` (a), `inference_per_item` (b), `max_train_batch` (B_train), `train_base` (c), `train_per_item` (d). Inference service time for batch size n is a + b*n. Training service time for batch size n is c + d*n.\n\n`latencies.csv` columns: `env_id,step_index,latency`. For each env, sort its rows by `step_index` and split the sorted rows into consecutive groups of H rows (last group may be shorter). One group is one rollout segment. Its simulation duration is the sum of latency values in the group. Segment indices start at 0 within each env.\n\n## Pipeline rules\n1. At time 0, every env with at least one segment puts an inference request for its segment 0 into the inference FIFO queue. Initial requests are ordered by increasing env_id.\n2. Inference server: whenever it is idle and its queue is non-empty, it starts immediately at the current time. It takes the first min(queue_length, B_inf) requests from the FIFO queue. It is busy for a + b*n time units, where n is the batch size. Requests arriving during this interval wait.\n3. When an inference batch completes at time t, each request in that batch causes its env to begin simulating that segment. Different envs simulate concurrently. The segment finishes at t plus the segment duration.\n4. When a segment finishes simulation at time t, append (env_id, segment_index) to the trainer FIFO queue. If the env has a next segment, append its inference request to the inference queue at time t. The env does not wait for training.\n5. Trainer: whenever it is idle, if the trainer queue has at least B_train segments, start a batch of exactly the oldest B_train segments. Otherwise, if all segments from all envs have finished simulation and the trainer queue is non-empty, start one final batch containing all currently queued segments. Otherwise wait. A training batch of size n is busy for c + d*n time units.\n6. At a single timestamp t, process all completion events that occur at t before starting any new inference or training batch at t. For deterministic FIFO order, simultaneous simulation completions are processed in the order their inference requests were served inside the earlier inference batch; if still tied, by lower env_id and then lower segment index. New inference requests created by simultaneous completions are enqueued in that same order.\n\n## Required metrics\n`async_makespan` is the completion time of the last training batch. `env_finish_times[e]` is the time when env e's last segment finishes simulation (0 if env e has no segments).\n\nFor the synchronous baseline, let S be the maximum number of segments over all envs. For round j = 0..S-1, let A_j be the set of envs that have a j-th segment. If A_j is empty, skip it. The round time is max_{e in A_j}(duration[e][j]) + (a + b*|A_j|) + (c + d*|A_j|). Sum all round times to get `sync_makespan`. This models lockstep rounds: all active envs finish their segment, then one inference batch and one training batch run for the round.\n\n## Deliverable\nWrite `answer.json` with these keys (extra keys are allowed):\n- `async_makespan`: integer.\n- `sync_makespan`: integer.\n- `speedup`: round(sync_makespan / async_makespan, 4) as a number; use 0.0 if async_makespan is 0.\n- `inference_batches`: integer count of inference batches started.\n- `train_batches`: integer count of training batches started.\n- `avg_inference_batch_size`: round(total inference requests served / inference_batches, 4) as a number; use 0.0 if there are no inference batches.\n- `env_finish_times`: list of E integers.\nUse Python round(x, 4) for all rounded values. The grader compares rounded values exactly. Example shape (not the fixture answer): {async_makespan: 123, sync_makespan: 150, speedup: 1.2195, inference_batches: 7, train_batches: 3, avg_inference_batch_size: 2.2857, env_finish_times: [10, 20, 30, 40]}.\n\nWork offline using only the Python 3.12 standard library. Do not use the network.\n\nWork in /workdir. The graded file is `answer.json` in that directory.\n"


@final
class PaperTask(Task):
    id = 'rl-vla-3-flexible-asynchronous-reinforce-205765'

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
