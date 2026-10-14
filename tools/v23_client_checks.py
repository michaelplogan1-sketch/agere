"""Checks of the v2.3 client-data handling on a built semantic layer (read only).

Usage: .venv/bin/python tools/v23_client_checks.py --build artifacts/<build> [--out <json>]

* PDC (pdc_statins, pdc_diabetes, pdc_rasa): evaluated as shipped, then again with B2-reversed fills let through; the
  patients whose PDC changes show the reversal exclusion at work, and no reversed fill may be counted.
* Payer claims: version_status counts; the governed payer-feed metrics (counts_in_metrics) next to a naive query over
  every claim row, and the metric layer's lint on both (the naive one must draw ML-VERSION).
* Utilization: ED visits / admissions by source mix, claims-only members' rates, Medicaid vs commercial ED rate
  (member months by line of business).
* Labs, order-to-fill linkage, lists, outside documents: the summary counts the semantic layer recorded.
Synthetic data only; no network, no paid calls; never reads private/.
"""
import argparse, json, sqlite3, sys
from collections import Counter
from contextlib import contextmanager
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from clinical_core.agere.clinical import measures, metrics, semantic_layer, vbc  # noqa: E402

PDC = ('pdc_statins', 'pdc_diabetes', 'pdc_rasa')


@contextmanager
def reversed_fills_allowed():
    """The measure engine as if medication.fill_reversed did not exist (naive: every paid claim is a fill)."""
    original = measures.Context.has
    measures.Context.has = lambda self, table, column=None: False if (table, column) == ('medication', 'fill_reversed') else original(self, table, column)
    try: yield
    finally: measures.Context.has = original


def _pdc_patients(db, mid, as_of):
    start, end = measures.default_period(as_of)
    rep = measures.evaluate_db(db, measures.library()[mid], start, end, as_of, output='gaps', top=10 ** 6)
    gaps = {g['patient_id'] for g in rep.get('care_gaps') or []}
    return rep, gaps


def pdc_check(db, as_of):
    out = {}
    for mid in PDC:
        rep, gaps = _pdc_patients(db, mid, as_of)
        with reversed_fills_allowed():
            naive, naive_gaps = _pdc_patients(db, mid, as_of)
        classes = measures.library()[mid]['numerator']['criteria'][0]['classes']
        q = ','.join('?' * len(classes))
        reversed_fills = db.execute(f'SELECT count(DISTINCT record_id) FROM medication WHERE fill_reversed = 1 AND drug_class IN ({q})', classes).fetchone()[0]
        out[mid] = {'declined': rep.get('declined'), 'overall': rep.get('overall'), 'naive_overall': naive.get('overall'),
                    'reversed_fills_of_class': reversed_fills, 'gap_patients': len(gaps), 'naive_gap_patients': len(naive_gaps),
                    'patients_with_gap_only_when_reversals_excluded': len(gaps - naive_gaps),
                    'patients_with_gap_only_when_reversals_counted': len(naive_gaps - gaps)}
    return out


def claims_check(db, as_of, source):
    out = {'version_status': dict(db.execute('SELECT version_status, count(*) FROM payer_claim GROUP BY 1').fetchall())}
    q_denial = 'what is the payer claim denial rate this year'
    q_pmpm = 'payer feed allowed pmpm this year'
    governed, naive = {}, {}
    for q, mid, naive_sql in [
            (q_denial, 'payer_claim_denial_rate', "SELECT round(100.0 * sum(final_status IN ('denied','denied_upheld')) / count(*), 1) "
                                                  "FROM payer_claim WHERE service_from BETWEEN '{s}' AND '{e}'"),
            (q_pmpm, 'payer_allowed_pmpm', "SELECT round(((SELECT coalesce(sum(allowed_cents),0) FROM payer_claim WHERE final_status = 'paid' AND "
                                           "service_from BETWEEN '{s}' AND '{e}') + (SELECT coalesce(sum(coalesce(plan_paid_cents,0) + "
                                           "coalesce(patient_pay_cents,0)),0) FROM payer_pharmacy_claim WHERE claim_status = 'paid' AND fill_date "
                                           "BETWEEN '{s}' AND '{e}')) / 100.0 / nullif((SELECT count(*) FROM member_month WHERE month BETWEEN "
                                           "substr('{s}',1,7) AND substr('{e}',1,7)), 0), 2)")]:
        r = metrics.resolve({'question': q, 'as_of': as_of})
        assert r and r['id'] == mid, (q, r and r['id'])
        sql = r['sql']; p = r['params']
        bad = naive_sql.format(s=p['period_start'], e=p['period_end'])
        governed[mid] = {'value': db.execute(sql).fetchone()[0], 'lint': [f['rule'] for f in metrics.lint(q, sql, as_of, source=source)]}
        naive[mid] = {'value': db.execute(bad).fetchone()[0], 'lint': [f['rule'] for f in metrics.lint(q, bad, as_of, source=source)]}
    out.update(governed=governed, naive_every_claim_row=naive)
    out['counted_claims_with_carc18_or_superseded'] = db.execute(
        "SELECT count(*) FROM payer_claim WHERE counts_in_metrics = 1 AND (duplicate_submission = 1 OR superseded_by_payer_claim_id IS NOT NULL)").fetchone()[0]
    out['reversed_pharmacy_claims_counted_as_fills'] = db.execute(
        'SELECT count(*) FROM payer_pharmacy_claim WHERE counts_as_fill = 1 AND reversed_by_record_id IS NOT NULL').fetchone()[0]
    return out


def utilization_check(db, path, as_of):
    out = {'events_by_kind_and_sources': [list(r) for r in db.execute(
        'SELECT kind, sources, count(*) FROM utilization_event GROUP BY 1, 2 ORDER BY 1, 3 DESC')]}
    seg = dict(db.execute('SELECT patient_id, data_segment FROM patient'))
    year = str(int(as_of[:4]) - 1)
    rates = {}
    for kind in ('ed_visit', 'inpatient'):
        ev = Counter(seg.get(pid) for (pid,) in db.execute('SELECT patient_id FROM utilization_event WHERE kind = ? AND substr(event_date,1,4) = ?', (kind, year)))
        mm = Counter(seg.get(pid) for (pid,) in db.execute('SELECT patient_id FROM member_month WHERE substr(month,1,4) = ?', (year,)))
        rates[kind] = {s: round(1000 * ev[s] / (mm[s] / 12), 1) for s in mm if mm[s]}
    out[f'per_1000_member_years_{year}_by_segment'] = rates
    lob_ed = {}
    for lob, n, mm in db.execute(
            """SELECT m.line_of_business, (SELECT count(*) FROM utilization_event u WHERE u.kind = 'ed_visit' AND u.patient_id IN
                 (SELECT patient_id FROM member_month WHERE line_of_business = m.line_of_business AND substr(month,1,4) = ?)
                 AND substr(u.event_date,1,4) = ? AND substr(u.event_date,1,7) IN (SELECT month FROM member_month x WHERE x.patient_id = u.patient_id
                 AND x.line_of_business = m.line_of_business)), count(*) FROM member_month m WHERE substr(m.month,1,4) = ? GROUP BY 1""", (year, year, year)):
        lob_ed[lob] = round(1000 * n / (mm / 12), 1) if mm else None
    out[f'ed_per_1000_by_line_of_business_{year}'] = lob_ed
    if lob_ed.get('medicaid') and lob_ed.get('commercial'):
        out['medicaid_to_commercial_ed_ratio'] = round(lob_ed['medicaid'] / lob_ed['commercial'], 2)
    claims_only = [pid for pid, s in seg.items() if s == 'claims_only']
    if claims_only:
        r = vbc.execute(path, claims_only, {'metric': 'utilization', 'period': {'start': f'{year}-01-01', 'end': f'{year}-12-31'}}, as_of)
        out['vbc_claims_only_members'] = {k: r['rows'][0].get(k) for k in ('members', 'member_years', 'ed_visits', 'admissions', 'ed_per_1000',
                                                                            'admits_per_1000')} if r['rows'] else None
    return out


def main():
    p = argparse.ArgumentParser(); p.add_argument('--build', type=Path, required=True); p.add_argument('--out', type=Path)
    a = p.parse_args()
    stack = a.build / 'stack'
    target = semantic_layer.ensure(stack)
    db = sqlite3.connect(f'file:{target}?mode=ro', uri=True)
    try:
        meta = dict(db.execute('SELECT key, value FROM meta'))
        as_of = meta['as_of']
        feeds = json.loads(meta['population_feeds'])
        source = {d: metrics.universe(db, d) for d in metrics.DIMENSIONS}
        result = {'synthetic': True, 'semantic_layer': meta['version'], 'as_of': as_of,
                  'client_data': feeds.get('client_data'), 'client_claims': feeds.get('client_claims'), 'prescriptions': feeds.get('prescriptions'),
                  'pdc': pdc_check(db, as_of), 'payer_claims': claims_check(db, as_of, source),
                  'utilization': utilization_check(db, stack, as_of),
                  'fuzzy_links': {'orders': [list(r) for r in db.execute(
                      "SELECT fill_status, count(*), round(avg(match_confidence), 3) FROM prescription WHERE fill_basis = 'fuzzy_prescription_chain' GROUP BY 1")],
                      'confidence_bands': [list(r) for r in db.execute(
                          "SELECT CASE WHEN match_confidence >= 0.95 THEN '>=0.95' WHEN match_confidence >= 0.8 THEN '0.80-0.95' "
                          "WHEN match_confidence >= 0.5 THEN '0.50-0.80' ELSE 'unlinked' END, count(*) FROM prescription "
                          "WHERE fill_basis = 'fuzzy_prescription_chain' GROUP BY 1")]}}
    finally:
        db.close()
    text = json.dumps(result, indent=2, default=str)
    if a.out: a.out.write_text(text + '\n')
    print(text)
    pc = result['payer_claims']
    assert pc['counted_claims_with_carc18_or_superseded'] == 0 and pc['reversed_pharmacy_claims_counted_as_fills'] == 0
    assert all(not v['lint'] for v in pc['governed'].values()), pc['governed']
    assert all('ML-VERSION' in v['lint'] for v in pc['naive_every_claim_row'].values()), pc['naive_every_claim_row']
    return 0


if __name__ == '__main__':
    sys.exit(main())
