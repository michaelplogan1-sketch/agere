"""Materialize complete synthetic review queues; preserve original run receipts."""
import argparse
import json
from pathlib import Path
import sqlite3
from unittest.mock import patch
from uuid import uuid4

from clinical_core.agere.clinical import program_work as w
from clinical_core.agere.clinical.analysis_graph import catalog
from clinical_core.agere.clinical.graph_planner import propose
from clinical_core.agere.clinical.graph_workbench import work_examples
from clinical_core.agere.clinical.stack_runtime import Stack
from run_integrated_stack import keys
from clinical_core.agere.clinical.context_student import REQUEST_MAX_BYTES  # shared provider request bound


def lifecycle_demo(stack_path, out, proposal):
    """Isolated operational simulation; never add fake completion to the main queue."""
    demo=out/'program-lifecycle-demo';demo.mkdir(exist_ok=True)
    (demo/'run.json').write_bytes((stack_path/'run.json').read_bytes())
    if not (demo/'stack.sqlite3').exists():(demo/'stack.sqlite3').symlink_to((stack_path/'stack.sqlite3').resolve())
    pid=proposal['patients'][0]['patient_id']
    p=w.prepare(demo,'hf_review',[pid]);w.materialize(demo,p['proposal_id'],'synthetic lifecycle demonstration')
    item=w.list_items(demo)['items'][0]
    if item['state']=='completed':return {'isolated_path':str(demo),'replayed_existing_demonstration':True,'detail':w.inspect_item(demo,item['id'])}
    assert item['state']=='review_needed'
    history=w.inspect_item(demo,item['id']);ref=history['assessment']['evidence'][0]['record_id']
    stages=[('assign',{'owner':'Simulated PharmD','due_date':'2026-10-15'}),
        ('start_review',{'note':'Synthetic simulation: chart review'}),
        ('finish_review',{'note':'Synthetic simulation only: offer decision','disposition':'offer_program','record_ids':[ref]}),
        ('offer',{'note':'Synthetic simulation only: no offer actually sent'}),
        ('enroll',{'note':'Synthetic simulation only: no actual consent obtained','consent':'confirmed'}),
        ('start_program',{'note':'Synthetic simulation only: program start'}),
        ('record_monitoring',{'note':'Synthetic simulation only: monitoring report','record_ids':[ref],'due_date':'2026-10-22'}),
        ('complete_program',{'note':'Synthetic simulation only: operational completion, no verified clinical benefit','record_ids':[ref]})]
    for action,data in stages:
        request={'item_id':item['id'],'expected_revision':item['revision'],'request_id':str(uuid4()),
                 'actor':'synthetic lifecycle demonstration','action':action,'data':data}
        result=w.change(demo,request);item=result['item']
        retry=w.change(demo,request);assert retry['idempotent_replay'] and retry['item']==item
    assert item['state']=='completed' and item['clinical_goal_verified'] is False
    return {'isolated_path':str(demo),'operational_simulation_only':True,'detail':w.inspect_item(demo,item['id'])}


def main():
    ap=argparse.ArgumentParser();ap.add_argument('--stack',type=Path,default=Path('artifacts/agere-population-stack-v3'))
    ap.add_argument('--live',action='store_true');args=ap.parse_args()
    if args.live:keys()
    stack=Stack(args.stack,allow_live=args.live);out=args.stack/'examples';out.mkdir(exist_ok=True)
    try:
        report={'synthetic':True,'schema':w.VERSION,'code_identity':w.code_identity(),'programs':{},'new_paid_api_calls':0,'external_actions_executed':0}
        db=sqlite3.connect((args.stack/'stack.sqlite3').resolve().as_uri()+'?mode=ro',uri=True)
        population=db.execute('SELECT count(*) FROM patients').fetchone()[0]
        for example in work_examples():
            r=stack.run_graph(example['graph']);p=r['results'][-1]['result'];program=p['program']['id']
            direct=sum(json.loads(raw)['workflow']['review_candidate'] is True for raw, in db.execute('SELECT payload FROM analyses WHERE operator=?',(p['program']['operator'],)))
            assert p['counts']['candidates']==direct and p['counts']['selected']==population
            saved=w.materialize(args.stack,p['proposal_id'],'Synthetic warehouse build')
            repeat=w.materialize(args.stack,p['proposal_id'],'Synthetic warehouse build')
            assert repeat['changes']['created']==0 and repeat['changes']['evidence_changed']==0
            assert repeat['changes']['unchanged']==direct
            replay=stack.run_graph(example['graph']);assert replay['cache_hit'] and replay['new_paid_api_calls']==0
            report['programs'][program]={'counts':p['counts'],'independent_raw_assessment_candidates':direct,
                'first_save':saved['changes'],'repeat_save':repeat['changes'],'work_summary':w.list_items(args.stack,program=program)['summary']}
            (out/('program-work-'+program+'.json')).write_text(json.dumps(r,indent=2))
            if program=='hf_review':
                demo=lifecycle_demo(args.stack,out,p)
                (out/'program-lifecycle-simulation.json').write_text(json.dumps(demo,indent=2))
                report['isolated_simulation']={'path':demo['isolated_path'],'state':demo['detail']['item']['state'],
                    'clinical_goal_verified':demo['detail']['item']['clinical_goal_verified'],'events':len(demo['detail']['history'])}
        db.close()
        def request_size(body,*a,**kw):
            size=len(json.dumps(body).encode());assert size<=REQUEST_MAX_BYTES,size
            report['maximum_ascii_question_request_bytes']=size
        with patch('clinical_core.agere.clinical.graph_planner.send',side_effect=request_size):
            propose('x'*1500,args.stack,None,snapshot=stack.retriever.meta['snapshot'],graph_catalog=catalog(args.stack),
                    observed_values=json.loads((args.stack/'cohort-values.json').read_text()))
        if args.live:
            r=stack.compose(work_examples()[0]['question'],planning='frontier')
            (out/'frontier-program-work.json').write_text(json.dumps(r,indent=2))
            nodes=[n['result'] for n in r['results'] if n['node']['operation']=='worklist']
            assert r['graph']['coverage']=='supported_observation' and len(nodes)==1
            assert nodes[0]['counts']==report['programs']['hf_review']['counts']
            report['frontier']={'coverage':r['graph']['coverage'],'operations':[n['node']['operation'] for n in r['results']],
                'counts':nodes[0]['counts'],'planner_receipt':r['planner_receipt']}
            report['new_paid_api_calls']+=r['new_paid_api_calls']
        report['limits']=w.LIMITS
        destination=out/('program-work-execution-live.json' if args.live else 'program-work-execution.json')
        destination.write_text(json.dumps(report,indent=2))
        print(json.dumps(report,indent=2))
    finally:stack.close()


if __name__=='__main__':main()
