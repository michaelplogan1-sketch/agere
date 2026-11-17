"""Read-only access to the semantic layer, and the one place SQL is executed.

A customer's warehouse rarely has this layer's tables. Two ways to run on one (AGERE_TIERED_DB names its SQLite file):
  * mapped (AGERE_TIERED_MAPPING names a mapping file): each semantic-layer table is a view over the customer's own tables,
    created on every connection, so every recipe, saved recipe and governed definition runs unchanged. The mapping declares
    the semantic-layer version it presents; saved recipes verified on that version apply.
  * foreign (AGERE_TIERED_FOREIGN=on, no mapping): the customer's tables are shown as they are. Recipes and
    saved recipes do not apply; the frontier SQL writer works from that schema and its own vocabulary.
"""
import json
import collections
import contextvars
import os
import re
import sqlite3
import threading
import time
from pathlib import Path

DEFAULT_DB = 'artifacts/agere-full-stack-rerun-2026-10-30/focused/runtime/stack/semantic.sqlite3'
# Tables a recipe may read. `document` is a catalog view over notes, readable scans and exchange documents; document
# text itself is never selectable by a recipe: it is fetched by texts() for the reading tier only.
TABLES = ('document', 'note_fact', 'note_therapy_change', 'therapy_barrier', 'utilization_event', 'stay', 'stay_episode', 'appointment', 'visit', 'fill',
          'hospital_visit', 'payer_claim', 'claim', 'patient', 'concept_patient', 'member_month', 'measurement', 'measurement_latest', 'immunization', 'death',
          'organization', 'clinician', 'enrollment_span', 'condition', 'encounter', 'prescription', 'allergy_entry', 'smoking_status', 'social_need',
          'wellness_visit', 'cancer_screening', 'medication_administration', 'encounter_location', 'encounter_diagnosis', 'procedure_event', 'risk_score', 'patient_hcc', 'aco_contract', 'measure_readiness', 'measure_requirement', 'own_organization')
# Governed views: definitions the semantic layer computes once, so a query reads them instead of re-deriving them. Each is
# created on every connection over the (possibly mapped or cohort-restricted) tables it reads.
GOVERNED = ('stay_episode', 'hospital_visit', 'risk_score', 'patient_hcc', 'aco_contract', 'measure_readiness', 'measure_requirement', 'measure_alias', 'own_organization')   # the last three: aco.py
SECONDS = 20.0


# A database for this request only (fhir_live: one patient's layer), so concurrent requests never see each other's data.
DB_OVERRIDE = contextvars.ContextVar('agere_tiered_db', default=None)


def path():
    return Path(DB_OVERRIDE.get() or os.environ.get('AGERE_TIERED_DB', DEFAULT_DB))


# Every cache of what a warehouse holds (its years, vocabularies, columns, readiness, as-of date ...) lives here, keyed by the
# warehouse in use: its file (path, modification time, size), the mapping file and the source mode. A refreshed warehouse, a
# mapped one or a live one-patient database (fhir_live) gets its own entries; the 16 most recent warehouses are kept.
_SCOPED = collections.OrderedDict()
MAX_WAREHOUSES = 16
_scope_lock = threading.Lock()


def _stamp(p):
    try:
        st = Path(p).stat()
        return (st.st_mtime_ns, st.st_size)
    except OSError:
        return None


def warehouse_key():
    p, m = path(), os.environ.get('AGERE_TIERED_MAPPING')
    return (str(p), _stamp(p), m, _stamp(m) if m else None, os.environ.get('AGERE_TIERED_FOREIGN', 'off'))


def scoped(name):
    """-> the dict named `name` for the warehouse in use (created empty on first use)."""
    k = warehouse_key()
    with _scope_lock:
        bucket = _SCOPED.get(k)
        if bucket is None:
            bucket = _SCOPED[k] = {}
            while len(_SCOPED) > MAX_WAREHOUSES: _SCOPED.popitem(last=False)
        else:
            _SCOPED.move_to_end(k)
        return bucket.setdefault(name, {})


def clear_caches():
    """Forget everything cached about every warehouse (tests, or after a warehouse is replaced in place)."""
    with _scope_lock:
        _SCOPED.clear()


def mapping():
    """The mapping file in force, or None. {'semantic_layer': version, 'as_of': date, 'views': {table: SELECT over main.<table>}}"""
    name = os.environ.get('AGERE_TIERED_MAPPING')
    if not name: return None
    cache = scoped('mapping')   # keyed with the mapping file's own timestamp: an edited mapping is read again
    if 'm' not in cache:
        m = json.loads(Path(name).read_text())
        if not isinstance(m.get('views'), dict) or not m.get('semantic_layer'): raise ValueError(f'Mapping {name} needs views and semantic_layer')
        cache['m'] = m
    return cache['m']




def foreign():
    """True when the warehouse is declared not to be this semantic layer (AGERE_TIERED_FOREIGN=on) and no mapping presents it
    as one. Declared, not guessed: a small or partial copy of this layer is still this layer."""
    return not mapping() and os.environ.get('AGERE_TIERED_FOREIGN', 'off') == 'on'


def tables(db=None):
    """Tables a recipe may read on this warehouse: this layer's, the mapped ones, or a foreign warehouse's own."""
    m = mapping()
    if m: return tuple(t for t in TABLES if t in m['views'] or t in ('stay_episode', 'aco_contract'))
    if foreign():
        own = db is None
        db = db or connect()
        try:
            return tuple(r[0] for r in db.execute("SELECT name FROM sqlite_master WHERE type = 'table' AND name NOT LIKE 'sqlite%' ORDER BY name"))
        finally:
            if own: db.close()
    from . import customer
    return TABLES + tuple(customer.view_names())   # the customer's definition views are read like the layer's tables


# Tables whose rows belong to one patient. Restricting these restricts every recipe: the document catalog is built
# from the three document tables, so it is restricted with them.
PATIENT_TABLES = tuple(t for t in TABLES if t not in ('document', 'organization', 'clinician') + GOVERNED) + ('clinical_note', 'scanned_document', 'hie_document', 'visit_group')
MAX_COHORT = 5000


def connect(cohort=None):
    """cohort: patient ids. When given, every patient table on this connection holds only those patients' rows
    (a temporary view of the same name over the real table), so any recipe runs inside the cohort with no change
    to its SQL and no model involved."""
    db = sqlite3.connect(path().resolve().as_uri() + '?mode=ro', uri=True)
    if cohort is not None:
        if foreign(): raise RecipeError('A patient group cannot be selected on a warehouse with no mapping')
        db.execute('CREATE TEMP TABLE cohort(patient_id TEXT PRIMARY KEY)')
        db.executemany('INSERT OR IGNORE INTO cohort VALUES(?)', [(str(p),) for p in cohort])
    m = mapping()
    if m:
        # Each semantic-layer table is the mapping's SELECT over the customer's tables (always written main.<table>), restricted
        # to the cohort the same way as below.
        for table, sql in m['views'].items():
            body = f'SELECT * FROM ({sql})' + (' WHERE patient_id IN (SELECT patient_id FROM cohort)' if cohort is not None and table in PATIENT_TABLES else '')
            db.execute(f'CREATE TEMP VIEW "{table}" AS {body}')
        if 'meta' not in m['views']:
            db.execute("CREATE TEMP VIEW meta(key, value) AS VALUES ('version', ?), ('as_of', ?)".replace('?', "'%s'") % (m['semantic_layer'].replace("'", ''), str(m.get('as_of', '')).replace("'", '')))
        if {'clinical_note', 'scanned_document', 'hie_document'} <= set(m['views']): db.execute(DOCUMENT_VIEW)
    elif not foreign():
        present = {r[0] for r in db.execute("SELECT name FROM sqlite_master WHERE type IN ('table', 'view')")}
        if cohort is not None:
            for table in PATIENT_TABLES:
                if table in present:   # a source without this table (a FHIR import has no notes) simply lacks it
                    db.execute(f'CREATE TEMP VIEW {table} AS SELECT * FROM main.{table} WHERE patient_id IN (SELECT patient_id FROM cohort)')
        if {'clinical_note', 'scanned_document', 'hie_document'} <= present: db.execute(DOCUMENT_VIEW)
    if not foreign():
        have = {r[0] for r in db.execute("SELECT name FROM sqlite_master WHERE type IN ('table', 'view') UNION SELECT name FROM sqlite_temp_master WHERE type = 'view'")}
        if 'stay' in have and 'stay_episode' not in have: db.execute('CREATE TEMP VIEW stay_episode AS ' + _stay_episode())
        if 'visit_group' in have and 'hospital_visit' not in have: db.execute('CREATE TEMP VIEW hospital_visit AS ' + HOSPITAL_VISIT)
        from . import aco, readiness
        aco.install(db, cohort)
        readiness.install(db)
        install_own_facilities(db)
        from . import customer
        customer.install(db)   # the customer's own certified definitions, as views (onboarding)
    db.execute('PRAGMA query_only=ON')
    return db


DOCUMENT_VIEW = """CREATE TEMP VIEW document AS
SELECT record_id, patient_id, 'clinical_note' AS source, note_type AS document_type, event_date, organization_id, text_chars FROM clinical_note
UNION ALL SELECT record_id, patient_id, 'scanned_document', document_type, event_date, NULL, text_chars FROM scanned_document WHERE readable = 1
UNION ALL SELECT record_id, patient_id, 'hie_document', document_type, event_date, author_organization_id, text_chars FROM hie_document"""


def _stay_episode():
    from .recipes import _STAYS   # the merge the reviewed readmission recipes use, so every route counts a stay the same way
    return ("WITH " + _STAYS + "e AS (SELECT patient_id, admit_date, MAX(discharge_date) AS discharge_date, MIN(organization_id) AS organization_id "
            "FROM st GROUP BY patient_id, admit_date) "
            "SELECT e.patient_id, e.admit_date, e.discharge_date, e.organization_id, "
            "CASE WHEN e.discharge_date IS NOT NULL THEN CAST(julianday(e.discharge_date) - julianday(e.admit_date) AS INTEGER) END AS los_days, "
            "EXISTS (SELECT 1 FROM e p WHERE p.patient_id = e.patient_id AND p.discharge_date IS NOT NULL AND p.admit_date < e.admit_date "
            "AND julianday(e.admit_date) - julianday(p.discharge_date) BETWEEN 1 AND 30) AS is_readmission, "
            "CASE WHEN e.discharge_date IS NOT NULL THEN EXISTS (SELECT 1 FROM e n WHERE n.patient_id = e.patient_id AND n.admit_date > e.admit_date "
            "AND julianday(n.admit_date) - julianday(e.discharge_date) BETWEEN 1 AND 30) END AS readmitted_within_30_days FROM e")


# One row per hospital visit (an ED visit, an inpatient stay, or both when the patient was admitted through the ED), from
# visit_group, with the governed timing rules:
#   * ED departure of a patient admitted from the ED is the inpatient admission time when the recorded departure is missing
#     or later than it (the recorded value is often the hospital discharge);
#   * ED minutes are arrival to departure and boarding minutes decision-to-admit to departure, only for minute-precision
#     times, and only when 0 to 4,320 (72 hours); other intervals are implausible: NULL, and flagged;
#   * the ED timeliness eCQM (ECAT) thresholds that the data can evaluate: boarding over 4 hours for admitted patients, ED
#     stay over 8 hours for discharged patients. Time to a treatment space and leaving before evaluation are not recorded;
#   * sepsis and heart failure hospitalizations by principal diagnosis (A40, A41, R65.2; I50); death in hospital is the
#     disposition 'expired'.
HOSPITAL_VISIT = """SELECT v.visit_group_id, v.patient_id, v.organization_id, v.visit_class, v.has_ed, v.has_inpatient, v.has_observation,
  v.start_date, v.end_date, v.ed_arrival, d.departure AS ed_departure, v.admit_decision, v.inpatient_admit, v.discharge_time,
  v.time_precision, v.disposition, v.principal_icd10, v.unit, v.los_days,
  CASE WHEN v.has_inpatient = 1 THEN 'admitted' WHEN v.has_observation = 1 THEN 'observation' WHEN v.has_ed = 1 THEN 'discharged' END AS ed_outcome,
  CASE WHEN m.ed BETWEEN 0 AND 4320 THEN ROUND(m.ed, 1) END AS ed_minutes,
  CASE WHEN m.board BETWEEN 0 AND 4320 THEN ROUND(m.board, 1) END AS boarding_minutes,
  CASE WHEN m.ed IS NOT NULL AND NOT m.ed BETWEEN 0 AND 4320 THEN 1 WHEN m.board IS NOT NULL AND NOT m.board BETWEEN 0 AND 4320 THEN 1 ELSE 0 END AS implausible_times,
  CASE WHEN v.has_inpatient = 1 AND m.board BETWEEN 0 AND 4320 THEN (m.board > 240) END AS ecat_boarding_over_4h,
  CASE WHEN v.has_ed = 1 AND v.has_inpatient = 0 AND v.has_observation = 0 AND m.ed BETWEEN 0 AND 4320 THEN (m.ed > 480) END AS ecat_discharged_over_8h,
  (v.principal_icd10 LIKE 'A40%' OR v.principal_icd10 LIKE 'A41%' OR v.principal_icd10 LIKE 'R652%' OR v.principal_icd10 LIKE 'R65.2%') AS is_sepsis,
  (v.principal_icd10 LIKE 'I50%') AS is_heart_failure,
  (v.disposition = 'expired') AS died_in_hospital
FROM visit_group v
JOIN (SELECT visit_group_id, CASE WHEN has_inpatient = 1 AND inpatient_admit IS NOT NULL AND (ed_departure IS NULL OR ed_departure > inpatient_admit)
             THEN inpatient_admit ELSE ed_departure END AS departure FROM visit_group) d USING (visit_group_id)
JOIN (SELECT g.visit_group_id,
             CASE WHEN g.has_ed = 1 AND g.time_precision = 'minute' THEN ROUND((julianday(x.departure) - julianday(g.ed_arrival)) * 1440, 3) END AS ed,
             CASE WHEN g.time_precision = 'minute' THEN ROUND((julianday(x.departure) - julianday(g.admit_decision)) * 1440, 3) END AS board
      FROM visit_group g JOIN (SELECT visit_group_id, CASE WHEN has_inpatient = 1 AND inpatient_admit IS NOT NULL AND (ed_departure IS NULL OR ed_departure > inpatient_admit)
             THEN inpatient_admit ELSE ed_departure END AS departure FROM visit_group) x USING (visit_group_id)) m USING (visit_group_id)"""


def _literal(v):
    if v is None: return 'NULL'
    if isinstance(v, (int, float)) and not isinstance(v, bool): return repr(v)
    return "'" + str(v).replace("'", "''") + "'"


OWN_FACILITIES = Path('config/agere/organization')


def own_facilities():
    """-> [(organization_id, display name)] the customer's own facilities, from config/agere/organization/*.json."""
    out = []
    for f in sorted(OWN_FACILITIES.glob('*.json')) if OWN_FACILITIES.is_dir() else []:
        try:
            c = json.loads(f.read_text())
        except (OSError, ValueError):
            continue
        if c.get('schema') == 'agere-own-facilities/1': out += [str(i) for i in c.get('own_organization_ids') or []]
    return out


def install_own_facilities(db):
    """own_organization: the customer's own facilities (onboarding setting), so "our hospitals" has one meaning."""
    db.execute('CREATE TEMP TABLE own_organization(organization_id TEXT PRIMARY KEY)')
    db.executemany('INSERT OR IGNORE INTO own_organization VALUES (?)', [(i,) for i in own_facilities()])


def portable(sql, cohort=None, params=None):
    """portable_sql (below) plus the governed tables Agere computes (aco_contract, risk_score, patient_hcc), inlined as
    VALUES so the saved SQL runs on the warehouse file alone. Risk rows are limited to the year the answer was asked for."""
    text = _portable(sql, cohort)
    reads = [t for t in ('aco_contract', 'risk_score', 'patient_hcc', 'own_organization') if re.search(rf'(?i)(?<![.\w]){t}\b', text)]
    if not reads: return text
    year = (params or {}).get('year')
    year = year.get('value') if isinstance(year, dict) else year
    db = connect(cohort)
    try:
        ctes = []
        for table in reads:
            cols = [c[1] for c in db.execute(f'PRAGMA table_info("{table}")')]
            where = ' WHERE year = ?' if year and table not in ('aco_contract', 'own_organization') else ''
            rows = db.execute(f'SELECT * FROM "{table}"{where}', (str(year),) if where else ()).fetchall()
            body = ('VALUES ' + ', '.join('(' + ', '.join(_literal(v) for v in r) + ')' for r in rows)) if rows else \
                   'SELECT ' + ', '.join('NULL' for _ in cols) + ' WHERE 0'
            ctes.append(f'{table}({", ".join(cols)}) AS ({body})')
    finally:
        db.close()
    if re.match(r'(?is)^\s*with\s+recursive\b', text): raise RecipeError('A recursive recipe cannot be saved with governed tables')
    if re.match(r'(?is)^\s*with\b', text): return re.sub(r'(?is)^\s*with\b', 'WITH ' + ', '.join(ctes) + ',', text, count=1)
    return 'WITH ' + ', '.join(ctes) + ' ' + text


def _portable(sql, cohort=None):
    """The same SELECT runnable on the semantic layer by anyone: the document catalog is inlined as a CTE, because
    the view exists only on this module's connections. With a cohort, the patient ids and the restricted tables are
    inlined the same way, so the saved SQL returns the cohort's result and not the whole population's."""
    text = sql.strip().rstrip(';')
    m = mapping()
    if m:   # on a mapped warehouse the semantic-layer tables a query reads are inlined from the mapping, in mapping order
        reads = lambda s, name: re.search(rf'(?i)(?<![.\w]){name}\b(?!_)', s)
        need = {t for t in m['views'] if reads(text, t)} | ({'stay'} if reads(text, 'stay_episode') and 'stay' in m['views'] else set())
        while True:   # a mapped view may read another mapped view
            more = {t for t, v in m['views'].items() if t not in need and any(reads(m['views'][n], t) for n in need)}
            if not more: break
            need |= more
        ctes = []
        if cohort is not None:
            ids = sorted({str(p) for p in cohort})
            ctes.append('cohort(patient_id) AS (' + ('VALUES ' + ', '.join("('" + p.replace("'", "''") + "')" for p in ids) if ids else 'SELECT NULL WHERE 0') + ')')
        for table, view in m['views'].items():
            if table not in need: continue
            inside = cohort is not None and table in PATIENT_TABLES
            ctes.append(f'"{table}" AS (' + (f'SELECT * FROM ({view}) WHERE patient_id IN (SELECT patient_id FROM cohort)' if inside else view) + ')')
        if reads(text, 'stay_episode'): ctes.append('stay_episode AS (' + _stay_episode() + ')')
        if not ctes: return text
        if re.match(r'(?is)^\s*with\s+recursive\b', text): raise RecipeError('A recursive recipe cannot be saved on a mapped warehouse')
        if re.match(r'(?is)^\s*with\b', text): return re.sub(r'(?is)^\s*with\b', 'WITH ' + ', '.join(ctes) + ',', text, count=1)
        return 'WITH ' + ', '.join(ctes) + ' ' + text
    uses_documents = bool(re.search(r'(?i)\bdocument\b(?!_)', text))
    episodes = bool(re.search(r'(?i)\bstay_episode\b', text))
    visits = bool(re.search(r'(?i)\bhospital_visit\b', text))
    ctes = []
    if cohort is not None:
        ids = sorted({str(p) for p in cohort})
        values = 'VALUES ' + ', '.join("('" + p.replace("'", "''") + "')" for p in ids) if ids else 'SELECT NULL WHERE 0'
        ctes.append(f'cohort(patient_id) AS ({values})')
        for table in PATIENT_TABLES:
            if re.search(rf'(?i)\b{table}\b(?!_)', text) or (uses_documents and table in ('clinical_note', 'scanned_document', 'hie_document')) or (episodes and table == 'stay'):
                ctes.append(f'{table} AS (SELECT * FROM main.{table} WHERE patient_id IN (SELECT patient_id FROM cohort))')
    if uses_documents: ctes.append('document AS (' + DOCUMENT_VIEW.split(' AS\n', 1)[1] + ')')
    if episodes: ctes.append('stay_episode AS (' + _stay_episode() + ')')
    if visits:
        if cohort is not None: ctes.append('visit_group AS (SELECT * FROM main.visit_group WHERE patient_id IN (SELECT patient_id FROM cohort))')
        ctes.append('hospital_visit AS (' + HOSPITAL_VISIT + ')')
    if not ctes: return text
    if re.match(r'(?is)^\s*with\s+recursive\b', text): raise RecipeError('A recursive recipe cannot be saved with a cohort or the document catalog')
    if re.match(r'(?is)^\s*with\b', text):
        return re.sub(r'(?is)^\s*with\b', 'WITH ' + ', '.join(ctes) + ',', text, count=1)
    return 'WITH ' + ', '.join(ctes) + ' ' + text


def texts(record_ids, db):
    """Document text for the reading tier. -> {record_id: {'text', 'event_date', 'source'}}. Only catalogued documents."""
    out, ids = {}, list(dict.fromkeys(record_ids))
    for i in range(0, len(ids), 400):
        chunk = ids[i:i + 400]
        marks = ','.join('?' * len(chunk))
        for rid, source, day, text in db.execute(
                f"SELECT d.record_id, d.source, d.event_date, COALESCE(t.text, json_extract(r.payload, '$.text')) FROM document d "
                f"LEFT JOIN scanned_document_text t ON t.record_id = d.record_id AND d.source = 'scanned_document' "
                f"LEFT JOIN record r ON r.record_id = d.record_id AND d.source <> 'scanned_document' WHERE d.record_id IN ({marks})", chunk):
            out[rid] = {'text': text or '', 'event_date': day, 'source': source}
    return out


def schema_version(db=None):
    """What a saved recipe is valid for: the semantic layer's version and the tables and columns it presents, not its data. A
    data refresh with the same schema keeps every saved recipe answering; a schema change retires them for re-verification.
    (Until 2026-11-16 this was the data digest, so each refresh hid every saved recipe: design review.)"""
    m = mapping()
    if m: return m['semantic_layer']   # the semantic-layer version the mapping presents; saved recipes verified on it apply
    if foreign(): return 'foreign:' + path().name
    cache = scoped('schema_version')
    if 'v' not in cache:
        own = db is None
        db = db or connect()
        try:
            version = (db.execute("SELECT value FROM meta WHERE key = 'version'").fetchone() or [None])[0]
            shape = []
            # The warehouse's stored tables only (main schema): Agere's own governed views are code, versioned by git, and
            # adding one must not retire every saved recipe.
            stored = [r[0] for r in db.execute("SELECT name FROM main.sqlite_master WHERE type IN ('table', 'view') AND name NOT LIKE 'sqlite_%'")]
            for t in sorted(stored):
                try:
                    shape.append(t + '(' + ','.join(c[1] for c in db.execute(f'PRAGMA table_info("{t}")')) + ')')
                except sqlite3.Error:
                    continue
            import hashlib
            cache['v'] = f"schema:{version}:{hashlib.sha256(';'.join(shape).encode()).hexdigest()[:16]}"
        finally:
            if own: db.close()
    return cache['v']


def data_version(db=None):
    """The semantic layer's version and source data digest: what saved recipes were keyed by before 2026-11-16."""
    if mapping() or foreign(): return None
    own = db is None
    db = db or connect()
    try:
        rows = dict(db.execute("SELECT key, value FROM meta WHERE key IN ('version', 'source_digest')"))
        return f"{rows.get('version')}:{str(rows.get('source_digest'))[:16]}"
    finally:
        if own: db.close()


def as_of(db):
    """The semantic layer's own snapshot date: the clock for anything dated, never today's date."""
    cache = scoped('as_of')
    if 'd' not in cache:
        if foreign():
            cache['d'] = os.environ.get('AGERE_TIERED_AS_OF') or time.strftime('%Y-%m-%d')
        else:
            cache['d'] = db.execute("SELECT value FROM meta WHERE key = 'as_of'").fetchone()[0][:10]
    return cache['d']


def schema_text(db=None):
    """Table and column names only: what a model may see. Never rows."""
    own = db is None
    db = db or connect()
    try:
        def columns(t):
            try:
                return [c[1] for c in db.execute(f'PRAGMA table_info("{t}")')]
            except sqlite3.Error:   # a view whose sources this warehouse lacks
                return []
        shown = [(t, columns(t)) for t in tables(db)]
        return '\n'.join(f"{t}({', '.join(cols)})" for t, cols in shown if cols)   # a table this warehouse does not have is not shown
    finally:
        if own: db.close()


def vocabulary_text(db=None, limit=40):
    """For a foreign warehouse: each text column with at most `limit` distinct values, and those values. Column metadata,
    not rows: what a person writing SQL for an unfamiliar warehouse would look up first."""
    cache = scoped('vocabulary_text')
    key = limit
    if key not in cache:
        own = db is None
        db = db or connect()
        lines = []
        try:
            for table in tables(db):
                for col, kind in [(c[1], (c[2] or '').upper()) for c in db.execute(f'PRAGMA table_info("{table}")')]:
                    if 'INT' in kind or 'REAL' in kind or re.search(r'(?i)date|_id$|amount|value_as_number|days', col): continue
                    values = [r[0] for r in db.execute(f'SELECT DISTINCT "{col}" FROM "{table}" WHERE "{col}" IS NOT NULL LIMIT {limit + 1}')]
                    if 0 < len(values) <= limit: lines.append(f'{table}.{col}: ' + ', '.join(sorted(str(v) for v in values)))
        finally:
            if own: db.close()
        cache[key] = '\n'.join(lines)
    return cache[key]


class RecipeError(ValueError):
    pass


def run(sql, params=None, db=None):
    """Execute one read-only SELECT. -> (columns, rows). Raises RecipeError on anything else."""
    text = sql.strip().rstrip(';')
    if ';' in text or not re.match(r'(?is)^\s*(select|with)\b', text):
        raise RecipeError('A recipe is exactly one SELECT statement')
    if re.search(r'(?i)\b(scanned_document_text|payload)\b', text):
        raise RecipeError('A recipe may not select document text; use a reading plan')
    # The words alone are not enough: SELECT * FROM record returns record.payload (buyer review). The database itself refuses
    # any read of document text in a recipe, however the query is written; documents are read only through texts().
    if re.search(r'(?i)\b(concept|drug_class)\s*\)?\s+(not\s+)?(like|glob)\b', text):
        raise RecipeError('Compare concept and drug_class with = or IN against exact vocabulary values; LIKE would also match other values (diabetes matches prediabetes)')
    # Statements that write or change the database (REPLACE alone is also a text function, allowed as one); the connection is
    # read-only and query_only besides.
    if re.search(r'(?i)\b(attach|pragma|insert|update|delete|drop|alter|create|vacuum)\b|\breplace\s+into\b|^\s*replace\b', text):
        raise RecipeError('A recipe may only read')
    own = db is None
    db = db or connect()
    deadline = time.monotonic() + SECONDS
    db.set_progress_handler(lambda: 1 if time.monotonic() > deadline else 0, 20000)
    db.set_authorizer(_no_document_text)
    try:
        cur = db.execute(text, params or {})
        return [d[0] for d in cur.description], [list(r) for r in cur.fetchall()]
    except sqlite3.DatabaseError as exc:
        if 'prohibited' in str(exc).lower() or 'not authorized' in str(exc).lower():
            raise RecipeError('A recipe may not read document text (record.payload, scanned_document_text); use a reading plan') from exc
        raise RecipeError(f'{type(exc).__name__}: {exc}') from exc
    finally:
        db.set_authorizer(None)
        db.set_progress_handler(None, 0)
        if own: db.close()


DOCUMENT_TEXT = {('record', 'payload'), ('scanned_document_text', 'text')}


def _no_document_text(action, table, column, _db, _source):
    """sqlite3 authorizer for recipe SQL: deny reading document text, allow everything else run() already permits."""
    if action == sqlite3.SQLITE_READ and (str(table).lower(), str(column).lower()) in DOCUMENT_TEXT:
        return sqlite3.SQLITE_DENY
    return sqlite3.SQLITE_OK
