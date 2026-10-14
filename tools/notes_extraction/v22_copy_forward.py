# Usage: PYTHONPATH=. .venv/bin/python tools/notes_extraction/v22_copy_forward.py [n_patients=120] [out.json]
"""Copy-forward effect of the note_fact loader on v2.2: for a hash-chosen sample of patients with >= 3 signed notes, load all
their notes and HIE documents into a scratch `record` table, run note_facts.rows (every fact type admitted) and report rows
kept vs dropped as copied-forward text, by fact type and section, with examples. Read-only on the release; CPU-light."""
import hashlib, json, sqlite3, sys
from collections import Counter
from clinical_core.agere.clinical import note_facts

R = '/Users/mikel/synthetic-patient-ecosystem/releases/healthcare-warehouse-v2.2/warehouse.sqlite'


def main(n=120, out=None):
    src = sqlite3.connect(f'file:{R}?mode=ro', uri=True)
    counts = src.execute("SELECT patient_id, count(*) FROM source_records WHERE source_table = 'notes' AND "
                         "json_extract(facts, '$.template_style') IS NOT NULL GROUP BY patient_id HAVING count(*) >= 3").fetchall()
    pids = sorted((p for p, _ in counts), key=lambda p: hashlib.sha256(('cf:' + p).encode()).hexdigest())[:n]
    db = sqlite3.connect(':memory:')
    db.execute('CREATE TABLE record(record_id TEXT PRIMARY KEY, patient_id TEXT, stream TEXT, event_date TEXT, recorded_date TEXT, payload TEXT)')
    for i in range(0, len(pids), 200):
        chunk = pids[i:i + 200]
        db.executemany('INSERT INTO record VALUES (?,?,?,?,?,?)', src.execute(
            f"SELECT record_id, patient_id, source_table, event_date, recorded_date, facts FROM source_records WHERE patient_id IN "
            f"({','.join('?' * len(chunk))}) AND source_table IN ('notes', 'hie_documents')", chunk).fetchall())
    stats = {}
    kept = list(note_facts.rows(db, set(note_facts.FACT_DEFINITIONS), stats))
    # re-run without dedup to attribute the dropped facts
    allf = Counter(); dropped = Counter(); changed = Counter(); examples = []
    cur, last, prev = None, {}, {}
    for rid, pid, stream, ev, rec, payload in db.execute(
            "SELECT record_id, patient_id, stream, event_date, recorded_date, payload FROM record WHERE stream IN "
            f"({','.join('?' * len(note_facts.TEXT_SOURCES))}) ORDER BY patient_id, coalesce(event_date, recorded_date), record_id",
            list(note_facts.TEXT_SOURCES)):
        if pid != cur: cur, last, prev = pid, {}, {}
        for field, text in note_facts.units((rid, stream, payload)):
            facts = note_facts.extract(text, stream, field)
            allf.update(f['fact_type'] for f in facts)
            before = dict(last)
            kept_here, _ = note_facts.without_copy_forward(facts, last)
            ids = {id(f) for f in kept_here}
            for f in facts:
                s = note_facts.copy_forward_slot(f)
                if s is None: continue
                if id(f) in ids:
                    if s in before: changed[(f['fact_type'], f['section'])] += 1  # line changed since the previous note: kept
                    continue
                dropped[(f['fact_type'], f['section'])] += 1
                if len(examples) < 12 and prev.get(s, (None,))[0] != ev:
                    examples.append({'patient': pid, 'fact': [f['fact_type'], str(f['value']), f['negated']],
                                     'sentence': f['context'], 'previous_note': prev.get(s), 'copy': [rid, ev]})
            for f in facts:
                s = note_facts.copy_forward_slot(f)
                if s is not None: prev[s] = (ev, rid)
    report = {'patients': len(pids), 'records': db.execute('SELECT count(*) FROM record').fetchone()[0],
              'facts_without_dedup': sum(allf.values()), 'rows_kept': len(kept), 'copy_forward_dropped': stats['copy_forward_dropped'],
              'dropped_by_type_section': {f'{a}|{b}': c for (a, b), c in dropped.most_common()},
              'kept_changed_line_by_type_section': {f'{a}|{b}': c for (a, b), c in changed.most_common()}, 'examples': examples}
    print(json.dumps({k: v for k, v in report.items() if k != 'examples'}, indent=1))
    for e in examples[:6]: print(e)
    if out: open(out, 'w').write(json.dumps(report, indent=1))


if __name__ == '__main__':
    main(int(sys.argv[1]) if len(sys.argv) > 1 else 120, sys.argv[2] if len(sys.argv) > 2 else None)
