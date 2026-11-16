"""Audit source grounding for every annotation and replay a bounded cache sample."""
from pathlib import Path
import json
import sqlite3
import time
import argparse
from clinical_core.agere.warehouse import Warehouse
from clinical_core.agere.narrative_sources import text_fields
from clinical_core.agere.clinical.evidence_reader import EvidenceReader

ROOT = Path(__file__).resolve().parent
OUT = ROOT/'artifacts/agere-evidence-reader-v2'


def main():
    parser=argparse.ArgumentParser()
    parser.add_argument('--model',type=Path,default=ROOT/'artifacts/agere-evidence-reader-v2')
    OUT=parser.parse_args().model.resolve()
    report = json.loads((OUT/'warehouse/run.json').read_text())
    w = Warehouse(ROOT/'artifacts/agere-full/warehouse.sqlite3')
    db = sqlite3.connect((OUT/'warehouse/annotations.sqlite3').as_uri()+'?mode=ro', uri=True)
    reader = None
    try:
        patients, snapshot = w.snapshot(report['snapshot']['release'], report['snapshot']['as_of'])
        if snapshot != report['snapshot']:
            raise ValueError('Snapshot mismatch')
        ocr = {r['record_id']: r for r in json.loads((ROOT/'artifacts/agere-full/document-extractions.json').read_text())['records']}
        records = {}
        for p in patients.values():
            for r in p['records']:
                if r['record_id'] in ocr:
                    e = ocr[r['record_id']]
                    if e['patient_id'] != r['patient_id'] or r['source_table'] != 'documents':
                        raise ValueError('OCR identity mismatch')
                    r = {**r, 'extracted_text': e['text']}
                records[r['record_id']] = r
        n = 0; sample = []
        for (payload,) in db.execute('SELECT payload FROM annotations ORDER BY patient,id'):
            row = json.loads(payload); source = row['source']; r = records[source['record_id']]
            assert row['patient_id'] == r['patient_id'] and row['synthetic'] is True
            for k in ('source_table', 'event_date', 'available_date'):
                assert source[k] == r[k]
            fields = {f['field']: f['text'] for f in text_fields(r)}; text = fields[source['field']]
            assert text[source['start']:source['end']] == source['quote']
            assert text[row['window_start']:row['window_end']] == row['input']['text']
            x = row['input']; assert x['text'][x['target_start']:x['target_end']] == source['quote']
            assert row['annotation']['model_identity'] == report['model_identity']
            if row['annotation'].get('scope'):
                scope=row['annotation']['scope']
                assert x['text'][scope['focus_start']:scope['focus_end']]==scope['focus_text']
                assert scope['focus_start']<=x['target_start']<x['target_end']<=scope['focus_end']
                for cue in scope.get('cue_spans', []):
                    assert x['text'][cue['start']:cue['end']]==cue['quote']
                    assert scope['focus_start']<=cue['start']<cue['end']<=scope['focus_end']
            assert row['decision_authority'] is False and row['annotation']['decision'] == 'abstain'
            n += 1
            if len(sample) < 1000:
                sample.append(row)
        assert n == report['counts']['annotation_bindings']
        from clinical_core.agere.clinical.reader_factory import load_reader
        reader=load_reader(OUT, OUT/'warehouse-predictions.sqlite3')
        started = time.perf_counter(); predictions = reader.predict([r['input'] for r in sample])
        elapsed = time.perf_counter()-started
        assert all(p == r['annotation'] for p, r in zip(predictions, sample))
        assert reader.stats['new_unique_predictions'] == 0
        result = {'synthetic': True, 'status': 'passed', 'exact_source_bindings': n,
                  'cache_replay_examples': len(sample), 'cache_replay_seconds': elapsed,
                  'cache_stats': dict(reader.stats), 'new_paid_api_calls': 0,
                  'scope': 'Source/identity/offset integrity and exact cached prediction replay, not semantic correctness.'}
        (OUT/'warehouse/source-audit.json').write_text(json.dumps(result, indent=2))
        print(json.dumps(result, indent=2))
    finally:
        if reader is not None:
            reader.close()
        w.close(); db.close()


if __name__ == '__main__':
    main()
