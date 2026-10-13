"""Run the attached longitudinal layer against synthetic source records."""
import argparse,json,sqlite3
from collections import defaultdict
from datetime import date
from pathlib import Path
from unittest.mock import patch

from clinical_core.agere.clinical.analysis_graph import catalog
from clinical_core.agere.clinical.graph_planner import propose
from clinical_core.agere.clinical.graph_workbench import longitudinal_examples
from clinical_core.agere.clinical.stack_runtime import Stack
from run_integrated_stack import keys
from clinical_core.agere.clinical.context_student import REQUEST_MAX_BYTES  # shared provider request bound


def independent_counts(path):
    db=sqlite3.connect((path/'stack.sqlite3').resolve().as_uri()+'?mode=ro',uri=True)
    records=defaultdict(list)
    for pid,raw in db.execute("SELECT patient,payload FROM records WHERE stream='adt'"):
        records[pid].append(json.loads(raw))
    patients=db.execute('SELECT count(*) FROM patients').fetchone()[0];db.close()
    pairs=[];ids=set();clinical_dates=0
    for pid,rows in records.items():
        aa=[r for r in rows if r.get('event')=='A01'];bb=[r for r in rows if r.get('event')=='A03']
        clinical_dates+=sum(bool(r.get('admission_date')) for r in aa)
        for a in aa:
            for b in bb:
                if a['record_id']==b['record_id'] or not a.get('organization_id') or a['organization_id']!=b.get('organization_id'):continue
                if a['available_date']>'2026-09-30' or b['available_date']>'2026-09-30':continue
                days=(date.fromisoformat(b['event_date'])-date.fromisoformat(a['event_date'])).days
                if 0<=days<=30:
                    ids.add(pid);pairs.append({'patient_id':pid,'admission_record':a['record_id'],'discharge_record':b['record_id'],'days':days})
    return {'patients':patients,'adt_patients':len(records),'sequence_patients':len(ids),
            'observed_pairs':len(pairs),'admissions_with_explicit_clinical_date':clinical_dates,'pairs':pairs}


def main():
    parser=argparse.ArgumentParser();parser.add_argument('--stack',type=Path,default=Path('artifacts/agere-population-stack-v3'))
    parser.add_argument('--live',action='store_true');args=parser.parse_args()
    if args.live:keys()
    stack=Stack(args.stack,allow_live=args.live);out=args.stack/'examples';examples=longitudinal_examples()
    try:
        summary={'synthetic':True,'new_paid_api_calls':0,'results':{}}
        results=[]
        for name,example in zip(('longitudinal-source','longitudinal-clinical'),examples):
            r=stack.run_graph(example['graph']);results.append(r)
            (out/(name+'.json')).write_text(json.dumps(r,indent=2,allow_nan=False))
            replay=stack.run_graph(r['graph']);assert replay['cache_hit'] and replay['new_paid_api_calls']==0
            summary['results'][name]={'result_id':r['result_id'],'summary':r['results'][-1]['result']['summary'],
                                      'cache_replay_paid_calls':replay['new_paid_api_calls']}
        direct=independent_counts(args.stack)
        sequence=results[0]['results'][1]['result']
        assert sequence['counts']['matched']==direct['sequence_patients']
        assert sequence['observed_pairs']==direct['observed_pairs']
        summary['independent_check']={k:v for k,v in direct.items() if k!='pairs'}
        (out/'longitudinal-independent-counts.json').write_text(json.dumps(direct,indent=2))
        def request_size(body,*a,**kw):
            size=len(json.dumps(body).encode());assert size<=REQUEST_MAX_BYTES,size
            summary['maximum_ascii_question_request_bytes']=size
        with patch('clinical_core.agere.clinical.graph_planner.send',side_effect=request_size):
            propose('x'*1500,args.stack,None,snapshot=stack.retriever.meta['snapshot'],graph_catalog=catalog(args.stack),
                    observed_values=json.loads((args.stack/'cohort-values.json').read_text()))
        if args.live:
            r=stack.compose(examples[0]['question'],planning='frontier');assert r['graph']['coverage']=='supported_observation'
            assert {'sequence','longitudinal'}<=set(x['node']['operation'] for x in r['results'])
            seq=[x['result'] for x in r['results'] if x['node']['operation']=='sequence']
            assert len(seq)==1 and seq[0]['counts']['matched']==direct['sequence_patients']
            (out/'frontier-longitudinal.json').write_text(json.dumps(r,indent=2,allow_nan=False))
            summary['frontier']={'result_id':r['result_id'],'operations':[x['node']['operation'] for x in r['results']],
                                'matched':seq[0]['counts']['matched'],'planner_receipt':r['planner_receipt']}
            summary['new_paid_api_calls']+=r['new_paid_api_calls']
        (out/('longitudinal-execution-live.json' if args.live else 'longitudinal-execution.json')).write_text(json.dumps(summary,indent=2))
        print(json.dumps(summary,indent=2))
    finally:stack.close()


if __name__=='__main__':main()
