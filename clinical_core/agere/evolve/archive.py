"""MAP-Elites archive of policies.

Niches: a BINS x BINS grid over two behavior descriptors of a policy's training result (evaluator.behavior): program cost
(share of the cost of contacting every routed patient in the first quarter) and gap closures (share of the closures
available). Each niche keeps, for every objective, the best policy that landed in it, so the archive holds cheap / narrow
policies next to expensive / broad ones instead of collapsing onto one optimum. Champions per objective are the best
entries over all niches (feasible ones first: an equity failure ranks below every feasible policy).
"""
from __future__ import annotations

import hashlib

BINS = 5


def cell_of(behavior, bins=BINS):
    return tuple(min(bins - 1, int(b * bins)) for b in behavior)


class Archive:
    def __init__(self, objectives, bins=BINS):
        self.objectives = list(objectives)
        self.bins = bins
        self.cells = {}        # cell -> {objective: entry}
        self.entries = []      # every evaluated candidate (valid and invalid), in order
        self.by_hash = {}

    @staticmethod
    def code_hash(code):
        return hashlib.sha256(code.strip().encode()).hexdigest()[:16]

    def seen(self, code):
        return self.code_hash(code) in self.by_hash

    def add(self, entry):
        """entry: {'id', 'code', 'status': 'valid'|'invalid', 'fitness': {obj: x}, 'behavior': (a, b), ...} -> improved objectives."""
        h = self.code_hash(entry['code'])
        entry['hash'] = h
        self.entries.append(entry)
        if h in self.by_hash:
            entry['duplicate_of'] = self.by_hash[h]['id']
            return []
        self.by_hash[h] = entry
        if entry.get('status') != 'valid':
            return []
        c = cell_of(entry['behavior'], self.bins)
        entry['cell'] = list(c)
        slot = self.cells.setdefault(c, {})
        improved = []
        for o in self.objectives:
            cur = slot.get(o)
            if cur is None or entry['fitness'][o] > cur['fitness'][o]:
                slot[o] = entry
                improved.append(o)
        return improved

    def elites(self, objective=None):
        seen, out = set(), []
        for c in sorted(self.cells):
            for o, e in sorted(self.cells[c].items()):
                if (objective is None or o == objective) and e['id'] not in seen:
                    seen.add(e['id'])
                    out.append(e)
        return out

    def best(self, objective, k=1):
        valid = [e for e in self.by_hash.values() if e.get('status') == 'valid']
        valid.sort(key=lambda e: (-e['fitness'][objective], e['id']))
        return valid[:k]

    def summary(self):
        return {'candidates': len(self.entries), 'unique': len(self.by_hash),
                'valid': sum(1 for e in self.by_hash.values() if e.get('status') == 'valid'),
                'invalid': sum(1 for e in self.entries if e.get('status') == 'invalid'),
                'niches_filled': len(self.cells), 'niches_total': self.bins ** 2}
