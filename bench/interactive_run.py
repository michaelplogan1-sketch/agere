"""Interactive follow-up benchmark (BIRD-INTERACT / EHR-ChatQA style) for Agere's conversation layer, FM-20 / FM-21 / FM-22.

Runs bench/interactive/chains.json through conversation.handle_compose (one conversation id per chain) and scores each follow-up
turn against an independent oracle, not against the system's own memory:

  slot_kept        the follow-up's patients == the earlier turn's patients intersected with a SQL filter run on the semantic db
  no_stale_reuse   a changed slot returns different patients / a different count from the turn it differs from (never the cache)
  reuse_consistent an identical or paraphrased repeat returns the same patients / count as the first ask

A turn that errors or returns no patients and no count fails its check (it is data, not an exception). Free tier by default: the
stack is opened with allow_live=False, so no paid call can happen. Synthetic data only.

    python bench/interactive_run.py [--out DIR] [--chains bench/interactive/chains.json] [--live]
"""
from __future__ import annotations

import argparse
import json
import sqlite3
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
CHAINS = ROOT / 'bench' / 'interactive' / 'chains.json'
CHECKS = ('slot_kept', 'no_stale_reuse', 'reuse_consistent')


def load(path=CHAINS):
    data = json.loads(Path(path).read_text())
    for c in data['chains']:
        for t in c['turns']:
            if t.get('check') and t['check'] not in CHECKS: raise ValueError(f"{c['id']}: unknown check {t['check']}")
            if t.get('check') and not 0 <= t['of'] < c['turns'].index(t): raise ValueError(f"{c['id']}: 'of' must point at an earlier turn")
    return data['chains']


def observe(result=None, exc=None):
    """{'status', 'ids' (frozenset | None), 'count'} from a compose result or exception."""
    if exc is not None: return {'status': f'error:{type(exc).__name__}', 'ids': None, 'count': None, 'error': str(exc)[:200]}
    a = (result or {}).get('answer') or {}
    ids = a.get('patient_ids')
    return {'status': a.get('status'), 'ids': frozenset(ids) if ids is not None else None,
            'count': a.get('count') if a.get('count') is not None else (len(ids) if ids is not None else None),
            'headline': str(a.get('headline') or a.get('value') or '')[:160]}


def same(a, b):
    if a['ids'] is not None and b['ids'] is not None: return a['ids'] == b['ids']
    return a['count'] is not None and a['count'] == b['count']


def judge(turn, obs, earlier, oracle=None):
    """(passed, reason) for one follow-up turn. earlier: the observation of turn['of']; oracle: set of ids from intersect_sql."""
    if obs['status'] not in ('answered', 'partial') or (obs['ids'] is None and obs['count'] is None):
        return False, f"no usable answer ({obs['status']})"
    if earlier['ids'] is None and earlier['count'] is None: return False, 'the turn it follows had no usable answer'
    kind = turn['check']
    if kind == 'slot_kept':
        if oracle is None or earlier['ids'] is None or obs['ids'] is None: return False, 'needs patient ids on both turns and an oracle filter'
        want = earlier['ids'] & oracle
        return (obs['ids'] == want, f"{len(obs['ids'])} patients vs {len(want)} expected (previous {len(earlier['ids'])} intersect filter)")
    if kind == 'no_stale_reuse':
        return (not same(obs, earlier), 'answer equals the earlier turn despite a changed slot' if same(obs, earlier) else 'answer changed with the slot')
    return (same(obs, earlier), 'repeat matches the first ask' if same(obs, earlier) else 'repeat differs from the first ask')


def run_chains(chains, ask, oracle_ids):
    """ask(conversation_id, question) -> compose result (or raises); oracle_ids(sql) -> set of patient ids."""
    rows = []
    for c in chains:
        obs = []
        for i, t in enumerate(c['turns']):
            started = time.perf_counter()
            try: o = observe(ask(f"interactive-{c['id']}", t['q']))
            except Exception as exc: o = observe(exc=exc)  # noqa: BLE001 - a failed turn is data
            obs.append(o)
            row = {'chain': c['id'], 'turn': i, 'failure_mode': c['failure_mode'], 'question': t['q'], 'status': o['status'], 'count': o['count'],
                   'check': t.get('check'), 'seconds': round(time.perf_counter() - started, 1), 'headline': o.get('headline')}
            if t.get('check'):
                ok, why = judge(t, o, obs[t['of']], frozenset(oracle_ids(t['intersect_sql'])) if t.get('intersect_sql') else None)
                row.update({'passed': ok, 'reason': why})
            rows.append(row)
    return rows


def summarize(rows):
    scored = [r for r in rows if r.get('check')]
    by = {}
    for r in scored:
        b = by.setdefault(r['failure_mode'], {'turns': 0, 'passed': 0})
        b['turns'] += 1; b['passed'] += bool(r['passed'])
    return {'turns': len(rows), 'scored_follow_ups': len(scored), 'passed': sum(bool(r['passed']) for r in scored),
            'by_failure_mode': by, 'chains_fully_passed': sum(all(r['passed'] for r in scored if r['chain'] == c) for c in {r['chain'] for r in scored})}


def main(argv=None):
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument('--out', type=Path, default=ROOT / 'bench' / 'results' / 'interactive')
    p.add_argument('--chains', type=Path, default=CHAINS)
    p.add_argument('--live', action='store_true', help='allow live System One / provider calls (default: free tier, none)')
    a = p.parse_args(argv)
    sys.path.insert(0, str(ROOT))
    from clinical_core.agere.clinical import conversation
    from clinical_core.agere.clinical.stack_runtime import Stack
    build = Path(json.loads((ROOT / 'config/agere/active_warehouse.json').read_text())['build'])
    stack = Stack(build / 'stack', allow_live=a.live)
    db = sqlite3.connect(f"file:{build/'stack'/'semantic.sqlite3'}?mode=ro", uri=True)
    rows = run_chains(load(a.chains), lambda cid, q: conversation.handle_compose(stack, {'question': q, 'conversation_id': cid}),
                      lambda sql: {r[0] for r in db.execute(sql)})
    a.out.mkdir(parents=True, exist_ok=True)
    stamp = time.strftime('%Y%m%dT%H%M%S')
    (a.out / f'interactive-{stamp}.jsonl').write_text(''.join(json.dumps(r, default=str) + '\n' for r in rows))
    summary = summarize(rows)
    (a.out / f'interactive-{stamp}.summary.json').write_text(json.dumps(summary, indent=1))
    print(json.dumps(summary, indent=1))
    for r in rows:
        if r.get('check'): print(('PASS' if r['passed'] else 'FAIL'), r['chain'], r['turn'], r['question'], '-', r['reason'])
    return summary


if __name__ == '__main__':
    main()
