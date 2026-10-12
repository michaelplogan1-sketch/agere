"""Execute previously prepared synthetic source groups without changing selection."""
import argparse
import os
import json
import sqlite3
import urllib.request
from urllib.error import HTTPError
from pathlib import Path

OUT=Path(os.environ.get('AGERE_VERIFY_OUT','artifacts/agere-remaining-questions-v1'));BASE='http://localhost:8885/api/stack/'
if (OUT/'implementation-receipt.json').exists():
    raise ValueError('Verification output is sealed; choose a new AGERE_VERIFY_OUT directory')
def call(target,route,body=None):
    if target.exists():return json.loads(target.read_text())
    error=target.with_name(target.stem+'-error.json')
    if error.exists():raise ValueError('Saved failed attempt retained; do not retry this request automatically')
    req=urllib.request.Request(BASE+route,data=json.dumps(body).encode() if body is not None else None,headers={'Content-Type':'application/json'} if body is not None else {})
    try:
        with urllib.request.urlopen(req,timeout=900) as r:result=json.load(r)
    except HTTPError as e:error.write_bytes(e.read());raise
    target.write_text(json.dumps(result,indent=2)+'\n');return result

def main():
    args=argparse.ArgumentParser();args.add_argument('--domain',choices=['payer','cardiometabolic','medication'],required=True);args.add_argument('--execute',action='store_true');args.add_argument('--resume-with-saved-stages',action='store_true');a=args.parse_args()
    folder=OUT/a.domain;folder.mkdir(parents=True,exist_ok=True)
    original=json.loads((Path('artifacts/agere-source-selection-v1/decision')/(a.domain+'-prepare.json')).read_text())['response']['source_plan']
    plan=call(folder/'source-plan.json','retrieval-source-plan/inspect',{'id':original['plan']['id']})
    assert plan==original
    config=json.loads(Path('config/agere/active_warehouse.json').read_text());path=Path(config['build'])/'stack'
    pre=[]
    for g,c in zip(plan['plan']['groups'],plan['contexts']):
        ids=g['record_ids']
        with sqlite3.connect(path/'stack.sqlite3') as db:
            rows=[json.loads(r[0]) for r in db.execute('SELECT payload FROM records WHERE id IN ('+','.join('?' for _ in ids)+')',ids)]
        assert len(rows)==len(ids) and all(r.get('synthetic') is True and r['patient_id']==c['patient_id'] for r in rows)
        preview=call(folder/('group-'+str(g['group_number'])+'-preflight.json'),'context/plan',{'context_id':g['context_id']})
        pre.append({'group':g['group_number'],'context_id':g['context_id'],'explicit_synthetic_records':len(rows),
            'source_units':preview['source_units'],'request_bytes':preview['request_bytes'],'maximum_inference_calls':preview['maximum_inference_calls']})
    audit={'status':'all_exact_records_synthetic','domain':a.domain,'plan_id':plan['plan']['id'],
        'selection_unchanged':True,'source_release':config['source_release'],'destination':'Existing authorized Anthropic model integration',
        'scope':'Already prepared bounded groups of original-cohort synthetic records; no employer or real patient data, new service or external care',
        'groups':pre,'ledger':'Existing uncapped ledger, exact request cache and failed-key protections'}
    (folder/'payload-preflight.json').write_text(json.dumps(audit,indent=2)+'\n');print(json.dumps(audit),flush=True)
    if not a.execute:return
    attachments=[]
    for g in plan['plan']['groups']:
        prefix='group-'+str(g['group_number'])
        stage_file=folder/(prefix+'-source-stage.json')
        use_stage=a.resume_with_saved_stages and (folder/(prefix+'-answer-error.json')).exists() and stage_file.exists()
        if use_stage:
            answer=json.loads(stage_file.read_text())
            assert answer['status']=='complete' and answer['context_id']==g['context_id'] and answer['coverage']['complete_declared_scope']
        else:
            try:answer=call(folder/(prefix+'-answer.json'),'context/synthesize',{'context_id':g['context_id']})
            except HTTPError:
                if not a.resume_with_saved_stages:raise
                saved=call(folder/(prefix+'-reopened-source-stage.json'),'context/source-stage?context_id='+g['context_id'])
                answer=saved.get('answer')
                if not answer or answer['status']!='complete' or not answer['coverage']['complete_declared_scope']:
                    raise ValueError('Pipeline failed before a complete source stage; preserved without retry')
                assert answer['context_id']==g['context_id']
                canonical=call(stage_file,'case-synthesis?id='+answer['id'])
                assert canonical==answer
                use_stage=True
                print(json.dumps({'domain':a.domain,'group':g['group_number'],'reconciliation_incomplete':True,
                    'failure_preserved':str(folder/(prefix+'-answer-error.json')),'source_stage_reopened_without_inference':True}),flush=True)
        attach=call(folder/(prefix+('-source-stage-attachment.json' if use_stage else '-attachment.json')),'source-plan-answers/attach',{'plan_id':plan['plan']['id'],'answer_id':answer['id']})
        attachments.append(attach['attachment']['id']);print(json.dumps({'domain':a.domain,'group_complete':g['group_number'],'answer_id':answer['id']}),flush=True)
    snapshot=call(folder/'snapshot.json','source-plan-answers/prepare',{'plan_id':plan['plan']['id'],'attachment_ids':attachments})
    print(json.dumps({'domain':a.domain,'all_groups_attached':len(attachments),'snapshot_id':snapshot['snapshot']['id']}),flush=True)

if __name__=='__main__':main()
