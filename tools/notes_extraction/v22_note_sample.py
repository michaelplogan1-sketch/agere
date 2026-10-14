# Usage: PYTHONPATH=. .venv/bin/python tools/notes_extraction/v22_note_sample.py <out units.json>   (200 units: 150 signed notes over style x type, 20 problem_list_update, 30 HIE)
"""Run the rule extractor (note_facts rules/1) on a stratified sample of v2.2's new signed notes; dump facts for labelling."""
import hashlib, json, sqlite3, sys
from collections import Counter, defaultdict
from clinical_core.agere.clinical import note_facts

R = '/Users/mikel/synthetic-patient-ecosystem/releases/healthcare-warehouse-v2.2/warehouse.sqlite'
OUT = sys.argv[1]
db = sqlite3.connect(f'file:{R}?mode=ro', uri=True)
h = lambda s: hashlib.sha256(s.encode()).hexdigest()
strata = defaultdict(list)
for rid, st, f in db.execute("SELECT record_id, source_table, facts FROM source_records WHERE source_table IN ('notes','hie_documents') "
                             "AND (json_extract(facts,'$.template_style') IS NOT NULL OR json_extract(facts,'$.note_type')='problem_list_update' "
                             "OR source_table='hie_documents')"):
    f = json.loads(f)
    key = 'hie:' + str(f.get('document_type')) if st == 'hie_documents' else (
        'plu' if f.get('note_type') == 'problem_list_update' else f"{f.get('template_style')}|{f.get('note_type')}")
    strata[key].append((h(rid), rid, st, f))
print({k: len(v) for k, v in sorted(strata.items())}, file=sys.stderr)
# quotas: 150 new-note units spread over style x type, 20 problem_list_update, 30 HIE (not a current text source)
note_keys = sorted(k for k in strata if '|' in k)
chosen = []
per = max(1, 150 // len(note_keys))
for k in note_keys: chosen += sorted(strata[k])[:per]
chosen = chosen[:150]
chosen += sorted(strata['plu'])[:20]
for k in sorted(k for k in strata if k.startswith('hie:')): chosen += sorted(strata[k])[:15]
units = []
for _, rid, st, f in chosen:
    text = f.get('text') or ''
    facts = note_facts.extract(text, 'notes' if st == 'notes' else st, 'text')
    units.append({'record_id': rid, 'stream': st, 'note_type': f.get('note_type') or f.get('document_type'),
                  'template_style': f.get('template_style'), 'author_credential': f.get('author_credential'), 'text': text,
                  'facts': [{k: x[k] for k in ('fact_type', 'value', 'negated', 'temporality', 'qualifier', 'span_start', 'span_end', 'evidence_text')}
                            for x in facts]})
json.dump(units, open(OUT, 'w'), indent=1)
c = Counter(x['fact_type'] for u in units for x in u['facts'])
print(len(units), 'units', sum(c.values()), 'facts', dict(c), file=sys.stderr)
