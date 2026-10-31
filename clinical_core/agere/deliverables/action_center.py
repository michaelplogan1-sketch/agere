"""Trusted action workspace over saved /ask evidence and durable review work."""
from pathlib import Path


def inspection_view(record):
    """Present a saved investigation without transferring duplicate native history.

    Mutation and source checks still use the complete immutable server context.
    The browser retains every answer/patient row and judgment; expanded SQL and
    intermediate copies remain in that context, referenced by the native view.
    """
    context = record.get('context')
    investigation = (context or {}).get('investigation') or {}
    calls = investigation.get('tool_calls') or []
    if not calls:
        return record
    from ..clinical.backstop_agent import execution_review_graph
    graph = execution_review_graph(calls, context.get('answer'))
    receipt = dict(context.get('planner_receipt') or {})
    contract = receipt.get('request_contract')
    if isinstance(contract, dict) and contract.get('kind') == 'checked_graph':
        receipt['request_contract'] = {**contract, 'graph': graph}
    return {**record, 'context': {**context,
        'graph': graph, 'results': None, 'planner_receipt': receipt,
        'investigation': {
            'model': investigation.get('model'),
            'tool_call_count': len(calls),
            'document_review_count': len(investigation.get('document_reviews') or []),
            'answer_reviews': investigation.get('answer_reviews') or [],
            'native_context_id': context['id'],
            'complete_native_history_retained': True,
        },
        'inspection_projection': {
            'schema': 'native-investigation-workspace-view/1',
            'native_context_id': context['id'],
            'complete_native_history_retained': True,
            'clinical_acceptance_changed': False,
        },
    }}


def page():
    assets = Path(__file__).parent
    return (assets / 'action_center.html').read_text().replace(
        '/* ACTION_CENTER_CSS */', (assets / 'action_center.css').read_text() + '\n' +
        (assets / 'workspace_skin.css').read_text()
    ).replace('/* ACTION_CENTER_JS */', (assets / 'action_center.js').read_text())
