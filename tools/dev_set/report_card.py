"""Per-tier report card for a saved dev-set run (the JSON `tools.dev_set.run --out` writes).

    .venv/bin/python -m tools.dev_set.report_card /tmp/run.json

Per tier: reached (asked at least once, not skipped), proposed (accepted or its proposal was rejected by a check), accepted,
correct (accepted and the question scored correct), precision = correct / accepted, seconds spent in the tier, and $ of the
questions that tier answered (the cost receipt is per question, not per tier, so it is credited to the accepting tier).
Then overall p50 / p90 latency and $ per question. Runs saved before the trace existed (no `trace` on a row) still read: those
rows contribute to the overall figures and to the accepting tier through their `route`.
"""
from __future__ import annotations

import json
import statistics
import sys
from collections import defaultdict
from pathlib import Path

PROPOSED = {'accepted', 'rejected'}


def _pct(x, n):
    return f'{100 * x / n:.1f}%' if n else 'n/a'


def _quantile(vals, f):
    vals = sorted(vals)
    return vals[min(len(vals) - 1, int(f * len(vals)))] if vals else None


def load(path):
    d = json.loads(Path(path).read_text())
    return d['rows'] if isinstance(d, dict) and 'rows' in d else d


def card(rows):
    tiers = defaultdict(lambda: {'reached': 0, 'proposed': 0, 'accepted': 0, 'correct': 0, 'seconds': 0.0, 'usd': 0.0})
    for r in rows:
        trace = r.get('trace') or {}
        log = trace.get('tiers') or []
        accepted = None
        seen = set()
        for e in log:
            name = e.get('tier') or '?'
            t = tiers[name]
            out = e.get('outcome')
            if out != 'skipped' and name not in seen:
                t['reached'] += 1
                seen.add(name)
            if out in PROPOSED:
                t['proposed'] += 1
            if isinstance(e.get('seconds'), (int, float)) and out != 'skipped':
                t['seconds'] += e['seconds']
            if out == 'accepted':
                accepted = name
        if accepted is None and r.get('answered') and r.get('route') not in (None, 'none'):
            accepted = r['route']  # older run: only the winning route was saved
            if accepted not in seen:
                tiers[accepted]['reached'] += 1
                tiers[accepted]['proposed'] += 1
        if accepted is not None:
            t = tiers[accepted]
            t['accepted'] += 1
            t['correct'] += 1 if r.get('correct') is True else 0
            if isinstance(r.get('usd'), (int, float)):
                t['usd'] += r['usd']
    return dict(tiers)


def render(rows):
    out = [f'{"tier":28} {"reached":>7} {"proposed":>8} {"accepted":>8} {"correct":>7} {"precision":>9} {"usd":>8} {"seconds":>8}']
    for name, t in sorted(card(rows).items(), key=lambda kv: (-kv[1]['accepted'], -kv[1]['reached'], kv[0])):
        out.append(f'{name[:28]:28} {t["reached"]:>7} {t["proposed"]:>8} {t["accepted"]:>8} {t["correct"]:>7} '
                   f'{_pct(t["correct"], t["accepted"]):>9} {t["usd"]:>8.4f} {t["seconds"]:>8.1f}')
    secs = [r['seconds'] for r in rows if isinstance(r.get('seconds'), (int, float))]
    usd = [r['usd'] for r in rows if isinstance(r.get('usd'), (int, float))]
    out.append('')
    if secs:
        out.append(f'latency p50={statistics.median(secs):.1f}s p90={_quantile(secs, 0.9):.1f}s over {len(secs)} questions')
    if usd:
        out.append(f'$ per question={sum(usd) / len(usd):.4f} (known for {len(usd)}/{len(rows)}; total {sum(usd):.4f})')
    else:
        out.append(f'$ per question: unknown (no usd on any of {len(rows)} rows)')
    traced = sum(1 for r in rows if r.get('trace'))
    out.append(f'rows with trace: {traced}/{len(rows)}' + ('' if traced else ' (older run: tiers inferred from route only)'))
    return '\n'.join(out)


def main(argv=None):
    argv = list(sys.argv[1:] if argv is None else argv)
    if len(argv) != 1:
        print(__doc__)
        return 2
    print(render(load(argv[0])))
    return 0


if __name__ == '__main__':
    sys.exit(main())
