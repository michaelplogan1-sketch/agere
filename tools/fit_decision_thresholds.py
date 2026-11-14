"""Read existing execution events and write an OFFLINE threshold receipt."""
import argparse
import json
import sys
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from clinical_core.agere.clinical.decision_evaluation import read_journal,fit


def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--journal',required=True,type=Path)
    p.add_argument('--output',required=True,type=Path)
    p.add_argument('--probe-cost',type=float,help='Measured all-attempt cost of the confidence probe, in the same units as paired costs; absent means unknown.')
    p.add_argument('--min-pairs',type=int,default=20)
    p.add_argument('--max-success-loss',type=float,default=0.0)
    args=p.parse_args()
    if args.output.resolve()==args.journal.resolve():p.error('Output cannot overwrite the journal')
    rows,counts=read_journal(args.journal)
    result={**fit(rows,min_pairs=args.min_pairs,max_success_loss=args.max_success_loss,probe_cost=args.probe_cost),
        'journal':str(args.journal.resolve()),'event_counts':counts,
        'pair_receipts':len(rows),'fitting_scope':'Observed development pairs only; no protected evaluation, fixture outcomes or provider execution.'}
    args.output.parent.mkdir(parents=True,exist_ok=True)
    args.output.write_text(json.dumps(result,indent=2,allow_nan=False)+'\n')
    print(json.dumps({'status':result['status'],'pairs':len(rows),'groups':len(result['groups']),'runtime_policy_changed':False}))


if __name__=='__main__':main()
