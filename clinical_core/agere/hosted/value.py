"""Physician value for the hosted app: "What I earned. Why I earned it. When I get paid." from receipted work.

    physician_value(ws_dir, rates=None, npi=None) -> {rates, totals, by_physician, credits, schedule}

Reads the workspace action store (plans/backbone.py, read-only). A reward step is credited once per patient when one of the
patient's work items carries a receipt matching the step (step + outcome), or a follow-on pathway ends in a listed state;
milestone bonuses follow the number of distinct steps credited for that patient. Credits go to the attributed physician
(NPI) on the plan item's task. Rates come from config/agere/physician_value.json and are illustrative: they are not
contracted rates and nothing here is a payment.
"""
from __future__ import annotations

from collections import defaultdict
from datetime import date
import json
from pathlib import Path
import sqlite3

RATES_PATH = Path(__file__).resolve().parents[3] / 'config' / 'agere' / 'physician_value.json'


def load_rates(path=None):
    return json.loads(Path(path or RATES_PATH).read_text())


def pay_date(on, schedule):
    """The scheduled payment date for a credit earned on `on` (ISO date)."""
    d = date.fromisoformat(str(on)[:10])
    lag = int(schedule.get('lag_months', 1))
    m = d.month - 1 + lag
    y, m = d.year + m // 12, m % 12 + 1
    return date(y, m, min(int(schedule.get('day', 15)), 28)).isoformat()


def _npi(payload):
    req = ((payload.get('task') or {}).get('required_fields') or {}).get('attributed_npi')
    v = req.get('value') if isinstance(req, dict) else req
    return str(v) if v else None


def _payloads(ws_dir):
    p = Path(ws_dir) / 'action' / 'program-work.sqlite3'
    if not p.is_file():
        return []
    db = sqlite3.connect(f'file:{p}?mode=ro', uri=True)
    try:
        return [json.loads(r[0]) for r in db.execute("SELECT payload FROM work_items WHERE program LIKE 'plan:%' AND program NOT LIKE '%:followup:%'")]
    finally:
        db.close()


def physician_value(ws_dir, rates=None, npi=None):
    rates = rates or load_rates()
    sched = rates.get('pay_schedule') or {}
    steps = sorted(rates.get('steps') or [], key=lambda s: s.get('order', 0))
    items = [p for p in _payloads(ws_dir) if p.get('state') != 'cancelled' and isinstance(p.get('action'), dict)]
    npi_of_item = {p['id']: _npi(p) for p in items}
    per_patient = defaultdict(dict)  # patient -> step id -> credit
    for p in items:
        a = p['action']
        who = npi_of_item.get(p['id']) or npi_of_item.get(p.get('parent_item')) or 'unattributed'
        receipts = [s['receipt'] for s in a.get('steps') or [] if s.get('receipt')]
        last = receipts[-1] if receipts else None
        for st in steps:
            if st['id'] in per_patient[p['patient_id']]:
                continue
            hit = next((r for r in receipts for m in st.get('match') or [] if r['step'] == m['step'] and r['outcome'] == m['outcome']), None)
            if hit is None and a.get('state') in ((st.get('states') or {}).get(a.get('program_id')) or []) and last:
                hit = last
            if hit is None:
                continue
            per_patient[p['patient_id']][st['id']] = {
                'patient_id': p['patient_id'], 'npi': who, 'step': st['id'], 'label': st['label'], 'why': st['why'],
                'amount': float(st['amount']), 'earned_on': hit['at'][:10], 'pays_on': pay_date(hit['at'], sched),
                'receipt_id': hit['receipt_id'], 'program_id': a.get('program_id'), 'work_item': p['id'], 'kind': 'action'}
    credits = []
    for pid, got in per_patient.items():
        cs = sorted(got.values(), key=lambda c: (c['earned_on'], c['step']))
        credits += cs
        for b in rates.get('bonuses') or []:
            n = int(b['after_steps'])
            if len(cs) >= n:
                c = cs[n - 1]
                credits.append({**c, 'step': b['id'], 'label': b['label'], 'why': f"{n} care steps completed for this patient",
                                'amount': float(b['amount']), 'kind': 'bonus'})
    if npi:
        credits = [c for c in credits if c['npi'] == str(npi)]
    by = defaultdict(lambda: {'earned': 0.0, 'actions': 0.0, 'bonuses': 0.0, 'patients': set(), 'credits': 0, 'next': {}})
    for c in credits:
        b = by[c['npi']]
        b['earned'] += c['amount']
        b['bonuses' if c['kind'] == 'bonus' else 'actions'] += c['amount']
        b['patients'].add(c['patient_id'])
        b['credits'] += 1
        b['next'][c['pays_on']] = b['next'].get(c['pays_on'], 0) + c['amount']
    phys = []
    for k, b in by.items():
        sched_rows = sorted(b['next'].items())
        phys.append({'npi': k, 'earned': round(b['earned'], 2), 'action_rewards': round(b['actions'], 2), 'milestone_bonuses': round(b['bonuses'], 2),
                     'patients': len(b['patients']), 'credits': b['credits'],
                     'schedule': [{'pays_on': d, 'amount': round(v, 2)} for d, v in sched_rows]})
    phys.sort(key=lambda r: -r['earned'])
    schedule = defaultdict(float)
    for c in credits:
        schedule[c['pays_on']] += c['amount']
    by_step = defaultdict(lambda: {'count': 0, 'amount': 0.0})
    for c in credits:
        by_step[c['label']]['count'] += 1
        by_step[c['label']]['amount'] += c['amount']
    return {'illustrative': bool(rates.get('illustrative', True)), 'currency': rates.get('currency', 'USD'),
            'rates': {'steps': steps, 'bonuses': rates.get('bonuses') or [], 'pay_schedule': sched, 'notes': rates.get('notes'),
                      'source': 'config/agere/physician_value.json'},
            'totals': {'earned': round(sum(c['amount'] for c in credits), 2),
                       'action_rewards': round(sum(c['amount'] for c in credits if c['kind'] != 'bonus'), 2),
                       'milestone_bonuses': round(sum(c['amount'] for c in credits if c['kind'] == 'bonus'), 2),
                       'patients': len({c['patient_id'] for c in credits}), 'physicians': len(phys), 'credits': len(credits)},
            'by_step': [{'label': k, **{kk: (round(vv, 2) if isinstance(vv, float) else vv) for kk, vv in v.items()}} for k, v in by_step.items()],
            'by_physician': phys[:200],
            'credits': sorted(credits, key=lambda c: c['earned_on'], reverse=True)[:300],
            'schedule': [{'pays_on': d, 'amount': round(v, 2)} for d, v in sorted(schedule.items())],
            'limits': ['Illustrative rates, not contracted terms and not payments.',
                       'Credits come from simulated-counterparty receipts in this workspace (no real care was delivered).']}
