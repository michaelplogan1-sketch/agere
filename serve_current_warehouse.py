"""Start a verified local connection, or inspect a separately restored checkpoint."""
import argparse
import json
import os
from pathlib import Path
from clinical_core.agere.local_release import ROOT, build_lock, validate_config
from clinical_core.agere.service import serve

def main(argv=None):
    p = argparse.ArgumentParser()
    p.add_argument('--port', type=int, default=8885)
    p.add_argument('--config', default=str(ROOT / 'config/agere/active_warehouse.json'))
    p.add_argument('--check', action='store_true', help='Validate configuration and identities without loading models')
    p.add_argument('--allow-live-stack', action='store_true')
    p.add_argument('--ledger', type=Path, help='Explicit provider ledger for this application; exact cache and usage share this store')
    a = p.parse_args(argv)
    config = validate_config(a.config)
    output = Path(config['build'])
    recovery = config.get('recovery')
    if recovery and a.allow_live_stack:
        p.error('Restored checkpoints permit inspection only; live stack is unavailable')
    # A configuration check must not alter request ownership, caches or usage.
    if a.check:
        print(json.dumps({'status': 'validated', 'build': str(output), 'recovery': recovery,
                          'scope': 'Startup identity checks; not clinical/model qualification'}))
        return
    if a.ledger is not None:
        os.environ['AGERE_LEDGER_PATH'] = str(a.ledger.resolve())
    with build_lock(output):
        if a.allow_live_stack:
            transport = os.environ.get('AGERE_CLAUDE_TRANSPORT', 'subscription').strip().lower()
            if transport != 'subscription':
                raise ValueError('Live application requires Claude subscription transport; external Anthropic API is disabled')
            os.environ['AGERE_CLAUDE_TRANSPORT'] = 'subscription'
            from run_integrated_stack import keys
            from clinical_core.agere import subscription_transport
            keys()  # Existing credentials; never print values.
            if not subscription_transport.install():
                raise ValueError('Claude subscription transport did not initialize')
        # Pending reservations belong to their requesting process. Startup must
        # never relabel another active request as abandoned.
        serve(output / 'clinical', a.port, annotations=output / 'annotations',
              evidence_queries=output / 'evidence', stack=output / 'stack',
              allow_live_stack=a.allow_live_stack, recovery=recovery)


if __name__ == '__main__':
    main()
