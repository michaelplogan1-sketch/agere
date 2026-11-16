"""Independent current-source timing, join and protected-boundary verification."""
from collections import Counter
import json
from pathlib import Path
import sqlite3
import zlib
from clinical_core.contracts import digest
from clinical_core.agere.active import stack_path
from clinical_core.agere.clinical import prediction_registry as R
from clinical_core.agere.clinical.native_snapshot_cache import install, uninstall

OUT=Path('artifacts/agere-prediction-qualification-v1')


def main():
    stack=stack_path();db=sqlite3.connect(stack/'stack.sqlite3');roster={r[0] for r in db.execute('SELECT id FROM patients')};results=[]
    install(stack)
    try:
        for index in range(1,6):
            root=OUT/f'learning-{index}';report=R.read(root/'run.json');dataset=Path(report['dataset']);manifest=R.read(dataset/'manifest.json')
            xs,ys=[[json.loads(line) for line in (dataset/n).read_text().splitlines()] for n in ('features.jsonl','targets.jsonl')]
            assert roster=={r['patient_id'] for r in xs}=={r['patient_id'] for r in ys}
            assert len(xs)==len(ys)==len(roster)
            source=sqlite3.connect(stack/('decision-landmarks.sqlite3' if report['task']['adapter']=='receipt_day' else 'baseline-evidence.sqlite3'))
            odb=sqlite3.connect(stack/'outcomes.sqlite3');counts=Counter();eligible=Counter()
            try:
                for x,y in zip(xs,ys):
                    assert x['patient_id']==y['patient_id'] and x['baseline_receipt_id']==y['baseline_receipt_id']
                    assert not {'recorded_label','qualification','patient_id','outcome_status','decision'}&set(x['values'])
                    row=json.loads(zlib.decompress(source.execute('SELECT payload FROM receipts WHERE id=?',(x['baseline_receipt_id'],)).fetchone()[0]))
                    baseline=row['baseline'] if report['task']['adapter']=='receipt_day' else row
                    for ref in baseline['included_sources']:
                        record=json.loads(db.execute('SELECT payload FROM records WHERE id=?',(ref['record_id'],)).fetchone()[0])
                        assert record['patient_id']==x['patient_id'] and digest(record)==ref['record_hash']
                        assert record['event_date']<=x['feature_cutoff'] and record['available_date']<=x['feature_cutoff'];counts['source_references']+=1
                    q=y['qualification']
                    if q['timing_eligible_observed_target']:
                        assert not q['exclusion_reasons'] and y['clinical_label'] is None;eligible[str(y['recorded_label'])]+=1
                    if report['task']['adapter']=='receipt_day':
                        if row['decision']:
                            assert row['decision']['feature_cutoff']==row['index']['receipt_date']
                            for key in row['index']['evidence_ids']:
                                for ref in row['evidence'][key].get('clinical_date_evidence',[]):
                                    assert ref['patient_id']==x['patient_id'] and ref['arrival_date']<=row['decision']['feature_cutoff'];counts['index_clock_dependencies']+=1
                        for match in row['matches']:
                            assert row['window']['start']<=match['clinical_date']<=row['window']['end']
                            assert row['decision']['feature_cutoff']<match['arrival_date']<=row['parameters']['available_by'];counts['future_endpoint_checks']+=1
                        for exclusion in row['excluded_endpoints']:
                            if exclusion['reason']=='clinical_event_before_decision_window':
                                assert all(row['evidence'][key]['clinical_date']<row['window']['start'] for key in exclusion['evidence_ids']);counts['historical_endpoint_exclusions']+=1
                        if q['timing_eligible_observed_target']:assert row['calendar_mature'] and row['candidate_observed_target']
                    else:
                        target=json.loads(zlib.decompress(odb.execute('SELECT payload FROM receipts WHERE id=?',(y['outcome_receipt_id'],)).fetchone()[0]))
                        if target['index']:
                            dates=[target['evidence'][key]['arrival_date'] for key in target['index']['evidence_ids']]
                            assert any(d and d<=target['index']['date'] for d in dates)==q['index_known_by_index_day']
                            if q['timing_eligible_observed_target']:
                                assert target['window']['end']<=target['window']['available_by'] and x['feature_cutoff']<target['index']['date']
                        else:assert not q['timing_eligible_observed_target'] and 'incomplete_calendar_followup' not in q['exclusion_reasons']
            finally:source.close();odb.close()
            qualification=R.read(root/'qualification.json');policy,lm=R.L.learning_queue.lineage(report['lineage_root'],R.read(stack/'run.json'));protect=R.L.semantic_learning.protections()
            parts={};families={}
            for row in qualification['rows']:
                assert R.L.learning_queue.eligibility(row['patient_id'],policy,protect) is None
                parts.setdefault(row['component'],set()).add(row['partition']);families.setdefault(row['family'],set()).add(row['partition'])
            assert all(len(v)==1 for v in list(parts.values())+list(families.values()))
            assert dict(eligible)==report['source_qualification']['timing_eligible_label_counts']
            assert report['status']=='not_fitted' and not list((root/'models').glob('*.pkl'))
            results.append({'task':report['task']['contract'],'patients':len(xs),'checks':dict(counts),'timing_eligible_labels':dict(eligible),
                'development_eligible':len(qualification['rows']),'partition_class_counts':qualification['partition_class_counts'],'model_status':report['status']})
        attached=R.status(stack);assert attached['all_current'] and len(attached['reports'])==5
        result={'passed':True,'tasks':results,'clinical_prediction_authorized':False,'new_paid_api_calls':0}
        R.L.write(OUT/'source-verification.json',result);print(json.dumps(result,indent=2))
    finally:db.close();uninstall(stack)


if __name__=='__main__':main()
