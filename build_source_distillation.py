"""Freeze development sources, run native teachers, export imitation targets."""
import argparse
import json
from pathlib import Path
from clinical_core.agere.active import stack_path
from clinical_core.agere.clinical import source_distillation as sd


def main():
    p=argparse.ArgumentParser();p.add_argument('--stack',type=Path,default=stack_path())
    sub=p.add_subparsers(dest='command',required=True)
    plan=sub.add_parser('plan');plan.add_argument('--output',type=Path,required=True)
    plan.add_argument('--teacher',choices=sd.PROVIDERS,default='kev')
    acquire=sub.add_parser('acquire');acquire.add_argument('--plan',type=Path,required=True)
    acquire.add_argument('--provider',choices=sd.PROVIDERS,required=True)
    freeze=sub.add_parser('freeze');freeze.add_argument('--plan',type=Path,required=True)
    freeze.add_argument('--dataset',type=Path,required=True)
    sub.add_parser('status')
    sub.add_parser('qualify-design',help='Inspect sample minimums before inference; never trains or promotes')
    a=p.parse_args()
    if a.command=='plan':result=sd.prepare(a.stack,a.output,teacher=a.teacher)
    elif a.command=='acquire':
        result=sd.acquire(a.stack,a.plan,a.provider,notify=lambda r:print(json.dumps(r),flush=True))
    elif a.command=='freeze':result=sd.freeze(a.stack,a.plan,a.dataset)
    elif a.command=='qualify-design':
        from clinical_core.agere.clinical.distillation_qualification import inspect
        result=inspect(a.stack)
    else:result=sd.status(a.stack)
    print(json.dumps(result,indent=2))


if __name__=='__main__':main()
