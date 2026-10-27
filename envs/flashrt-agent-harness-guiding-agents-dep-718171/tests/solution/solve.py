import json
import heapq


def main():
    with open('pipeline.json', 'r', encoding='utf-8') as f:
        data = json.load(f)
    devices = data['devices']
    stages = data['stages']
    weights = data['weights']
    min_gain = float(data['min_gain'])
    max_passes = int(data['max_passes'])
    stage_map = {s['id']: s for s in stages}

    indeg = {s['id']: 0 for s in stages}
    children = {s['id']: [] for s in stages}
    for s in stages:
        for dep in s.get('deps', []):
            indeg[s['id']] += 1
            children[dep].append(s['id'])
    heap = [sid for sid, deg in indeg.items() if deg == 0]
    heapq.heapify(heap)
    topo = []
    while heap:
        sid = heapq.heappop(heap)
        topo.append(sid)
        for ch in children[sid]:
            indeg[ch] -= 1
            if indeg[ch] == 0:
                heapq.heappush(heap, ch)
    if len(topo) != len(stages):
        raise ValueError('cycle detected')

    scope_stages = {}
    for sid in topo:
        key = stage_map[sid].get('state_key')
        if key is not None:
            scope_stages.setdefault(key, []).append(sid)
    state_scopes = [
        {'key': key, 'stages': scope_stages[key]}
        for key in sorted(scope_stages.keys())
    ]

    baseline = {}
    for s in stages:
        ref = None
        for c in s['candidates']:
            if c['name'] == 'ref':
                ref = c
                break
        if ref is None:
            raise ValueError('missing ref candidate for ' + s['id'])
        baseline[s['id']] = ref

    def is_valid(plan):
        state_placements = {}
        for sid, cand in plan.items():
            st = stage_map[sid]
            key = st.get('state_key')
            if key is not None:
                if cand['shards'] != 1:
                    return False
                if key in state_placements and state_placements[key] != cand['placement']:
                    return False
                state_placements[key] = cand['placement']
        return True

    def metrics(plan):
        latency = 0.0
        for sid, cand in plan.items():
            st = stage_map[sid]
            latency += st['base_latency_ms'] / cand['shards'] + 0.8 * (cand['chunk'] - 1)
        for sid, cand_v in plan.items():
            for dep in stage_map[sid].get('deps', []):
                cand_u = plan[dep]
                if cand_u['placement'] != cand_v['placement']:
                    latency += 3.0
        loads = {d['id']: 0.0 for d in devices}
        for sid, cand in plan.items():
            st = stage_map[sid]
            loads[cand['placement']] += st['work'] / (cand['shards'] * cand['chunk'])
        max_ratio = 0.0
        for d in devices:
            cap = float(d['capacity'])
            load = loads[d['id']]
            ratio = load / cap if cap > 0.0 else float('inf')
            if ratio > max_ratio:
                max_ratio = ratio
        if max_ratio <= 1e-12:
            throughput = 0.0
        else:
            throughput = 1000.0 / max_ratio
        objective = weights['latency'] * latency - weights['throughput'] * throughput
        return {'latency_ms': latency, 'throughput': throughput, 'objective': objective}

    current = dict(baseline)
    current_metrics = metrics(current)
    passes_used = 0
    for _ in range(max_passes):
        changed = False
        for sid in topo:
            st = stage_map[sid]
            current_cand = current[sid]
            best_cand = None
            best_metrics = None
            best_obj = current_metrics['objective']
            for cand in sorted(st['candidates'], key=lambda c: c['name']):
                if cand['name'] == current_cand['name']:
                    continue
                trial = dict(current)
                trial[sid] = cand
                if not is_valid(trial):
                    continue
                m = metrics(trial)
                gain = (current_metrics['objective'] - m['objective']) / max(abs(current_metrics['objective']), 1e-9)
                if gain > min_gain and m['objective'] < best_obj - 1e-12:
                    best_cand = cand
                    best_metrics = m
                    best_obj = m['objective']
            if best_cand is not None:
                current[sid] = best_cand
                current_metrics = best_metrics
                changed = True
        if changed:
            passes_used += 1
        else:
            break

    final_plan = [sid + ':' + current[sid]['name'] for sid in topo]
    accepted = [sid + ':' + current[sid]['name'] for sid in topo if current[sid]['name'] != 'ref']
    answer = {
        'topological_order': topo,
        'state_scopes': state_scopes,
        'final_plan': final_plan,
        'accepted': accepted,
        'metrics': {
            'latency_ms': round(current_metrics['latency_ms'], 3),
            'throughput': round(current_metrics['throughput'], 3),
            'objective': round(current_metrics['objective'], 3),
        },
        'passes_used': passes_used,
    }
    with open('answer.json', 'w', encoding='utf-8') as f:
        json.dump(answer, f, indent=2)


if __name__ == '__main__':
    main()
