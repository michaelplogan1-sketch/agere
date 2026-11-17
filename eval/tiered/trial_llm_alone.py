"""LLM-alone arms for the triage trials (2026-11-17): the frontier model given what a person would give it, no Agere code.

    equivalence  is a saved query right for its template for every parameter value? (the 30 flagged recipes + 30 that passed)
    stale        after this delivery, is this saved answer's number affected? (the 9 changed + 31 unchanged pairs)
    runout       ultimate paid for the last six incurred months, from the paid triangle as of the valuation date

Every result is appended to artifacts/agere-tiered-core/trials/llm-alone-<arm>.jsonl as it is scored; a rerun skips
what is there. Claude through the subscription transport only.

    python eval/tiered/trial_llm_alone.py equivalence|stale|runout
"""
import json
import os
import random
import sqlite3
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
os.environ.pop('ANTHROPIC_API_KEY', None)
from clinical_core.agere.providers import load_keys  # noqa: E402
load_keys(); os.environ.pop('ANTHROPIC_API_KEY', None)
from clinical_core.agere.tiered import models, store  # noqa: E402

OUT = Path('artifacts/agere-tiered-core/trials')


def _done(arm):
    p = OUT / f'llm-alone-{arm}.jsonl'
    return p, ({json.loads(l)['key'] for l in p.read_text().splitlines() if l.strip()} if p.exists() else set())


def _ask(system, user):
    text, step = models._claude(models.LLM, system, user, timeout=180)
    try: out = models._json(text)
    except ValueError: out = {'raw': text[:400]}
    return out, step.get('usd', 0.0)


def equivalence():
    path, done = _done('equivalence')
    rs = [json.loads(l) for l in open(OUT / 'equivalence-journal.jsonl')]
    flagged = [r for r in rs if r['verdict'].startswith('differ')]
    passed = random.Random(17).sample([r for r in rs if r['verdict'] == 'agree'], 30)
    db = sqlite3.connect('artifacts/agere-tiered-core/bottles.sqlite3')
    schema = store.schema_text()
    system = ('You review saved SQL recipes for a hospital analytics warehouse (SQLite). A recipe is reused for every parameter '
              'value its template allows. Say whether the SQL is correct for its template for every allowed parameter value, '
              'including how it treats NULLs. Return JSON {"correct": true|false, "why": "<one sentence>"}.')
    for r in flagged + passed:
        if r['bottle'] in done: continue
        sql, params = db.execute('SELECT sql, params_json FROM bottle WHERE id = ?', (r['bottle'],)).fetchone()
        out, usd = _ask(system, f'Schema:\n{schema}\n\nTemplate: {r["template"]}\nParameters: {params}\nSQL: {sql}')
        with open(path, 'a') as f: f.write(json.dumps({'key': r['bottle'], 'verdict': r['verdict'], **out, 'usd': usd}) + '\n')


def stale():
    path, done = _done('stale')
    rs = [json.loads(l) for l in open(OUT / 'stale-journal-derived.jsonl')]
    changed = [r for r in rs if r['answer_changed'] and not (r['delivery'] == 'corrected_lab' and r['bottle'] == 448)]   # 448: a query timeout, not a change
    rest = random.Random(17).sample([r for r in rs if not r['answer_changed']], 31)
    from clinical_core.agere.tiered import freshness
    db = sqlite3.connect('artifacts/agere-tiered-core/bottles.sqlite3')
    said = {**{k: v['said'] for k, v in freshness.DELIVERIES.items()}, 'feed_stopped': freshness.STOPPED['said']}
    system = ('You keep a hospital board report current. A data delivery has just arrived. Given one saved number (its SQL and '
              'the parameter values it was computed with), say whether this delivery changes that number. '
              'Return JSON {"affected": true|false, "why": "<one sentence>"}.')
    for r in changed + rest:
        key = f"{r['delivery']}:{r['bottle']}"
        if key in done: continue
        sql, = db.execute('SELECT sql FROM bottle WHERE id = ?', (r['bottle'],)).fetchone()
        ev = json.load(open('eval/tiered/trial_stale_v1.json'))
        ce = next(a['check_event'] for a in ev['answers'] if a['bottle'] == r['bottle'])
        values = db.execute('SELECT params_json FROM bottle_event WHERE id = ?', (ce,)).fetchone()[0]
        extra = ' Derived tables rebuilt for the touched patients: ' + ', '.join(freshness.DERIVED.get(r['delivery'], {})) if freshness.DERIVED.get(r['delivery']) else ''
        out, usd = _ask(system, f'Delivery: {said[r["delivery"]]}.{extra}\nSaved number SQL: {sql}\nParameter values: {values}')
        with open(path, 'a') as f: f.write(json.dumps({'key': key, 'answer_changed': r['answer_changed'], **out, 'usd': usd}) + '\n')


def runout():
    path, done = _done('runout')
    from clinical_core.agere.tiered import runout as R
    from collections import defaultdict
    from datetime import date
    claims = R.load()
    spec = json.load(open('eval/tiered/trial_runout_v1.json'))
    system = ('You are a health-care actuary. From a paid-claims triangle (incurred month x months of development, cumulative '
              'dollars paid as of the valuation date), estimate the ultimate paid for each of the six most recent incurred '
              'months. Return JSON {"ultimate": {"YYYY-MM": dollars, ...}, "method": "<one sentence>"}.')
    for val in spec['valuations']:
        if val in done: continue
        v = date.fromisoformat(val); vm = R._month(v)
        tri = defaultdict(lambda: defaultdict(float))
        for s, _, p, n, _ in claims:
            if p and p <= v and s <= v and R._month(s) >= R._add_months(vm, -24): tri[R._month(s)][R._lag(s, p)] += n
        lines = []
        for m in sorted(tri):
            k = (int(vm[:4]) - int(m[:4])) * 12 + int(vm[5:]) - int(m[5:])
            lines.append(m + ': ' + ', '.join(f'{sum(x for l, x in tri[m].items() if l <= j):.0f}' for j in range(min(k, 18) + 1)))
        months = [R._add_months(vm, -k) for k in range(5, -1, -1)]
        out, usd = _ask(system, f'Valuation date {val}. Rows: incurred month: cumulative paid at development month 0, 1, 2, ...\n' + '\n'.join(lines)
                        + f'\nEstimate ultimate paid for {", ".join(months)}.')
        truth = R.truth(claims, months)
        got = sum(float(x) for m, x in (out.get('ultimate') or {}).items() if m in months) if isinstance(out.get('ultimate'), dict) else None
        whole = sum(truth.values())
        with open(path, 'a') as f: f.write(json.dumps({'key': val, **out, 'truth_total': whole, 'error_pct': round(100 * (got - whole) / whole, 2) if got else None, 'usd': usd}) + '\n')


if __name__ == '__main__':
    {'equivalence': equivalence, 'stale': stale, 'runout': runout}[sys.argv[1]]()
