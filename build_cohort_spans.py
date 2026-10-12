"""Acquire bounded open-vocabulary spans without refitting frozen cohorts."""
import argparse
from concurrent.futures import ThreadPoolExecutor, as_completed
import json
from pathlib import Path
from clinical_core.agere.active import stack_path
from clinical_core.agere.clinical import cohort_span_acquisition as a
from clinical_core.agere.gateway import BudgetLedger


def main():
    p=argparse.ArgumentParser();p.add_argument('command',choices=['prepare','acquire','status'])
    p.add_argument('--cohort',required=True);p.add_argument('--stack',type=Path,default=stack_path())
    p.add_argument('--live',action='store_true');p.add_argument('--workers',type=int,default=3)
    args=p.parse_args()
    if not 1<=args.workers<=4:raise ValueError('One to four concurrent bounded requests required')
    if args.command=='prepare':
        plan=a.prepare(args.stack,args.cohort)
        print(json.dumps({k:plan[k] for k in ('id','inventory_fields','source_categories','partition_counts')}));return
    if args.command=='acquire':
        if not args.live:raise ValueError('Explicit live acquisition required')
        from run_integrated_stack import keys
        keys();session=a.Session(args.stack,args.cohort)
        def acquire(key):
            if (session.root/'receipts'/(key+'.json')).exists():
                row=session.inspect(key)
                return {'item_id':key,'status':'saved','proposals':len(row['proposals']),'new_paid_api_calls':0}
            if any((session.root/kind/(key+'.json')).exists() for kind in ('attempts','preflight-failures')):return {'item_id':key,'status':'prior_attempt_no_retry'}
            ledger=BudgetLedger(a.cohorts.reader.ROOT/'artifacts/agere-full/provider-budget.sqlite3')
            try:
                row=session.propose(key,ledger,allow_live=True)
                return {'item_id':key,'status':'abstained' if row['abstained'] else 'proposed',
                    'proposals':len(row['proposals']),'new_paid_api_calls':row['new_paid_api_calls'],'usage':row['usage']}
            except Exception as exc:
                if not (session.root/'attempts'/(key+'.json')).exists():
                    a.storage.save(session.root/'preflight-failures'/(key+'.json'),{'item_id':key,'error_type':type(exc).__name__,'detail':str(exc)[:250]})
                return {'item_id':key,'status':'failed','error_type':type(exc).__name__,'detail':str(exc)[:250]}
            finally:ledger.close()
        with ThreadPoolExecutor(max_workers=args.workers) as pool:
            jobs=[pool.submit(acquire,key) for key in session.selected]
            for done in as_completed(jobs):print(json.dumps(done.result()),flush=True)
    report=a.status(args.stack,args.cohort)
    a.cohorts.queue.write(a.root(args.stack,args.cohort)/'status-latest.json',report)
    print(json.dumps({k:v for k,v in report.items() if k not in ('rows','usage')},indent=2))


if __name__=='__main__':main()
