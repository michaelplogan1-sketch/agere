"""Activate only a qualified original-cohort synthetic build."""
import argparse,json
from pathlib import Path
from clinical_core.agere.warehouse_activation import activate

if __name__=='__main__':
    p=argparse.ArgumentParser();p.add_argument('--build',type=Path,required=True)
    p.add_argument('--config',type=Path,default=Path(__file__).resolve().parent/'config/agere/active_warehouse.json')
    p.add_argument('--restatement',action='store_true',help='The new release restates history (corrections to prior rows); requires a restatement audit')
    p.add_argument('--expansion',action='store_true',help='The new release adds patients (every prior patient kept); requires an expansion audit. '
                   'Combine with --restatement when prior rows also changed')
    p.add_argument('--retraction',action='store_true',help='The new release drops prior rows (v2.6+: care dated after a death); requires a '
                   'retraction audit explaining every missing row by the declared post-death rule. Combines with the flags above')
    p.add_argument('--rebaseline',action='store_true',help='Fresh baseline load (the release regenerates the population; not a continuation of the '
                   'prior release). Every non-migration receipt must pass; the prior-release migration audit must exist and is recorded as '
                   'an informational diff, not gated. Mutually exclusive with --restatement/--expansion/--retraction')
    a=p.parse_args()
    if a.rebaseline and (a.restatement or a.expansion or a.retraction):p.error('--rebaseline cannot be combined with --restatement, --expansion or --retraction')
    print(json.dumps(activate(a.build,a.config,restatement=a.restatement,expansion=a.expansion,retraction=a.retraction,rebaseline=a.rebaseline),indent=2))
