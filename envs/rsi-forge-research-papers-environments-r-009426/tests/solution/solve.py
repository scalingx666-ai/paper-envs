import csv
import json
from collections import defaultdict


def main():
    runs = defaultdict(list)
    with open('attempts.csv', newline='') as f:
        reader = csv.DictReader(f)
        for row in reader:
            row['attempt'] = int(row['attempt'])
            row['train_score'] = float(row['train_score'])
            row['dev_score'] = float(row['dev_score'])
            row['holdout_score'] = float(row['holdout_score'])
            row['se'] = float(row['se'])
            row['exploration'] = int(row['exploration'])
            runs[row['run_id']].append(row)

    info = {}
    for rid, attempts in runs.items():
        attempts.sort(key=lambda r: r['attempt'])
        base = attempts[0]
        final = attempts[-1]
        delta = final['holdout_score'] - base['holdout_score']
        genuine = delta > final['se']
        beyond = genuine and final['edit'] not in ('tuning', 'none')
        tuning_overfit = (
            final['edit'] == 'tuning'
            and final['dev_score'] > base['dev_score']
            and delta <= final['se']
        )
        info[rid] = {
            'genuine': genuine,
            'beyond': beyond,
            'tuning_overfit': tuning_overfit,
            'final_holdout': final['holdout_score'],
            'exploration_gain': final['exploration'] - base['exploration'],
        }

    genuine_ids = sorted(rid for rid, v in info.items() if v['genuine'])
    beyond_ids = sorted(rid for rid, v in info.items() if v['beyond'])
    overfit_ids = sorted(rid for rid, v in info.items() if v['tuning_overfit'])

    total = len(info)
    genuine_rate = round(len(genuine_ids) / total, 3) if total else 0.0
    beyond_rate = round(len(beyond_ids) / len(genuine_ids), 3) if genuine_ids else 0.0

    if genuine_ids:
        top_id = min(
            genuine_ids,
            key=lambda rid: (-info[rid]['final_holdout'], -info[rid]['exploration_gain'], rid),
        )
        top_final = round(info[top_id]['final_holdout'], 3)
        mean_gain = round(
            sum(info[rid]['exploration_gain'] for rid in genuine_ids) / len(genuine_ids), 3
        )
    else:
        top_id = ''
        top_final = 0.0
        mean_gain = 0.0

    answer = {
        'genuine_runs': genuine_ids,
        'genuine_rate': genuine_rate,
        'beyond_tuning_runs': beyond_ids,
        'beyond_tuning_rate': beyond_rate,
        'tuning_overfit_runs': overfit_ids,
        'top_run': top_id,
        'top_final_holdout': top_final,
        'mean_exploration_gain_genuine': mean_gain,
    }
    with open('answer.json', 'w') as f:
        json.dump(answer, f, indent=2, sort_keys=True)


if __name__ == '__main__':
    main()
