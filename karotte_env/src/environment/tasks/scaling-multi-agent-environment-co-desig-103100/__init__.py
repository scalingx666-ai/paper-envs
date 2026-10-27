"""Karotte mirror of the Harbor task scaling-multi-agent-environment-co-desig-103100."""

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
_INSTRUCTION = "You are given config.json. It defines K = num_obstacles obstacles on the real line, indexed 0..K-1 in increasing order. A configuration x is feasible when x[i+1] - x[i] >= min_gap for every i. There are no upper or lower bounds.\n\nThe reward density is:\nf(x) = sum over attractors of weight * exp(-((x - center)^2) / (2 * sigma^2))\n     - sum over repellers of weight * exp(-((x - center)^2) / (2 * sigma^2))\n     - global_pull.weight * (x - global_pull.center)^2.\n\nThe derivative is:\nf'(x) = sum over attractors of weight * (-(x - center) / sigma^2) * exp(...)\n      - sum over repellers of weight * (-(x - center) / sigma^2) * exp(...)\n      - 2 * global_pull.weight * (x - global_pull.center).\n\nA dense critic field is defined by critic_samples and critic_bandwidth h:\nC(x) = [sum_k weight_k * gradient_k * exp(-((x - position_k)^2) / (2 * h^2))]\n     / [sum_k weight_k * exp(-((x - position_k)^2) / (2 * h^2))],\nif the denominator is greater than 1e-15; otherwise C(x) = 0. Samples with weight 0 contribute nothing.\n\nInitialize x = initial_positions. Do not project before the first update. For t = 0, 1, ..., iterations-1:\n  beta = beta_start + (beta_end - beta_start) * t / (iterations - 1) if iterations > 1, else beta_end.\n  For every i, using the current x, compute:\n    g_reward = f'(x[i])\n    g_critic = C(x[i])\n    x_new[i] = x[i] + step_size * ((1 - beta) * g_reward + beta * g_critic)\n  Then project the whole vector x_new onto the feasible set using this exact PAVA procedure:\n    shifted[i] = x_new[i] - i * min_gap for i = 0..K-1.\n    Run pool-adjacent-violators on shifted to produce a nondecreasing sequence y:\n      stack = empty list of blocks, each block = (sum, count)\n      merges_this_projection = 0\n      for val in shifted:\n        push (val, 1)\n        while there are at least two blocks and mean(previous block) > mean(last block):\n          pop the last two blocks, merge them into one block, count one merge\n      expand each block mean count times to obtain y[0..K-1]\n    Set x[i] = y[i] + i * min_gap for all i.\n    Add merges_this_projection to total_projection_merges.\nAfter all iterations, write answer.json with:\n  positions: final x values rounded to 6 decimals (list of K floats)\n  reward: sum_i f(x[i]) rounded to 6 decimals\n  min_gap: min_i (x[i+1] - x[i]) rounded to 6 decimals\n  projection_merges: total number of block merges across all projections (integer)\nExtra keys are allowed. Use Python round to 6 decimals. The comparison is exact after rounding. Answer path is relative: answer.json.\nExample schema: {'positions': [10.123456, 20.0, ...], 'reward': 12.345678, 'min_gap': 8.0, 'projection_merges': 17}.\n\nWork offline using only the Python 3.12 standard library. Do not use the network.\n\nWork in /workdir. The graded file is `answer.json` in that directory.\n"


@final
class PaperTask(Task):
    id = 'scaling-multi-agent-environment-co-desig-103100'

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
