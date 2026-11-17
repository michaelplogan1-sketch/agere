"""PHI route guard (candidate, off by default: AGERE_PHI_GUARD=off).

A deployment prerequisite, not an accuracy feature: when the data is real patient data (AGERE_PHI_DATA=on), a model call may
leave this machine only through a route listed as covered. The list below is a candidate taken from the provider's BAA page
as summarised by the research session on 2026-11-17 (privacy.claude.com); it has NOT been confirmed with the provider or
counsel and must be before any real data is used. Every call records the route it took.

    route_for(transport)      -> route name for a transport ('api' -> messages_api, the Claude subscription CLI -> subscription_cli)
    check(route)              raises PHIRouteRefused when PHI is flagged and the route is not covered
"""
import os

# route -> (covered for PHI, why)
ROUTES = {
    'messages_api': (True, 'Messages API, including prompt caching and structured outputs (candidate: BAA page)'),
    'batch_api': (False, 'Message Batches: not listed as covered'),
    'files_api': (False, 'Files API: not listed as covered'),
    'skills': (False, 'Skills: not listed as covered'),
    'code_execution': (False, 'Code execution tool: not listed as covered'),
    'web_fetch': (False, 'Web fetch tool: not listed as covered'),
    'connectors': (False, 'Connectors: not listed as covered'),
    'mcp': (False, 'MCP servers: not listed as covered'),
    'subscription_cli': (False, 'Claude subscription through the CLI: a consumer plan, not under a BAA'),
}


class PHIRouteRefused(RuntimeError):
    pass


def enabled():
    return os.environ.get('AGERE_PHI_GUARD', 'off') == 'on'


def phi_flagged():
    return os.environ.get('AGERE_PHI_DATA', 'off') == 'on'


def route_for(transport):
    return 'messages_api' if str(transport).strip().lower() == 'api' else 'subscription_cli'


def check(route):
    """-> the route, recorded by the caller. Raises when the guard is on, PHI is flagged and the route is not covered
    (an unknown route is not covered)."""
    if enabled() and phi_flagged() and not ROUTES.get(route, (False, ''))[0]:
        raise PHIRouteRefused(f'PHI may not be sent through {route}: {ROUTES.get(route, (False, "unknown route"))[1]}')
    return route
