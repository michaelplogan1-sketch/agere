"""Population-wide events from payer feeds, folded into the semantic layer's own entity tables.

The deep-chart EHR carries services, claims and fills for the original clinic roster; population members (and services our
EHR never saw) arrive only as payer claims. Every question about "our patients" should read both, deduplicated, so this pass
adds payer evidence to the tables planners already use instead of leaving it in separate feed tables:

  services   payer claim lines (voided claims excluded) -> wellness_visit (AWV G0438/G0439), immunization (influenza vaccine
             CPT / G0008), cancer_screening (screening mammogram 77067 / G0202), measurement (HbA1c test CPT 83036/83037, no
             value). A claim service is the same event as an EHR record of the same kind for the patient within 1 day.
             immunization.season = the recorded season, else the July-June season of the date.
  claims     claim gains every payer_medical_claims claim that is not the payer's copy of one of our provider claims (same
             patient, service date and billed amount); claim_source says which. One claim per original submission: a
             frequency-7 '-R' replacement folds into the claim it replaces (its payment counts, it is not a new claim); a
             void reverses the claim's prior payment. claim_adjudication = one row per adjudication of either source (money
             deltas: reversals negative, recoupments negative paid / 0 allowed, voids reverse the prior net), so net paid,
             denial %, days to first payment and reversal counts are population-wide. first_paid_date / first_reversal_date
             are on claim.
  pharmacy   pharmacy_claim_event = provider pharmacy_claim_transactions + payer_pharmacy_claims, one row per transaction:
             status, reject_reason (NCPDP 79 = REFILL_TOO_SOON, ...), payer_paid_delta_cents (a payer reversal takes back the
             reversed claim's plan payment when it is known), return_to_stock (a payer reversal of a fill that was never picked
             up), payer. returned_fill = prescriptions prepared but never picked up: pharmacy_dispenses 'returned_to_stock'
             (one per fill_id) + payer_pharmacy_claims return-to-stock reversals (one per rx_claim_id). The provider claim
             transaction reversing a returned dispense is that same fill (claim_id = fill_id), so it adds no row.

Idempotent: re-running replaces only what it added (rows with source/claim_source from payer feeds), so an existing
semantic.sqlite3 can be upgraded in place (apply()).
"""
from collections import defaultdict
from datetime import date, timedelta
import json, sqlite3

VERSION = 'population-events/1'

FLU_VACCINE_CODES = ('90630', '90653', '90654', '90655', '90656', '90657', '90658', '90660', '90661', '90662', '90664', '90666', '90667',
                     '90668', '90672', '90673', '90674', '90682', '90685', '90686', '90687', '90688', '90689', '90694', '90756', 'G0008')
SCREENING_MAMMOGRAM_CODES = ('77067', 'G0202')
AWV_CODES = {'G0438': 'initial', 'G0439': 'subsequent'}
HBA1C_TEST_CODES = ('83036', '83037')
NCPDP_REJECT = {'79': 'REFILL_TOO_SOON', '75': 'PRIOR_AUTHORIZATION_REQUIRED', '70': 'PRODUCT_NOT_COVERED', '76': 'PLAN_LIMITATIONS_EXCEEDED',
                '88': 'DUR_REJECT', '65': 'PATIENT_NOT_COVERED', 'MR': 'PRODUCT_NOT_ON_FORMULARY'}
SAME_EVENT_DAYS = 1
OWN_TABLES = ('cancer_screening', 'claim_adjudication', 'pharmacy_claim_event', 'returned_fill')

DDL = """
CREATE TABLE IF NOT EXISTS cancer_screening(record_id TEXT PRIMARY KEY, patient_id TEXT, test TEXT, result_status TEXT, event_date TEXT,
  result_date TEXT, birads_category INTEGER, source TEXT, code TEXT);
CREATE TABLE IF NOT EXISTS claim_adjudication(claim_id TEXT, claim_source TEXT, record_id TEXT, patient_id TEXT, sequence INTEGER,
  status TEXT, frequency_code TEXT, event_date TEXT, service_date TEXT, payer TEXT, billed_cents INTEGER, payer_paid_delta_cents INTEGER,
  allowed_delta_cents INTEGER, replaces_claim_id TEXT);
CREATE TABLE IF NOT EXISTS pharmacy_claim_event(claim_id TEXT, source TEXT, record_id TEXT, patient_id TEXT, status TEXT, reject_code TEXT,
  reject_reason TEXT, event_date TEXT, drug_name TEXT, ndc TEXT, days_supply INTEGER, payer_paid_delta_cents INTEGER,
  return_to_stock INTEGER, payer TEXT);
CREATE TABLE IF NOT EXISTS returned_fill(fill_key TEXT, source TEXT, record_id TEXT, patient_id TEXT, event_date TEXT, drug_name TEXT,
  ndc TEXT, reason TEXT);
"""

SCHEMA_DOC = {
    'cancer_screening': 'One row per cancer screening result (cancer_screening_results: test e.g. bilateral_screening_mammography, '
                        'result_status, event_date = performed_date) plus screening mammograms known only from a payer claim line '
                        '(77067 / G0202; source payer_claim_line, result_status claim_billed). Same patient within 1 day = one screen.',
    'claim_adjudication': 'One row per adjudication of a medical claim from either source (claim_source medical_claims = our provider '
                          'remittances, payer_medical_claims = the payer feed). payer_paid_delta_cents / allowed_delta_cents: '
                          'reversals negative, recoupments negative paid and 0 allowed, a void reverses the prior net; facility DRG '
                          'rows without deltas use their paid/allowed amounts. status paid | denied | reversed | voided | recouped ...; '
                          'replaces_claim_id = the original claim of a frequency-7 replacement. Net paid in a period = sum of deltas '
                          'by event_date; claims reversed by the payer = distinct claim_id with status reversed.',
    'pharmacy_claim_event': 'Pharmacy claim transactions from both sources (source pharmacy_claim_transactions | payer_pharmacy_claims), '
                            'one row per transaction: status paid | rejected | reversed | recouped; reject_reason normalises NCPDP codes '
                            "(79 = REFILL_TOO_SOON); payer_paid_delta_cents (reversal = minus the reversed claim's payment when known); "
                            'return_to_stock = 1 for a payer reversal of a fill never picked up; payer = paying organization. '
                            'Net pharmacy payer spend in a period = sum(payer_paid_delta_cents) by event_date. Rejections: '
                            "count distinct (source, claim_id) with status rejected (refill too soon = reject_reason REFILL_TOO_SOON).",
    'returned_fill': 'Prescriptions prepared but never picked up (returned to stock), one row per fill: source pharmacy_dispenses '
                     "(status returned_to_stock, fill_key = fill_id) or payer_pharmacy_claims (return-to-stock reversal, fill_key = "
                     'rx_claim_id); event_date = the return. Count distinct (source, fill_key). A returned fill is never a pickup.',
}


def _cents(v):
    return None if v is None else int(round(float(v) * 100))


def _rows(out, stream):
    for rid, pid, payload, d, av in out.execute('SELECT record_id, patient_id, payload, event_date, available_date FROM record WHERE stream = ?',
                                                (stream,)):
        yield rid, pid, json.loads(payload), d, av


def _near(days, d):
    """True when date d is within SAME_EVENT_DAYS of any date in the patient's EHR set."""
    try:
        x = date.fromisoformat(d[:10])
    except (TypeError, ValueError):
        return False
    return any((x + timedelta(days=k)).isoformat() in days for k in range(-SAME_EVENT_DAYS, SAME_EVENT_DAYS + 1))


def season_of(d):
    """Influenza season (July through June) of a date: 2029-10-01 -> '2029-2030', 2030-02-01 -> '2029-2030'."""
    if not d: return None
    y, m = int(d[:4]), int(d[5:7])
    return f'{y}-{y + 1}' if m >= 7 else f'{y - 1}-{y}'


def _tables(out):
    return {r[0] for r in out.execute("SELECT name FROM sqlite_master WHERE type = 'table'")}


def claim_service_lines(out, codes):
    """Payer claim lines with one of the codes, on claims that were not voided (and are not duplicate submissions)."""
    if not {'payer_claim_line', 'payer_claim'} <= _tables(out): return []
    q = ','.join('?' * len(codes))
    return out.execute(f"""SELECT l.record_id, l.patient_id, l.service_date, coalesce(l.cpt, l.hcpcs) FROM payer_claim_line l
        LEFT JOIN payer_claim c ON c.payer_claim_id = l.payer_claim_id
        WHERE coalesce(l.cpt, l.hcpcs) IN ({q}) AND l.service_date IS NOT NULL AND coalesce(l.counts_in_metrics, 1) = 1
          AND coalesce(c.final_status, '') <> 'voided' AND coalesce(c.version_status, '') NOT IN ('voided', 'superseded_by_replacement')
        ORDER BY l.patient_id, l.service_date, l.record_id""", codes).fetchall()


def _fold(lines, ehr):
    """Claim lines that are not the same event as an EHR record (same patient within 1 day) nor another claim line already kept."""
    kept, seen = [], defaultdict(set)
    for rid, pid, d, code in lines:
        if _near(ehr.get(pid, set()), d) or _near(seen[pid], d): continue
        seen[pid].add(d[:10]); kept.append((rid, pid, d[:10], code))
    return kept


def _services(out):
    from .semantic_layer import _add
    have = _tables(out)
    summary = {}
    if 'wellness_visit' in have:
        _add(out, 'wellness_visit', 'source TEXT')
        out.execute("DELETE FROM wellness_visit WHERE source = 'payer_claim_line'")
        out.execute("UPDATE wellness_visit SET source = 'ehr' WHERE source IS NULL")
        ehr = defaultdict(set)
        for pid, d in out.execute("SELECT patient_id, event_date FROM wellness_visit WHERE awv = 1 AND event_date IS NOT NULL"): ehr[pid].add(d[:10])
        rows = _fold(claim_service_lines(out, tuple(AWV_CODES)), ehr)
        out.executemany("""INSERT OR IGNORE INTO wellness_visit(record_id, patient_id, encounter_id, visit_type, hcpcs_code, awv, awv_kind, status,
            coding_status, event_date, available_date, organization_id, source) VALUES (?,?,NULL,'medicare_annual_wellness',?,1,?,'completed',
            'claim',?,NULL,NULL,'payer_claim_line')""", [(rid, pid, code, AWV_CODES[code], d) for rid, pid, d, code in rows])
        summary['awv_from_claims'] = len(rows)
    if 'immunization' in have:
        _add(out, 'immunization', 'season TEXT', 'source TEXT', 'code TEXT')
        out.execute("DELETE FROM immunization WHERE source = 'payer_claim_line'")
        out.execute("UPDATE immunization SET source = 'ehr' WHERE source IS NULL")
        seasons = {rid: (p.get('season') or None) for rid, _, p, _, _ in _rows(out, 'immunizations')}
        out.executemany('UPDATE immunization SET season = ? WHERE record_id = ?',
                        [(s or season_of(d), rid) for rid, d in out.execute('SELECT record_id, event_date FROM immunization').fetchall()
                         for s in [seasons.get(rid)]])
        ehr = defaultdict(set)
        for pid, d in out.execute("SELECT patient_id, event_date FROM immunization WHERE lower(vaccine) LIKE 'influenza%' AND event_date IS NOT NULL"):
            ehr[pid].add(d[:10])
        rows = _fold(claim_service_lines(out, FLU_VACCINE_CODES), ehr)
        out.executemany("""INSERT INTO immunization(record_id, patient_id, vaccine, product, cvx, mvx, status, event_date, available_date, season,
            source, code) VALUES (?,?,'influenza',NULL,NULL,NULL,'completed',?,NULL,?,'payer_claim_line',?)""",
                        [(rid, pid, d, season_of(d), code) for rid, pid, d, code in rows])
        summary['influenza_from_claims'] = len(rows)
    out.executescript(DDL)
    out.execute('DELETE FROM cancer_screening')
    screens = []
    for rid, pid, p, d, av in _rows(out, 'cancer_screening_results'):
        screens.append((rid, pid, p.get('test'), p.get('result_status'), (p.get('performed_date') or d or '')[:10] or None, d,
                        p.get('birads_category'), 'ehr', None))
    out.executemany('INSERT OR IGNORE INTO cancer_screening VALUES (?,?,?,?,?,?,?,?,?)', screens)
    ehr = defaultdict(set)
    for pid, d in out.execute("SELECT patient_id, event_date FROM cancer_screening WHERE test LIKE '%mammograph%' AND event_date IS NOT NULL"):
        ehr[pid].add(d[:10])
    rows = _fold(claim_service_lines(out, SCREENING_MAMMOGRAM_CODES), ehr)
    out.executemany("INSERT OR IGNORE INTO cancer_screening VALUES (?,?,'bilateral_screening_mammography','claim_billed',?,NULL,NULL,'payer_claim_line',?)",
                    [(rid, pid, d, code) for rid, pid, d, code in rows])
    summary.update(cancer_screening_results=len(screens), screening_mammograms_from_claims=len(rows))
    if 'measurement' in have:
        out.execute("DELETE FROM measurement WHERE source_table = 'payer_claim_line'")
        ehr = defaultdict(set)
        for pid, d in out.execute("SELECT patient_id, event_date FROM measurement WHERE concept = 'hba1c' AND event_date IS NOT NULL"): ehr[pid].add(d[:10])
        rows = _fold(claim_service_lines(out, HBA1C_TEST_CODES), ehr)
        out.executemany("""INSERT INTO measurement(record_id, patient_id, source_table, concept, name, value, unit, value_canonical, unit_canonical,
            result_status, event_date) VALUES (?,?,'payer_claim_line','hba1c',?,NULL,NULL,NULL,NULL,'claim_billed',?)""",
                        [(rid, pid, f'CPT {code} (billed test, no result)', d) for rid, pid, d, code in rows])
        summary['hba1c_tests_from_claims'] = len(rows)
    return summary


def _denied_then_paid(statuses):
    denied = next((i for i, s in enumerate(statuses) if s in ('denied', 'denied_upheld')), None)
    return int(denied is not None and 'paid' in statuses[denied + 1:])


def _claims(out):
    from .semantic_layer import _add
    have = _tables(out)
    if 'claim' not in have: return {}
    _add(out, 'claim', 'claim_source TEXT', 'first_paid_date TEXT', 'first_reversal_date TEXT')
    out.executescript(DDL)
    out.execute("DELETE FROM claim WHERE claim_source = 'payer_medical_claims'")
    out.execute('DELETE FROM claim_adjudication')
    out.execute("UPDATE claim SET claim_source = 'medical_claims' WHERE claim_source IS NULL")
    adj = []
    prov = defaultdict(list)
    for rid, pid, p, d, av in _rows(out, 'medical_claims'):
        cid = p.get('claim_id')
        if not cid: continue
        deltas = p.get('financial_deltas_cents') if isinstance(p.get('financial_deltas_cents'), dict) else None
        paid = deltas.get('payer') if deltas is not None else p.get('payer_paid_cents')
        allowed = deltas.get('allowed') if deltas is not None else p.get('allowed_cents')
        prov[cid].append((d or '', 1 if p.get('status') == 'reversed' else 0, rid, pid, p.get('status'), paid, allowed, p.get('billed_cents'),
                          p.get('service_date'), p.get('source_system') or p.get('organization_id')))
    for cid, h in prov.items():
        h.sort()
        for i, (d, _, rid, pid, st, paid, allowed, billed, sd, payer) in enumerate(h, 1):
            adj.append((cid, 'medical_claims', rid, pid, i, st, None, d or None, sd, payer, billed, paid, allowed, None))
    provider_keys = {(pid, sd, b) for cid, h in prov.items() for (_, _, _, pid, _, _, _, b, sd, _) in h if sd}
    payer = defaultdict(list)
    for rid, pid, p, d, av in _rows(out, 'payer_medical_claims'):
        if p.get('payer_claim_id'): payer[p['payer_claim_id']].append((p.get('adjudication_sequence') or 0, d or '', rid, pid, p, av))
    copies, rows = set(), []
    for cid, h in payer.items():
        h.sort(key=lambda x: (x[0], x[1], x[2]))
        first = h[0][4]
        if (h[0][3], first.get('service_from'), _cents(max((x[4].get('total_charge') or 0) for x in h))) in provider_keys:
            copies.add(cid); continue  # the payer's copy of one of our provider claims: the provider claim is kept
        net = 0
        for seq, d, rid, pid, p, av in h:
            st = p.get('claim_status')
            paid = -net if st == 'voided' else (_cents(p.get('paid_amount')) or 0)
            allowed = 0 if st in ('recouped', 'voided') else _cents(p.get('allowed_amount'))
            net += paid
            orig = p.get('original_payer_claim_id') if p.get('claim_frequency_code') == '7' else None
            adj.append((cid, 'payer_medical_claims', rid, pid, seq, st, p.get('claim_frequency_code'), d or None, p.get('service_from'),
                        p.get('payer_id'), _cents(p.get('total_charge')), paid, allowed, orig))
    out.executemany('INSERT INTO claim_adjudication VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)', adj)
    out.execute('CREATE INDEX IF NOT EXISTS claim_adjudication_claim ON claim_adjudication(claim_id)')
    # Claim rows for payer-feed submissions (a '-R' replacement folds into the claim it replaces).
    agg = {}
    for (cid, src, rid, pid, seq, st, freq, d, sd, pay, billed, paid, allowed, orig) in adj:
        if src != 'payer_medical_claims': continue
        key = orig if (orig and orig in payer and orig not in copies) else cid
        a = agg.setdefault(key, {'pid': pid, 'payer': pay, 'sd': sd, 'billed': billed, 'events': []})
        a['events'].append((d or '', seq, st, paid or 0, allowed or 0, cid))
    claim_rows = []
    for key, a in agg.items():
        ev = sorted(a['events'])
        own = [e for e in ev if e[5] == key] or ev
        first = own[0]
        statuses = [e[2] for e in ev]
        paid_dates = [e[0] for e in ev if e[2] == 'paid' and e[5] == key]
        rev_dates = [e[0] for e in ev if e[2] == 'reversed']
        last = payer.get(key, [[None, None, None, None, {}, None]])[-1][4]
        claim_rows.append((key, a['pid'], a['payer'], a['sd'], (a['sd'] or '')[:4] or None, first[2], ev[-1][2],
                           int('denied' in statuses or 'denied_upheld' in statuses),
                           _denied_then_paid(statuses),
                           sum(e[3] for e in ev), a['billed'], sum(e[4] for e in ev), first[0] or None, ev[-1][0] or None, len(ev),
                           first[0] or None, min(paid_dates) if paid_dates else None, min(rev_dates) if rev_dates else None,
                           last.get('ms_drg'), last.get('billing_provider_npi'), last.get('place_of_service'), last.get('type_of_bill'),
                           last.get('admission_date'), last.get('discharge_date'), last.get('principal_diagnosis'), 'payer_medical_claims'))
    out.executemany("""INSERT OR IGNORE INTO claim(claim_id, patient_id, payer, service_date, benefit_year, first_status, final_status, ever_denied,
        denied_then_paid, net_payer_paid_cents, billed_cents, allowed_cents, first_available_date, last_available_date, versions,
        first_adjudication_date, first_paid_date, first_reversal_date, drg, billing_npi, place_of_service, type_of_bill, admission_date,
        discharge_date, principal_diagnosis, claim_source) VALUES (""" + ','.join('?' * 26) + ')', claim_rows)
    # Provider claims: first payment and first payer reversal dates from their adjudications.
    out.execute("""UPDATE claim SET first_paid_date = (SELECT min(event_date) FROM claim_adjudication a WHERE a.claim_id = claim.claim_id
                     AND a.claim_source = 'medical_claims' AND a.status = 'paid'),
                   first_reversal_date = (SELECT min(event_date) FROM claim_adjudication a WHERE a.claim_id = claim.claim_id
                     AND a.claim_source = 'medical_claims' AND a.status = 'reversed') WHERE claim_source = 'medical_claims'""")
    return {'claim_adjudications': len(adj), 'payer_claims_added': len(claim_rows), 'payer_copies_of_provider_claims': len(copies)}


def _pharmacy(out):
    out.executescript(DDL)
    out.execute('DELETE FROM pharmacy_claim_event')
    rows = []
    for rid, pid, p, d, av in _rows(out, 'pharmacy_claim_transactions'):
        code = p.get('reject_code')
        rows.append((p.get('claim_id'), 'pharmacy_claim_transactions', rid, pid, p.get('transaction_status'), code,
                     NCPDP_REJECT.get(str(code), code) if code else None, d, None, p.get('ndc'), p.get('days_supply_reported'),
                     p.get('payer_paid_delta_cents'), int(p.get('transaction_status') == 'reversed' and p.get('reversal_reason') == 'return_to_stock'),
                     p.get('source_system') or p.get('organization_id')))
    payer = defaultdict(list)
    for rid, pid, p, d, av in _rows(out, 'payer_pharmacy_claims'):
        payer[p.get('rx_claim_id') or rid].append((d or '', rid, pid, p))
    for cid, h in payer.items():
        h.sort(key=lambda x: (x[0], x[1]))
        paid_before = 0
        for d, rid, pid, p in h:
            st, code = p.get('claim_status'), p.get('reject_code')
            plan = _cents(p.get('plan_paid')) or 0
            if st == 'reversed':
                delta = -paid_before  # the payer reversal row carries 0; it takes back the reversed claim's payment when known
            elif st in ('paid', 'recouped'):
                delta = plan
            else:
                delta = 0
            paid_before += delta
            rows.append((cid, 'payer_pharmacy_claims', rid, pid, st, code, NCPDP_REJECT.get(str(code), code) if code else None, d or None,
                         p.get('drug_name'), p.get('ndc'), p.get('days_supply'), delta,
                         int(st == 'reversed' and (p.get('reversal_reason') or 'return_to_stock') == 'return_to_stock'), p.get('payer_id')))
    out.executemany('INSERT INTO pharmacy_claim_event VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)', rows)
    out.execute('CREATE INDEX IF NOT EXISTS pharmacy_claim_event_date ON pharmacy_claim_event(event_date)')
    out.execute('DELETE FROM returned_fill')
    returned = [(p.get('fill_id') or rid, 'pharmacy_dispenses', rid, pid, d, p.get('medication_name'), p.get('ndc'), p.get('return_reason'))
                for rid, pid, p, d, av in _rows(out, 'pharmacy_dispenses') if p.get('status') == 'returned_to_stock']
    returned += [(cid, 'payer_pharmacy_claims', rid, pid, d, drug, ndc, 'return_to_stock')
                 for cid, src, rid, pid, st, _, _, d, drug, ndc, _, _, rts, _ in rows if src == 'payer_pharmacy_claims' and rts]
    out.executemany('INSERT INTO returned_fill VALUES (?,?,?,?,?,?,?,?)', returned)
    return {'pharmacy_claim_events': len(rows), 'returned_fills': len(returned)}


def build(out):
    """Run every pass on an open semantic-layer connection; returns the summary stored in meta.population_events."""
    summary = {'version': VERSION}
    for t in OWN_TABLES: out.execute(f'DROP TABLE IF EXISTS {t}')  # tables this pass owns entirely: rebuilt from scratch
    out.executescript(DDL)
    summary.update(_services(out))
    summary.update(_claims(out))
    summary.update(_pharmacy(out))
    return summary


def apply(semantic_path):
    """Upgrade an existing semantic.sqlite3 in place (idempotent) and record the pass in meta."""
    out = sqlite3.connect(semantic_path)
    try:
        summary = build(out)
        out.execute("DELETE FROM meta WHERE key = 'population_events'")
        out.execute("INSERT INTO meta VALUES ('population_events', ?)", (json.dumps(summary),))
        out.commit()
        return summary
    finally:
        out.close()
