# Hybrid fine-tuning module selection

You are given `modules.csv`. Each row describes one linear module in a small model. Your program must compute a deterministic hybrid post-training plan: choose a subset of modules to train with full fine-tuning (FFT), and leave the remaining modules on low-rank adaptation (LoRA). The FFT subset must respect a fixed parameter budget equal to 10% of the total FFT parameter count.

## Input
`modules.csv` has columns:
`module,kind,in_dim,out_dim,rank,lora_gain,fft_gain`
- `module`: unique name.
- `kind`: metadata only; do not use it in calculations.
- `in_dim`, `out_dim`: integer dimensions of the linear weight.
- `rank`: LoRA rank.
- `lora_gain`, `fft_gain`: integer validation gains (higher is better) if this module is adapted with LoRA or full fine-tuned, respectively.

For each row compute:
- FFT parameter count: `fft_params = in_dim * out_dim`
- LoRA parameter count: `lora_params = rank * (in_dim + out_dim)`
- Marginal gain: `delta = fft_gain - lora_gain`
- Extra FFT cost: `extra = fft_params - lora_params` (all rows have `extra > 0`)
- Hybrid-LoRA score: `score = delta / extra`

## Budget
Let `total_fft = sum(fft_params over all rows)`.
The FFT budget is `budget = floor(0.10 * total_fft)`. With integer arithmetic this is `total_fft // 10`.

## Selection rule
Choose a subset `S` of modules to full fine-tune such that:
`sum(fft_params for i in S) <= budget`.

Among all such subsets, maximize `sum(delta for i in S)`. The empty set is allowed; you do not have to spend the whole budget. Do not select a module with `delta <= 0` unless it is part of a true optimum (it never is here, because cost only increases).

Tie-breaking for the selected subset:
1. Maximize total `delta`.
2. If tied, minimize total `fft_params` used.
3. If still tied, take the subset whose module names, sorted in ascending lexicographic order, form the lexicographically smallest list (standard Python list comparison).

## Output
Write `answer.json` in the current directory. Extra keys are allowed. Required keys:

- `budget` (integer): computed budget.
- `ranking` (list of strings): all module names sorted by `score` descending; ties by module name ascending. Use the exact fraction `delta/extra` for ordering, not a rounded score.
- `selected_modules` (list of strings): names in the chosen subset `S`, sorted ascending.
- `budget_used` (integer): sum of `fft_params` over `selected_modules`.
- `baseline_lora_gain` (integer): sum of `lora_gain` over all modules.
- `hybrid_gain` (integer): `baseline_lora_gain + sum(delta for i in S)`.
- `improvement` (integer): `hybrid_gain - baseline_lora_gain`.
- `top_score` (number): the `score` of the first module in `ranking`, rounded to exactly 6 decimal places.

Example shape (values are illustrative, not the answer):
{
  "budget": 1234,
  "ranking": ["module_a", "module_b"],
  "selected_modules": ["module_a"],
  "budget_used": 1000,
  "baseline_lora_gain": 500,
  "hybrid_gain": 530,
  "improvement": 30,
  "top_score": 0.012345
}

All integer and list fields are compared exactly. `top_score` is compared exactly after rounding to 6 decimals. Do not round any other field.

Work offline using only the Python 3.12 standard library. Do not use the network.
