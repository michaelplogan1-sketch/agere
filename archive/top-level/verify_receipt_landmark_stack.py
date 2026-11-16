"""Independent raw-source timing and protected-partition audit for new landmarks."""
import argparse
from collections import Counter
import json
from pathlib import Path
import sqlite3
import zlib
from clinical_core.contracts import digest
from clinical_core.agere.active import stack_path
from clinical_core.agere.clinical import decision_landmarks as D, outcome_learning as Q


def main():
    p=argparse.ArgumentParser();p.add_argument('--output',required=True,type=Path);a=p.parse_args()
    stack=stack_path();db=sqlite3.connect(stack/'stack.sqlite3');receipts=D.storage(stack)
    expected={r[0] for r in db.execute('SELECT id FROM patients')};results=[]
    try:
        for i in (1,2):
            folder=a.output/f'dataset-{i}';manifest=json.loads((folder/'manifest.json').read_text())
            assert D.prepare(stack,manifest['parameters'])['identity']==manifest['identity']
            xs,ys=[[json.loads(line) for line in (folder/n).read_text().splitlines()] for n in ('features.jsonl','targets.jsonl')]
            assert expected=={x['patient_id'] for x in xs}=={y['patient_id'] for y in ys}
            checks=Counter();statuses=Counter();labels=Counter()
            for x,y in zip(xs,ys):
                assert x['patient_id']==y['patient_id'] and x['baseline_receipt_id']==y['outcome_receipt_id']
                r=json.loads(zlib.decompress(receipts.execute('SELECT payload FROM receipts WHERE id=?',(y['outcome_receipt_id'],)).fetchone()[0]))
                assert r['base']==manifest['source_identity'];statuses[r['status']]+=1
                assert not {'recorded_label','clinical_index_date','decision','patient_id'}&set(x['values'])
                for s in r['baseline']['included_sources']:
                    raw=json.loads(db.execute('SELECT payload FROM records WHERE id=?',(s['record_id'],)).fetchone()[0])
                    assert raw['patient_id']==r['patient_id'] and digest(raw)==s['record_hash']
                    assert raw['event_date']<=x['feature_cutoff'] and raw['available_date']<=x['feature_cutoff']
                    assert x['feature_cutoff']<r['decision']['date'];checks['baseline_raw_records']+=1
                if r['decision']:
                    assert r['decision']['feature_cutoff']==r['index']['receipt_date']
                    for key in r['index']['evidence_ids']:
                        e=r['evidence'][key]
                        for ref in e.get('clinical_date_evidence',[]):
                            assert ref['patient_id']==r['patient_id'] and ref['arrival_date']<=r['decision']['feature_cutoff']
                            checks['index_clock_dependencies']+=1
                for match in r['matches']:
                    assert r['window']['start']<=match['clinical_date']<=r['window']['end']
                    assert r['decision']['feature_cutoff']<match['arrival_date']<=r['parameters']['available_by'];checks['future_endpoints']+=1
                for exclusion in r['excluded_endpoints']:
                    if exclusion['reason']=='clinical_event_before_decision_window':
                        assert all(r['evidence'][key]['clinical_date']<r['window']['start'] for key in exclusion['evidence_ids'])
                        checks['historical_endpoints_excluded']+=1
                q=y['qualification']
                if q['timing_eligible_observed_target']:
                    assert r['calendar_mature'] and r['candidate_observed_target'] and not q['exclusion_reasons']
                    labels[str(y['recorded_label'])]+=1
            qualification=json.loads((a.output/f'learning-{i}/qualification.json').read_text())
            models=json.loads((a.output/f'learning-{i}/models/run.json').read_text());groups={}
            for row in qualification['rows']:
                if row['component'] in groups:assert groups[row['component']]==row['partition']
                groups[row['component']]=row['partition']
            if not qualification['fit_input_contract_ready']:
                assert models['status']=='not_fitted' and not list((a.output/f'learning-{i}/models').glob('*.pkl'))
            results.append({'contract':manifest['parameters']['contract'],'patients':len(xs),'status_counts':dict(statuses),
                'candidate_label_counts':dict(labels),'source_checks':dict(checks),'model_status':models['status'],
                'development_eligible_rows':qualification['eligible_rows'],'partition_class_counts':qualification['partition_class_counts']})
        # The older failed experiment must remain usable and unchanged.
        previous=Q.attachment_status(stack)
        assert len(previous['reports'])==3 and all(r['status']=='not_fitted' for r in previous['reports'])
        result={'synthetic':True,'passed':True,'tasks':results,'prior_index_day_reports_still_valid':True,
            'new_paid_api_calls':0,'clinical_prediction_authorized':False}
        Q.write(a.output/'verification.json',result);print(json.dumps(result,indent=2))
    finally:db.close();receipts.close()


if __name__=='__main__':main()
