"""Karotte mirror of the Harbor task rsi-forge-research-papers-environments-r-009426."""

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
_INSTRUCTION = 'You are auditing repeated attempts from an automated coding loop. Each run uses fixed model weights; attempts inherit code and notes from earlier attempts. Your job is to decide which runs show a real holdout improvement, which improved through work beyond parameter tuning, which show tuning-to-development overfitting, and which run ranks best.\n\nInput: `attempts.csv` in the current directory. It has columns:\n`run_id, model, task, attempt, train_score, dev_score, holdout_score, se, edit, exploration`.\n- `attempt` is an integer. In this file each run has attempts 1, 2, 3.\n- `train_score`, `dev_score`, and `holdout_score` are higher-is-better.\n- `se` is the reported standard error of the holdout estimate. Use the final attempt\'s `se`.\n- `edit` is one of `none`, `tuning`, `algorithm`, `feature`, `bugfix`.\n- `exploration` is an integer count of distinct probes tried during the attempt.\n\nFor each distinct `run_id`, sort rows by `attempt`. Use attempt 1 as baseline and the largest attempt number as final. Compute:\n- `delta = final_holdout_score - baseline_holdout_score`\n- `exploration_gain = final_exploration - baseline_exploration`\n- A run is `genuine` if `delta > final_se` (strictly greater).\n- A genuine run is `beyond_tuning` if its final `edit` is not `tuning` and not `none`.\n- A run is `tuning_overfit` if all three hold: final `edit` is `tuning`; final `dev_score > baseline dev_score`; and `delta <= final_se`.\n\nWrite `answer.json` with these keys (extra keys are allowed):\n1. `genuine_runs`: sorted list of run ids that are genuine.\n2. `genuine_rate`: number of genuine runs divided by total distinct runs, rounded to 3 decimal places.\n3. `beyond_tuning_runs`: sorted list of genuine run ids that are beyond_tuning.\n4. `beyond_tuning_rate`: number of beyond_tuning runs divided by number of genuine runs, rounded to 3 decimal places; use 0.0 if there are no genuine runs.\n5. `tuning_overfit_runs`: sorted list of run ids that are tuning_overfit.\n6. `top_run`: among genuine runs only, the run id with highest final `holdout_score`; break ties by higher `exploration_gain`; break remaining ties by lexicographically smallest `run_id`. If there are no genuine runs, use the empty string.\n7. `top_final_holdout`: final `holdout_score` of `top_run`, rounded to 3 decimal places; 0.0 if no genuine run.\n8. `mean_exploration_gain_genuine`: mean `exploration_gain` over genuine runs, rounded to 3 decimal places; 0.0 if no genuine run.\n\nExample shape (values are illustrative only):\n{"genuine_runs":["R01","R02"],"genuine_rate":0.4,"beyond_tuning_runs":["R01"],"beyond_tuning_rate":0.5,"tuning_overfit_runs":["R06"],"top_run":"R01","top_final_holdout":0.74,"mean_exploration_gain_genuine":12.5}\n\nUse only the Python standard library. The deliverable path is `answer.json` relative to the current directory.\n\nWork offline using only the Python 3.12 standard library. Do not use the network.\n\nWork in /workdir. The graded file is `answer.json` in that directory.\n'


@final
class PaperTask(Task):
    id = 'rsi-forge-research-papers-environments-r-009426'

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
