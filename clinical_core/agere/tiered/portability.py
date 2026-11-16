"""Portability: does Agere carry to a warehouse laid out differently from its own semantic layer?

A real customer's warehouse has its own tables, identifiers, codes and units. This module builds a second synthetic warehouse
("layout B", in the style of open clinical data models) from the same facts as the semantic layer, so that answers on it can
be scored against references computed on the original, and runs the mapping steps a customer onboarding would need:

    python -m clinical_core.agere.tiered.portability build          # writes the layout B warehouse
    python -m clinical_core.agere.tiered.portability check MAPPING  # row counts of each mapped table against the original
    python -m clinical_core.agere.tiered.portability draft          # a frontier model drafts a mapping from layout B's schema

Layout B differs on purpose in the ways customer warehouses differ: integer surrogate ids beside the source ids, codes in
place of words (visit types, appointment statuses, lines of business, lab codes with a lookup table), dollars in place of
cents, eligibility months as YYYYMM numbers, hospital stays and utilization events in one visit table told apart by feed,
social-need screens stored as observations, wellness visits and mammograms as billing codes, vaccines as CVX or billing
codes. It holds no documents. The `check` step uses the original warehouse, which a real customer would not have; it is a
test diagnostic only.
"""
import json
import os
import sqlite3
import sys
import time
from pathlib import Path

from . import store

OUT = Path('artifacts/agere-tiered-core/portability')
LAYOUT_B = OUT / 'layout_b.sqlite3'
REVIEWED = Path('config/agere/mappings/layout_b.reviewed.json')

LOB = {'commercial': 'COM', 'medicaid': 'MCD', 'medicare': 'MCR', 'dual': 'DUAL', 'medigap': 'MGAP', 'cob_secondary': 'COB2'}
APPOINTMENT = {'fulfilled': 'CMP', 'attended': 'ATT', 'arrived': 'ARR', 'booked': 'BKD', 'cancelled': 'CXL', 'no_show': 'NOS',
               'rescheduled': 'RSC', 'unassigned_requires_review': 'UNK'}
DRUG_NAMES = {'glp1_agonist': 'GLP-1 receptor agonist', 'sglt2_inhibitor': 'SGLT2 inhibitor', 'ace_inhibitor': 'ACE inhibitor',
              'arb': 'Angiotensin receptor blocker', 'ssri': 'SSRI', 'snri': 'SNRI', 'nsaid': 'NSAID', 'mra': 'Mineralocorticoid antagonist',
              'laba': 'Long-acting beta agonist', 'lama': 'Long-acting muscarinic antagonist', 'ndri': 'NDRI',
              'hf_evidence_based_beta_blocker': 'Beta blocker (heart failure evidence-based)', 'statin': 'HMG-CoA reductase inhibitor (statin)'}
LAB_NAMES = {'hba1c': 'Hemoglobin A1c', 'ldl': 'LDL cholesterol', 'hdl': 'HDL cholesterol', 'egfr': 'Estimated GFR', 'systolic_bp': 'Systolic blood pressure',
             'diastolic_bp': 'Diastolic blood pressure', 'nt_probnp': 'NT-proBNP', 'uacr': 'Urine albumin/creatinine ratio', 'bun': 'Blood urea nitrogen',
             'wbc': 'White blood cell count', 'spo2': 'Oxygen saturation', 'phq9': 'PHQ-9 score', 'tsh': 'Thyroid stimulating hormone',
             'gfr_unnormalized': 'GFR (not normalized)'}


def _label(snake, names):
    return names.get(snake) or snake.replace('_', ' ').capitalize()


def build(source=None, out=LAYOUT_B):
    source = Path(source or store.DEFAULT_DB)
    out.parent.mkdir(parents=True, exist_ok=True)
    if out.exists(): out.unlink()
    db = sqlite3.connect(out.resolve().as_uri(), uri=True)
    db.execute(f"ATTACH DATABASE '{source.resolve().as_uri()}?mode=ro' AS src")
    x = db.execute
    x('CREATE TEMP TABLE pid AS SELECT patient_id, ROW_NUMBER() OVER (ORDER BY patient_id) AS person_id FROM src.patient')
    x('CREATE UNIQUE INDEX temp.pid_i ON pid(patient_id)')
    x('CREATE TEMP TABLE sid AS SELECT organization_id, ROW_NUMBER() OVER (ORDER BY organization_id) AS care_site_id FROM src.organization')
    x('CREATE UNIQUE INDEX temp.sid_i ON sid(organization_id)')
    lob = 'CASE {} ' + ' '.join(f"WHEN '{k}' THEN '{v}'" for k, v in LOB.items()) + ' END'

    x('''CREATE TABLE person AS SELECT p.person_id, s.patient_id AS person_source_value,
         CASE s.sex_reported WHEN 'F' THEN 'FEMALE' WHEN 'M' THEN 'MALE' END AS gender_source_value,
         s.birth_date || ' 00:00:00' AS birth_datetime, s.race AS race_source_value, s.ethnicity AS ethnicity_source_value,
         s.zip, s.county, s.state FROM src.patient s JOIN pid p USING (patient_id) ORDER BY p.person_id''')
    x('''CREATE TABLE care_site AS SELECT i.care_site_id, o.organization_id AS care_site_source_value, o.display_name AS care_site_name,
         o.organization_type AS care_site_type FROM src.organization o JOIN sid i USING (organization_id) ORDER BY i.care_site_id''')
    x('''CREATE TABLE visit_occurrence AS SELECT ROW_NUMBER() OVER (ORDER BY feed, person_id, visit_start_date) AS visit_occurrence_id, * FROM (
           SELECT p.person_id, CASE lower(s.type) WHEN 'observation' THEN 'OBS' ELSE 'IP' END AS visit_type, s.type AS visit_source_value,
                  s.admit_date AS visit_start_date, s.discharge_date AS visit_end_date,
                  CASE s.discharge_known WHEN 1 THEN 'Y' ELSE 'N' END AS end_date_confirmed, i.care_site_id, 'ADT' AS feed
             FROM src.stay s JOIN pid p USING (patient_id) LEFT JOIN sid i USING (organization_id)
           UNION ALL
           SELECT p.person_id, CASE u.kind WHEN 'ed_visit' THEN 'ER' WHEN 'observation' THEN 'OBS' ELSE 'IP' END, u.kind,
                  u.event_date, u.end_date, CASE u.discharge_known WHEN 1 THEN 'Y' ELSE 'N' END, i.care_site_id, 'UTILIZATION'
             FROM src.utilization_event u JOIN pid p USING (patient_id) LEFT JOIN sid i USING (organization_id))''')
    classes = [r[0] for r in x('SELECT DISTINCT drug_class FROM src.fill WHERE drug_class IS NOT NULL ORDER BY 1')]
    x('CREATE TABLE drug_class_ref(drug_class_id INTEGER PRIMARY KEY, drug_class_name TEXT)')
    db.executemany('INSERT INTO drug_class_ref VALUES (?, ?)', [(i + 1, _label(c, DRUG_NAMES)) for i, c in enumerate(classes)])
    x('CREATE TEMP TABLE dc(drug_class TEXT PRIMARY KEY, drug_class_id INTEGER)')
    db.executemany('INSERT INTO dc VALUES (?, ?)', [(c, i + 1) for i, c in enumerate(classes)])
    x('''CREATE TABLE drug_exposure AS SELECT ROW_NUMBER() OVER (ORDER BY f.fill_id) AS drug_exposure_id, p.person_id,
         f.fill_date AS drug_exposure_start_date, f.days_supply, f.drug_name AS drug_source_value, c.drug_class_id
         FROM src.fill f JOIN pid p USING (patient_id) LEFT JOIN dc c USING (drug_class)''')
    concepts = [r[0] for r in x('SELECT DISTINCT concept FROM src.measurement WHERE concept IS NOT NULL ORDER BY 1')]
    x('CREATE TABLE lab_test_ref(lab_code TEXT PRIMARY KEY, lab_name TEXT)')
    db.executemany('INSERT INTO lab_test_ref VALUES (?, ?)', [(f'LAB{i + 1:03d}', _label(c, LAB_NAMES)) for i, c in enumerate(concepts)])
    x('CREATE TEMP TABLE lc(concept TEXT PRIMARY KEY, lab_code TEXT)')
    db.executemany('INSERT INTO lc VALUES (?, ?)', [(c, f'LAB{i + 1:03d}') for i, c in enumerate(concepts)])
    x('''CREATE TABLE measurement AS SELECT ROW_NUMBER() OVER (ORDER BY m.record_id) AS measurement_id, p.person_id,
         m.event_date AS measurement_date, l.lab_code, m.name AS measurement_source_value, m.value_canonical AS value_as_number,
         m.unit_canonical AS unit_source_value FROM src.measurement m JOIN pid p USING (patient_id) LEFT JOIN lc l USING (concept)''')
    labels = {r[0]: r[1] for r in x('SELECT concept, MIN(label) FROM src.condition WHERE concept IS NOT NULL GROUP BY 1')}
    x('CREATE TEMP TABLE cg(concept TEXT PRIMARY KEY, condition_group TEXT)')
    db.executemany('INSERT INTO cg VALUES (?, ?)', [(c, c.replace('_', ' ').capitalize()) for c in labels])
    x('''CREATE TABLE condition_occurrence AS SELECT ROW_NUMBER() OVER (ORDER BY c.record_id) AS condition_occurrence_id, p.person_id,
         c.event_date AS condition_start_date, c.icd10 AS condition_source_value, g.condition_group, i.care_site_id
         FROM src.condition c JOIN pid p USING (patient_id) LEFT JOIN cg g USING (concept) LEFT JOIN sid i USING (organization_id)''')
    x(f'''CREATE TABLE eligibility_month AS SELECT p.person_id, CAST(replace(m.month, '-', '') AS INTEGER) AS elig_month,
          {lob.format('m.line_of_business')} AS lob_code, m.payer_id AS payer_source_value, m.attribution_practice_id AS attributed_practice_code
          FROM src.member_month m JOIN pid p USING (patient_id)''')
    x(f'''CREATE TABLE medical_claim AS SELECT ROW_NUMBER() OVER (ORDER BY c.payer_claim_id) AS claim_id, p.person_id,
          c.service_from AS service_start_date, c.service_to AS service_end_date,
          CASE c.claim_type WHEN 'institutional' THEN 'UB-04' WHEN 'professional' THEN 'CMS-1500' END AS bill_form,
          {lob.format('c.line_of_business')} AS lob_code, ROUND(c.paid_cents / 100.0, 2) AS paid_amount,
          CASE c.counts_in_metrics WHEN 1 THEN 'Y' ELSE 'N' END AS reportable FROM src.payer_claim c JOIN pid p USING (patient_id)''')
    status = 'CASE a.final_status ' + ' '.join(f"WHEN '{k}' THEN '{v}'" for k, v in APPOINTMENT.items()) + ' END'
    x(f'''CREATE TABLE appointment_schedule AS SELECT ROW_NUMBER() OVER (ORDER BY a.appointment_id) AS appt_id, p.person_id,
          a.scheduled_date AS appt_date, a.booked_date, {status} AS status_code, i.care_site_id
          FROM src.appointment a JOIN pid p USING (patient_id) LEFT JOIN sid i USING (organization_id)''')
    x('''CREATE TABLE observation AS SELECT ROW_NUMBER() OVER (ORDER BY s.record_id, s.need) AS observation_id, p.person_id,
         s.event_date AS observation_date, 'SDOH-' || upper(s.need) AS observation_source_value,
         CASE s.positive WHEN 1 THEN 'Positive' ELSE 'Negative' END AS value_as_string, s.instrument
         FROM src.social_need s JOIN pid p USING (patient_id)''')
    x('CREATE TABLE death AS SELECT p.person_id, d.death_date FROM src.death d JOIN pid p USING (patient_id)')
    x('''CREATE TABLE procedure_occurrence AS SELECT ROW_NUMBER() OVER (ORDER BY kind, person_id, procedure_date) AS procedure_occurrence_id, * FROM (
           SELECT 'wellness' AS kind, p.person_id, w.event_date AS procedure_date, COALESCE(w.hcpcs_code, 'PREVENTIVE-VISIT') AS procedure_source_value
             FROM src.wellness_visit w JOIN pid p USING (patient_id)
           UNION ALL
           SELECT 'screening', p.person_id, c.event_date, CASE c.test WHEN 'bilateral_screening_mammography' THEN '77067' WHEN 'FIT' THEN '82274' ELSE c.test END
             FROM src.cancer_screening c JOIN pid p USING (patient_id))''')
    x('ALTER TABLE procedure_occurrence DROP COLUMN kind')
    x('''CREATE TABLE immunization AS SELECT ROW_NUMBER() OVER (ORDER BY m.record_id) AS immunization_id, p.person_id,
         m.event_date AS admin_date, m.cvx AS cvx_code, m.code AS billing_code, m.product AS product_name
         FROM src.immunization m JOIN pid p USING (patient_id)''')
    as_of = x("SELECT value FROM src.meta WHERE key = 'as_of'").fetchone()[0]
    x("CREATE TABLE cdm_source(cdm_source_name TEXT, cdm_release_date TEXT)")
    x("INSERT INTO cdm_source VALUES ('Layout B synthetic warehouse', ?)", (as_of,))
    db.commit()
    x('DETACH DATABASE src')
    db.execute('VACUUM')
    counts = {t: db.execute(f'SELECT COUNT(*) FROM "{t}"').fetchone()[0] for (t,) in db.execute("SELECT name FROM sqlite_master WHERE type = 'table' ORDER BY name").fetchall()}
    db.close()
    return counts


def check(mapping_path, source=None, layout=LAYOUT_B):
    """Test diagnostic: each mapped table's row count, and its count of distinct patient ids, against the original table."""
    m = json.loads(Path(mapping_path).read_text())
    orig = sqlite3.connect(Path(source or store.DEFAULT_DB).resolve().as_uri() + '?mode=ro', uri=True)
    old = os.environ.get('AGERE_TIERED_DB'), os.environ.get('AGERE_TIERED_MAPPING')
    os.environ['AGERE_TIERED_DB'], os.environ['AGERE_TIERED_MAPPING'] = str(layout), str(mapping_path)
    lines = []
    try:
        db = store.connect()
        for table in m['views']:
            if table == 'meta': continue
            cols = {c[1] for c in db.execute(f'PRAGMA table_info("{table}")')}
            who = 'COUNT(DISTINCT patient_id)' if 'patient_id' in cols else '0'
            try:
                got = db.execute(f'SELECT COUNT(*), {who} FROM "{table}"').fetchone()
                want = orig.execute(f'SELECT COUNT(*), {who} FROM "{table}"').fetchone()
            except sqlite3.Error as exc:
                lines.append(f'{table:22} ERROR {exc}'); continue
            lines.append(f"{table:22} rows {got[0]:>7} vs {want[0]:>7}  patients {got[1]:>5} vs {want[1]:>5}  {'same' if tuple(got) == tuple(want) else 'DIFFERS'}")
            # Short-list text columns: the count of each value, mapped against the original.
            for col in sorted(cols & {c[1] for c in orig.execute(f'PRAGMA table_info("{table}")')}):
                try:
                    have = dict(orig.execute(f'SELECT "{col}", COUNT(*) FROM "{table}" GROUP BY 1 LIMIT 81').fetchall())
                except sqlite3.Error:
                    continue
                if len(have) > 80 or not all(isinstance(k, str) or k is None for k in have): continue
                mine = dict(db.execute(f'SELECT "{col}", COUNT(*) FROM "{table}" GROUP BY 1 LIMIT 200').fetchall())
                bad = {k: (mine.get(k, 0), v) for k, v in have.items() if mine.get(k, 0) != v}
                bad.update({k: (v, 0) for k, v in mine.items() if k not in have})
                if bad: lines.append(f"    {col}: {len(bad)} values differ, e.g. " + '; '.join(f'{k}: {a} vs {b}' for k, (a, b) in list(bad.items())[:4]))
        db.close()
    finally:
        for k, v in zip(('AGERE_TIERED_DB', 'AGERE_TIERED_MAPPING'), old):
            if v is None: os.environ.pop(k, None)
            else: os.environ[k] = v
    return '\n'.join(lines)


DRAFT_SYSTEM = '''You are onboarding a customer's healthcare warehouse to an analytics product. The product answers questions over
its own semantic layer; you write the SQLite view that presents one semantic-layer table from the customer's tables.

Return exactly one JSON object and nothing else:
{"sql": "<one SELECT producing the semantic-layer table, columns named exactly as listed>",
 "unmapped_columns": ["<target columns the customer warehouse has no source for; omit them from the SELECT>"],
 "notes": "<one sentence on any judgment you made>"}
or, when the customer warehouse holds nothing this table could come from: {"cannot_map": "<why>"}

Rules:
- One SELECT (WITH allowed), no semicolons. Write every customer table as main.<table>. Use only the customer tables and columns listed.
- Translate the customer's codes and units into the semantic layer's documented values and units exactly (the values listed
  for each target column), so that a query written for the semantic layer gives the same answer on the customer's data.
- Keep one output row per customer row the target table describes; do not invent rows, filter rows the target table holds,
  or drop rows the target table holds.
- patient_id must be the same identifier in every table you map: use the customer's own patient identifier consistently.
- Dates as ISO text (YYYY-MM-DD; months YYYY-MM). Money as the semantic layer states it.'''


def documented(orig, table):
    """What Agere documents about one of its semantic-layer tables: columns, the values of short-list text columns, the
    columns it always fills, and the date shapes it uses. Product documentation about the layer, not customer data."""
    cols = [(c[1], c[2]) for c in orig.execute(f'PRAGMA table_info("{table}")')]
    vocab, required, shapes = {}, set(), {}
    total = orig.execute(f'SELECT COUNT(*) FROM "{table}"').fetchone()[0]
    for col, kind in cols:
        nulls = orig.execute(f'SELECT COUNT(*) FROM "{table}" WHERE "{col}" IS NULL').fetchone()[0]
        if total and not nulls: required.add(col)
        if 'INT' in (kind or '').upper() or 'REAL' in (kind or '').upper() or col.endswith('_id'): continue
        sample = [r[0] for r in orig.execute(f'SELECT "{col}" FROM "{table}" WHERE "{col}" IS NOT NULL LIMIT 200')]
        if sample and all(isinstance(v, str) and len(v) == 10 and v[4] == '-' and v[7] == '-' for v in sample): shapes[col] = 'YYYY-MM-DD'; continue
        if sample and all(isinstance(v, str) and len(v) == 7 and v[4] == '-' for v in sample): shapes[col] = 'YYYY-MM'; continue
        if 'date' in col: continue
        vals = [r[0] for r in orig.execute(f'SELECT DISTINCT "{col}" FROM "{table}" WHERE "{col}" IS NOT NULL LIMIT 81')]
        if 0 < len(vals) <= 80 and all(isinstance(v, str) for v in vals): vocab[col] = sorted(vals)
    return {'columns': [c for c, _ in cols], 'vocabulary': vocab, 'required': required, 'shapes': shapes}


def audit(mapping, layout=LAYOUT_B, source=None):
    """Checks a mapping can be given before anyone trusts it, using only the customer warehouse and what Agere documents about
    its own layer (no original data): values outside the layer's vocabulary, empty values in columns the layer always fills,
    and dates in another shape. -> {table: [finding, ...]} for the tables with findings."""
    orig = sqlite3.connect(Path(source or store.DEFAULT_DB).resolve().as_uri() + '?mode=ro', uri=True)
    conn = sqlite3.connect(Path(layout).resolve().as_uri() + '?mode=ro', uri=True)
    for table, sql in mapping['views'].items():   # as on a mapped connection: a view may read another mapped view
        try:
            conn.execute(f'CREATE TEMP VIEW "{table}" AS {sql}')
        except sqlite3.Error:
            pass
    out = {}
    for table, sql in mapping['views'].items():
        doc, found = documented(orig, table), []
        sql = f'SELECT * FROM temp."{table}"'
        try:
            have = [d[0] for d in conn.execute(f'SELECT * FROM ({sql}) LIMIT 0').description]
            n = conn.execute(f'SELECT COUNT(*) FROM ({sql})').fetchone()[0]
        except sqlite3.Error as exc:
            out[table] = [f'does not run: {exc}']; continue
        for col in have:
            if col in doc['vocabulary']:
                bad = conn.execute(f'SELECT "{col}", COUNT(*) FROM ({sql}) WHERE "{col}" IS NOT NULL GROUP BY 1 ORDER BY 2 DESC').fetchall()
                bad = [(v, k) for v, k in bad if v not in doc['vocabulary'][col]]
                if bad: found.append(f'{col} holds values the semantic layer does not use: ' + ', '.join(f'{v!r} ({k} rows)' for v, k in bad[:8])
                                     + f'; its values are: {", ".join(doc["vocabulary"][col])}')
            if col in doc['required'] and n:
                empty = conn.execute(f'SELECT COUNT(*) FROM ({sql}) WHERE "{col}" IS NULL').fetchone()[0]
                if empty: found.append(f'{col} is empty in {empty} of {n} rows; the semantic layer always fills it')
            if col in doc['shapes']:
                shape = doc['shapes'][col]
                pattern = '____-__-__' if shape == 'YYYY-MM-DD' else '____-__'
                off = conn.execute(f'SELECT COUNT(*), MIN("{col}") FROM ({sql}) WHERE "{col}" IS NOT NULL AND (CAST("{col}" AS TEXT) NOT LIKE \'{pattern}\' OR length(CAST("{col}" AS TEXT)) <> {len(shape)})').fetchone()
                if off[0]: found.append(f'{col} is not {shape} in {off[0]} rows (for example {off[1]!r})')
        if found: out[table] = found
    return out


def revise(mapping_path=OUT / 'layout_b.drafted.json', out=OUT / 'layout_b.revised.json', layout=LAYOUT_B, source=None):
    """One revision round: the drafter sees the audit's findings for each table it mapped and returns a corrected view. A
    revision is kept only when it runs and the audit finds less in it. Nothing is edited by hand. -> (mapping, lines)."""
    from . import models
    mapping = json.loads(Path(mapping_path).read_text())
    found = audit(mapping, layout, source)
    conn = sqlite3.connect(Path(layout).resolve().as_uri() + '?mode=ro', uri=True)
    orig = sqlite3.connect(Path(source or store.DEFAULT_DB).resolve().as_uri() + '?mode=ro', uri=True)
    views, lines, usd = dict(mapping['views']), [], 0.0
    for table, issues in found.items():
        doc = documented(orig, table)
        user = (f'Semantic-layer table: {table}({", ".join(doc["columns"])})\nYour view over the customer warehouse:\n{views[table]}\n\n'
                'An audit of the rows your view returns found:\n' + '\n'.join(f'- {i}' for i in issues) +
                "\n\nReturn the corrected view as the same JSON object. Translate the customer's codes into the listed values exactly.")
        text, step = models._claude(models.LLM, DRAFT_SYSTEM, user)
        usd += step.get('usd', 0.0)
        try:
            sql = models._json(text)['sql']
            conn.execute(f'SELECT * FROM ({sql}) LIMIT 1').fetchall()
            after = audit({'views': {**views, table: sql}}, layout, source).get(table, [])
        except Exception as exc:
            lines.append(f'{table:20} revision not kept: {str(exc)[:120]}'); continue
        if len(after) < len(issues):
            views[table] = sql
            lines.append(f'{table:20} revised: {len(issues)} findings -> {len(after)}')
        else:
            lines.append(f'{table:20} revision not kept: {len(issues)} findings -> {len(after)}')
    revised = {**mapping, 'views': views, 'written_by': mapping.get('written_by', '') + '; one audit-driven revision, unedited',
               'usd': round(mapping.get('usd', 0.0) + usd, 4)}
    Path(out).write_text(json.dumps(revised, indent=1) + '\n')
    remaining = audit(revised, layout, source)
    lines.append(f'revision cost ${usd:.4f}; findings before {sum(map(len, found.values()))}, after {sum(map(len, remaining.values()))}; written to {out}')
    return revised, lines


def draft(layout=LAYOUT_B, out=OUT / 'layout_b.drafted.json', source=None):
    """A frontier model drafts the mapping, one semantic-layer table per call, from the semantic layer's documented tables,
    columns, values and governed definitions, and the customer warehouse's schema and short-list values. Each view must
    execute (one corrected retry on an error). Nothing is edited by hand. -> (mapping, receipt lines)."""
    from . import catalogue, models
    source = Path(source or store.DEFAULT_DB)
    orig = sqlite3.connect(source.resolve().as_uri() + '?mode=ro', uri=True)
    version = dict(orig.execute("SELECT key, value FROM meta WHERE key IN ('version', 'source_digest', 'as_of')"))
    old = {k: os.environ.get(k) for k in ('AGERE_TIERED_DB', 'AGERE_TIERED_MAPPING', 'AGERE_TIERED_FOREIGN')}
    os.environ['AGERE_TIERED_DB'], os.environ['AGERE_TIERED_FOREIGN'] = str(layout), 'on'; os.environ.pop('AGERE_TIERED_MAPPING', None)
    try:
        customer = store.schema_text() + '\n\nCustomer text columns with few distinct values:\n' + store.vocabulary_text()
        conn = sqlite3.connect(Path(layout).resolve().as_uri() + '?mode=ro', uri=True)
    finally:
        for k, v in old.items():
            if v is None: os.environ.pop(k, None)
            else: os.environ[k] = v
    governed = '\n'.join(f'- {name}: {text}' for name, _, text in catalogue.DEFINITIONS)
    targets = [t for t in store.TABLES if t != 'document'] + ['measurement', 'payer_claim']
    views, lines, usd = {}, [], 0.0
    for table in targets:
        cols = [(c[1], c[2]) for c in orig.execute(f'PRAGMA table_info("{table}")')]
        vocab = []
        for col, kind in cols:
            if 'INT' in (kind or '').upper() or 'REAL' in (kind or '').upper() or col.endswith('_id') or 'date' in col: continue
            vals = [r[0] for r in orig.execute(f'SELECT DISTINCT "{col}" FROM "{table}" WHERE "{col}" IS NOT NULL LIMIT 81')]
            if 0 < len(vals) <= 80 and all(isinstance(v, str) for v in vals): vocab.append(f'  {col}: ' + ', '.join(sorted(vals)))
        user = (f'Semantic-layer table to produce: {table}({", ".join(c for c, _ in cols)})\n'
                + ('Documented values of its text columns:\n' + '\n'.join(vocab) + '\n' if vocab else '')
                + 'Money columns ending in _cents are integer cents. Boolean columns are 0/1.\n'
                + f'\nThe semantic layer\'s governed definitions:\n{governed}\n\nCustomer warehouse tables and columns:\n{customer}')
        error, verdict = None, None
        for attempt in range(2):
            text, step = models._claude(models.LLM, DRAFT_SYSTEM, user + (f'\n\nYour previous SQL failed: {error}\nReturn a corrected JSON object.' if error else ''))
            usd += step.get('usd', 0.0)
            try:
                got = models._json(text)
            except Exception as exc:
                error = f'not valid JSON: {exc}'; continue
            if got.get('cannot_map'):
                verdict = f'not mapped: {str(got["cannot_map"])[:160]}'; break
            try:
                conn.execute(f'SELECT * FROM ({got["sql"]}) LIMIT 1').fetchall()
            except Exception as exc:
                error = f'{type(exc).__name__}: {exc}'; continue
            views[table] = got['sql']
            verdict = 'mapped' + (f'; no source for {", ".join(got.get("unmapped_columns") or [])}' if got.get('unmapped_columns') else '')
            break
        lines.append(f'{table:20} {verdict or "failed: " + str(error)[:160]}')
    mapping = {'schema': 'agere-mapping/1', 'semantic_layer': f"{version['version']}:{version['source_digest'][:16]}", 'as_of': version['as_of'],
               'warehouse': str(layout), 'written_by': f'{models.LLM}, unedited, {time.strftime("%Y-%m-%d")}', 'usd': round(usd, 4), 'views': views}
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(mapping, indent=1) + '\n')
    lines.append(f'cost ${usd:.4f}; {len(views)} of {len(targets)} tables mapped; written to {out}')
    return mapping, lines


if __name__ == '__main__':
    cmd = sys.argv[1:2]
    if cmd == ['build']:
        print(json.dumps(build(), indent=1))
    elif cmd == ['check']:
        print(check(sys.argv[2]))
    elif cmd == ['audit']:
        for table, issues in audit(json.loads(Path(sys.argv[2]).read_text())).items():
            print(table); [print('  -', i[:300]) for i in issues]
    elif cmd == ['revise']:
        os.environ.pop('ANTHROPIC_API_KEY', None)
        from run_integrated_stack import keys
        keys(); os.environ.pop('ANTHROPIC_API_KEY', None)
        print('\n'.join(revise()[1]))
    elif cmd == ['draft']:
        os.environ.pop('ANTHROPIC_API_KEY', None)
        from run_integrated_stack import keys
        keys(); os.environ.pop('ANTHROPIC_API_KEY', None)
        print('\n'.join(draft()[1]))
    else:
        print(__doc__)
