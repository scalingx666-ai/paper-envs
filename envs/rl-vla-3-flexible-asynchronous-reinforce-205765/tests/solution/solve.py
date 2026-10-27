import csv, json, heapq
from collections import deque

def main():
    with open('config.json', 'r', encoding='utf-8') as f:
        cfg = json.load(f)
    E = int(cfg['env_count'])
    chunk = int(cfg['chunk_steps'])
    B_inf = int(cfg['max_inference_batch'])
    inf_base = int(cfg['inference_base'])
    inf_per = int(cfg['inference_per_item'])
    B_train = int(cfg['max_train_batch'])
    train_base = int(cfg['train_base'])
    train_per = int(cfg['train_per_item'])

    rows = {e: [] for e in range(E)}
    with open('latencies.csv', 'r', newline='', encoding='utf-8') as f:
        reader = csv.DictReader(f)
        for row in reader:
            e = int(row['env_id'])
            s = int(row['step_index'])
            lat = int(row['latency'])
            if 0 <= e < E:
                rows[e].append((s, lat))

    segments = []
    for e in range(E):
        rs = sorted(rows[e], key=lambda x: (x[0], x[1]))
        segs = []
        for i in range(0, len(rs), chunk):
            segs.append(sum(lat for _, lat in rs[i:i+chunk]))
        segments.append(segs)

    total_segments = sum(len(s) for s in segments)

    inference_queue = deque()
    for e in range(E):
        if segments[e]:
            inference_queue.append((e, 0))
    train_queue = deque()

    inf_busy = False
    train_busy = False
    completed = 0
    env_finish = [0] * E
    inference_batches = 0
    train_batches = 0
    total_inference_items = 0
    makespan = 0
    events = []
    seq = 0

    def push(t, typ, payload):
        nonlocal seq
        heapq.heappush(events, (t, typ, seq, payload))
        seq += 1

    def start_inference(t):
        nonlocal inf_busy, inference_batches, total_inference_items
        if inf_busy or not inference_queue:
            return
        b = min(len(inference_queue), B_inf)
        batch = [inference_queue.popleft() for _ in range(b)]
        inf_busy = True
        inference_batches += 1
        total_inference_items += b
        service = inf_base + inf_per * b
        push(t + service, 1, batch)

    def start_training(t):
        nonlocal train_busy, train_batches
        if train_busy:
            return
        if len(train_queue) >= B_train:
            b = B_train
        elif completed == total_segments and train_queue:
            b = len(train_queue)
        else:
            return
        batch = [train_queue.popleft() for _ in range(b)]
        train_busy = True
        train_batches += 1
        service = train_base + train_per * b
        push(t + service, 2, batch)

    start_inference(0)
    start_training(0)

    while events:
        t = events[0][0]
        while events and events[0][0] == t:
            _, typ, _, payload = heapq.heappop(events)
            if typ == 1:
                inf_busy = False
                for e, g in payload:
                    d = segments[e][g]
                    push(t + d, 0, (e, g))
            elif typ == 0:
                e, g = payload
                completed += 1
                env_finish[e] = t
                train_queue.append((e, g))
                if g + 1 < len(segments[e]):
                    inference_queue.append((e, g + 1))
            else:
                train_busy = False
                makespan = t
        start_inference(t)
        start_training(t)

    max_segs = max((len(s) for s in segments), default=0)
    sync_time = 0
    for j in range(max_segs):
        active = [e for e in range(E) if j < len(segments[e])]
        if not active:
            continue
        max_sim = max(segments[e][j] for e in active)
        n = len(active)
        sync_time += max_sim + (inf_base + inf_per * n) + (train_base + train_per * n)

    speedup = round(sync_time / makespan, 4) if makespan else 0.0
    avg_inf = round(total_inference_items / inference_batches, 4) if inference_batches else 0.0
    out = {
        'async_makespan': makespan,
        'sync_makespan': sync_time,
        'speedup': speedup,
        'inference_batches': inference_batches,
        'train_batches': train_batches,
        'avg_inference_batch_size': avg_inf,
        'env_finish_times': env_finish,
    }
    with open('answer.json', 'w', encoding='utf-8') as f:
        json.dump(out, f, indent=2, sort_keys=True)
        f.write('\n')

if __name__ == '__main__':
    main()
