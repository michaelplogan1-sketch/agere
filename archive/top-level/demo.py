"""Offline synthetic baseline. No trained model or real clinical data is used."""
import html
import json
import re
import sqlite3
from datetime import date, timedelta
from pathlib import Path

ROOT = Path(__file__).resolve().parent
AS_OF = date(2026, 10, 6)
ONTOLOGY = {
    'DEMO:T2D': {'label': 'Type 2 diabetes', 'parent': 'DEMO:DIABETES'},
    'DEMO:DIABETES': {'label': 'Diabetes', 'parent': None},
}
# Deliberately narrow templates: unsupported language goes to review.
TEMPLATES = [
    ('present', r'Patient has ({})\.'),
    ('negated', r'No evidence of ({})\.'),
    ('family_history', r'Mother has ({})\.'),
    ('uncertain', r'Possible ({})\.'),
    ('historical', r'History of ({})\.'),
]
NOTES = [
    ('P01', 'N01', '2026-09-01', 'Patient has type 2 diabetes.'),
    ('P02', 'N02', '2026-09-02', 'No evidence of type 2 diabetes.'),
    ('P03', 'N03', '2026-09-03', 'Mother has type 2 diabetes.'),
    ('P04', 'N04', '2026-09-04', 'Possible type 2 diabetes.'),
    ('P05', 'N05', '2026-09-05', 'Patient has T2DM.'),
    ('P06', 'N06', '2026-09-06', 'Patient has type 2 diabetes.'),
    ('P07', 'N07', '2026-09-07', 'History of type 2 diabetes.'),
    ('P08', 'N08', '2026-09-08', 'Patient has type 2 diabetes.'),
]
LABS = [
    ('L01', 'P01', '2026-09-10', 9.1, '%'),
    ('L02', 'P02', '2026-09-10', 8.4, '%'),
    ('L03', 'P03', '2026-09-10', 8.7, '%'),
    ('L04', 'P04', '2026-09-10', 8.8, '%'),
    ('L05', 'P05', '2026-09-10', 8.9, '%'),
    ('L06', 'P06', '2025-01-01', 9.5, '%'),
    ('L07', 'P07', '2026-09-10', 8.8, '%'),
    ('L08', 'P08', '2026-07-01', 9.0, '%'),
    ('L09', 'P08', '2026-09-10', 7.2, '%'),
]
CLAIMS = [('C01', 'P01', '2026-09-01', 'type 2 diabetes', 'paid'),
          ('C05', 'P05', '2026-08-01', 'type 2 diabetes', 'paid'),
          ('C02', 'P02', '2026-09-01', 'type 2 diabetes', 'paid')]
PHARMACY = [('RX01', 'P01', '2026-09-02', 'metformin', 'dispensed'),
            ('RX05', 'P05', '2026-08-03', 'metformin', 'reversed')]
RISK = [('R01', 'P01', '2026', 'DEMO:DIABETES', 'recorded in synthetic feed'),
        ('R02', 'P02', '2026', 'DEMO:DIABETES', 'recorded in synthetic feed')]


def classify(text, version):
    terms = 'type 2 diabetes' if version == 'v1' else 'type 2 diabetes|T2DM'
    for status, template in TEMPLATES:
        match = re.fullmatch(template.format(terms), text, re.I)
        if match:
            return 'DEMO:T2D', status, match.start(1), match.end(1)
    return None, 'review', None, None


def build_database(path=':memory:'):
    db = sqlite3.connect(path)
    db.row_factory = sqlite3.Row
    db.executescript('''
      CREATE TABLE notes(patient TEXT, id TEXT PRIMARY KEY, recorded TEXT, text TEXT);
      CREATE TABLE labs(id TEXT PRIMARY KEY, patient TEXT, measured TEXT, value REAL, unit TEXT);
      CREATE TABLE concepts(id TEXT PRIMARY KEY, label TEXT, parent TEXT);
      CREATE TABLE claims(id TEXT PRIMARY KEY, patient TEXT, service_date TEXT,
        diagnosis TEXT, status TEXT);
      CREATE TABLE pharmacy(id TEXT PRIMARY KEY, patient TEXT, fill_date TEXT,
        drug TEXT, status TEXT);
      CREATE TABLE risk_feed(id TEXT PRIMARY KEY, patient TEXT, year TEXT,
        category TEXT, status TEXT);
      CREATE TABLE annotations(note TEXT, version TEXT, concept TEXT, status TEXT,
        span_start INTEGER, span_end INTEGER, classifier TEXT, ontology_version TEXT,
        PRIMARY KEY(note, version));
    ''')
    db.executemany('INSERT INTO notes VALUES (?,?,?,?)', NOTES)
    db.executemany('INSERT INTO labs VALUES (?,?,?,?,?)', LABS)
    db.executemany('INSERT INTO claims VALUES (?,?,?,?,?)', CLAIMS)
    db.executemany('INSERT INTO pharmacy VALUES (?,?,?,?,?)', PHARMACY)
    db.executemany('INSERT INTO risk_feed VALUES (?,?,?,?,?)', RISK)
    db.executemany('INSERT INTO concepts VALUES (?,?,?)',
                   [(k, v['label'], v['parent']) for k, v in ONTOLOGY.items()])
    for version in ('v1', 'v2'):
        for _, note, _, text in NOTES:
            db.execute('INSERT INTO annotations VALUES (?,?,?,?,?,?,?,?)',
                       (note, version, *classify(text, version),
                        'synthetic-template-baseline-' + version, 'demo-ontology-1'))
    db.commit()
    return db


COHORT_SQL = '''
WITH RECURSIVE concepts_in_scope(id) AS (
  SELECT id FROM concepts WHERE id = :concept
  UNION ALL
  SELECT c.id FROM concepts c JOIN concepts_in_scope s ON c.parent = s.id
), latest_lab AS (
  SELECT *, ROW_NUMBER() OVER (PARTITION BY patient ORDER BY measured DESC, id DESC) AS rn
  FROM labs WHERE measured <= :as_of AND unit = '%'
)
SELECT DISTINCT n.patient, n.id AS note_id, n.text AS evidence,
  a.concept, a.status, l.id AS lab_id, l.measured, l.value
FROM notes n JOIN annotations a ON a.note = n.id
JOIN latest_lab l ON l.patient = n.patient AND l.rn = 1
WHERE a.version = :version AND a.status = 'present'
  AND a.concept IN (SELECT id FROM concepts_in_scope)
  AND n.recorded BETWEEN :since AND :as_of
  AND l.measured >= :since AND l.value > :threshold
ORDER BY n.patient
'''


def cohort(db, version='v2', concept='DEMO:DIABETES'):
    return [dict(row) for row in db.execute(COHORT_SQL, {
        'version': version, 'concept': concept, 'threshold': 8.0,
        'as_of': AS_OF.isoformat(), 'since': (AS_OF - timedelta(days=180)).isoformat(),
    })]


def report(db):
    previous, current = cohort(db, 'v1'), cohort(db, 'v2')
    annotations = [dict(r) for r in db.execute('''
      SELECT n.patient, n.id, n.text, a.status, a.version, a.concept,
             a.span_start, a.span_end, a.classifier, a.ontology_version
      FROM notes n JOIN annotations a ON a.note=n.id ORDER BY n.id, a.version
    ''')]
    cross_source = []
    for patient in sorted({row[0] for row in NOTES}):
        cross_source.append({'patient': patient, **{
            table: [dict(row) for row in db.execute(
                'SELECT * FROM ' + table + ' WHERE patient = ?', (patient,))]
            for table in ('claims', 'pharmacy', 'risk_feed')
        }})
    result = {'mode': 'synthetic deterministic baseline; no LLM or System One weights loaded',
              'as_of': str(AS_OF), 'population_size': len(NOTES),
              'previous_cohort': previous, 'current_cohort': current,
              'annotations': annotations, 'cross_source_evidence': cross_source}
    (ROOT / 'results.json').write_text(json.dumps(result, indent=2))
    esc = lambda x: html.escape(str(x))
    evidence_rows = ''.join('<tr>' + ''.join('<td>' + esc(r[k]) + '</td>' for k in
        ('patient', 'note_id', 'evidence', 'lab_id', 'measured', 'value')) + '</tr>' for r in current)
    annotation_rows = ''.join('<tr>' + ''.join('<td>' + esc(r[k]) + '</td>' for k in
        ('patient', 'text', 'version', 'status', 'concept')) + '</tr>' for r in annotations)
    source_rows = ''.join('<tr><td>' + esc(r['patient']) + '</td>' + ''.join(
        '<td>' + ('<br>'.join(esc(json.dumps(item)) for item in r[table]) or
                   'Not observed in supplied demo feed') + '</td>'
        for table in ('claims', 'pharmacy', 'risk_feed')) + '</tr>' for r in cross_source)
    document = '''<!doctype html><html lang="en"><meta charset="utf-8">
    <meta name="viewport" content="width=device-width,initial-scale=1">
    <title>Local clinical insights — synthetic baseline</title>
    <style>body{font:16px/1.6 system-ui;margin:40px auto;max-width:1100px;padding:0 24px;
    background:#f5f7f9;color:#172b38}h1{font-size:38px;line-height:1.15}h2{margin-top:36px}
    .badge{color:#23675e;font-weight:700} .card{background:white;border:1px solid #dbe3e9;
    padding:24px;border-radius:14px;margin:18px 0}table{border-collapse:collapse;width:100%;
    font-size:14px}td,th{padding:12px;text-align:left;border-bottom:1px solid #dbe3e9}
    .scroll{overflow:auto}code{background:#e7edf2;padding:2px 5px}summary{cursor:pointer}
    </style><p class="badge">LOCAL RESEARCH PROTOTYPE · SYNTHETIC DATA ONLY</p>
    <h1>Every cohort result has a source.</h1>
    <p>This executable baseline demonstrates ontology mapping, assertion classification,
    versioned reclassification, and SQL analytics. No trained model is connected.</p>
    <div class="card"><strong>Question</strong><p>Which synthetic patients have an explicit
    present type 2 diabetes statement and a latest HbA1c greater than 8%, with both evidence
    and measurement within the 180 days ending October 6, 2026?</p>
    <p>The threshold is a demonstration filter, not a clinical care recommendation.</p></div>
    <div class="card"><h2>RESULT_COUNT of 8 synthetic patients match</h2>
    <p>Before adding the T2DM synonym: OLD_COUNT. After reclassification: RESULT_COUNT.
    Original annotations remain available below.</p>
    <div class="scroll"><table><tr><th>Patient</th><th>Note</th><th>Exact source text</th>
    <th>Lab</th><th>Date</th><th>HbA1c %</th></tr>EVIDENCE_ROWS</table></div></div>
    <h2>Why other records do not qualify</h2><p>P02 is negated; P03 refers to a family member;
    P04 is uncertain; P06 has an old lab; P07 is historical; P08's latest lab is below the
    filter despite an older high value. Exclusion does not establish absence of disease.</p>
    <h2>Claims, pharmacy and risk evidence alongside the clinical record</h2>
    <p>P02 has a paid diagnosis claim and a risk-feed entry despite a negated note: this is a
    discrepancy to investigate. P05 has a reversed pharmacy transaction, which is not evidence
    of dispensing. These source facts do not override the note or prove medication use.</p>
    <div class="scroll card"><table><tr><th>Patient</th><th>Claims</th><th>Pharmacy</th>
    <th>Risk feed (demo categories)</th></tr>SOURCE_ROWS</table></div>
    <p>Missing rows mean not observed in the supplied feed. Enrollment, feed completeness,
    claim replacements and reversals must be resolved before production care-gap analysis.</p>
    <details class="card"><summary>Inspect classification history (16 annotations)</summary>
    <div class="scroll"><table><tr><th>Patient</th><th>Source</th><th>Version</th><th>Status</th>
    <th>Concept</th></tr>ANNOTATION_ROWS</table></div></details>
    <h2>Next: replace the baseline and measure the difference</h2><p>A local System One model
    selects concepts and assertion labels. A local LLM handles question interpretation and
    evidence summaries. A reclassification service reruns affected records after terminology,
    model, or reviewer changes. SQL continues to calculate the cohort.</p>
    <p>Current limits: eight authored notes, narrow sentence templates, a two-node demo ontology,
    one note per patient, one lab type. No real clinical validation or full OMOP/FHIR implementation.</p>
    </html>'''
    for key, value in {'RESULT_COUNT': str(len(current)), 'OLD_COUNT': str(len(previous)),
                       'EVIDENCE_ROWS': evidence_rows, 'ANNOTATION_ROWS': annotation_rows,
                       'SOURCE_ROWS': source_rows}.items():
        document = document.replace(key, value)
    (ROOT / 'report.html').write_text(document)
    return result


if __name__ == '__main__':
    # In-memory execution avoids overwriting any persisted clinical database.
    with build_database() as db:
        result = report(db)
    print(json.dumps({'mode': result['mode'], 'before': len(result['previous_cohort']),
                      'after': len(result['current_cohort']), 'report': str(ROOT / 'report.html')}, indent=2))
