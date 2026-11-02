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
    # The verifier's graph may contain inline SQL/JSON and full intermediate
    # reviews. The workspace only uses it as an evidence index. Bind every
    # parameter/output to the immutable native context instead of sending those
    # copies to the browser. Complete answer rows and judgments remain below.
    nodes=[]
    for node in graph['nodes']:
        index=int(node['id'].removeprefix('tool_'))
        reference={'native_context_id':context['id'],'tool_call_index':index,
                   'context_derivation':(context.get('dependencies')or{}).get('derivation')}
        nodes.append({'id':node['id'],'operation':node['operation'],
            'parameters':{'native_reference':{**reference,'path':['investigation','tool_calls',index,'input']},
                          'parameter_names':list(calls[index].get('input')or{})},
            'output':{**{k:node['output'][k]for k in ('result_view','columns','computed_rows','table_rows','truncated')if k in node['output']},
                      'native_reference':{**reference,'path':['investigation','tool_calls',index,'result']}}})
    graph={**graph,'nodes':nodes}
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
            'schema': 'native-investigation-workspace-view/2',
            'native_context_id': context['id'],
            'complete_native_history_retained': True,
            'clinical_acceptance_changed': False,
        },
    }}


def encode_inspection(record, accept_encoding=''):
    """Lossless standard HTTP compression, with explicit gzip refusal honored."""
    import gzip
    import json
    raw=json.dumps(record,default=str).encode()
    for entry in accept_encoding.split(','):
        name,*parameters=entry.strip().split(';')
        if name.strip().lower()!='gzip':continue
        quality=1.0
        for parameter in parameters:
            key,separator,value=parameter.strip().partition('=')
            if key.lower()=='q'and separator:
                try:quality=float(value)
                except ValueError:quality=0.0
        if 0<quality<=1:return gzip.compress(raw,mtime=0),'gzip'
        break
    return raw,None


def page():
    assets = Path(__file__).parent
    return (assets / 'action_center.html').read_text().replace(
        '/* ACTION_CENTER_CSS */', (assets / 'action_center.css').read_text() + '\n' +
        (assets / 'workspace_skin.css').read_text()
    ).replace('/* ACTION_CENTER_JS */', (assets / 'action_center.js').read_text())
