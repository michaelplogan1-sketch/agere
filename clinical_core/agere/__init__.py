"""Agere's connected, source-grounded local healthcare intelligence runtime."""

import os as _os
if _os.environ.get('AGERE_CLAUDE_TRANSPORT', '').strip().lower() == 'subscription':  # test runs on the Claude Code subscription (subscription_transport.py)
    from . import subscription_transport as _subscription_transport
    _subscription_transport.install()
