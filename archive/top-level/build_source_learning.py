"""Prepare a protected source-review queue or add bounded machine suggestions."""
import argparse
import json
import os
import uuid
from pathlib import Path
from clinical_core.agere.clinical import learning_queue as queue
from clinical_core.agere.gateway import BudgetLedger
from clinical_core.agere.active import stack_path


def main():
    p=argparse.ArgumentParser();p.add_argument('--stack',type=Path,default=stack_path())
    sub=p.add_subparsers(dest='command',required=True)
    build=sub.add_parser('build');build.add_argument('--release',type=Path,required=True);build.add_argument('--output',type=Path,required=True);build.add_argument('--size',type=int,default=96)
    suggest=sub.add_parser('suggest');suggest.add_argument('--maximum-items',type=int,default=6)
    suggest.add_argument('--providers',nargs='+',choices=['jev','perplexity','student_lexical','student_minilm'],required=True)
    suggest.add_argument('--live',action='store_true')
    a=p.parse_args()
    if a.command=='build':
        a.output.mkdir(parents=True,exist_ok=False)
        lineage=queue.import_lineage(a.stack,a.release,a.output/'lineage')
        result=queue.build(a.stack,a.output/'lineage',a.output/'queue',size=a.size)
        print(json.dumps({'lineage':lineage,'queue':{k:v for k,v in result.items() if k not in ('protection_register','source_identity')}},indent=2));return
    if not 1<=a.maximum_items<=24:p.error('At most 24 selected source items per explicit run')
    if len(a.providers)!=len(set(a.providers)):p.error('Duplicate providers')
    if not a.live and any(p in ('jev','perplexity') for p in a.providers):p.error('Hosted suggestions require --live')
    if a.live:
        for line in Path('/Users/mikel/insight-engine/.env').read_text().splitlines():
            if '=' in line:
                key,value=line.split('=',1)
                if key.strip() in ('TYPESAFE_API_KEY','PERPLEXITY_API_KEY'):os.environ[key.strip()]=value.strip().strip('"\'')
    root,run,_,items=queue.connected(a.stack)
    # Acquisition order was frozen before any new model suggestion. No labels
    # select which examples receive the second reader.
    chosen=[r for r in items if r['target']][:a.maximum_items]
    ledger=BudgetLedger(queue.reader.ROOT/'artifacts/agere-full/provider-budget.sqlite3')
    batch={'synthetic':True,'queue_run_sha256':queue.sha(root/'run.json'),'maximum_items':a.maximum_items,
           'providers':a.providers,'budget_before':ledger.receipt(),'rows':[],'labels_created':False,
           'scope':'Machine suggestions for source review; no consensus label, training, accuracy score or clinical acceptance.'}
    receipt=root/('suggestions-'+uuid.uuid4().hex+'.json')
    try:
        for item in chosen:
            for provider in a.providers:
                try:
                    result=queue.suggest(a.stack,item['id'],provider,allow_live=a.live)
                    record={'item_id':item['id'],'provider':provider,'reading_id':result['id'],'labels':result['reading']['labels'],
                            'cache_hit':result['cache_hit'],'new_paid_api_calls':result['new_paid_api_calls'],'usage':result['usage']}
                except (ValueError,RuntimeError) as exc:
                    record={'item_id':item['id'],'provider':provider,'error':str(exc)[:300],'retry_attempted':False}
                batch['rows'].append(record);batch['budget_after']=ledger.receipt();queue.write(receipt,batch)
                print(json.dumps(record),flush=True)
                if record.get('error') and any(s in record['error'].lower() for s in ('authorization exhausted','credential','pending/failed','rate limit')):return
    finally:ledger.close()


if __name__=='__main__':main()
