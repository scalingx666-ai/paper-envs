import json
import math

def load_config(path='config.json'):
    with open(path, 'r') as f:
        return json.load(f)

def f_value(x, cfg):
    total = 0.0
    for a in cfg.get('attractors', []):
        c = a['center']
        w = a['weight']
        s = a['sigma']
        total += w * math.exp(-((x - c) ** 2) / (2.0 * s * s))
    for r in cfg.get('repellers', []):
        c = r['center']
        w = r['weight']
        s = r['sigma']
        total -= w * math.exp(-((x - c) ** 2) / (2.0 * s * s))
    pull = cfg.get('global_pull')
    if pull:
        total -= pull['weight'] * ((x - pull['center']) ** 2)
    return total

def f_prime(x, cfg):
    total = 0.0
    for a in cfg.get('attractors', []):
        c = a['center']
        w = a['weight']
        s = a['sigma']
        e = math.exp(-((x - c) ** 2) / (2.0 * s * s))
        total += w * (-(x - c) / (s * s)) * e
    for r in cfg.get('repellers', []):
        c = r['center']
        w = r['weight']
        s = r['sigma']
        e = math.exp(-((x - c) ** 2) / (2.0 * s * s))
        total -= w * (-(x - c) / (s * s)) * e
    pull = cfg.get('global_pull')
    if pull:
        total -= 2.0 * pull['weight'] * (x - pull['center'])
    return total

def critic_value(x, cfg):
    h = cfg['critic_bandwidth']
    num = 0.0
    den = 0.0
    for sample in cfg.get('critic_samples', []):
        p = sample['position']
        g = sample['gradient']
        w = sample['weight']
        e = math.exp(-((x - p) ** 2) / (2.0 * h * h))
        num += w * g * e
        den += w * e
    if den > 1e-15:
        return num / den
    return 0.0

def pava_project(x, min_gap):
    K = len(x)
    shifted = [x[i] - i * min_gap for i in range(K)]
    stack = []
    merges = 0
    for val in shifted:
        stack.append([val, 1])
        while len(stack) >= 2:
            s_prev, c_prev = stack[-2]
            s_last, c_last = stack[-1]
            if s_prev / c_prev > s_last / c_last:
                s1, c1 = stack.pop()
                s2, c2 = stack.pop()
                stack.append([s1 + s2, c1 + c2])
                merges += 1
            else:
                break
    y = []
    for s, c in stack:
        mean = s / c
        y.extend([mean] * c)
    projected = [y[i] + i * min_gap for i in range(K)]
    return projected, merges

def main():
    cfg = load_config()
    K = cfg['num_obstacles']
    d = cfg['min_gap']
    eta = cfg['step_size']
    T = cfg['iterations']
    x = list(cfg['initial_positions'])
    total_merges = 0
    for t in range(T):
        if T > 1:
            beta = cfg['beta_start'] + (cfg['beta_end'] - cfg['beta_start']) * (t / (T - 1))
        else:
            beta = cfg['beta_end']
        new_x = []
        for xi in x:
            gr = f_prime(xi, cfg)
            gc = critic_value(xi, cfg)
            new_x.append(xi + eta * ((1.0 - beta) * gr + beta * gc))
        x, merges = pava_project(new_x, d)
        total_merges += merges
    positions = [round(v, 6) for v in x]
    reward = round(sum(f_value(v, cfg) for v in x), 6)
    if K >= 2:
        min_gap_val = min(x[i + 1] - x[i] for i in range(K - 1))
    else:
        min_gap_val = 0.0
    out = {
        'positions': positions,
        'reward': reward,
        'min_gap': round(min_gap_val, 6),
        'projection_merges': total_merges
    }
    with open('answer.json', 'w') as f:
        json.dump(out, f, indent=2, sort_keys=True)

if __name__ == '__main__':
    main()
