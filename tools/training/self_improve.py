"""One offline recursive component-learning job from explicit approved inputs.

New reviewed observations -> family-separated export -> classical/encoder fitting
-> calibration -> independent verification -> versioned candidate/release. Runtime
feedback can quarantine a release; the next reviewed export drives its revision.
No provider calls, legacy answer stores, benchmark discovery or scheduler.
"""
import argparse
import json
import os
import sys
from pathlib import Path
ROOT=Path(__file__).resolve().parents[2]
sys.path.insert(0,str(ROOT))
VERSION='self-improve/2'


def training_root():return Path(os.environ.get('AGERE_TRAINING_DIR') or ROOT/'artifacts'/'training')


def run(*,root=None,stack=None,manifest=None,encoder=None,review_splits=None,role=None,include_machine=False,**options):
    from clinical_core.agere.clinical import component_learning as C
    if manifest is None and review_splits is not None:
        if stack is None or role is None:raise ValueError('Reviewed export needs the explicit stack, role and family splits')
        root=root or C.root_for(stack)
        manifest=C.export(C.root_for(stack),C.checked_path(root)/'reviewed-export',role,review_splits,include_machine=include_machine)
    if manifest is None:
        return {'version':VERSION,'status':'requires_approved_manifest','provider_calls':0,
                'reason':'Supply an approved manifest or explicit newly reviewed observations and family splits. Legacy store and benchmark discovery is disabled.'}
    if json.loads(C.checked_path(manifest).read_text()).get('schema')=='approved-adapter-data/1':
        from tools.training.adapters import run as adapter_run
        if stack is None or not options.get('base'):raise ValueError('Adapter learning requires explicit source stack and pinned base')
        return adapter_run(manifest,root or C.root_for(stack),stack=stack,base=options['base'],train=bool(options.get('train_adapters',False)),
            install=bool(options.get('install',False)),backend=options.get('backend'),candidate_adapter=options.get('candidate_adapter'))
    from tools.training.approved import run as approved_run
    return approved_run(manifest,root or (C.root_for(stack) if stack is not None else training_root()/'component-learning'),stack=stack,encoder=encoder,
                        install=bool(options.get('install',False)))


def main(argv=None):
    ap=argparse.ArgumentParser(description=__doc__)
    ap.add_argument('--root');ap.add_argument('--stack');ap.add_argument('--manifest')
    ap.add_argument('--review-splits',help='Explicit JSON mapping of newly reviewed source families to fit/calibration/verification')
    ap.add_argument('--role',choices=('router','tier_router','shape'));ap.add_argument('--install',action='store_true')
    ap.add_argument('--include-machine-supervision',action='store_true')
    ap.add_argument('--base');ap.add_argument('--train-adapters',action='store_true');ap.add_argument('--learning-plan')
    args=ap.parse_args(argv)
    from clinical_core.agere.clinical.component_learning import checked_path
    if args.learning_plan:
        if not args.stack:raise ValueError('Learning plan requires an explicit stack')
        from clinical_core.agere.clinical.component_learning import run_plan
        from types import SimpleNamespace
        from unittest.mock import patch
        env={'AGERE_COMPONENT_LEARNING_PLAN':str(checked_path(args.learning_plan)),
             'AGERE_COMPONENT_RELEASE_ROOT':str(checked_path(args.root or Path(args.stack)/'component-learning'))}
        with patch.dict(os.environ,env):
            print(json.dumps(run_plan(SimpleNamespace(path=Path(args.stack),retriever=None),train_adapters=args.train_adapters),indent=2))
        return
    splits=json.loads(checked_path(args.review_splits).read_text()) if args.review_splits else None
    print(json.dumps(run(root=args.root,stack=args.stack,manifest=args.manifest,review_splits=splits,
                         role=args.role,include_machine=args.include_machine_supervision,install=args.install,base=args.base,train_adapters=args.train_adapters),indent=2))


if __name__=='__main__':main()
