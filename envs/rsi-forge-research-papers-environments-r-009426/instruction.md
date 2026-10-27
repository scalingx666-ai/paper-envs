You are auditing repeated attempts from an automated coding loop. Each run uses fixed model weights; attempts inherit code and notes from earlier attempts. Your job is to decide which runs show a real holdout improvement, which improved through work beyond parameter tuning, which show tuning-to-development overfitting, and which run ranks best.

Input: `attempts.csv` in the current directory. It has columns:
`run_id, model, task, attempt, train_score, dev_score, holdout_score, se, edit, exploration`.
- `attempt` is an integer. In this file each run has attempts 1, 2, 3.
- `train_score`, `dev_score`, and `holdout_score` are higher-is-better.
- `se` is the reported standard error of the holdout estimate. Use the final attempt's `se`.
- `edit` is one of `none`, `tuning`, `algorithm`, `feature`, `bugfix`.
- `exploration` is an integer count of distinct probes tried during the attempt.

For each distinct `run_id`, sort rows by `attempt`. Use attempt 1 as baseline and the largest attempt number as final. Compute:
- `delta = final_holdout_score - baseline_holdout_score`
- `exploration_gain = final_exploration - baseline_exploration`
- A run is `genuine` if `delta > final_se` (strictly greater).
- A genuine run is `beyond_tuning` if its final `edit` is not `tuning` and not `none`.
- A run is `tuning_overfit` if all three hold: final `edit` is `tuning`; final `dev_score > baseline dev_score`; and `delta <= final_se`.

Write `answer.json` with these keys (extra keys are allowed):
1. `genuine_runs`: sorted list of run ids that are genuine.
2. `genuine_rate`: number of genuine runs divided by total distinct runs, rounded to 3 decimal places.
3. `beyond_tuning_runs`: sorted list of genuine run ids that are beyond_tuning.
4. `beyond_tuning_rate`: number of beyond_tuning runs divided by number of genuine runs, rounded to 3 decimal places; use 0.0 if there are no genuine runs.
5. `tuning_overfit_runs`: sorted list of run ids that are tuning_overfit.
6. `top_run`: among genuine runs only, the run id with highest final `holdout_score`; break ties by higher `exploration_gain`; break remaining ties by lexicographically smallest `run_id`. If there are no genuine runs, use the empty string.
7. `top_final_holdout`: final `holdout_score` of `top_run`, rounded to 3 decimal places; 0.0 if no genuine run.
8. `mean_exploration_gain_genuine`: mean `exploration_gain` over genuine runs, rounded to 3 decimal places; 0.0 if no genuine run.

Example shape (values are illustrative only):
{"genuine_runs":["R01","R02"],"genuine_rate":0.4,"beyond_tuning_runs":["R01"],"beyond_tuning_rate":0.5,"tuning_overfit_runs":["R06"],"top_run":"R01","top_final_holdout":0.74,"mean_exploration_gain_genuine":12.5}

Use only the Python standard library. The deliverable path is `answer.json` relative to the current directory.

Work offline using only the Python 3.12 standard library. Do not use the network.
