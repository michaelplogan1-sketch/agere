"""Trusted action workspace over saved /ask evidence and durable review work."""
from pathlib import Path


def _review_prose(text, reference):
    """Keep every prose character outside bound structured reviewer data."""
    import json
    if not isinstance(text,str):return text,[]
    decoder=json.JSONDecoder();parts=[];references=[];cursor=index=0
    marker='[Structured review details retained in the native source review.]'
    while index<len(text):
        if text[index] not in '[{':index+=1;continue
        try:value,end=decoder.raw_decode(text,index)
        except ValueError:index+=1;continue
        if isinstance(value,(list,dict)) and end-index>len(marker):
            parts.extend([text[cursor:index],marker]);cursor=end
            references.append({**reference,'start':index,'end':end})
        index=end
    return ''.join([*parts,text[cursor:]]),references


def _review_projection(context):
    """One complete set of current field judgments, with exact history links."""
    from ...contracts import digest
    def reference(path):
        return {'native_context_id':context['id'],'path':path,
                'context_derivation':(context.get('dependencies')or{}).get('derivation')}
    def history(reviews,path):
        if not isinstance(reviews,list):return reviews
        return [{**{k:v for k,v in review.items()if k not in ('feedback','support')},
                 'native_reference':reference(path+[i])} if isinstance(review,dict)and isinstance(review.get('support'),dict)
                else review for i,review in enumerate(reviews)]
    def receipt_view(receipt,path):
        if not isinstance(receipt,dict):return receipt
        out=dict(receipt)
        if isinstance(out.get('backstop'),dict)and isinstance(out['backstop'].get('answer_reviews'),list):
            backstop=out['backstop'];out['backstop']={**backstop,
                'answer_reviews':history(backstop.get('answer_reviews'),path+['backstop','answer_reviews'])}
        if isinstance(out.get('request_contract'),dict):
            contract=out['request_contract'];out['request_contract']={**contract}
            if isinstance(contract.get('legacy_receipt'),dict):
                out['request_contract']['legacy_receipt']=receipt_view(contract['legacy_receipt'],path+['request_contract','legacy_receipt'])
        return out
    answer=context.get('answer')or{};review=answer.get('investigation_review')
    shown=answer
    if isinstance(review,dict)and isinstance(review.get('support'),dict):
        path=['answer','investigation_review'];support=review['support'];claims=support.get('claim_feedback')
        view=dict(review);feedback=review.get('feedback');guidance,spans=_review_prose(feedback,reference(path+['feedback']))
        if spans:view.update(feedback=guidance,feedback_native_spans=spans)
        if isinstance(claims,list)and isinstance(support.get('packets'),list):
            indices={digest(claim):i for i,claim in enumerate(claims)};packets=[]
            for packet in support['packets']:
                feedbacks=packet.get('claim_feedback')if isinstance(packet,dict)else None
                if isinstance(feedbacks,list)and all(digest(claim)in indices for claim in feedbacks):
                    packet={k:v for k,v in packet.items()if k!='claim_feedback'}
                    packet['claim_feedback_native_references']=[reference(path+['support','claim_feedback',indices[digest(claim)]])for claim in feedbacks]
                packets.append(packet)
            view['support']={**support,'packets':packets}
        shown={**answer,'investigation_review':view}
        if spans and isinstance(answer.get('limitations'),list):
            limitations=[];bindings=[]
            for i,text in enumerate(answer['limitations']):
                if isinstance(text,str)and feedback in text:
                    limitations.append(text.replace(feedback,guidance));bindings.append(reference(['answer','limitations',i]))
                else:limitations.append(text)
            shown.update(limitations=limitations,limitation_native_references=bindings)
    return shown,receipt_view(context.get('planner_receipt')or{},['planner_receipt']),history(
        (context.get('investigation')or{}).get('answer_reviews')or[],['investigation','answer_reviews'])


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
    answer,receipt,reviews = _review_projection(context)
    contract = receipt.get('request_contract')
    if isinstance(contract, dict) and contract.get('kind') == 'checked_graph':
        receipt['request_contract'] = {**contract, 'graph': graph}
    return {**record, 'context': {**context,
        'graph': graph, 'results': None, 'planner_receipt': receipt,'answer':answer,
        'investigation': {
            'model': investigation.get('model'),
            'tool_call_count': len(calls),
            'document_review_count': len(investigation.get('document_reviews') or []),
            'answer_reviews': reviews,
            'native_context_id': context['id'],
            'complete_native_history_retained': True,
        },
        'inspection_projection': {
            'schema': 'native-investigation-workspace-view/3',
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
