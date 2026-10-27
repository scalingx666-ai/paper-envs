# KV-Cache LRU Working Set

## Goal
You are given a trace of references to KV-cache pages and a list of candidate cache capacities in pages. Implement the Mattson stack algorithm to compute the LRU stack distance of every page reference, then use those distances to report cache hit counts/rates and the minimum capacity needed for two target hit rates.

## Inputs
- `trace.json`: an object with key `requests`. Each request has a `pages` list of page-id strings. Concatenate the `pages` lists in the given request order (and in list order within a request) to form the single reference sequence. Empty `pages` lists contribute nothing.
- `config.json`: an object with:
  - `capacities`: a list of positive integer capacities in pages,
  - `target_hit_rate`: a floating-point target,
  - `stretch_target_hit_rate`: a second floating-point target.

## Required computation
Maintain an LRU stack of distinct page ids, ordered most-recently-used first. For each reference `p` in the concatenated sequence:
1. If `p` is currently in the stack at zero-based position `d`, record finite stack distance `d` and move `p` to the front.
2. If `p` is not in the stack, record a cold miss (infinite distance), then insert `p` at the front.

A cache with capacity `C` pages is modeled as retaining the first `C` entries of this LRU stack. Therefore a reference is a hit at capacity `C` iff it has finite stack distance `d` and `d < C`. Cold misses are hits at no finite capacity. The hit count for `C` is the number of references satisfying this condition; the hit rate is `hit_count / total_accesses`.

Define:
- `D` = maximum finite stack distance observed, or `-1` if there are no finite distances.
- `stack_distance_histogram[d]` for `d = 0..D` = number of references with finite stack distance exactly `d`. If `D == -1`, use `[]`.

For a target `T`, `min_capacity` is the smallest integer `C >= 1` with `hit_count(C) / total_accesses >= T`. If no finite capacity reaches `T`, use `null`. Treat equality as reaching the target.

## Deliverable
Write exactly one JSON object to `answer.json` with these keys:
- `total_accesses`: integer length of the concatenated reference sequence.
- `distinct_pages`: integer number of distinct page ids seen.
- `stack_distance_histogram`: list of integers as defined above.
- `hit_counts_by_capacity`: list of integers aligned with `config.json` `capacities`.
- `hit_rates_by_capacity`: list of numbers aligned with `capacities`; each must be `round(hit_count / total_accesses, 6)`.
- `min_capacity_for_target`: integer or `null` for `target_hit_rate`.
- `min_capacity_for_stretch_target`: integer or `null` for `stretch_target_hit_rate`.

Extra keys are allowed. Use Python's `round(x, 6)` for rates. Rounded rates are compared exactly; do not rely on an implicit tolerance. Do not write any other files.

Work offline using only the Python 3.12 standard library. Do not use the network.
