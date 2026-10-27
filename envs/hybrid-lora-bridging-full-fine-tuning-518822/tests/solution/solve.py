import csv
import itertools
import json


def main():
    modules = []
    with open('modules.csv', newline='') as f:
        reader = csv.DictReader(f)
        for row in reader:
            in_dim = int(row['in_dim'])
            out_dim = int(row['out_dim'])
            rank = int(row['rank'])
            lora_gain = int(row['lora_gain'])
            fft_gain = int(row['fft_gain'])
            fft_params = in_dim * out_dim
            lora_params = rank * (in_dim + out_dim)
            extra = fft_params - lora_params
            delta = fft_gain - lora_gain
            if extra <= 0:
                raise ValueError('extra must be positive')
            modules.append({
                'module': row['module'],
                'fft_params': fft_params,
                'lora_gain': lora_gain,
                'fft_gain': fft_gain,
                'delta': delta,
                'extra': extra,
                'score': delta / extra,
            })

    total_fft = sum(m['fft_params'] for m in modules)
    budget = total_fft // 10

    ranked = sorted(modules, key=lambda m: (-m['score'], m['module']))
    ranking = [m['module'] for m in ranked]
    baseline = sum(m['lora_gain'] for m in modules)

    best_delta = 0
    best_cost = 0
    best_names = []
    n = len(modules)
    for r in range(n + 1):
        for combo in itertools.combinations(modules, r):
            cost = sum(m['fft_params'] for m in combo)
            if cost > budget:
                continue
            delta = sum(m['delta'] for m in combo)
            names = sorted(m['module'] for m in combo)
            better = False
            if delta > best_delta:
                better = True
            elif delta == best_delta:
                if cost < best_cost:
                    better = True
                elif cost == best_cost:
                    if names < best_names:
                        better = True
            if better:
                best_delta = delta
                best_cost = cost
                best_names = names

    answer = {
        'budget': budget,
        'ranking': ranking,
        'selected_modules': best_names,
        'budget_used': best_cost,
        'baseline_lora_gain': baseline,
        'hybrid_gain': baseline + best_delta,
        'improvement': best_delta,
        'top_score': round(ranked[0]['score'], 6),
    }
    with open('answer.json', 'w') as f:
        json.dump(answer, f, indent=2)
        f.write('\n')


if __name__ == '__main__':
    main()
