You are given config.json. It defines K = num_obstacles obstacles on the real line, indexed 0..K-1 in increasing order. A configuration x is feasible when x[i+1] - x[i] >= min_gap for every i. There are no upper or lower bounds.

The reward density is:
f(x) = sum over attractors of weight * exp(-((x - center)^2) / (2 * sigma^2))
     - sum over repellers of weight * exp(-((x - center)^2) / (2 * sigma^2))
     - global_pull.weight * (x - global_pull.center)^2.

The derivative is:
f'(x) = sum over attractors of weight * (-(x - center) / sigma^2) * exp(...)
      - sum over repellers of weight * (-(x - center) / sigma^2) * exp(...)
      - 2 * global_pull.weight * (x - global_pull.center).

A dense critic field is defined by critic_samples and critic_bandwidth h:
C(x) = [sum_k weight_k * gradient_k * exp(-((x - position_k)^2) / (2 * h^2))]
     / [sum_k weight_k * exp(-((x - position_k)^2) / (2 * h^2))],
if the denominator is greater than 1e-15; otherwise C(x) = 0. Samples with weight 0 contribute nothing.

Initialize x = initial_positions. Do not project before the first update. For t = 0, 1, ..., iterations-1:
  beta = beta_start + (beta_end - beta_start) * t / (iterations - 1) if iterations > 1, else beta_end.
  For every i, using the current x, compute:
    g_reward = f'(x[i])
    g_critic = C(x[i])
    x_new[i] = x[i] + step_size * ((1 - beta) * g_reward + beta * g_critic)
  Then project the whole vector x_new onto the feasible set using this exact PAVA procedure:
    shifted[i] = x_new[i] - i * min_gap for i = 0..K-1.
    Run pool-adjacent-violators on shifted to produce a nondecreasing sequence y:
      stack = empty list of blocks, each block = (sum, count)
      merges_this_projection = 0
      for val in shifted:
        push (val, 1)
        while there are at least two blocks and mean(previous block) > mean(last block):
          pop the last two blocks, merge them into one block, count one merge
      expand each block mean count times to obtain y[0..K-1]
    Set x[i] = y[i] + i * min_gap for all i.
    Add merges_this_projection to total_projection_merges.
After all iterations, write answer.json with:
  positions: final x values rounded to 6 decimals (list of K floats)
  reward: sum_i f(x[i]) rounded to 6 decimals
  min_gap: min_i (x[i+1] - x[i]) rounded to 6 decimals
  projection_merges: total number of block merges across all projections (integer)
Extra keys are allowed. Use Python round to 6 decimals. The comparison is exact after rounding. Answer path is relative: answer.json.
Example schema: {'positions': [10.123456, 20.0, ...], 'reward': 12.345678, 'min_gap': 8.0, 'projection_merges': 17}.

Work offline using only the Python 3.12 standard library. Do not use the network.
