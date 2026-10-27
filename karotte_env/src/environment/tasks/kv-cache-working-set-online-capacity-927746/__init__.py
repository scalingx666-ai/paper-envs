"""Karotte mirror of the Harbor task kv-cache-working-set-online-capacity-927746."""

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
_INSTRUCTION = "# KV-Cache LRU Working Set\n\n## Goal\nYou are given a trace of references to KV-cache pages and a list of candidate cache capacities in pages. Implement the Mattson stack algorithm to compute the LRU stack distance of every page reference, then use those distances to report cache hit counts/rates and the minimum capacity needed for two target hit rates.\n\n## Inputs\n- `trace.json`: an object with key `requests`. Each request has a `pages` list of page-id strings. Concatenate the `pages` lists in the given request order (and in list order within a request) to form the single reference sequence. Empty `pages` lists contribute nothing.\n- `config.json`: an object with:\n  - `capacities`: a list of positive integer capacities in pages,\n  - `target_hit_rate`: a floating-point target,\n  - `stretch_target_hit_rate`: a second floating-point target.\n\n## Required computation\nMaintain an LRU stack of distinct page ids, ordered most-recently-used first. For each reference `p` in the concatenated sequence:\n1. If `p` is currently in the stack at zero-based position `d`, record finite stack distance `d` and move `p` to the front.\n2. If `p` is not in the stack, record a cold miss (infinite distance), then insert `p` at the front.\n\nA cache with capacity `C` pages is modeled as retaining the first `C` entries of this LRU stack. Therefore a reference is a hit at capacity `C` iff it has finite stack distance `d` and `d < C`. Cold misses are hits at no finite capacity. The hit count for `C` is the number of references satisfying this condition; the hit rate is `hit_count / total_accesses`.\n\nDefine:\n- `D` = maximum finite stack distance observed, or `-1` if there are no finite distances.\n- `stack_distance_histogram[d]` for `d = 0..D` = number of references with finite stack distance exactly `d`. If `D == -1`, use `[]`.\n\nFor a target `T`, `min_capacity` is the smallest integer `C >= 1` with `hit_count(C) / total_accesses >= T`. If no finite capacity reaches `T`, use `null`. Treat equality as reaching the target.\n\n## Deliverable\nWrite exactly one JSON object to `answer.json` with these keys:\n- `total_accesses`: integer length of the concatenated reference sequence.\n- `distinct_pages`: integer number of distinct page ids seen.\n- `stack_distance_histogram`: list of integers as defined above.\n- `hit_counts_by_capacity`: list of integers aligned with `config.json` `capacities`.\n- `hit_rates_by_capacity`: list of numbers aligned with `capacities`; each must be `round(hit_count / total_accesses, 6)`.\n- `min_capacity_for_target`: integer or `null` for `target_hit_rate`.\n- `min_capacity_for_stretch_target`: integer or `null` for `stretch_target_hit_rate`.\n\nExtra keys are allowed. Use Python's `round(x, 6)` for rates. Rounded rates are compared exactly; do not rely on an implicit tolerance. Do not write any other files.\n\nWork offline using only the Python 3.12 standard library. Do not use the network.\n\nWork in /workdir. The graded file is `answer.json` in that directory.\n"


@final
class PaperTask(Task):
    id = 'kv-cache-working-set-online-capacity-927746'

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
