# Pipeline Deployment Optimizer

You are given a small dataflow pipeline and must simulate a measurement-gated deployment optimizer. All data is in `pipeline.json` in the current directory. Write a deterministic Python 3.12 program using only the standard library. It must read `pipeline.json` and write `answer.json`.

## Input

`pipeline.json` contains:
- `devices`: list of objects `{id, capacity}`. `capacity` is positive.
- `stages`: list of stage objects. Each has `id`, `base_latency_ms`, `work`, `state_key` (string or null), `deps` (stage ids that must run before this stage), and `candidates`. Each candidate has `name`, `placement`, `shards`, and `chunk`. Every stage has exactly one candidate named `ref`.
- `weights`: object with numeric `latency` and `throughput`.
- `min_gain`: float.
- `max_passes`: positive integer.

## 1. Intermediate representation

Build the dependency DAG from each stage's `deps`. Produce `topological_order` using Kahn's algorithm: start with stages that have no remaining dependencies; whenever several are ready, choose the lexicographically smallest stage id. If a cycle exists, raise an error.

Group stages with a non-null `state_key` into state scopes. For each key, list its stage ids in topological order. Output `state_scopes` as a list of objects sorted by `key` lexicographically, for example `[{'key': 'kv', 'stages': ['s3', 's4']}]`.

## 2. Plan validity

A plan assigns one candidate to every stage. A plan is valid if every stage with a non-null `state_key` uses a candidate with `shards == 1`, and all stages sharing the same non-null `state_key` use the same `placement`. Dependencies may cross devices. The baseline plan uses the candidate named `ref` for every stage.

## 3. Metrics for a plan

For each stage `s` with chosen candidate `c`, compute `stage_latency = base_latency_ms / c.shards + 0.8 * (c.chunk - 1)`.

For each dependency edge `u -> v` where `u` is in `v.deps`, if `placement(u) != placement(v)`, add `3.0` ms.

`latency_ms` is the sum of all stage latencies plus all cross-device edge costs.

For each device `d`, let `load[d]` be the sum over stages placed on `d` of `work / (c.shards * c.chunk)`. Let `max_ratio = max(load[d] / capacity[d])` over all devices. If `max_ratio <= 1e-12`, set `throughput = 0.0`; otherwise `throughput = 1000.0 / max_ratio`.

`objective = weights.latency * latency_ms - weights.throughput * throughput`. Lower objective is better.

## 4. Measurement-gated coordinate descent

Start from the baseline plan. Repeat for at most `max_passes` passes. In each pass, visit stages in `topological_order`.

For the current stage:
- Consider every candidate for that stage in lexicographic order of `name`, skipping the candidate already selected for that stage.
- Form a trial plan by replacing only this stage's candidate. If the trial plan is invalid, ignore that candidate.
- Compute its metrics. Let `gain = (current_objective - trial_objective) / max(abs(current_objective), 1e-9)`.
- Among candidates with `gain > min_gain` and `trial_objective < best_so_far - 1e-12`, select the one with the smallest trial objective. Strict inequalities are required: a tie or a gain exactly equal to `min_gain` is not accepted.
- If a candidate is selected, update the current plan and current metrics before moving to the next stage. Otherwise leave the stage unchanged.

A pass counts as changed if at least one stage's candidate was updated. Stop early when a pass makes no change. `passes_used` is the number of passes that made at least one change.

## 5. Deliverable

Write `answer.json` with these keys (extra keys are allowed):

{
  'topological_order': ['s1', 's2'],
  'state_scopes': [{'key': 'kv', 'stages': ['s3', 's4']}],
  'final_plan': ['s1:ref', 's2:shard2'],
  'accepted': ['s2:shard2'],
  'metrics': {
    'latency_ms': 112.6,
    'throughput': 1000.0,
    'objective': 12.6
  },
  'passes_used': 2
}

`final_plan` is a list in `topological_order`; each entry is `'<stage_id>:<candidate_name>'`. `accepted` is the list of final-plan entries whose candidate name is not `ref`, in `topological_order`. Round `metrics.latency_ms`, `metrics.throughput`, and `metrics.objective` to 3 decimal places. `passes_used` is an integer.

Use only Python standard library. Do not use the network. The program must be deterministic.

Work offline using only the Python 3.12 standard library. Do not use the network.
