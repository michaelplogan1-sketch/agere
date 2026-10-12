"""Attach an immutable event/reference-date ledger to the existing warehouse stack."""
from clinical_core.agere.active import stack_path as active_stack_path
import argparse,json
from pathlib import Path
from clinical_core.agere.clinical.longitudinal import build

if __name__=='__main__':
    p=argparse.ArgumentParser();p.add_argument('--stack',type=Path,default=active_stack_path())
    p.add_argument('--output',type=Path,default=Path('artifacts/agere-longitudinal-v1'))
    a=p.parse_args();print(json.dumps(build(a.stack,a.output),indent=2))
