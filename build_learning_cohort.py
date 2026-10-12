"""Extend protected source learning without replacing attached acquisition."""
import argparse
import json
from pathlib import Path
from clinical_core.agere.active import stack_path
from clinical_core.agere.clinical import learning_cohorts as cohorts


def main():
    parser=argparse.ArgumentParser();parser.add_argument('--stack',type=Path,default=stack_path())
    commands=parser.add_subparsers(dest='command',required=True)
    prepare=commands.add_parser('prepare');prepare.add_argument('--output',type=Path,required=True)
    prepare.add_argument('--size',type=int,default=500);prepare.add_argument('--anchor-plan',type=Path,action='append',default=[])
    for name in ('acquire','freeze','fit','status'):
        command=commands.add_parser(name);command.add_argument('--cohort',required=True)
    commands.add_parser('list');args=parser.parse_args()
    if args.command=='prepare':
        result=cohorts.prepare(args.stack,args.output,size=args.size,anchor_paths=args.anchor_plan)
        result={k:result[k] for k in ('id','target_count','counts','components','inherited_targets','conflicting_targets_excluded','design')}
    elif args.command=='list':result=cohorts.listing(args.stack)
    elif args.command=='acquire':
        result=cohorts.acquire(args.stack,args.cohort,notify=lambda r:print(json.dumps(r),flush=True))
        result={'attempts':len(result['rows']),'statuses':dict(cohorts.Counter(r['status'] for r in result['rows']))}
    else:result=getattr(cohorts,args.command)(args.stack,args.cohort)
    print(json.dumps(result,indent=2))


if __name__=='__main__':main()
