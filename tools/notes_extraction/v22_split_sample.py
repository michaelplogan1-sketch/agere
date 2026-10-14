# Usage: PYTHONPATH=. .venv/bin/python tools/notes_extraction/v22_split_sample.py [out_dir]
"""Fresh dev/test samples of v2.2 text units, split by unit-id hash (same rule as label.py: sha256(unit_id) odd = test).

The 200-unit sample drawn by v22_note_sample.py (used to find the v2.2 failure modes) is development data: its units are
excluded from the test sample whatever their hash. Strata: 25 signed-note (template_style x note_type) cells x 6,
20 problem_list_update notes, 20 ED + 20 discharge HIE summaries per split. Writes <out_dir>/{dev,test}_units.jsonl
(unit_id, record_id, patient_id, event_date, stream, note_type, template_style, text). Read-only on the release.
"""
import hashlib, json, sqlite3, sys
from collections import defaultdict
from pathlib import Path

R = '/Users/mikel/synthetic-patient-ecosystem/releases/healthcare-warehouse-v2.2/warehouse.sqlite'
OUT = Path(sys.argv[1] if len(sys.argv) > 1 else '/Users/mikel/clinical-insights-local/artifacts/notes-extraction/v22')
h = lambda s: hashlib.sha256(s.encode()).hexdigest()


def split_of(unit_id):
    return 'test' if int(h(unit_id), 16) % 2 else 'dev'


def main():
    db = sqlite3.connect(f'file:{R}?mode=ro', uri=True)
    strata = defaultdict(list)
    for rid, pid, st, ev, f in db.execute(
            "SELECT record_id, patient_id, source_table, event_date, facts FROM source_records WHERE source_table IN "
            "('notes','hie_documents') AND (json_extract(facts,'$.template_style') IS NOT NULL OR "
            "json_extract(facts,'$.note_type')='problem_list_update' OR source_table='hie_documents')"):
        f = json.loads(f)
        key = 'hie:' + str(f.get('document_type')) if st == 'hie_documents' else (
            'plu' if f.get('note_type') == 'problem_list_update' else f"{f.get('template_style')}|{f.get('note_type')}")
        strata[key].append({'unit_id': f'{rid}#text', 'record_id': rid, 'patient_id': pid, 'event_date': ev, 'stream': st,
                            'note_type': f.get('note_type') or f.get('document_type'), 'template_style': f.get('template_style'),
                            'text': f.get('text') or ''})
    # the original development sample (v22_note_sample.py selection rule)
    note_keys = sorted(k for k in strata if '|' in k)
    per = max(1, 150 // len(note_keys))
    old = []
    for k in note_keys: old += sorted(strata[k], key=lambda u: h(u['record_id']))[:per]
    old = old[:150] + sorted(strata['plu'], key=lambda u: h(u['record_id']))[:20]
    for k in sorted(k for k in strata if k.startswith('hie:')): old += sorted(strata[k], key=lambda u: h(u['record_id']))[:15]
    old_ids = {u['record_id'] for u in old}
    quota = {**{k: 6 for k in note_keys}, 'plu': 20, **{k: 20 for k in strata if k.startswith('hie:')}}
    OUT.mkdir(parents=True, exist_ok=True)
    for split in ('dev', 'test'):
        chosen = []
        for k, n in sorted(quota.items()):
            pool = [u for u in strata[k] if u['record_id'] not in old_ids and split_of(u['unit_id']) == split]
            chosen += sorted(pool, key=lambda u: h('v22-' + split + ':' + u['record_id']))[:n]
        (OUT / f'{split}_units.jsonl').write_text(''.join(json.dumps({**u, 'split': split}, ensure_ascii=False) + '\n' for u in chosen))
        print(split, len(chosen), 'units', file=sys.stderr)
    (OUT / 'original_dev_sample_ids.json').write_text(json.dumps(sorted(old_ids)))


if __name__ == '__main__':
    main()
