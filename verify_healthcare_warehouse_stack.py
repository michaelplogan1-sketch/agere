"""Exercise the connected original cohort; never reuse old-population results."""
import argparse
import hashlib
import json
import sqlite3
from pathlib import Path
from unittest.mock import patch
from clinical_core.agere.clinical.stack_runtime import Stack
from clinical_core.agere.clinical.graph_workbench import baseline_examples,snapshot_examples
from clinical_core.agere.clinical import analysis_graph,graph_planner
from clinical_core.agere.warehouse_activation import code_identity as qualification_code


def write(path,value):path.write_text(json.dumps(value,indent=2,allow_nan=False)+'\n')


def main():
    p=argparse.ArgumentParser();p.add_argument('--build',type=Path,required=True);a=p.parse_args()
    out=a.build.resolve();root=Path(__file__).resolve().parent;connection=json.loads((out/'connection.json').read_text())
    assert connection['status']=='connected'
    source=Path(connection['snapshot']['source_dataset']['source'])
    # Independent queries on public raw input, not engine conclusions.
    src=sqlite3.connect(source.as_uri()+'?mode=ro',uri=True)
    db=sqlite3.connect(out/'stack'/'stack.sqlite3')
    try:
        expected={p[0] for p in src.execute('SELECT patient_id FROM patients')}
        actual={p[0] for p in db.execute('SELECT id FROM patients')}
        assert actual==expected
        source_counts=dict(src.execute('SELECT source_table,count(*) FROM source_records GROUP BY source_table'))
        counts=dict(db.execute('SELECT stream,count(*) FROM records GROUP BY stream'))
        assert counts==source_counts
        indexed=db.execute("SELECT count(DISTINCT json_extract(source,'$.record_id')) FROM passages").fetchone()[0]
        assert indexed==sum(counts.values())
        financial={pid for pid,raw in src.execute("SELECT patient_id,facts FROM source_records WHERE source_table='financial_screens'") if json.loads(raw).get('difficulty_paying_medical_bills') is True}
        adt=dict(src.execute("SELECT json_extract(facts,'$.event'),count(*) FROM source_records WHERE source_table='adt' GROUP BY 1"))
    finally:src.close();db.close()
    assert not (out/'stack'/'semantic-student-link.json').exists(), 'Frozen students require separate new-source qualification'
    examples=out/'verification';examples.mkdir(exist_ok=True)
    stack=Stack(out/'stack',allow_live=False)
    try:
        results=[]
        for i,q in enumerate(['Inspect source completeness','Calculate renal patterns','Reconcile warehouse source meanings']):
            r=stack.compose(q);write(examples/f'question-{i+1}.json',r)
            assert r['new_paid_api_calls']==0
            assert r['results'][0]['result']['counts']['evaluated']==len(expected)
            results.append({'question':q,'result_id':r['result_id'],'new_paid_api_calls':r['new_paid_api_calls']})
        from clinical_core.agere.clinical.evidence_queries import execute
        r=execute(out/'evidence',{'match':'all','clauses':[{'source':'structured','concept':'financial_hardship','value_equals':True}]},limit=10)
        write(examples/'financial-evidence.json',r)
        assert r['patients_matching_evidence_plan']==len(financial)
        # New source phrases use literal admission/discharge labels, not A01/A03.
        for i,ex in enumerate(snapshot_examples(baseline_examples(),connection['snapshot']['as_of'])):
            r=stack.compose(ex['question']);write(examples/f'baseline-{i+1}.json',r)
            results.append({'question':ex['title'],'status_counts':r['results'][-1]['result']['status_counts'],
                            'new_paid_api_calls':r['new_paid_api_calls']})
        evidence=stack.ask('What do the received records say about financial hardship and difficulty paying for care?')
        write(examples/'retrieval.json',evidence)
        assert evidence['retrieval']['evidence']
        assert all(x['source']['patient_id'] in expected for x in evidence['retrieval']['evidence'])
        from verify_scoped_planner import exercise
        wire=exercise(out/'stack');sizes=[r['request_bytes'] for r in wire['calls']]
        write(examples/'planner-wire.json',wire)
        receipt={'synthetic':True,'original_patient_ids_match':True,'patients':len(actual),'received_records':sum(counts.values()),
            'source_snapshot':connection['snapshot'],'qualification_code_identity':qualification_code(),
            'source_categories':len(counts),'searchable_records':indexed,'direct_financial_report_patients':len(financial),
            'direct_adt_record_counts':adt,'questions':results,'new_paid_api_calls':0,
            'frontier_request_bytes_1500_char_question':max(sizes),
            'clinical_accuracy_measured':False,'source_file_sha256':connection['snapshot']['source_dataset']['source_sha256']}
        receipt['semantic_students']={'attached':False,'reason':'Narrative source contract changed; separate requalification required.'}
        write(out/'verification.json',receipt);print(json.dumps(receipt,indent=2),flush=True)
        if max(sizes)>20000:raise ValueError('New source catalog exceeds existing provider request bound')
    finally:stack.close()


if __name__=='__main__':main()
