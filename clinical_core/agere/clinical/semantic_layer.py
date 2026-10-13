"""Semantic layer: typed, concept-normalised tables materialised from the received store.

Built once per source digest into `semantic.sqlite3` beside the store. Planners
query it with ordinary read-only SQL instead of exploring raw JSON, so one
generated query replaces many exploratory tool calls. Every row keeps its
source record_id; concept columns come from the concept library (all coding
systems and label variants), and measurement values carry both the received
value/unit and the canonical unit conversion.
"""
from collections import defaultdict
from pathlib import Path
import json, re, sqlite3, time

from . import aggregate, concepts, source_profile

VERSION = 'semantic-layer/9'

# Source contract: streams whose records are successive snapshots of one business entity (key field).
# Other repeated ids (payments per account, lines per claim, fills per prescription) are separate events, not versions.
VERSIONED_STREAMS = {'patient_billing_accounts': 'account_id', 'medical_claims': 'claim_id', 'pharmacy_claim_transactions': 'claim_id',
                     'appointments': 'appointment_id', 'provider_remittance_advice': 'claim_id', 'advance_care_document_status': 'document_id'}

DDL = """
CREATE TABLE meta(key TEXT PRIMARY KEY, value TEXT);
CREATE TABLE patient(patient_id TEXT PRIMARY KEY, age_reported INTEGER, sex_reported TEXT, coverage_type TEXT,
  coverage_status TEXT, pcp_clinician_id TEXT, pcp_practice_id TEXT);
CREATE TABLE record(record_id TEXT PRIMARY KEY, patient_id TEXT, stream TEXT, event_date TEXT, recorded_date TEXT,
  available_date TEXT, organization_id TEXT, payload TEXT);
CREATE TABLE condition(record_id TEXT, patient_id TEXT, source_table TEXT, concept TEXT, label TEXT, icd10 TEXT,
  snomed TEXT, onset TEXT, event_date TEXT, recorded_date TEXT, available_date TEXT, organization_id TEXT);
CREATE TABLE measurement(record_id TEXT, patient_id TEXT, source_table TEXT, concept TEXT, name TEXT, value REAL,
  unit TEXT, value_canonical REAL, unit_canonical TEXT, result_status TEXT, event_date TEXT, recorded_date TEXT,
  available_date TEXT, organization_id TEXT);
CREATE TABLE encounter(record_id TEXT, patient_id TEXT, source_table TEXT, encounter_id TEXT, type TEXT,
  type_concept TEXT, status TEXT, event_date TEXT, end_date TEXT, available_date TEXT, organization_id TEXT,
  attending_clinician_id TEXT, disposition TEXT);
CREATE TABLE visit(encounter_id TEXT, patient_id TEXT, type TEXT, type_concept TEXT, status TEXT, event_date TEXT,
  end_date TEXT, available_date TEXT, organization_id TEXT, attending_clinician_id TEXT, disposition TEXT, versions INTEGER);
CREATE TABLE stay(patient_id TEXT, admit_date TEXT, discharge_date TEXT, type TEXT, organization_id TEXT,
  readmitted_30d INTEGER, days_to_readmission INTEGER, discharge_known INTEGER);
CREATE TABLE link(record_id TEXT, stream TEXT, field TEXT, target_record_id TEXT);
CREATE TABLE concept_patient(concept TEXT, kind TEXT, patient_id TEXT, records INTEGER, first_date TEXT, last_date TEXT,
  PRIMARY KEY(concept, patient_id));
CREATE TABLE claim_adjustment(claim_id TEXT, record_id TEXT, patient_id TEXT, adjudication_status TEXT, group_code TEXT,
  reason_code TEXT, category TEXT, amount_cents INTEGER, current_version INTEGER);
CREATE TABLE medication(record_id TEXT, patient_id TEXT, source_stream TEXT, evidence TEXT, drug_name TEXT, drug_class TEXT,
  status TEXT, event_date TEXT, available_date TEXT);
CREATE TABLE measurement_latest(patient_id TEXT, concept TEXT, event_date TEXT, value_canonical REAL, unit_canonical TEXT,
  same_day_values INTEGER, same_day_min REAL, record_id TEXT, PRIMARY KEY(patient_id, concept));
CREATE TABLE current_record(stream TEXT, key_field TEXT, business_id TEXT, record_id TEXT, patient_id TEXT, versions INTEGER,
  first_available_date TEXT, available_date TEXT, event_date TEXT, payload TEXT, PRIMARY KEY(stream, business_id));
CREATE TABLE social_need(patient_id TEXT, need TEXT, positive INTEGER, instrument TEXT, source_stream TEXT, source_field TEXT,
  event_date TEXT, record_id TEXT);
CREATE TEMP VIEW v_claim_rows AS SELECT json_extract(payload,'$.claim_id') AS claim_id, patient_id,
  json_extract(payload,'$.source_system') AS payer, json_extract(payload,'$.service_date') AS service_date,
  CAST(json_extract(payload,'$.benefit_year') AS TEXT) AS benefit_year, json_extract(payload,'$.status') AS status,
  json_extract(payload,'$.payer_paid_cents') AS payer_paid_cents, json_extract(payload,'$.billed_cents') AS billed_cents,
  json_extract(payload,'$.allowed_cents') AS allowed_cents, available_date, json_extract(payload,'$.available_time') AS available_time,
  json_extract(payload,'$.service_organization_id') AS service_organization_id, record_id
  FROM record WHERE stream = 'medical_claims' AND json_extract(payload,'$.claim_id') IS NOT NULL;
CREATE TABLE appointment(appointment_id TEXT PRIMARY KEY, patient_id TEXT, scheduled_date TEXT, booked_date TEXT, final_status TEXT,
  ever_no_show INTEGER, ever_cancelled INTEGER, ever_rescheduled INTEGER, kept INTEGER, organization_id TEXT, clinician_id TEXT,
  purpose TEXT, versions INTEGER);
CREATE TABLE claim(claim_id TEXT PRIMARY KEY, patient_id TEXT, payer TEXT, service_date TEXT, benefit_year TEXT, first_status TEXT,
  final_status TEXT, ever_denied INTEGER, denied_then_paid INTEGER, net_payer_paid_cents INTEGER, billed_cents INTEGER,
  allowed_cents INTEGER, first_available_date TEXT, last_available_date TEXT, service_organization_id TEXT, versions INTEGER);
"""
INDEXES = """
CREATE INDEX record_stream ON record(stream, patient_id);
CREATE INDEX condition_concept ON condition(concept, patient_id);
CREATE INDEX measurement_concept ON measurement(concept, patient_id);
CREATE INDEX encounter_concept ON encounter(type_concept, patient_id);
CREATE INDEX concept_patient_patient ON concept_patient(patient_id);
CREATE INDEX link_target ON link(target_record_id, stream);
CREATE INDEX visit_concept ON visit(type_concept, patient_id);
CREATE INDEX link_source ON link(record_id);
"""

SCHEMA_DOC = {
    'patient': 'One row per roster patient. age_reported/sex_reported as received; coverage_type/status from latest coverage record; '
               'pcp_* = latest assigned primary-care relationship.',
    'condition': 'One row per (condition record, matching library concept); concept NULL when no library concept matches. '
                 'Sources: conditions, condition_assessments, problem_lists. label = received description.',
    'measurement': 'Labs and vitals, one row per (record, concept); concept NULL when unmapped. value/unit as received; '
                   'value_canonical in unit_canonical when the unit converts (else NULL). Cancelled results have value NULL.',
    'encounter': 'Encounter records plus ADT notifications (source_table adt, type = ADT event). type_concept = library encounter concept.',
    'visit': 'One row per encounter (encounter records repeat once per status version; use visit, not encounter, to count visits). '
             'Latest version wins; versions = number of received versions.',
    'stay': 'Distinct hospital stays (inpatient/observation encounters collapsed by admit day and overlap) with 30-day readmission flag. '
            'discharge_date and los_days are NULL when no discharge time was received (discharge_known = 0); never treat those as 0 days.',
    'concept_patient': 'Patients having each library concept (condition, measurement, encounter), with record counts and first/last dates.',
    'link': 'Every record-to-record reference (fields ending _record_id/_record_ids): record_id/stream of the referring record, '
            'field name, target_record_id. Indexed on target_record_id - use it for linkage questions (e.g. orders without dispenses).',
    'record': 'Every received record (all streams). Use json_extract(payload, \'$.field\') for fields of streams not modelled above.',
    'appointment': 'One row per appointment_id (appointments arrive as a booked record plus later status records). scheduled_date from '
                   'the booking; final_status = latest status received; ever_* flags over all versions; kept = arrived or fulfilled.',
    'claim_adjustment': 'One row per adjustment line on each medical claim adjudication record: group_code (CO/PR/OA), reason_code '
                        '(X12 CARC), category (deductible, coinsurance, copay, contractual_adjustment, else group-code), amount_cents; '
                        'current_version = 1 on the claim\'s latest version.',
    'medication': 'Every medication mention, one row per (record, drug_class): source_stream and evidence (dispensed, claim, ordered, '
                  'listed, administered, reported, pharmacy_history), drug_name as received, status. drug_class from the active '
                  'ingredient (statin, beta_blocker, hf_evidence_based_beta_blocker, ace_inhibitor, arb, arni, mra, sglt2_inhibitor, '
                  'glp1_agonist, metformin, insulin, anticoagulant, opioid, ...); NULL when unclassified.',
    'measurement_latest': 'Latest DATED value per patient and measurement concept (undated rows never count as latest); '
                          'same_day_values/same_day_min for same-date ties (HEDIS uses the lowest).',
    'current_record': 'Latest version of each business entity in versioned (snapshot) streams: stream, key_field, business_id, '
                      'versions, payload of the latest version. Use it for current state (current balance, current claim status).',
    'social_need': 'Social-needs screening answers normalised across instruments: one row per (screen record, need) with positive 0/1. '
                   'need = food | transport | cost_of_care. transport positive = transport_difficulty_reported or transport unreliable '
                   '(family_ride/reliable = negative); cost_of_care = difficulty paying for care (social screen) or medical bills '
                   '(financial screen). Screened = has a row for that need.',
    'claim': 'One row per medical claim_id (adjudications arrive as successive records: denied, paid, reversed, re-paid). first/final '
             'status by arrival order; denied_then_paid = a denial later followed by payment; net_payer_paid_cents = paid minus reversed. '
             'Legacy rows without claim_id are excluded.',
}


def _date(r): return (r.get('event_date') or '')[:10] or None


MED_SOURCES = [  # stream, name field, status field, evidence label
    ('pharmacy_dispenses', 'medication_name', 'status', 'dispensed'), ('pharmacy_claims', 'medication_name', 'transaction_status', 'claim'),
    ('medication_orders', 'medication_name', 'status', 'ordered'), ('medication_lists', 'name', None, 'listed'),
    ('medication_administrations', 'medication_name', 'administration_status', 'administered'),
    ('reported_medication_entries', 'medication_name_reported', 'use_statement', 'reported'),
    ('pharmacy_history', 'medication_name', 'reported_status', 'pharmacy_history')]


# X12 claim adjustment reason codes most used in patient-responsibility questions.
CARC = {('PR', '1'): 'deductible', ('PR', '2'): 'coinsurance', ('PR', '3'): 'copay', ('CO', '45'): 'contractual_adjustment'}


def _revenue_cycle(out):
    """Claim timing and adjustment primitives: first original submission, first payer cash, net cash, and adjustment lines."""
    for col in ('first_submitted_date TEXT', 'first_payer_cash_date TEXT', 'net_payer_cash_cents INTEGER'):
        out.execute(f'ALTER TABLE claim ADD COLUMN {col}')
    out.execute("""CREATE TEMP TABLE subs AS SELECT json_extract(payload,'$.claim_id') AS claim_id, min(event_date) AS first_sub
                     FROM record WHERE stream = 'medical_claim_submissions'
                     AND coalesce(json_extract(payload,'$.submission_kind'),'original') = 'original' GROUP BY 1""")
    out.execute('CREATE INDEX temp.subs_claim ON subs(claim_id)')
    out.execute('UPDATE claim SET first_submitted_date = (SELECT first_sub FROM subs WHERE subs.claim_id = claim.claim_id)')
    out.execute("""CREATE TEMP TABLE cash AS SELECT json_extract(payload,'$.claim_id') AS claim_id,
                     min(CASE WHEN json_extract(payload,'$.allocated_cents') > 0 THEN event_date END) AS first_cash,
                     sum(json_extract(payload,'$.allocated_cents')) AS net_cash
                     FROM record WHERE stream = 'payer_cash_allocations' GROUP BY 1""")
    out.execute('CREATE INDEX temp.cash_claim ON cash(claim_id)')
    out.execute('UPDATE claim SET first_payer_cash_date = (SELECT first_cash FROM cash WHERE cash.claim_id = claim.claim_id), '
                'net_payer_cash_cents = (SELECT net_cash FROM cash WHERE cash.claim_id = claim.claim_id)')
    current = {rid for (rid,) in out.execute("SELECT record_id FROM current_record WHERE stream = 'medical_claims'")}
    rows = []
    for rid, pid, payload in out.execute("SELECT record_id, patient_id, payload FROM record WHERE stream = 'medical_claims'"):
        r = json.loads(payload)
        for a in (r.get('adjustments') or []):
            if not isinstance(a, dict): continue
            g, code = str(a.get('group') or ''), str(a.get('reason_code') or '')
            rows.append((r.get('claim_id'), rid, pid, r.get('status'), g, code, CARC.get((g, code), f'{g}-{code}'),
                         a.get('amount_cents'), int(rid in current)))
    out.executemany('INSERT INTO claim_adjustment VALUES (?,?,?,?,?,?,?,?,?)', rows)
    out.execute('CREATE INDEX claim_adjustment_claim ON claim_adjustment(claim_id)')


def _medications(out):
    """One row per (medication record, drug class) across every medication source; class from the ingredient."""
    from .drug_classes import classify
    rows = []
    for stream, name_f, status_f, evidence in MED_SOURCES:
        for rid, pid, payload, d, av in out.execute('SELECT record_id, patient_id, payload, event_date, available_date FROM record WHERE stream = ?', (stream,)):
            r = json.loads(payload)
            name = r.get(name_f)
            if not name: continue
            status = r.get(status_f) if status_f else None
            for c in classify(name) or [None]:
                rows.append((rid, pid, stream, evidence, name, c, status, d, av))
    out.executemany('INSERT INTO medication VALUES (?,?,?,?,?,?,?,?,?)', rows)
    out.executescript('CREATE INDEX medication_class ON medication(drug_class, patient_id); CREATE INDEX measurement_latest_concept ON measurement_latest(concept);')


def _derived(out):
    """Entity tables that resolve each stream's versioning, so queries count things, not records."""
    out.executescript("""
    INSERT INTO appointment
    SELECT appointment_id, max(patient_id),
      max(CASE WHEN status = 'booked' THEN scheduled_date END), min(CASE WHEN status = 'booked' THEN event_date END),
      max(CASE WHEN rn_last = 1 THEN status END),
      max(status = 'no_show'), max(status = 'cancelled'), max(status = 'rescheduled'), max(status IN ('arrived','fulfilled')),
      max(organization_id), max(clinician_id), max(purpose), count(*)
    FROM (SELECT json_extract(payload,'$.appointment_id') AS appointment_id, patient_id, json_extract(payload,'$.status') AS status,
                 coalesce(json_extract(payload,'$.scheduled_date'), event_date) AS scheduled_date, event_date, organization_id,
                 json_extract(payload,'$.clinician_id') AS clinician_id, json_extract(payload,'$.purpose') AS purpose,
                 row_number() OVER (PARTITION BY json_extract(payload,'$.appointment_id')
                                    ORDER BY available_date DESC, json_extract(payload,'$.available_time') DESC, record_id DESC) AS rn_last
          FROM record WHERE stream = 'appointments' AND json_extract(payload,'$.appointment_id') IS NOT NULL)
    GROUP BY appointment_id;
    INSERT INTO claim
    SELECT claim_id, max(patient_id), max(payer), max(service_date), max(benefit_year),
      max(CASE WHEN rn_first = 1 THEN status END), max(CASE WHEN rn_last = 1 THEN status END),
      max(status = 'denied'),
      coalesce(max(CASE WHEN status = 'paid' THEN ord END) > min(CASE WHEN status = 'denied' THEN ord END), 0),
      sum(CASE status WHEN 'paid' THEN coalesce(payer_paid_cents,0) WHEN 'reversed' THEN -abs(coalesce(payer_paid_cents,0)) ELSE 0 END),
      max(billed_cents), max(allowed_cents), min(available_date), max(available_date), max(service_organization_id), count(*)
    FROM (SELECT *, coalesce(available_date,'') || coalesce(available_time,'') || record_id AS ord,
                 row_number() OVER (PARTITION BY claim_id ORDER BY available_date, available_time, record_id) AS rn_first,
                 row_number() OVER (PARTITION BY claim_id ORDER BY available_date DESC, available_time DESC, record_id DESC) AS rn_last
          FROM v_claim_rows)
    GROUP BY claim_id;
    """)
    out.executescript("""
    INSERT INTO social_need SELECT patient_id, 'food', json_extract(payload,'$.food_difficulty_reported'), json_extract(payload,'$.instrument'),
      stream, 'food_difficulty_reported', event_date, record_id FROM record WHERE stream = 'social_screens'
      AND json_extract(payload,'$.food_difficulty_reported') IS NOT NULL;
    INSERT INTO social_need SELECT patient_id, 'transport',
      CASE WHEN json_extract(payload,'$.transport_difficulty_reported') = 1 OR json_extract(payload,'$.transport') = 'unreliable' THEN 1 ELSE 0 END,
      json_extract(payload,'$.instrument'), stream,
      CASE WHEN json_extract(payload,'$.transport_difficulty_reported') IS NOT NULL THEN 'transport_difficulty_reported' ELSE 'transport' END,
      event_date, record_id FROM record WHERE stream = 'social_screens'
      AND (json_extract(payload,'$.transport_difficulty_reported') IS NOT NULL OR json_extract(payload,'$.transport') IS NOT NULL);
    INSERT INTO social_need SELECT patient_id, 'cost_of_care', json_extract(payload,'$.difficulty_paying_for_care_reported'), json_extract(payload,'$.instrument'),
      stream, 'difficulty_paying_for_care_reported', event_date, record_id FROM record WHERE stream = 'social_screens'
      AND json_extract(payload,'$.difficulty_paying_for_care_reported') IS NOT NULL;
    INSERT INTO social_need SELECT patient_id, 'cost_of_care', json_extract(payload,'$.difficulty_paying_medical_bills'), 'financial_screen',
      stream, 'difficulty_paying_medical_bills', event_date, record_id FROM record WHERE stream = 'financial_screens'
      AND json_extract(payload,'$.difficulty_paying_medical_bills') IS NOT NULL;
    CREATE INDEX social_need_patient ON social_need(need, patient_id);
    """)
    for stream, key in VERSIONED_STREAMS.items():
        out.execute(f"""INSERT INTO current_record
            SELECT stream, '{key}', bid, record_id, patient_id, n, first_av, available_date, event_date, payload FROM (
              SELECT r.*, json_extract(r.payload,'$.{key}') AS bid, count(*) OVER w AS n, min(r.available_date) OVER w AS first_av,
                     row_number() OVER (PARTITION BY json_extract(r.payload,'$.{key}')
                                        ORDER BY r.available_date DESC, json_extract(r.payload,'$.available_time') DESC, r.record_id DESC) AS rn
              FROM record r WHERE r.stream = '{stream}' AND json_extract(r.payload,'$.{key}') IS NOT NULL
              WINDOW w AS (PARTITION BY json_extract(r.payload,'$.{key}'))) WHERE rn = 1""")
    out.execute('CREATE INDEX current_record_patient ON current_record(stream, patient_id)')
    _medications(out)
    _revenue_cycle(out)
    out.execute("""INSERT INTO measurement_latest
        SELECT patient_id, concept, event_date, value_canonical, unit_canonical, n, mn, record_id FROM (
          SELECT m.*, count(*) OVER d AS n, min(m.value_canonical) OVER d AS mn,
                 row_number() OVER (PARTITION BY m.patient_id, m.concept ORDER BY m.event_date DESC, m.available_date DESC, m.record_id DESC) AS rn
          FROM measurement m WHERE m.concept IS NOT NULL AND m.value_canonical IS NOT NULL AND m.event_date IS NOT NULL
          WINDOW d AS (PARTITION BY m.patient_id, m.concept, m.event_date)) WHERE rn = 1""")
    out.execute("ALTER TABLE visit ADD COLUMN in_person INTEGER")
    out.execute("ALTER TABLE visit ADD COLUMN is_practice INTEGER")
    out.execute("ALTER TABLE visit ADD COLUMN first_available_date TEXT")
    out.execute("""UPDATE visit SET in_person = CASE WHEN type_concept = 'telehealth_contact' OR lower(type) IN ('telephone','virtual','video','phone')
                     THEN 0 ELSE 1 END, is_practice = (organization_id LIKE '%PRACTICE%')""")
    out.execute("""CREATE TEMP TABLE first_arrival AS SELECT encounter_id, min(available_date) AS first_av FROM encounter
                     WHERE source_table = 'encounters' AND encounter_id IS NOT NULL GROUP BY 1""")
    out.execute('CREATE INDEX temp.first_arrival_enc ON first_arrival(encounter_id)')
    out.execute('UPDATE visit SET first_available_date = (SELECT first_av FROM first_arrival f WHERE f.encounter_id = visit.encounter_id)')
    out.execute("ALTER TABLE patient ADD COLUMN age_latest INTEGER")
    out.execute("""UPDATE patient SET age_latest = coalesce((SELECT CAST(json_extract(r.payload,'$.age_reported') AS INTEGER) FROM record r
                     WHERE r.stream = 'demographic_updates' AND r.patient_id = patient.patient_id
                       AND json_extract(r.payload,'$.age_reported') IS NOT NULL
                     ORDER BY r.event_date DESC, r.available_date DESC LIMIT 1), age_reported)""")
    out.execute("ALTER TABLE stay ADD COLUMN los_days INTEGER")
    out.execute("UPDATE stay SET los_days = CAST(julianday(discharge_date) - julianday(admit_date) AS INTEGER) WHERE discharge_date IS NOT NULL")
    out.executescript("CREATE INDEX appointment_patient ON appointment(patient_id); CREATE INDEX claim_patient ON claim(patient_id);")


def build(path):
    path = Path(path)
    started = time.perf_counter()
    profile = source_profile.load(path)
    resolved = {c: concepts.resolve(c, profile) for c in (*concepts.CONDITIONS, *concepts.MEASUREMENTS, *concepts.ENCOUNTERS)}
    cond_labels = defaultdict(set)   # (stream, field, value) -> concepts
    meas_names = defaultdict(set)
    enc_values = defaultdict(set)
    wide = defaultdict(set)          # vitals column -> concepts
    for c, r in resolved.items():
        for b in r['bindings']:
            if 'values' in b and r['kind'] == 'condition':
                for v in b['values']: cond_labels[(b['stream'], b['field'], v)].add(c)
            elif 'values' in b:
                for v in b['values']: enc_values[(b['stream'], b['field'], v)].add(c)
            elif 'names' in b:
                for v in b['names']: meas_names[(b['stream'], v)].add(c)
            else:
                wide[b['value_field']].add(c)
    stay_type = re.compile(aggregate.STAY_TYPES, re.I)
    target = path / 'semantic.sqlite3'
    tmp = path / 'semantic.sqlite3.tmp'
    tmp.unlink(missing_ok=True)
    out = sqlite3.connect(tmp)
    out.executescript(DDL)
    src = sqlite3.connect(f'file:{path / "stack.sqlite3"}?mode=ro', uri=True)
    try:
        people = {pid: json.loads(p) for pid, p in src.execute('SELECT id, payload FROM patients')}
        current, per_patient = None, []

        def flush(pid, rows):
            if pid is None: return
            emit(pid, rows)

        cp = defaultdict(lambda: [0, None, None])
        def note(concept, kind, pid, d):
            e = cp[(concept, kind, pid)]; e[0] += 1
            if d: e[1] = min(e[1] or d, d); e[2] = max(e[2] or d, d)

        def emit(pid, rows):
            for r in rows:
                st = r.get('source_table')
                rid = r.get('record_id')
                d = _date(r)
                for k, v in r.items():
                    if k.endswith('_record_id') and isinstance(v, str):
                        out.execute('INSERT INTO link VALUES (?,?,?,?)', (rid, st, k, v))
                    elif k.endswith('_record_ids') and isinstance(v, list):
                        out.executemany('INSERT INTO link VALUES (?,?,?,?)', [(rid, st, k, x) for x in v if isinstance(x, str)])
                out.execute('INSERT OR IGNORE INTO record VALUES (?,?,?,?,?,?,?,?)',
                            (rid, pid, st, d, (r.get('recorded_date') or '')[:10] or None, (r.get('available_date') or '')[:10] or None,
                             r.get('organization_id'), json.dumps(r)))
                if st in concepts.CONDITION_STREAMS:
                    spec = concepts.CONDITION_STREAMS[st]
                    found = set()
                    code = r.get(spec['code'])
                    if code: found |= cond_labels.get((st, spec['code'], code), set())
                    label = None
                    for f in spec['labels']:
                        if r.get(f): label = label or r.get(f); found |= cond_labels.get((st, f, r.get(f)), set())
                    for c in sorted(found) or [None]:
                        out.execute('INSERT INTO condition VALUES (?,?,?,?,?,?,?,?,?,?,?,?)',
                                    (rid, pid, st, c, label, r.get('icd10'), r.get('snomed'), r.get('onset'), d,
                                     (r.get('recorded_date') or '')[:10] or None, (r.get('available_date') or '')[:10] or None, r.get('organization_id')))
                        if c: note(c, 'condition', pid, d)
                if st in concepts.MEASUREMENT_STREAMS:
                    spec = concepts.MEASUREMENT_STREAMS[st]
                    name = r.get(spec['name']) or next((r.get(f) for f in spec['alt_names'] if r.get(f)), None)
                    series = []
                    if name is not None:
                        series.append((name, r.get(spec['value']), r.get(spec['unit']), meas_names.get((st, name), set())))
                    if st == 'vitals':
                        for col, cs in wide.items():
                            if r.get(col) is not None:
                                series.append((col, r.get(col), concepts.MEASUREMENTS[next(iter(cs))]['unit'], cs))
                    for name, value, unit, cs in series:
                        value = value if type(value) in (int, float) else None
                        for c in sorted(cs) or [None]:
                            canon, cunit = None, None
                            if c:
                                factor = concepts.MEASUREMENTS[c]['units'].get(unit)
                                cunit = concepts.MEASUREMENTS[c]['unit']
                                canon = value * factor if value is not None and factor is not None else None
                                note(c, 'measurement', pid, d)
                            out.execute('INSERT INTO measurement VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)',
                                        (rid, pid, st, c, name, value, unit, canon, cunit, r.get('result_status'), d,
                                         (r.get('recorded_date') or '')[:10] or None, (r.get('available_date') or '')[:10] or None, r.get('organization_id')))
                if st in ('encounters', 'adt'):
                    field = 'type' if st == 'encounters' else 'event'
                    cs = set()
                    for f in ('type', 'event', 'patient_class'):
                        if r.get(f) is not None: cs |= enc_values.get((st, f, r.get(f)), set())
                    for c in sorted(cs) or [None]:
                        out.execute('INSERT INTO encounter VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)',
                                    (rid, pid, st, r.get('encounter_id'), r.get(field), c, r.get('status'), d,
                                     (r.get('end_time') or '')[:10] or None, (r.get('available_date') or '')[:10] or None,
                                     r.get('organization_id'), r.get('attending_clinician_id'), r.get('disposition')))
                        if c: note(c, 'encounter', pid, d)
            for s in aggregate._stays(rows, {'start': '', 'end': ''}, '9999-12-31'):
                out.execute('INSERT INTO stay VALUES (?,?,?,?,?,?,?,?)', (pid, s['admit'], s['discharge'] if s['discharge_known'] else None,
                                                                          s['type'], s['organization'], int(s['readmitted']),
                                                                          s['days_to_readmission'], int(s['discharge_known'])))
            # Patient attributes (same rules as aggregate).
            person = people.get(pid, {})
            pcp = [r for r in rows if r.get('source_table') == 'care_team_relationships' and r.get('role') in aggregate.PCP_ROLES
                   and r.get('status') in (None, 'assigned')]
            pcp = max(pcp, key=lambda r: (r.get('effective_start') or r.get('event_date') or '')) if pcp else {}
            cov = [r for r in rows if r.get('source_table') == 'coverage']
            cov = max(cov, key=lambda r: r.get('event_date') or '') if cov else {}
            out.execute('INSERT OR REPLACE INTO patient VALUES (?,?,?,?,?,?,?)',
                        (pid, person.get('age_reported'), person.get('sex_reported'), cov.get('coverage_type'), cov.get('status'),
                         pcp.get('clinician_id'), pcp.get('organization_id')))

        for pid, payload in src.execute('SELECT patient, payload FROM records ORDER BY patient'):
            if pid != current:
                flush(current, per_patient); current, per_patient = pid, []
            per_patient.append(json.loads(payload))
        flush(current, per_patient)
        for pid, person in people.items():  # roster patients without records
            out.execute('INSERT OR IGNORE INTO patient(patient_id, age_reported, sex_reported) VALUES (?,?,?)',
                        (pid, person.get('age_reported'), person.get('sex_reported')))
        out.execute("""INSERT INTO visit SELECT encounter_id, patient_id, type, type_concept, status, event_date, end_date, available_date,
            organization_id, attending_clinician_id, disposition, n FROM (
              SELECT e.*, count(*) OVER (PARTITION BY coalesce(e.encounter_id, e.record_id)) AS n,
                     row_number() OVER (PARTITION BY coalesce(e.encounter_id, e.record_id)
                                        ORDER BY (e.status = 'completed') DESC, e.available_date DESC, e.record_id DESC) AS rn
              FROM encounter e WHERE e.source_table = 'encounters') WHERE rn = 1""")
        out.executemany('INSERT INTO concept_patient VALUES (?,?,?,?,?,?)',
                        [(c, k, pid, n, f, l) for (c, k, pid), (n, f, l) in cp.items()])
        out.executescript(INDEXES)
        out.execute("CREATE INDEX record_patient_stream ON record(patient_id, stream)")
        _derived(out)  # after indexes: derived entities read the record table by stream and patient
        meta = json.loads((path / 'run.json').read_text())
        out.executemany('INSERT INTO meta VALUES (?,?)', [('version', VERSION), ('source_digest', meta['source_digest']),
                                                          ('as_of', meta['snapshot']['as_of'])])
        out.commit()
    finally:
        src.close(); out.close()
    tmp.replace(target)
    return {'path': str(target), 'elapsed_seconds': round(time.perf_counter() - started, 1)}


def ensure(path):
    path = Path(path)
    target = path / 'semantic.sqlite3'
    meta = json.loads((path / 'run.json').read_text())
    if target.exists():
        db = sqlite3.connect(f'file:{target}?mode=ro', uri=True)
        try:
            row = dict(db.execute('SELECT key, value FROM meta').fetchall())
        finally:
            db.close()
        if row.get('version') == VERSION and row.get('source_digest') == meta['source_digest']:
            return target
    build(path)
    return target


def describe(path):
    """Schema + vocabulary for planners: table columns, meanings, concept ids."""
    target = ensure(path)
    db = sqlite3.connect(f'file:{target}?mode=ro', uri=True)
    try:
        tables = {}
        for (name,) in db.execute("SELECT name FROM sqlite_master WHERE type='table' AND name <> 'meta'"):
            cols = [r[1] for r in db.execute(f'PRAGMA table_info({name})')]
            tables[name] = {'columns': cols, 'meaning': SCHEMA_DOC.get(name, '')}
        streams = [r[0] for r in db.execute('SELECT DISTINCT stream FROM record ORDER BY stream')]
    finally:
        db.close()
    return {'tables': tables, 'record_streams': streams, 'concepts': concepts.catalog()}
