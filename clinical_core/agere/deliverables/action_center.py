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


def _patient_row_indices(context):
    """Share only exact answer rows, retaining order, duplicates and extras.

    Browser hydration points back to complete answer rows. An unmatched row
    keeps the original patient representation; no evidence is guessed or lost.
    """
    from collections import defaultdict,deque
    from ...contracts import digest
    table=(context.get('answer')or{}).get('table')
    patients=context.get('patient_rows')
    if not isinstance(table,list)or not isinstance(patients,list):return None
    available=defaultdict(deque)
    for index,row in enumerate(table):available[digest(row)].append(index)
    encoded=[]
    for patient in patients:
        if(not isinstance(patient,dict)or not isinstance(patient.get('evidence_rows'),list)
            or 'evidence_row_indices'in patient):return None
        indices=[]
        for row in patient['evidence_rows']:
            candidates=available[digest(row)]
            if not candidates:return None
            index=candidates.popleft()
            if table[index]!=row:return None
            indices.append(index)
        encoded.append({**{k:v for k,v in patient.items()if k!='evidence_rows'},'evidence_row_indices':indices})
    return encoded


def _evidence_binding(context, indices):
    """Bind deferred values to the saved answer and its current authority guards."""
    from ...contracts import digest
    keys=('id','question','original_question','source','dependencies','answer',
          'patient_ids','patient_profiles','synthetic','clinical_eligibility_established',
          'stale','invalidated','source_disposition','source_review_required',
          'dependency_validation','supports_patient_work','context_policy_identity')
    return digest({**{key:context.get(key) for key in keys},'patient_evidence_indices':indices})


def _workspace_rows(record, native, compact_rows):
    """Defer containers, never identities, scalar values, rows or review gates.

    This is an opt-in transport representation, not a smaller analytical cohort.
    Every deferred cell has an explicit native location; detail/search fetches
    read that same saved context without executing the analysis again.
    """
    import json
    from urllib.parse import urlencode
    if not compact_rows or not(native.get('dependencies')or{}).get('derivation'):return record
    indices=_patient_row_indices(native)
    if indices is None:return record
    shown={**record['context'],'patient_rows':indices,'patient_row_encoding':{
        'schema':'answer-row-indices/1','table':['answer','table'],
        'native_context_id':native['id'],
        'context_derivation':native['dependencies']['derivation']}}
    table=(native.get('answer')or{}).get('table')
    if not all(isinstance(row,dict)for row in table):return {**record,'context':shown}
    pending=[]
    for index,row in enumerate(table):
        for column,value in row.items():
            structured=value
            if isinstance(value,str):
                try:structured=json.loads(value)
                except (ValueError,TypeError):continue
            if isinstance(structured,(dict,list)):
                kind=('json_'if isinstance(value,str)else '')+('array'if isinstance(structured,list)else 'object')
                pending.append((index,column,kind,len(structured)))
    if pending:
        binding=_evidence_binding(native,indices)
        projected=[dict(row)for row in table]
        for index,column,kind,count in pending:
            projected[index][column]={'$agere_deferred':{
                'schema':'saved-evidence-cell/1','row':index,'column':column,
                'kind':kind,'entries':count}}
        query={'context':native['id'],'view':'evidence','binding':binding}
        shown.update(answer={**shown['answer'],'table':projected},workspace_evidence={
            'schema':'saved-workspace-evidence/1','binding':binding,
            'native_context_id':native['id'],
            'context_derivation':native['dependencies']['derivation'],
            'table_rows':len(table),'deferred_cells':len(pending),
            'complete':False,'url':'/api/stack/action-context?'+urlencode(query)})
    return {**record,'context':shown}


def evidence_view(record, *, binding, patient_id=None):
    """Complete exact saved values for one patient or full-text search.

    Source validation remains in the normal action-context inspection path.
    Changed source dispositions/authority/answer bytes cannot be hydrated into
    an older browser view, even if a caller reuses the same context identifier.
    """
    context=record['context']
    indices=_patient_row_indices(context)
    if indices is None:raise ValueError('Saved evidence references are unavailable. Inspect the native answer.')
    if not binding or binding!=_evidence_binding(context,indices):
        raise ValueError('Saved evidence or its source status changed. Refresh the workspace before loading details.')
    result={'schema':'saved-workspace-hydration/1','binding':binding,
            'native_context_id':context['id'],
            'context_derivation':context['dependencies']['derivation']}
    if patient_id is not None:
        matches=[p for p in context['patient_rows']if p['patient_id']==patient_id]
        if len(matches)!=1:raise ValueError('Patient is not uniquely present in this saved answer.')
        result['patient']=matches[0]
    else:
        result.update(table=context['answer']['table'],patient_rows=indices)
    return {'workspace_hydration':result}


def inspection_view(record, *, compact_rows=False):
    """Present a saved investigation without transferring duplicate native history.

    Mutation and source checks still use the complete immutable server context.
    The browser retains every answer/patient row and judgment; expanded SQL and
    intermediate copies remain in that context, referenced by the native view.
    """
    context = record.get('context')
    investigation = (context or {}).get('investigation') or {}
    calls = investigation.get('tool_calls') or []
    if not calls:
        graph=(context or{}).get('graph')or{}
        if not(graph.get('nodes')and isinstance(context.get('results'),list)
               and(context.get('dependencies')or{}).get('derivation')):return record
        from urllib.parse import urlencode
        native_url='/api/stack/action-context?'+urlencode({'context':context['id'],'view':'native'})
        reference={'native_context_id':context['id'],'path':['results'],
                   'context_derivation':context['dependencies']['derivation'],'url':native_url}
        shown={**context,'results':{'native_reference':reference},'inspection_projection':{
            'schema':'native-investigation-workspace-view/3','kind':'native_graph',
            'native_context_id':context['id'],'native_view_url':native_url,
            'complete_native_history_retained':True,'clinical_acceptance_changed':False}}
        return _workspace_rows({**record,'context':shown},context,compact_rows)
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
    return _workspace_rows({**record, 'context': {**context,
        'graph': graph, 'results': None, 'planner_receipt': receipt,'answer':answer,
        'native_population_scopes':None,
        'investigation': {
            'model': investigation.get('model'),
            'draft_resume_available': bool(investigation.get('recovery_checkpoint')) and not context.get('workflow'),
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
    }},context,compact_rows)


def compose_view(path, result):
    """Browser representation of a captured answer; native execution stays intact.

    Only replace history that matches the immutable saved context. Keep the
    ordinary response's graph, including operations outside the answer's
    dependency graph, and every answer, workflow and accounting field.
    """
    import json
    import re
    from . import answer_context
    key = (result.get('action_context') or {}).get('id')
    investigation = result.get('investigation') or {}
    if not isinstance(key, str) or not re.fullmatch(r'ANS-[0-9a-f]{24}', key):
        return result
    try:
        context = answer_context.read_snapshot(answer_context.root(path) / key / 'answer.json')
    except (OSError, ValueError):
        return result
    if (not isinstance(context, dict) or context.get('id') != key
            or any(context.get(k) != result.get(k) for k in ('investigation', 'graph', 'planner_receipt'))):
        return result
    # Capture may add exact cohort membership; it must not change the answer
    # represented here. No historical verdict is inherited by this projection.
    def without_membership(answer):
        return {k:v for k,v in (answer or {}).items() if k not in ('patient_ids', 'membership_role')}
    saved_answer = without_membership(context.get('answer'))
    shown_answer = without_membership(result.get('answer'))
    if saved_answer != shown_answer:
        # fulfill() can conservatively downgrade delivery after capture when
        # current source review is required. Recognize only that exact change;
        # the current answer/authority flags below remain untouched. This does
        # not inherit an earlier verdict or authorize any clinical work.
        source_review_answer = {**saved_answer,
            'captured_status': saved_answer.get('captured_status', saved_answer.get('status')),
            'status': 'partial', 'verification': 'unverified'}
        if (result.get('source_review_required') is not True
                or result.get('clinical_execution_authorized') is not False
                or shown_answer != source_review_answer):
            return result

    def reference(location):
        return {'native_context_id':key, 'path':location,
                'context_derivation':(context.get('dependencies') or {}).get('derivation')}
    graph = result.get('graph')
    compact_graph = graph
    if isinstance(graph, dict) and isinstance(graph.get('nodes'), list):
        nodes = []
        for index, node in enumerate(graph['nodes']):
            shown = dict(node)
            for field in ('parameters', 'output'):
                if field in node:
                    shown[field] = {'native_reference':reference(['graph', 'nodes', index, field])}
            nodes.append(shown)
        compact_graph = {**graph, 'nodes':nodes}

    _, receipt, reviews = _review_projection(context)
    def graphs(value):
        if isinstance(value, dict):
            return {k:(compact_graph if k == 'graph' and v == graph else graphs(v)) for k,v in value.items()}
        if isinstance(value, list):
            return [graphs(v) for v in value]
        return value
    receipt = graphs(receipt)
    out = {**result, 'graph':compact_graph, 'planner_receipt':receipt,
           'response_projection':{'schema':'saved-compose-view/1', 'native_context_id':key,
                                  'complete_native_history_retained':True,
                                  'clinical_acceptance_changed':False}}
    if investigation.get('tool_calls'):
        out['investigation']={
               'model':investigation.get('model'),
               'tool_call_count':len(investigation['tool_calls']),
               'document_review_count':len(investigation.get('document_reviews') or []),
               'answer_reviews':reviews,
               'draft_resume_available':bool(investigation.get('recovery_checkpoint')) and not result.get('workflow'),
               'native_reference':reference(['investigation']),
               'complete_native_history_retained':True}
    native_contract = (context.get('planner_receipt') or {}).get('request_contract')
    if 'request_contract' in result and result['request_contract'] == native_contract:
        out['request_contract'] = receipt.get('request_contract')
    if result.get('results') is not None and result['results'] == context.get('results'):
        out['results'] = {'native_reference':reference(['results'])}
    return out


def encode_inspection(record, accept_encoding=''):
    """Lossless standard HTTP compression, with explicit gzip refusal honored."""
    import gzip
    import json
    raw=json.dumps(record,default=str,allow_nan=False).encode()
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
    from ..application_shell import navigation, palette_css, SHELL_CSS, NAV_SCRIPT
    from .patient_dashboard import PATIENT_WORKSPACE_JS, PATIENT_WORKSPACE_CSS
    assets = Path(__file__).parent
    result = (assets / 'action_center.html').read_text().replace(
        '/* ACTION_CENTER_CSS */', (assets / 'action_center.css').read_text() + '\n' +
        (assets / 'workspace_skin.css').read_text() + palette_css() + SHELL_CSS + PATIENT_WORKSPACE_CSS
    ).replace('/* ACTION_CENTER_JS */', PATIENT_WORKSPACE_JS + '\n' + (assets / 'action_center.js').read_text() + '\n' + NAV_SCRIPT)
    import re
    extra = re.search(r'<nav class="workspace-nav".*?</nav>',result,re.S).group()
    result = re.sub(r'<aside class="sidebar">.*?</aside>',navigation('work',extra=extra),result,count=1,flags=re.S)
    return result.replace('<body>','<body class="wc-action">').replace('Agere · Action workspace','Wollstonecraft · Review &amp; work')
