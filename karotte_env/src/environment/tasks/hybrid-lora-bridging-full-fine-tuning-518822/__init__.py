"""Karotte mirror of the Harbor task hybrid-lora-bridging-full-fine-tuning-518822."""

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
_INSTRUCTION = '# Hybrid fine-tuning module selection\n\nYou are given `modules.csv`. Each row describes one linear module in a small model. Your program must compute a deterministic hybrid post-training plan: choose a subset of modules to train with full fine-tuning (FFT), and leave the remaining modules on low-rank adaptation (LoRA). The FFT subset must respect a fixed parameter budget equal to 10% of the total FFT parameter count.\n\n## Input\n`modules.csv` has columns:\n`module,kind,in_dim,out_dim,rank,lora_gain,fft_gain`\n- `module`: unique name.\n- `kind`: metadata only; do not use it in calculations.\n- `in_dim`, `out_dim`: integer dimensions of the linear weight.\n- `rank`: LoRA rank.\n- `lora_gain`, `fft_gain`: integer validation gains (higher is better) if this module is adapted with LoRA or full fine-tuned, respectively.\n\nFor each row compute:\n- FFT parameter count: `fft_params = in_dim * out_dim`\n- LoRA parameter count: `lora_params = rank * (in_dim + out_dim)`\n- Marginal gain: `delta = fft_gain - lora_gain`\n- Extra FFT cost: `extra = fft_params - lora_params` (all rows have `extra > 0`)\n- Hybrid-LoRA score: `score = delta / extra`\n\n## Budget\nLet `total_fft = sum(fft_params over all rows)`.\nThe FFT budget is `budget = floor(0.10 * total_fft)`. With integer arithmetic this is `total_fft // 10`.\n\n## Selection rule\nChoose a subset `S` of modules to full fine-tune such that:\n`sum(fft_params for i in S) <= budget`.\n\nAmong all such subsets, maximize `sum(delta for i in S)`. The empty set is allowed; you do not have to spend the whole budget. Do not select a module with `delta <= 0` unless it is part of a true optimum (it never is here, because cost only increases).\n\nTie-breaking for the selected subset:\n1. Maximize total `delta`.\n2. If tied, minimize total `fft_params` used.\n3. If still tied, take the subset whose module names, sorted in ascending lexicographic order, form the lexicographically smallest list (standard Python list comparison).\n\n## Output\nWrite `answer.json` in the current directory. Extra keys are allowed. Required keys:\n\n- `budget` (integer): computed budget.\n- `ranking` (list of strings): all module names sorted by `score` descending; ties by module name ascending. Use the exact fraction `delta/extra` for ordering, not a rounded score.\n- `selected_modules` (list of strings): names in the chosen subset `S`, sorted ascending.\n- `budget_used` (integer): sum of `fft_params` over `selected_modules`.\n- `baseline_lora_gain` (integer): sum of `lora_gain` over all modules.\n- `hybrid_gain` (integer): `baseline_lora_gain + sum(delta for i in S)`.\n- `improvement` (integer): `hybrid_gain - baseline_lora_gain`.\n- `top_score` (number): the `score` of the first module in `ranking`, rounded to exactly 6 decimal places.\n\nExample shape (values are illustrative, not the answer):\n{\n  "budget": 1234,\n  "ranking": ["module_a", "module_b"],\n  "selected_modules": ["module_a"],\n  "budget_used": 1000,\n  "baseline_lora_gain": 500,\n  "hybrid_gain": 530,\n  "improvement": 30,\n  "top_score": 0.012345\n}\n\nAll integer and list fields are compared exactly. `top_score` is compared exactly after rounding to 6 decimals. Do not round any other field.\n\nWork offline using only the Python 3.12 standard library. Do not use the network.\n\nWork in /workdir. The graded file is `answer.json` in that directory.\n'


@final
class PaperTask(Task):
    id = 'hybrid-lora-bridging-full-fine-tuning-518822'

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
