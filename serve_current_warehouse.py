"""Start a verified local connection, or inspect a separately restored checkpoint."""
import argparse
import json
from pathlib import Path
from clinical_core.agere.local_release import ROOT, build_lock, validate_config
from clinical_core.agere.service import serve

if __name__ == '__main__':
    p = argparse.ArgumentParser()
    p.add_argument('--port', type=int, default=8885)
    p.add_argument('--config', default=str(ROOT / 'config/agere/active_warehouse.json'))
    p.add_argument('--check', action='store_true', help='Validate configuration and identities without loading models')
    p.add_argument('--allow-live-stack', action='store_true')
    a = p.parse_args()
    config = validate_config(a.config)
    output = Path(config['build'])
    recovery = config.get('recovery')
    if recovery and a.allow_live_stack:
        p.error('Restored checkpoints permit inspection only; live stack is unavailable')
    with build_lock(output):
        if a.check:
            print(json.dumps({'status': 'validated', 'build': str(output), 'recovery': recovery,
                              'scope': 'Startup identity checks; not clinical/model qualification'}))
        else:
            if a.allow_live_stack:
                from run_integrated_stack import keys
                keys()  # Existing credentials; never print values.
            serve(output / 'clinical', a.port, annotations=output / 'annotations',
                  evidence_queries=output / 'evidence', stack=output / 'stack',
                  allow_live_stack=a.allow_live_stack, recovery=recovery)
