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
    p.add_argument('--carry-forward-verified',action='store_true',help='After activation, replay the prior stack\'s verified '
                   'question->SQL pairs on this build: passing pairs are carried with provenance, the rest quarantined with reasons')
    p.add_argument('--require-release-diff',action='store_true',help='Refuse activation unless BUILD/release-diff.json '
                   '(tools/release_diff_guard.py) exists for this build and has no unacknowledged flags')
    p.add_argument('--accept-release-diff',type=Path,help='JSON {"accepted": [finding ids or kinds]} acknowledged by a reviewer')
    p.add_argument('--require-source-coverage',action='store_true',help='Refuse activation when any received stream is neither '
                   'mapped, read by an engine nor declared in config/agere/unmapped_streams.json (default: that file\'s enforce flag)')
    p.add_argument('--no-quality-gates',action='store_true',help='Skip the release quality gates (sealed truth scorer aggregates + clinical task agreement)')
    p.add_argument('--require-sealed-gate',action='store_true',help='Refuse when the sealed truth gate regresses against the previous gate')
    a=p.parse_args()
    if a.rebaseline and (a.restatement or a.expansion or a.retraction):p.error('--rebaseline cannot be combined with --restatement, --expansion or --retraction')
    accepted=json.loads(a.accept_release_diff.read_text()).get('accepted',[]) if a.accept_release_diff else []
    print(json.dumps(activate(a.build,a.config,restatement=a.restatement,expansion=a.expansion,retraction=a.retraction,rebaseline=a.rebaseline,
                              carry_forward=a.carry_forward_verified,require_release_diff=a.require_release_diff,
                              release_diff_accepted=accepted,
                              source_coverage_enforce=True if a.require_source_coverage else None,
                              sealed_gate=False if a.no_quality_gates else None,enforce_sealed_gate=a.require_sealed_gate),indent=2))
