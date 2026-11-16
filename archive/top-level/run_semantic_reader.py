"""Identical-contract integration examples; no held-out accuracy or winner claim."""
import argparse
import hashlib
import json
from pathlib import Path
import sqlite3
import time
from clinical_core.agere.clinical import semantic_reader as reader
from clinical_core.agere.gateway import BudgetLedger


def targets(stack):
    from clinical_core.agere.clinical.longitudinal import connect
    db,_=connect(stack);rows=[]
    try:
        for pid,concept in [('ECO-11AEB2190629','preference declines'),('ECO-1B9860A33469','medication bb')]:
            raw=db.execute('SELECT payload FROM events WHERE patient=? AND concept=? AND relation=? ORDER BY id LIMIT 1',(pid,concept,'treatment_hold_stop')).fetchone()
            if raw is None:raise ValueError('Named development case absent from warehouse')
            event=json.loads(raw[0]);s=event['source']
            rows.append({'target':{'patient_id':pid,'record_id':s['record_id'],'field':s['field'],'start':s['start'],'end':s['end'],'concept':concept},
                'previous_reader':{k:event[k] for k in ('assertion','subject','time','relation')},
                'source_statement':event['statement'],
                'selection':'Two intentionally selected development integration cases; not a clinical benchmark or model selection cohort'})
    finally:db.close()
    return rows


def main():
    parser=argparse.ArgumentParser();parser.add_argument('provider',choices=['perplexity','jev','kev','d1','prepare']);parser.add_argument('--live',action='store_true')
    args=parser.parse_args();stack=Path('artifacts/agere-population-stack-v3');out=Path('artifacts/agere-semantic-reader-v1');out.mkdir(exist_ok=True)
    cases=targets(stack);(out/'targets.json').write_text(json.dumps({'synthetic':True,'cases':cases},indent=2))
    if args.provider=='prepare':
        for row in cases:
            record,text,meta=reader.source(stack,row['target']);p=reader.packet(record,text,row['target'],meta['snapshot']['as_of']);bodies=[reader.request(p,provider) for provider in ('perplexity','jev','kev','d1')]
            assert all(b['state']==bodies[0]['state'] and b['questions']==bodies[0]['questions'] for b in bodies)
            print(json.dumps({'patient':row['target']['patient_id'],'context_complete':p['context_complete'],'target_quote':p['target_quote'],'request_bytes':len(json.dumps(bodies[0]).encode())}))
        return
    model=None
    if args.provider in ('kev','d1'):
        from clinical_core.agere.clinical.local_decisions import KevMLX,LiquidD1
        model=KevMLX() if args.provider=='kev' else LiquidD1()
    if args.live:
        for line in Path('/Users/mikel/insight-engine/.env').read_text().splitlines():
            if '=' in line:
                key,value=line.split('=',1)
                if key.strip() in ('TYPESAFE_API_KEY','PERPLEXITY_API_KEY'):
                    import os
                    os.environ[key.strip()]=value.strip().strip('"\'')
    results=[]
    for row in cases:
        started=time.perf_counter();result=reader.read(stack,row['target'],args.provider,allow_live=args.live,local_model=model)
        replay=reader.read(stack,row['target'],args.provider,allow_live=args.live,local_model=model)
        assert replay['cache_hit'] and replay['new_paid_api_calls']==0
        results.append({'reading':result,'replay':{'id':replay['id'],'new_paid_api_calls':0,'cache_hit':True},'wall_seconds_with_replay':time.perf_counter()-started})
        (out/(args.provider+'.json')).write_text(json.dumps({'synthetic':True,'provider':args.provider,'results':results,
            'scope':'Selected development integration examples. No accuracy, clinical validity, calibration or winner claim.'},indent=2))
        print(json.dumps({'provider':args.provider,'patient':row['target']['patient_id'],'labels':result['reading']['labels'],'new_paid_api_calls':result['new_paid_api_calls'],'usage':result['usage']}),flush=True)


if __name__=='__main__':main()
