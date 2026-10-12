"""Audit actual current-cohort feature timing, joins, exclusions and fit receipts."""
import argparse
from collections import Counter
import json
from pathlib import Path
import sqlite3
import zlib
from clinical_core.agere.active import stack_path
from clinical_core.agere.clinical import outcome_learning as L


def main():
    parser=argparse.ArgumentParser();parser.add_argument('--output',type=Path,required=True);args=parser.parse_args()
    root=args.output;stack=stack_path();db=sqlite3.connect(stack/'stack.sqlite3');odb=sqlite3.connect(stack/'outcomes.sqlite3')
    expected={r[0] for r in db.execute('SELECT id FROM patients')};runs=[]
    try:
        for directory in sorted(root.glob('dataset-*')):
            index=directory.name.split('-')[-1]
            x=[json.loads(line) for line in (directory/'features.jsonl').read_text().splitlines()]
            y=[json.loads(line) for line in (directory/'targets.jsonl').read_text().splitlines()]
            assert {r['patient_id'] for r in x}==expected=={r['patient_id'] for r in y}
            evidence=0;eligible=Counter();late=0;unindexed=0
            for a,b in zip(x,y):
                assert a['patient_id']==b['patient_id'] and a['baseline_receipt_id']==b['baseline_receipt_id']
                assert not {'recorded_label','outcome_status','qualification','patient_id'} & set(a['values'])
                target=json.loads(zlib.decompress(odb.execute('SELECT payload FROM receipts WHERE id=?',(b['outcome_receipt_id'],)).fetchone()[0]))
                q=b['qualification']
                if target['index']:
                    day=target['index']['date'];known=[target['evidence'][k]['arrival_date'] for k in target['index']['evidence_ids']]
                    timely=any(v and v<=day for v in known)
                    assert timely==q['index_known_by_index_day'];late+=not timely
                    if q['timing_eligible_observed_target']:
                        assert timely and target['window']['end']<=target['window']['available_by']
                        assert a['feature_cutoff']<day and b['recorded_label'] in (0,1)
                        eligible[str(b['recorded_label'])]+=1
                else:
                    unindexed+=1;assert not q['timing_eligible_observed_target']
                    assert 'incomplete_calendar_followup' not in q['exclusion_reasons']
                for rid in a['input_record_ids']:
                    source=json.loads(db.execute('SELECT payload FROM records WHERE id=?',(rid,)).fetchone()[0])
                    assert source['patient_id']==a['patient_id']
                    assert source['event_date']<=a['feature_cutoff'] and source['available_date']<=a['feature_cutoff']
                    evidence+=1
            qualification=json.loads((root/f'learning-{index}/qualification.json').read_text())
            models=json.loads((root/f'learning-{index}/models/run.json').read_text())
            groups={}
            for row in qualification['rows']:
                if row['component'] in groups:assert groups[row['component']]==row['partition']
                groups[row['component']]=row['partition']
            if not qualification['fit_input_contract_ready']:
                assert models['status']=='not_fitted' and not list((root/f'learning-{index}/models').glob('*.pkl'))
            runs.append({'dataset':directory.name,'patients':len(x),'pre_index_source_refs_verified':evidence,
                'timing_eligible_labels':dict(eligible),'late_index_patients':late,'unindexed_patients':unindexed,
                'development_eligible_rows':qualification['eligible_rows'],'model_status':models['status']})
    finally:db.close();odb.close()
    current=L.attachment_status(stack)
    assert len(current['reports'])==len(runs)
    L.write(root/'verification.json',{'synthetic':True,'runs':runs,'live_attachment_source_validation':True,
        'new_paid_api_calls':0,'clinical_prediction_authorized':False})
    print(json.dumps(runs,indent=2))


if __name__=='__main__':main()
