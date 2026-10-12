"""Build/attach and execute clinical documentary state over synthetic populations."""
import argparse,json,sqlite3,hashlib
from pathlib import Path
from unittest.mock import patch
from clinical_core.agere.clinical import clinical_state as state
from clinical_core.agere.clinical.analysis_graph import catalog
from clinical_core.agere.clinical.graph_planner import propose
from clinical_core.agere.clinical.graph_workbench import state_examples
from clinical_core.agere.clinical.stack_runtime import Stack
from run_integrated_stack import keys


def main():
    ap=argparse.ArgumentParser();ap.add_argument('--stack',type=Path,default=Path('artifacts/agere-population-stack-v3'))
    ap.add_argument('--build',type=Path);ap.add_argument('--live',action='store_true');args=ap.parse_args()
    if args.build:
        state.build(args.stack,args.build)
        tmp=args.stack/'clinical-state-link.tmp';tmp.write_text(json.dumps({'path':str(args.build.resolve())},indent=2))
        tmp.replace(args.stack/'clinical-state-link.json')
    if args.live:keys()
    stack=Stack(args.stack,allow_live=args.live);out=args.stack/'examples'
    try:
        example=state_examples()[0];r=stack.run_graph(example['graph']);data=r['results'][-1]['result']
        replay=stack.run_graph(example['graph']);assert replay['cache_hit'] and replay['new_paid_api_calls']==0
        (out/'clinical-state-review.json').write_text(json.dumps(r,indent=2))
        db=sqlite3.connect((args.stack/'stack.sqlite3').resolve().as_uri()+'?mode=ro',uri=True)
        direct={'patients':db.execute('SELECT count(*) FROM patients').fetchone()[0],
                'egfr_patients_without_explicit_collection_date':0,'financial_screen_patients':0}
        egfr=set();financial=set();taking_flags=0
        for pid,raw in db.execute("SELECT patient,payload FROM records WHERE stream IN ('labs','financial','medication_history')"):
            row=json.loads(raw)
            if row['source_table']=='labs' and str(row.get('test','')).lower()=='egfr' and not row.get('collection_date') and not row.get('specimen_date'):egfr.add(pid)
            if row['source_table']=='financial' and type(row.get('difficulty_paying_medical_bills')) is bool:financial.add(pid)
            if row['source_table']=='medication_history' and row.get('medication_taking_observed') is False:taking_flags+=1
        direct.update(egfr_patients_without_explicit_collection_date=len(egfr),financial_screen_patients=len(financial),taking_not_observed_source_records=taking_flags)
        assert data['patients_evaluated']==direct['patients']
        # Independent raw flags are explicitly NOT converted into nonadherence counts.
        db.close()
        report={'synthetic':True,'source_identity':data['source_identity'],'patients_evaluated':data['patients_evaluated'],
                'status_counts':data['status_counts'],'requirement_counts':data['requirement_counts'],'independent_source_inventory':direct,
                'new_paid_api_calls':0,'cache_replay_new_paid_api_calls':replay['new_paid_api_calls'],'limits':data['limits']}
        def size(body,*a,**kw):
            count=len(json.dumps(body).encode());report['maximum_ascii_question_request_bytes']=count
            assert count<=20000,count
        with patch('clinical_core.agere.clinical.graph_planner.send',side_effect=size):
            propose('x'*1500,args.stack,None,snapshot=stack.retriever.meta['snapshot'],graph_catalog=catalog(args.stack),
                    observed_values=json.loads((args.stack/'cohort-values.json').read_text()))
        if args.live:
            live=stack.compose(example['question'],planning='frontier');(out/'frontier-clinical-state.json').write_text(json.dumps(live,indent=2))
            results=[x['result'] for x in live['results'] if x['node']['operation']=='state_review']
            assert len(results)==1 and results[0]['patients_evaluated']==direct['patients']
            assert results[0]['requirement_counts']==data['requirement_counts']
            report['frontier']={'coverage':live['graph']['coverage'],'operations':[x['node']['operation'] for x in live['results']],
                                'planner_receipt':live['planner_receipt']}
            report['new_paid_api_calls']=live['new_paid_api_calls']
        target=out/('clinical-state-execution-live.json' if args.live else 'clinical-state-execution.json')
        target.write_text(json.dumps(report,indent=2));print(json.dumps(report,indent=2))
    finally:stack.close()


if __name__=='__main__':main()
