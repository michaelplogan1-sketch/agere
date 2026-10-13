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

VERSION = 'semantic-layer/5'

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
    'claim': 'One row per medical claim_id (adjudications arrive as successive records: denied, paid, reversed, re-paid). first/final '
             'status by arrival order; denied_then_paid = a denial later followed by payment; net_payer_paid_cents = paid minus reversed. '
             'Legacy rows without claim_id are excluded.',
}


def _date(r): return (r.get('event_date') or '')[:10] or None


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
