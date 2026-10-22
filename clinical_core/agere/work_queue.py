"""Order-book work queues and approximate-count sketches (the matching-engine / high-frequency-feed toolkit, applied to work).

OrderBook: a price-time-priority book of work items. Higher priority first; equal priority is served in arrival order (FIFO).
Raising an item's priority re-queues it behind peers at the new level (like amending a limit price loses time priority), lowering
or cancelling follows the same rule, and `match` fills the best items against per-resource capacity (slots per counterparty)
the way a matching engine fills the best bids against available size: an item that cannot fit its resource stays resting and the
next best item that fits is filled (no head-of-line blocking). `depth` is the level-2 view: items and count per priority level.
O(log n) add / cancel (lazy deletion on a heap), deterministic, no clock reads (arrival is a sequence number).

CountMinSketch: approximate per-key counts in fixed memory (never undercounts; overcount <= eps * total with probability 1-delta).
HyperLogLog: approximate distinct counts (relative error ~ 1.04 / sqrt(2^p)); mergeable. Both ingest a large feed in one streaming pass
and merge across shards. Synthetic data only; the sketches state their error bounds and are never used where an exact count is cheap.
"""
import hashlib
import heapq
import itertools
import math

VERSION = 'order-book-sketches/1'


def _h64(key, salt=0):
    return int.from_bytes(hashlib.blake2b(f'{salt}|{key}'.encode(), digest_size=8).digest(), 'big')


class OrderBook:
    def __init__(self):
        self._heap, self._live, self._seq = [], {}, itertools.count()

    def __len__(self): return len(self._live)

    def add(self, key, priority, resource=None, payload=None):
        """Rest an item. Re-adding a key amends it (loses time priority, like a re-priced order)."""
        entry = (-float(priority), next(self._seq), key)
        self._live[key] = {'priority': float(priority), 'seq': entry[1], 'resource': resource, 'payload': payload}
        heapq.heappush(self._heap, entry)
        return entry[1]

    def cancel(self, key):
        return self._live.pop(key, None) is not None

    def amend(self, key, priority):
        cur = self._live.get(key)
        if cur is None: return False
        if float(priority) == cur['priority']: return True  # unchanged keeps its place
        self.add(key, priority, cur['resource'], cur['payload'])
        return True

    def _best(self):
        while self._heap:
            neg, seq, key = self._heap[0]
            cur = self._live.get(key)
            if cur is None or cur['seq'] != seq: heapq.heappop(self._heap); continue  # cancelled or superseded
            return self._heap[0]
        return None

    def peek(self):
        top = self._best()
        return None if top is None else (top[2], self._live[top[2]])

    def pop(self):
        top = self._best()
        if top is None: return None
        heapq.heappop(self._heap)
        return top[2], self._live.pop(top[2])

    def ordered(self):
        return [(k, v) for k, v in sorted(self._live.items(), key=lambda kv: (-kv[1]['priority'], kv[1]['seq']))]

    def match(self, capacity, limit=None):
        """Fill best-first against {resource: slots}. Items with resource None need no slot. Returns (filled, resting_blocked)."""
        left, filled, blocked = dict(capacity), [], []
        for key, v in self.ordered():
            if limit is not None and len(filled) >= limit: break
            r = v['resource']
            if r is not None and left.get(r, 0) <= 0:
                blocked.append(key)
                continue
            if r is not None: left[r] -= 1
            filled.append(key)
        for key in filled: self._live.pop(key, None)
        return filled, blocked

    def depth(self):
        levels = {}
        for key, v in self._live.items(): levels.setdefault(v['priority'], []).append((v['seq'], key))
        return [{'priority': p, 'items': len(v), 'next': sorted(v)[0][1]} for p, v in sorted(levels.items(), reverse=True)]


class CountMinSketch:
    def __init__(self, eps=0.001, delta=0.01):
        self.eps, self.delta = eps, delta
        self.width, self.depth = math.ceil(math.e / eps), math.ceil(math.log(1 / delta))
        self.rows = [[0] * self.width for _ in range(self.depth)]
        self.total = 0

    def add(self, key, n=1):
        for i in range(self.depth): self.rows[i][_h64(key, i) % self.width] += n
        self.total += n

    def count(self, key):
        return min(self.rows[i][_h64(key, i) % self.width] for i in range(self.depth))

    def merge(self, other):
        if (self.width, self.depth) != (other.width, other.depth): raise ValueError('sketch shapes differ')
        for i in range(self.depth):
            self.rows[i] = [a + b for a, b in zip(self.rows[i], other.rows[i])]
        self.total += other.total
        return self

    def error_bound(self): return {'overcount_at_most': round(self.eps * self.total, 2), 'probability': 1 - self.delta}


class HyperLogLog:
    def __init__(self, p=14):
        self.p, self.m = p, 1 << p
        self.reg = [0] * self.m

    def add(self, key):
        x = _h64(key)
        idx, rest = x >> (64 - self.p), x & ((1 << (64 - self.p)) - 1)
        rank = (64 - self.p) - rest.bit_length() + 1
        if rank > self.reg[idx]: self.reg[idx] = rank

    def count(self):
        m = self.m
        alpha = 0.7213 / (1 + 1.079 / m)
        est = alpha * m * m / sum(2.0 ** -r for r in self.reg)
        zeros = self.reg.count(0)
        if est <= 2.5 * m and zeros: est = m * math.log(m / zeros)  # small-range correction
        return int(round(est))

    def merge(self, other):
        if self.p != other.p: raise ValueError('sketch precision differs')
        self.reg = [max(a, b) for a, b in zip(self.reg, other.reg)]
        return self

    def relative_error(self): return round(1.04 / math.sqrt(self.m), 5)


def profile_feed(rows, key_of, distinct_of):
    """One pass over (row) -> {'count_min': CountMinSketch of key_of(row), 'distinct': {key: HyperLogLog of distinct_of(row)}}."""
    cm, hll = CountMinSketch(), {}
    for row in rows:
        k = key_of(row)
        cm.add(k)
        hll.setdefault(k, HyperLogLog()).add(distinct_of(row))
    return cm, hll
