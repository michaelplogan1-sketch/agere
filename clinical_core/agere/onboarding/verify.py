"""Verification of an onboarded semantic layer: invariants, acceptance questions, native reconciliation.

Invariants (each returns passed + measured detail; nothing is suppressed):
  row_counts          source rows reconcile to grain rows; entities to distinct business keys
  key_uniqueness      entity keys and lineage record ids are unique
  referential         patient/claim/organization/clinician references resolve
  code_validity       ICD-10-CM, LOINC (check digit), NPI (Luhn), CPT/HCPCS, RxNorm shapes
  date_sanity         ISO dates, plausible range, end >= start, service before receipt
  money               target cents reconcile to source totals at the scale the source values imply,
                      and the claim charge scale is plausible (catches a missing dollars->cents)
"""
from pathlib import Path
import sqlite3
import statistics

from . import codes

THRESHOLDS = {'required_filled': 0.5, 'referential_patient': 1.0, 'referential_other': 0.95, 'code_validity': 0.95, 'date_bad_share': 0.001}

ACCEPTANCE = [  # semantic-layer SQL; `mirrors` names the bench development question it corresponds to.
    {'id': 'A01', 'mirrors': 'D01', 'question': 'Patients in the roster', 'sql': 'SELECT count(*) FROM patient'},
    {'id': 'A02', 'mirrors': 'D02', 'question': 'Patients with female reported sex', 'sql': "SELECT count(*) FROM patient WHERE sex_reported = 'F'"},
    {'id': 'A03', 'mirrors': 'D04', 'question': 'Patients with an ICD-10 diabetes diagnosis (E08-E13 excl. E12)',
     'sql': "SELECT count(DISTINCT patient_id) FROM condition WHERE source_table = 'conditions' AND substr(icd10,1,3) IN ('E08','E09','E10','E11','E13')"},
    {'id': 'A04', 'mirrors': 'D06', 'question': 'Patients with any HbA1c above 9%',
     'sql': "SELECT count(DISTINCT patient_id) FROM measurement WHERE concept = 'hba1c' AND value_canonical > 9"},
    {'id': 'A05', 'mirrors': 'D07', 'question': 'Patients whose latest dated HbA1c is above 9%',
     'sql': "SELECT count(*) FROM measurement_latest WHERE concept = 'hba1c' AND value_canonical > 9"},
    {'id': 'A06', 'mirrors': 'D05', 'question': 'Diabetic patients with any HbA1c above 9%',
     'sql': "SELECT count(DISTINCT m.patient_id) FROM measurement m WHERE m.concept = 'hba1c' AND m.value_canonical > 9 AND m.patient_id IN "
            "(SELECT patient_id FROM condition WHERE source_table = 'conditions' AND substr(icd10,1,3) IN ('E08','E09','E10','E11','E13'))"},
    {'id': 'A07', 'mirrors': 'D17', 'question': 'Visits (encounters, latest version) with status completed',
     'sql': "SELECT count(*) FROM visit WHERE status = 'completed'"},
    {'id': 'A08', 'mirrors': 'D18', 'question': 'Patients with a metformin medication order',
     'sql': "SELECT count(DISTINCT patient_id) FROM medication WHERE evidence = 'ordered' AND drug_class = 'metformin'"},
    {'id': 'A09', 'mirrors': 'D29', 'question': 'Patients whose latest coverage is commercial and active',
     'sql': "SELECT count(*) FROM patient WHERE coverage_type = 'commercial' AND coverage_status = 'active'"},
    {'id': 'A10', 'mirrors': 'D28', 'question': 'Net payer paid (cents) on claims with 2028 service dates',
     'sql': "SELECT coalesce(sum(net_payer_paid_cents),0) FROM claim WHERE service_date LIKE '2028%'"},
]

AGGREGATES = [
    ('patients', 'SELECT count(*) FROM patient'),
    ('visits', 'SELECT count(*) FROM visit'),
    ('visits: telephone', "SELECT count(*) FROM visit WHERE type = 'telephone'"),
    ('visits: primary_care', "SELECT count(*) FROM visit WHERE type = 'primary_care'"),
    ('visits: office', "SELECT count(*) FROM visit WHERE type = 'office'"),
    ('visits: emergency', "SELECT count(*) FROM visit WHERE type = 'emergency'"),
    ('visits: inpatient', "SELECT count(*) FROM visit WHERE type = 'inpatient'"),
    ('visits: distinct types', 'SELECT count(DISTINCT type) FROM visit'),
    ('claims', 'SELECT count(*) FROM claim'),
    ('claims final paid', "SELECT count(*) FROM claim WHERE final_status = 'paid'"),
    ('claims final denied', "SELECT count(*) FROM claim WHERE final_status = 'denied'"),
    ('claims net payer paid (cents)', 'SELECT sum(net_payer_paid_cents) FROM claim'),
    ('claims billed (cents)', 'SELECT sum(billed_cents) FROM claim'),
    ('claims allowed (cents)', 'SELECT sum(allowed_cents) FROM claim'),
    ('claim lines', 'SELECT count(*) FROM claim_line'),
    ('claim line charges (cents)', 'SELECT sum(charge_cents) FROM claim_line'),
    ('claim lines with CPT', 'SELECT count(cpt) FROM claim_line'),
    ('A1c results', "SELECT count(*) FROM measurement WHERE concept = 'hba1c' AND value_canonical IS NOT NULL"),
    ('A1c patients', "SELECT count(DISTINCT patient_id) FROM measurement WHERE concept = 'hba1c' AND value_canonical IS NOT NULL"),
    ('A1c mean (%)', "SELECT round(avg(value_canonical), 4) FROM measurement WHERE concept = 'hba1c'"),
    ('A1c < 5.7', "SELECT count(*) FROM measurement WHERE concept = 'hba1c' AND value_canonical < 5.7"),
    ('A1c 5.7-6.4', "SELECT count(*) FROM measurement WHERE concept = 'hba1c' AND value_canonical >= 5.7 AND value_canonical < 6.5"),
    ('A1c 6.5-7.9', "SELECT count(*) FROM measurement WHERE concept = 'hba1c' AND value_canonical >= 6.5 AND value_canonical < 8"),
    ('A1c 8.0-9.0', "SELECT count(*) FROM measurement WHERE concept = 'hba1c' AND value_canonical >= 8 AND value_canonical <= 9"),
    ('A1c > 9', "SELECT count(*) FROM measurement WHERE concept = 'hba1c' AND value_canonical > 9"),
    ('glucose results (lab)', "SELECT count(*) FROM measurement WHERE concept = 'glucose' AND value_canonical IS NOT NULL"),
    ('glucose mean (mg/dL)', "SELECT round(avg(value_canonical), 3) FROM measurement WHERE concept = 'glucose'"),
    ('condition records', 'SELECT count(DISTINCT record_id) FROM condition'),
    ('diabetes patients (ICD-10)', ACCEPTANCE[2]['sql']),
    ('metformin patients (any evidence)', "SELECT count(DISTINCT patient_id) FROM medication WHERE drug_class = 'metformin'"),
    ('medication records', 'SELECT count(DISTINCT record_id) FROM medication'),
]


WARNING_ONLY = {  # source properties worth surfacing that are not onboarding defects
    'referential.claim_line.claim_id': 'claim lines whose claim has no adjudication yet (submitted, not adjudicated)',
}


def _check(name, passed, **detail):
    out = {'check': name, 'passed': bool(passed), 'severity': 'warning' if name in WARNING_ONLY else 'error', **detail}
    if name in WARNING_ONLY and not passed: out['note'] = WARNING_ONLY[name]
    return out


def _one(db, sql, args=()):
    return db.execute(sql, args).fetchone()[0]


def source_is_dollars(values, field_spec):
    """Scale of a money column the mapping feeds into a cents field. Decimals reveal dollars; the mapping's own declaration
    (a dollars_to_cents transform) wins for a whole-dollar column, where every value is an integer and nothing reveals the scale."""
    return any(float(v) != int(float(v)) for v in values) or any(t.get('op') == 'dollars_to_cents' for t in (field_spec.get('transforms') or []))


def invariants(db_path, mapping, conn, as_of=None, model=None):
    db = sqlite3.connect(f'file:{db_path}?mode=ro', uri=True)
    out = []
    try:
        ents = mapping['entities']
        # 1. Row counts.
        grain_tables = {'visit': 'visit_event', 'condition': 'condition', 'measurement': 'measurement', 'claim': 'claim_event',
                        'claim_line': 'claim_line', 'patient': 'patient', 'clinician': 'clinician', 'organization': 'organization'}
        for entity, table in grain_tables.items():
            e = ents.get(entity) or {}
            if not e.get('source_table'): continue
            src = conn.row_count(e['source_table'])
            tgt = _one(db, f'SELECT count(*) FROM {table}')
            out.append(_check(f'row_counts.{entity}', src == tgt, source_rows=src, target_rows=tgt, source_table=e['source_table']))
        if (ents.get('medication') or {}).get('source_table'):
            src = conn.row_count(ents['medication']['source_table'])
            tgt = _one(db, 'SELECT count(DISTINCT record_id) FROM medication')
            out.append(_check('row_counts.medication', src == tgt, source_rows=src, target_records=tgt,
                              target_rows_with_class_expansion=_one(db, 'SELECT count(*) FROM medication')))
        keys = _one(db, 'SELECT count(DISTINCT coalesce(encounter_id, record_id)) FROM visit_event')
        out.append(_check('row_counts.visit_entities', keys == _one(db, 'SELECT count(*) FROM visit'), distinct_keys=keys,
                          visits=_one(db, 'SELECT count(*) FROM visit'), versions_collapsed=_one(db, 'SELECT count(*) FROM visit_event') - keys))
        keys = _one(db, 'SELECT count(DISTINCT claim_id) FROM claim_event WHERE claim_id IS NOT NULL')
        out.append(_check('row_counts.claim_entities', keys == _one(db, 'SELECT count(*) FROM claim'), distinct_claim_ids=keys,
                          claims=_one(db, 'SELECT count(*) FROM claim'),
                          excluded_rows_without_claim_id=_one(db, 'SELECT count(*) FROM claim_event WHERE claim_id IS NULL')))
        # 1b. Completeness: every contract-required field of a built entity is mapped and populated.
        from . import contract as contract_mod
        targets = contract_mod.targets(model or contract_mod.load_model())
        for entity, table in {**grain_tables, 'medication': 'medication', 'coverage': None}.items():
            e = ents.get(entity) or {}
            if not e.get('source_table') or entity not in targets: continue
            for field, spec in targets[entity]['fields'].items():
                if spec['role'] != 'mapped' or not spec['required']: continue
                f = (e.get('fields') or {}).get(field) or {}
                if not (f.get('accept') and f.get('source')):
                    out.append(_check(f'completeness.{entity}.{field}', False, reason='required field not mapped/accepted'))
                    continue
                if table is None: continue
                expr = {('claim_line', 'service_code'): 'coalesce(cpt, hcpcs, local_charge_code)'}.get((entity, field), field)
                n, filled = db.execute(f'SELECT count(*), count({expr}) FROM {table}').fetchone()
                share = filled / n if n else 1.0
                out.append(_check(f'completeness.{entity}.{field}', share >= THRESHOLDS['required_filled'], rows=n, filled=filled, share=round(share, 4)))
        # 2. Keys.
        for table, key in [('patient', 'patient_id'), ('visit', 'encounter_id'), ('claim', 'claim_id'), ('clinician', 'clinician_id'),
                           ('organization', 'organization_id'), ('visit_event', 'record_id'), ('condition', 'record_id'),
                           ('measurement', 'record_id'), ('claim_event', 'record_id'), ('claim_line', 'record_id')]:
            n, d, nulls = db.execute(f'SELECT count({key}), count(DISTINCT {key}), count(*) - count({key}) FROM {table}').fetchone()
            ok = n == d and (nulls == 0 or (table == 'visit' and key == 'encounter_id'))
            out.append(_check(f'key_uniqueness.{table}.{key}', ok, values=n, distinct=d, nulls=nulls))
        # 3. Referential integrity.
        for table in ('visit', 'condition', 'measurement', 'medication', 'claim', 'claim_line'):
            n, bad = db.execute(f'SELECT count(*), sum(patient_id IS NULL OR patient_id NOT IN (SELECT patient_id FROM patient)) FROM {table}').fetchone()
            rate = 1 - (bad or 0) / n if n else 1.0
            out.append(_check(f'referential.{table}.patient_id', rate >= THRESHOLDS['referential_patient'], rows=n, unresolved=bad or 0, rate=round(rate, 4)))
        for table, col, ref, refcol in [('claim_line', 'claim_id', 'claim', 'claim_id'), ('visit', 'organization_id', 'organization', 'organization_id'),
                                        ('visit', 'attending_clinician_id', 'clinician', 'clinician_id'),
                                        ('clinician', 'organization_id', 'organization', 'organization_id'),
                                        ('claim', 'service_organization_id', 'organization', 'organization_id')]:
            n, bad = db.execute(f'SELECT count({col}), sum({col} IS NOT NULL AND {col} NOT IN (SELECT {refcol} FROM {ref})) FROM {table}').fetchone()
            rate = 1 - (bad or 0) / n if n else 1.0
            out.append(_check(f'referential.{table}.{col}', rate >= THRESHOLDS['referential_other'], non_null=n, unresolved=bad or 0, rate=round(rate, 4)))
        # 4. Code validity.
        for label, sql, fn in [('condition.icd10', 'SELECT icd10 FROM condition WHERE icd10 IS NOT NULL', codes.icd10_valid),
                               ('measurement.loinc', 'SELECT loinc FROM measurement WHERE loinc IS NOT NULL', codes.loinc_valid),
                               ('clinician.npi', 'SELECT npi FROM clinician WHERE npi IS NOT NULL', codes.npi_valid),
                               ('organization.npi', 'SELECT npi FROM organization WHERE npi IS NOT NULL', codes.npi_valid),
                               ('claim_line.cpt', 'SELECT cpt FROM claim_line WHERE cpt IS NOT NULL', lambda c: bool(codes.CPT.match(c))),
                               ('claim_line.hcpcs', 'SELECT hcpcs FROM claim_line WHERE hcpcs IS NOT NULL', lambda c: bool(codes.HCPCS.match(c))),
                               ('medication.rxnorm', 'SELECT rxnorm FROM medication WHERE rxnorm IS NOT NULL', lambda c: bool(codes.RXNORM.match(c)))]:
            vals = [r[0] for r in db.execute(sql)]
            ok = sum(1 for v in vals if fn(str(v)))
            rate = ok / len(vals) if vals else None
            out.append(_check(f'code_validity.{label}', rate is None or rate >= THRESHOLDS['code_validity'], values=len(vals),
                              valid=ok, rate=None if rate is None else round(rate, 4),
                              invalid_examples=sorted({str(v) for v in vals if not fn(str(v))})[:5]))
        # 5. Dates.
        as_of = as_of or max(filter(None, [_one(db, f'SELECT max(available_date) FROM {t}') for t in ('visit_event', 'measurement', 'claim_event')]), default='2099-12-31')
        upper = str(int(as_of[:4]) + 1) + as_of[4:]
        for table, cols in [('visit_event', ('event_date', 'end_date', 'available_date')), ('condition', ('event_date', 'recorded_date', 'available_date')),
                            ('measurement', ('event_date', 'available_date')), ('medication', ('event_date', 'available_date')),
                            ('claim_event', ('service_date', 'event_date', 'available_date')), ('claim_line', ('service_date',)),
                            ('patient', ('birth_date',))]:
            for col in cols:
                vals = [r[0] for r in db.execute(f'SELECT {col} FROM {table} WHERE {col} IS NOT NULL')]
                bad = [v for v in vals if codes.parse_date(v) != v or not ('1900-01-01' <= v <= upper)]
                share = len(bad) / len(vals) if vals else 0.0
                out.append(_check(f'date_sanity.{table}.{col}', share <= THRESHOLDS['date_bad_share'], values=len(vals), bad=len(bad),
                                  examples=sorted(set(map(str, bad)))[:3], upper_bound=upper))
        n = _one(db, 'SELECT count(*) FROM visit WHERE end_date IS NOT NULL AND event_date IS NOT NULL AND end_date < event_date')
        out.append(_check('date_sanity.visit.end_before_start', n == 0, rows=n))
        n = _one(db, 'SELECT count(*) FROM claim_event WHERE service_date > available_date')
        out.append(_check('date_sanity.claim.service_after_receipt', n == 0, rows=n))
        # 6. Money.
        for entity, field, table in [('claim', 'billed_cents', 'claim_event'), ('claim', 'allowed_cents', 'claim_event'),
                                     ('claim', 'payer_paid_cents', 'claim_event'), ('claim', 'patient_responsibility_cents', 'claim_event'),
                                     ('claim_line', 'charge_cents', 'claim_line')]:
            e = (ents.get(entity) or {})
            f = (e.get('fields') or {}).get(field) or {}
            if not (f.get('accept') and f.get('source')): continue
            vals = [r[0] for r in conn.rows(e['source_table'], [f['source']]) if r[0] is not None and codes.is_number(r[0])]
            dollars = source_is_dollars(vals, f)
            expected = round(sum(float(v) for v in vals) * (100 if dollars else 1))
            got = _one(db, f'SELECT coalesce(sum({field}),0) FROM {table}')
            tol = 0.5 * len(vals) + 1
            out.append(_check(f'money.{entity}.{field}', abs(got - expected) <= tol, source_scale='dollars' if dollars else 'cents',
                              source_total=round(sum(float(v) for v in vals), 2), expected_cents=expected, target_cents=got))
        charges = [r[0] for r in db.execute('SELECT billed_cents FROM claim_event WHERE billed_cents > 0')]
        if charges:
            med = statistics.median(charges)
            out.append(_check('money.claim.billed_scale_plausible', 5_000 <= med <= 10_000_000, median_billed_cents=med,
                              plausible_range_cents=[5_000, 10_000_000], basis='heuristic: median claim charge between $50 and $100,000'))
    finally:
        db.close()
    return {'passed': all(c['passed'] for c in out if c['severity'] == 'error'),
            'failed': [c['check'] for c in out if not c['passed'] and c['severity'] == 'error'],
            'warnings': [c['check'] for c in out if not c['passed'] and c['severity'] == 'warning'], 'checks': out}


def run_sql(db_path, items):
    db = sqlite3.connect(f'file:{db_path}?mode=ro', uri=True)
    try:
        res = {}
        for item in items:
            name, sql = (item['id'], item['sql']) if isinstance(item, dict) else item
            try:
                res[name] = db.execute(sql).fetchone()[0]
            except sqlite3.Error as e:
                res[name] = f'error: {e}'
        return res
    finally:
        db.close()


def native_extract(native_semantic, patient_ids, out_path):
    """Native semantic-layer rows for the sampled patients (reference side of the round trip). Reads native read-only."""
    out_path = Path(out_path)
    out_path.unlink(missing_ok=True)
    out = sqlite3.connect(f'file:{out_path}', uri=True)
    out.execute('ATTACH DATABASE ? AS n', (f'file:{native_semantic}?mode=ro',))
    out.execute('CREATE TEMP TABLE sample(patient_id TEXT PRIMARY KEY)')
    out.executemany('INSERT INTO sample VALUES (?)', [(p,) for p in patient_ids])
    for table, where in [('patient', ''), ('visit', ''), ('condition', "AND source_table = 'conditions'"),
                         ('measurement', "AND source_table = 'labs'"), ('measurement_latest', ''), ('medication', ''),
                         ('claim', ''), ('claim_line', '')]:
        out.execute(f'CREATE TABLE {table} AS SELECT * FROM n.{table} WHERE patient_id IN (SELECT patient_id FROM sample) {where}')
    for table in ('clinician', 'organization'):
        out.execute(f'CREATE TABLE {table} AS SELECT * FROM n.{table}')
    out.commit()
    out.execute('DETACH DATABASE n')
    out.close()
    return out_path


def reconcile(onboarded, native, tolerance=0.01):
    a, b = run_sql(onboarded, AGGREGATES), run_sql(native, AGGREGATES)
    rows = []
    for name, _ in AGGREGATES:
        x, y = a[name], b[name]
        if isinstance(x, (int, float)) and isinstance(y, (int, float)):
            diff = (x - y) / y if y else (0.0 if x == y else 1.0)
            rows.append({'aggregate': name, 'onboarded': x, 'native': y, 'rel_diff': round(diff, 6), 'within_tolerance': abs(diff) <= tolerance})
        else:
            rows.append({'aggregate': name, 'onboarded': x, 'native': y, 'rel_diff': None, 'within_tolerance': x == y})
    qa, qb = run_sql(onboarded, ACCEPTANCE), run_sql(native, ACCEPTANCE)
    questions = [{'id': q['id'], 'mirrors': q['mirrors'], 'question': q['question'], 'onboarded': qa[q['id']], 'native': qb[q['id']],
                  'match': qa[q['id']] == qb[q['id']] or (isinstance(qa[q['id']], (int, float)) and isinstance(qb[q['id']], (int, float))
                                                       and abs(qa[q['id']] - qb[q['id']]) <= tolerance * max(1, abs(qb[q['id']])))}
                 for q in ACCEPTANCE]
    return {'tolerance': tolerance, 'aggregates': rows, 'all_within_tolerance': all(r['within_tolerance'] for r in rows),
            'acceptance': questions, 'acceptance_matched': sum(q['match'] for q in questions)}
