import json

def main():
    with open('trace.json', 'r', encoding='utf-8') as f:
        trace = json.load(f)
    with open('config.json', 'r', encoding='utf-8') as f:
        config = json.load(f)

    sequence = []
    for request in trace.get('requests', []):
        sequence.extend(request.get('pages', []))

    stack = []
    finite_distances = []
    for page in sequence:
        try:
            distance = stack.index(page)
        except ValueError:
            distance = None
        if distance is None:
            stack.insert(0, page)
        else:
            finite_distances.append(distance)
            stack.pop(distance)
            stack.insert(0, page)

    total_accesses = len(sequence)
    distinct_pages = len(stack)

    if finite_distances:
        max_distance = max(finite_distances)
        histogram = [0] * (max_distance + 1)
        for d in finite_distances:
            histogram[d] += 1
    else:
        max_distance = -1
        histogram = []

    capacities = config.get('capacities', [])
    hit_counts = []
    for capacity in capacities:
        hit = sum(1 for d in finite_distances if d < capacity)
        hit_counts.append(hit)

    if total_accesses == 0:
        hit_rates = [0.0 for _ in capacities]
    else:
        hit_rates = [round(h / total_accesses, 6) for h in hit_counts]

    def min_capacity(target):
        if total_accesses == 0:
            return None
        upper = max_distance + 1 if finite_distances else 1
        for capacity in range(1, upper + 1):
            hit = sum(1 for d in finite_distances if d < capacity)
            if hit / total_accesses >= target:
                return capacity
        return None

    answer = {
        'total_accesses': total_accesses,
        'distinct_pages': distinct_pages,
        'stack_distance_histogram': histogram,
        'hit_counts_by_capacity': hit_counts,
        'hit_rates_by_capacity': hit_rates,
        'min_capacity_for_target': min_capacity(config.get('target_hit_rate')),
        'min_capacity_for_stretch_target': min_capacity(config.get('stretch_target_hit_rate')),
    }

    with open('answer.json', 'w', encoding='utf-8') as f:
        json.dump(answer, f, indent=2)

if __name__ == '__main__':
    main()
