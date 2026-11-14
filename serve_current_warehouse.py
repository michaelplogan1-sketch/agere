"""Start a verified local connection, or inspect a separately restored checkpoint."""
import argparse
from contextlib import contextmanager
import json
import os
import sqlite3
from pathlib import Path
from clinical_core.agere.local_release import ROOT, build_lock, validate_config
from clinical_core.agere.model_policy import application_runtime


def serve(*args, **kwargs):
    # Apply role configuration before importing modules with runtime defaults.
    from clinical_core.agere.service import serve as application_serve
    return application_serve(*args, **kwargs)


@contextmanager
def runtime_preflight(environment):
    """Check the selected policy before starting threads, then restore the caller.

    In particular --check must evaluate the intended note-enrichment settings
    without leaving those settings in the inspection process.
    """
    previous = {key: os.environ.get(key) for key in environment}
    os.environ.update(environment)
    try:
        yield
    finally:
        for key, value in previous.items():
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value

def validate_semantic_policy(stack):
    """Refuse an implicit enrichment rebuild in a connected live application."""
    path = Path(stack) / 'semantic.sqlite3'
    if not path.is_file():
        raise ValueError('Live application requires its materialized semantic layer')
    from clinical_core.agere.clinical import semantic_layer
    with sqlite3.connect(path.resolve().as_uri() + '?mode=ro', uri=True) as db:
        row = db.execute("SELECT value FROM meta WHERE key='postprocessing_identity'").fetchone()
    if row is None or row[0] != semantic_layer.policy_identity():
        raise ValueError('Semantic enrichment policy differs from the connected materialization. '
                         'Restore its pinned configuration or explicitly rebuild and verify the materialization before serving questions.')


def disable_providers(names):
    """Honor an explicit availability decision after loading local credentials.

    This is not a retry or a budget change. Ledger failures and usage stay intact.
    """
    credentials = {'perplexity': 'PERPLEXITY_API_KEY', 'jev': 'TYPESAFE_API_KEY'}
    for name in names:
        os.environ.pop(credentials[name], None)
    if names:
        order = os.environ.get('AGERE_DOCUMENT_TYPED_ORDER', 'perplexity,jev')
        os.environ['AGERE_DOCUMENT_TYPED_ORDER'] = ','.join(
            name for name in order.split(',') if name.strip() not in names)

def main(argv=None):
    p = argparse.ArgumentParser()
    p.add_argument('--port', type=int, default=8885)
    p.add_argument('--config', default=str(ROOT / 'config/agere/active_warehouse.json'))
    p.add_argument('--check', action='store_true', help='Validate configuration and identities without loading models')
    p.add_argument('--allow-live-stack', action='store_true')
    p.add_argument('--ledger', type=Path, help='Explicit provider ledger for this application; exact cache and usage share this store')
    p.add_argument('--disable-provider', action='append', default=[], choices=('perplexity', 'jev'),
                   help='Keep an unavailable provider disabled without probing it; repeat for multiple providers')
    p.add_argument('--runtime-profile', choices=('economical', 'configured'),
                   help='Default economical uses native/System One and hosted frontier roles; configured honors explicitly selected optional roles')
    a = p.parse_args(argv)
    config = validate_config(a.config)
    runtime = application_runtime(config, profile=a.runtime_profile)
    output = Path(config['build'])
    recovery = config.get('recovery')
    if recovery and a.allow_live_stack:
        p.error('Restored checkpoints permit inspection only; live stack is unavailable')
    if a.allow_live_stack:
        with runtime_preflight(runtime['environment']):
            validate_semantic_policy(output / 'stack')
    # A configuration check must not alter request ownership, caches or usage.
    if a.check:
        print(json.dumps({'status': 'validated', 'build': str(output), 'recovery': recovery,
                          'runtime': runtime,
                          'disabled_providers': sorted(set(a.disable_provider)),
                          'scope': 'Startup identity checks; not clinical/model qualification'}))
        return
    if a.allow_live_stack and os.environ.get('AGERE_CLAUDE_TRANSPORT', 'subscription').strip().lower() != 'subscription':
        raise ValueError('Live application requires Claude subscription transport; external Anthropic API is disabled')
    if not recovery:
        os.environ.update(runtime['environment'])
        os.environ['AGERE_RUNTIME_PROFILE'] = runtime['profile']
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
            disable_providers(a.disable_provider)
            if not subscription_transport.install():
                raise ValueError('Claude subscription transport did not initialize')
        # Pending reservations belong to their requesting process. Startup must
        # never relabel another active request as abandoned.
        serve(output / 'clinical', a.port, annotations=output / 'annotations',
              evidence_queries=output / 'evidence', stack=output / 'stack',
              allow_live_stack=a.allow_live_stack, recovery=recovery)


if __name__ == '__main__':
    main()
