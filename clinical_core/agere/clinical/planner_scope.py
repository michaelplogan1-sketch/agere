"""Bounded catalog selection with explicit requirement coverage, not truncation.

The selector sees every operator's meaning and the same source vocabulary. It
cannot answer or execute. Detailed contracts are loaded only after selection.
Coverage checks are structural safeguards, not proof of semantic correctness.
"""
import copy
import json
from ...contracts import digest
from .planner_catalog import serialize

VERSION='bounded-planner-scope/7'
from .context_student import REQUEST_MAX_BYTES
MAX_BYTES=REQUEST_MAX_BYTES
PLAN_MAX_BYTES=MAX_BYTES  # Match the shared provider transport bound.


CACHE=('cache_control',{'type':'ephemeral','ttl':'1h'})


def content(data,render=None):
    """User content: the catalog as one cacheable prefix block, then the question.

    The catalog is identical across questions for a snapshot, so repeated planning
    reads it from the prompt cache instead of paying full input price."""
    data=dict(data);question=data.pop('question',None)
    blocks=[{'type':'text','text':(render or serialize)(data),CACHE[0]:CACHE[1]}]
    if isinstance(question,str):blocks.append({'type':'text','text':'question: '+question})
    return blocks


def text(message):
    """Plain text of a user message (string or content blocks)."""
    c=message['content']
    return c if isinstance(c,str) else '\n'.join(b['text'] for b in c if b.get('type')=='text')


def catalog_text(message):
    """The serialized catalog block of a user message (string or content blocks)."""
    c=message['content']
    return c if isinstance(c,str) else c[0]['text']


def bounded(body,limit=MAX_BYTES):return len(json.dumps(body,allow_nan=False).encode())<=limit


def value(raw):
    if raw.get('stop_reason')!='end_turn':raise ValueError('Incomplete catalog selection')
    return json.loads(''.join(x['text'] for x in raw['content'] if x.get('type')=='text'))


def selection_schema(operations,streams=None,references=None):
    item={'type':'object','properties':{'text':{'type':'string'},
        'operations':{'type':'array','items':{'type':'string','enum':sorted(operations)}},'source_streams':{'type':'array','items':{'type':'string'}},'unmet':{'type':'string'}},
        'required':['text','operations','source_streams','unmet'],'additionalProperties':False}
    if references:
        item['properties']['reference_ids']={'type':'array','items':{'type':'string','enum':sorted(references)}}
        item['required'].append('reference_ids')
    if streams is not None:
        item['properties']['source_streams']['items']['enum']=sorted(streams)
    return {'type':'object','properties':{'segments':{'type':'array','items':item}},'required':['segments'],'additionalProperties':False}


def selection(raw,question,contracts,streams=None,references=None):
    result=value(raw)
    if set(result)!={'segments'} or not isinstance(result['segments'],list) or not 1<=len(result['segments'])<=16:raise ValueError('Bounded requirement segments required')
    for s in result['segments']:
        if set(s)-{'source_streams','reference_ids'}!={'text','operations','unmet'} or not isinstance(s['text'],str) or not s['text']:raise ValueError('Exact question segment required')
        if 'source_streams' in s:
            chosen=s['source_streams']
            if not isinstance(chosen,list) or len(chosen)>50 or any(not isinstance(x,str) or streams is None or x not in streams for x in chosen) or len(chosen)!=len(set(chosen)):raise ValueError('Exact registered source streams required')
        refs=s.get('reference_ids',[])
        if not isinstance(refs,list) or len(refs)!=len(set(refs)) or any(r not in (references or []) for r in refs):raise ValueError('Exact registered scientific reference scope required')
        if refs and 'reference_review' not in s['operations']:raise ValueError('Reference scope requires the consuming operation')
        if 'reference_review' in s['operations'] and not refs and not s['unmet']:raise ValueError('Reference review needs an exact selected reference or unmet reason')
        ops=s['operations']
        if not isinstance(ops,list) or len(ops)>16 or any(not isinstance(o,str) or o not in contracts for o in ops) or len(ops)!=len(set(ops)):raise ValueError('Unique registered scope operations required')
        if not isinstance(s['unmet'],str) or len(s['unmet'])>1000 or not (ops or s['unmet']):raise ValueError('Every segment needs operators or an explicit unmet requirement')
    if ''.join(s['text'] for s in result['segments'])!=question:raise ValueError('Scope must preserve the entire question exactly and in order')
    return result


def coverage(graph,rows,scope,*,inherited_unmet=False):
    segments=scope['segments'];nodes={n['id']:n for n in graph['nodes']}
    if not isinstance(rows,list) or len(rows)!=len(segments):raise ValueError('Coverage required for every question segment')
    seen=set();allowed={o for s in segments for o in s['operations']};gaps=[]
    if any(n['operation'] not in allowed for n in graph['nodes']):raise ValueError('Graph uses an unloaded operator contract')
    for r in rows:
        if set(r)!={'segment','node_ids','unmet'} or type(r['segment']) is not int or not 0<=r['segment']<len(segments) or r['segment'] in seen:raise ValueError('Unique exact segment coverage required')
        seen.add(r['segment']);s=segments[r['segment']];refs=r['node_ids']
        if not isinstance(refs,list) or any(not isinstance(k,str) or k not in nodes for k in refs) or len(refs)!=len(set(refs)):raise ValueError('Coverage must name actual graph nodes')
        if not isinstance(r['unmet'],str) or len(r['unmet'])>1000:raise ValueError('Bounded unmet requirement required')
        if s['unmet'] and ((not inherited_unmet and s['unmet'] not in r['unmet']) or s['unmet'] not in graph['unmet_requirements']):raise ValueError('Unmet selector requirement cannot disappear')
        if r['unmet']:gaps.append(r['unmet'])
        elif not (inherited_unmet and s['unmet']):
            # A cohort or helper feeding the delivering node covers that segment
            # too. Never count unrelated nodes elsewhere in the graph.
            ancestors=set()
            def visit(key):
                if key in ancestors:return
                ancestors.add(key)
                for parent in nodes[key].get('inputs',[]):
                    if parent not in nodes:raise ValueError('Unknown coverage dependency')
                    visit(parent)
            for key in refs:visit(key)
            # Selected operators are candidate capabilities, not mandatory steps: a
            # segment is delivered when actual nodes answer it, even by fewer operators.
            if not ancestors:raise ValueError('A selected requirement has no delivering graph node')
    if gaps and (graph['coverage']=='supported_observation' or any(g not in graph['unmet_requirements'] for g in gaps)):raise ValueError('Unmet scoped requirements must remain visible in the answer')
    return rows


def compile_requirements(wire,scope):
    """Derive public coverage from declarations, never from prose matching.

Selector gaps are immutable inputs. Each planner segment adds its own gap once;
there is no second model-authored list whose phrasing can silently disagree.
"""
    if set(wire)!={'interpretation','nodes','requirement_coverage'}:
        raise ValueError('Typed plan requires one authoritative requirement declaration')
    rows=wire['requirement_coverage']
    if not isinstance(rows,list):raise ValueError('Requirement coverage rows required')
    gaps=[s['unmet'] for s in scope['segments'] if s['unmet']]
    for row in rows:
        if not isinstance(row,dict) or not isinstance(row.get('unmet'),str):raise ValueError('Explicit unmet declaration required')
        if row['unmet']:gaps.append(row['unmet'])
    gaps=list(dict.fromkeys(gaps))
    return {'interpretation':wire['interpretation'],'nodes':wire['nodes'],
        'coverage':'unsupported' if not wire['nodes'] else 'partial' if gaps else 'supported_observation',
        'unmet_requirements':gaps},rows


def typed_request(body,catalog,*,scoped):
    from .graph_contracts import TYPED,parameter_schema
    schema=copy.deepcopy(body['output_config']['format']['schema'])
    if scoped:
        for key in ('coverage','unmet_requirements'):
            schema['properties'].pop(key);schema['required'].remove(key)
    payload=copy.deepcopy(catalog)
    for op,contract in payload['node_contracts'].items():
        if op in TYPED:contract['parameters']=parameter_schema(op,catalog)
    # Load dictionaries by consuming operator, not by patient/question words.
    # This is dependency loading, with every deferred dictionary identified.
    uses={
        # Analytic nodes accept a registered operator ID, never these values.
        # Their calculations load evidence internally. Do not make every analytic
        # question pay for dictionaries only other graph operations can consume.
        'concepts':{'evidence','sequence','longitudinal','context','context_passes','interpretation'},
        'text_axis_values':{'evidence','sequence','longitudinal'},
        'structured_scalar_values':{'evidence'},'boolean_valued_concepts':{'evidence'},
        'forecast':{'forecast'},'longitudinal':{'sequence','longitudinal'},
        'source_review_programs':{'domain_review','functional_review'},
        'work_programs':{'worklist'},'assertion_concepts':{'assertion_review','source_state','interpretation'},
        'documentary_contracts':{'requirements','evidence_portfolio'},
        'baseline_profiles':{'baseline'},'landmark_contracts':{'decision_landmark'},
        'care_protocols':{'protocol_review','state_review'},'protocol_versions':{'protocol_change','protocol_review'},
        'scientific_references':{'reference_review'},
        'program_documents':{'program_document','program_acquisition','program_document_change','measure_review','context','context_passes'},
        'outcome_contracts':{'outcome'},'clinical_state_available':{'state_review'},
        'medication_terminology_available':{'medication_identity'}}
    ops=set(catalog['node_contracts']);evidence=payload['evidence_catalog'];deferred={}
    for key,consumers in uses.items():
        if key in evidence and not ops&consumers:deferred[key]=digest(evidence.pop(key))
    if 'analyze' not in ops and 'analytic_operators' in payload:
        deferred['analytic_operators']=digest(payload.pop('analytic_operators'))
    if 'assertion_concepts' in evidence and 'interpretation' not in ops:
        evidence['assertion_concepts']='Exact concepts are enumerated in assertion_review/source_state parameter contracts; no additional bindings exist.'
    evidence.setdefault('concepts',{'text':[],'structured':[]})
    evidence.setdefault('longitudinal',{'available':False,'status':'Not loaded; selected operators do not consume this dictionary.'})
    payload['operator_catalog_loading']={'deferred_dictionaries':sorted(deferred),'identity':digest(deferred),
        'meaning':'Unused operator dictionaries deferred, not unavailable data or unsupported clinical needs.'}
    if scoped:
        payload['question']='Exact question = ordered concatenation of requirement_segments.text.'
        # Hashes and duplicate inventories are audit metadata, not planning inputs.
        # Keep complete names/fields in their authoritative dictionaries and the
        # full loading manifest in the receipt. This does not drop any capability.
        payload.pop('graph_contract_version',None)
        payload['operator_catalog_loading'].pop('identity')
        if 'source_contract_loading' in payload:
            payload['source_contract_loading']={
                'rule':'Read only source_streams; predicates also require cohort_fields.record_fields. Other streams are unloaded.'}
    if 'cohort' in ops:
        payload['cohort_fields'].pop('predicate_contract',None)
        payload['cohort_fields'].pop('operators',None)
    return {**body,'messages':[{'role':'user','content':content(payload)}],
        'output_config':{'format':{'type':'json_schema','schema':schema}}}


def combine_receipts(receipts,manifest):
    calls=[r for r in receipts if not r.get('cache_hit')];known=[r.get('cost_estimate',{}).get('estimated_usd') for r in calls]
    cost=sum(x for x in known if x is not None) if all(x is not None for x in known) else None
    return {'scope':manifest,'stages':receipts,'cache_hit':not calls,'new_paid_api_calls':len(calls),
        'cost_estimate':{'estimated_usd':cost,'invoice_verified':False},
        'usage':{k:sum((r.get('usage') or {}).get(k,0) for r in calls) for k in ('input_tokens','output_tokens')}}


def dispatch(body,catalog,send,*,parse):
    typed=bool(catalog.get('graph_contract_version'))
    all_streams=set(catalog.get('source_streams',catalog['cohort_fields']['record_fields']))
    reference_index=[{k:r[k] for k in ('id','title')} for r in catalog['evidence_catalog'].get('scientific_references',[])]
    all_references={r['id'] for r in reference_index}
    if bounded(body):
        direct=typed_request(body,catalog,scoped=False) if typed else body
        if bounded(direct):return send(direct,parse=parse)
    # No question/vocabulary truncation. All operator meanings remain visible.
    index=copy.deepcopy(catalog)
    if typed:index['source_streams']='See source_streams schema enum. Full record-reading streams differ from the cohort predicate field registry.'
    index['node_contracts']={o:{'parameters':{},'meaning':c['meaning'],'inputs':c.get('inputs',['earlier_set'])} for o,c in catalog['node_contracts'].items()}
    selector={**body,'max_tokens':3000,'system':
        'Scope v6. Select capabilities only; never answer or execute. Partition the WHOLE question into 1-16 exact ordered text segments including spaces. Each segment needs operators/helper dependencies or a nonempty unmet reason; preserve restrictions and prohibitions. Shared review already attaches results; do not add worklist/protocol_review just to save context. source_streams use only their enum; readable streams do not imply supported cohort predicates. Detailed field contracts load in planning. Use [] for self-contained operators. Select reference_ids by registered title for reference_review, otherwise []; exact dated questions load in planning. No inferred applicability.',
        'messages':[{'role':'user','content':content(index)}],
        'output_config':{'format':{'type':'json_schema','schema':selection_schema(catalog['node_contracts'],all_streams,all_references)}}}
    deferred=[]
    if not bounded(selector):
        # Operator selection needs capabilities. Detailed source-field contracts
        # are loaded in the next request without dropping any planning vocabulary.
        deferred=['cohort_fields']
        index['cohort_fields']={'loading':'Complete source-field vocabulary is deferred to the planning request.',
            'available_streams':sorted(all_streams),
            'identity':digest(catalog['cohort_fields'])}
        selector['system']=selector['system'].replace('and the full supplied source vocabulary are visible.',
            'are visible. Detailed cohort/source fields are explicitly deferred until planning. Do not assert that an individual field or restriction is supported before those contracts are loaded.')
        selector['messages']=[{'role':'user','content':content(index,lambda d:serialize_index(d,all_streams))}]
    if not bounded(selector):
        # With many source categories, literal concept/value dictionaries can
        # dominate selection. Keep operator meanings and every source name;
        # restore these exact dictionaries in the final planning request.
        deferred+=['evidence_catalog.concepts','observed_values']
        index['deferred_value_catalogs']={
            'concepts':{'identity':digest(catalog['evidence_catalog']['concepts']),
                        'counts':{k:len(v) for k,v in catalog['evidence_catalog']['concepts'].items()}},
            'observed_values':{'identity':digest(catalog['observed_values'])},
            'loading':'Exact concept and observed-value dictionaries load in planning; do not assert an individual value is supported during selection.'}
        index['evidence_catalog']['concepts']={'text':[],'structured':[]}
        index['observed_values']=None
        selector['system']+=' Concept and observed-value dictionaries are also explicitly deferred; choose capabilities without asserting individual value support.'
        selector['messages']=[{'role':'user','content':content(index,lambda d:serialize_index(d,all_streams))}]
    if not bounded(selector) and typed:
        # Selection needs operator meanings, current work programs and all source
        # names. Exact analytical dictionaries load in the next stage, with their
        # complete identity retained here; none are silently truncated.
        deferred+=['evidence_catalog.detail']
        index['deferred_evidence_catalog']={'identity':digest(catalog['evidence_catalog']),
            'loading':'All detailed vocabulary/contract dictionaries load in planning; no value support is asserted during capability selection.'}
        index['evidence_catalog']={'scientific_references':reference_index,'concepts':{'text':[],'structured':[]},'longitudinal':{'available':False},
            'longitudinal_status':'Deferred, not asserted unavailable.',
            'work_programs':catalog['evidence_catalog']['work_programs']}
        selector['messages']=[{'role':'user','content':content(index,lambda d:serialize_index(d,all_streams))}]
    if not bounded(selector) and typed and 'dependency_contract' in index:
        # Scope selection needs every operator meaning. Exact graph edge typing
        # remains authoritative in the fully loaded planning/execution stages.
        deferred+=['dependency_contract']
        if 'analytic_operators' in index:
            deferred+=['analytic_operators']
            index['analytic_operators']={'identity':digest(catalog['analytic_operators']),
                'loading':'Detailed analyze-function contracts load when selected.'}
        index['dependency_contract']={'identity':digest(catalog['dependency_contract']),
            'loading':'Exact graph input/output rules load in planning; all operator meanings remain above.'}
        selector['messages']=[{'role':'user','content':content(index,lambda d:serialize_index(d,all_streams))}]
    if not bounded(selector):raise ValueError('Complete capability index exceeds bound; no vocabulary was silently removed')
    scope,first=send(selector,parse=lambda raw:selection(raw,catalog['question'],catalog['node_contracts'],all_streams,all_references))
    ops={o for s in scope['segments'] for o in s['operations']}
    selected=copy.deepcopy(catalog);selected['node_contracts']={o:c for o,c in catalog['node_contracts'].items() if o in ops}
    reference_scope={r for seg in scope['segments'] for r in seg.get('reference_ids',[])}
    selected['evidence_catalog']['scientific_references']=[r for r in catalog['evidence_catalog'].get('scientific_references',[]) if r['id'] in reference_scope]
    stream_scope=(set().union(*(set(s['source_streams']) for s in scope['segments'])) if all('source_streams' in s for s in scope['segments']) else None)
    if stream_scope is not None:
        if typed:selected['source_streams']=sorted(stream_scope)
        selected['cohort_fields']['record_fields']={k:v for k,v in catalog['cohort_fields']['record_fields'].items() if k in stream_scope}
        selected['source_contract_loading']={'loaded':sorted(stream_scope),'cohort_predicate_streams':sorted(selected['cohort_fields']['record_fields']),'not_loaded':sorted(all_streams-stream_scope),
            'meaning':'Only loaded reading streams allowed. Predicates also require cohort_predicate_streams fields; report other requirements as unmet.',
            'full_field_catalog_identity':digest(catalog['cohort_fields'])}
    selected['requirement_segments']=scope['segments']
    schema=copy.deepcopy(body['output_config']['format']['schema'])
    schema['properties']['nodes']['items']['properties']['operation']['enum']=sorted(ops) or sorted(catalog['node_contracts'])
    schema['properties']['requirement_coverage']={'type':'array','items':{'type':'object','properties':{
        'segment':{'type':'integer'},'node_ids':{'type':'array','items':{'type':'string'}},'unmet':{'type':'string'}},
        'required':['segment','node_ids','unmet'],'additionalProperties':False}}
    schema['required'].append('requirement_coverage')
    planned={**body,'system':body['system']+' For every numbered requirement segment return requirement_coverage with its zero-based segment, delivering node_ids, and unmet. Load only supplied node contracts. Retain any unmet selector requirement verbatim in unmet_requirements; partial or unsupported coverage is required when unmet exists.',
        'messages':[{'role':'user','content':content(selected)}],
        'output_config':{'format':{'type':'json_schema','schema':schema}}}
    if typed and ops:
        planned['system']=body['system']+' Each node parameters_json must encode an object matching its supplied typed parameter contract (JSON Schema), or its explicit legacy contract. For every segment return requirement_coverage with zero-based segment, delivering node_ids and additional unmet reason (empty only if delivered). Count upstream dependencies as delivery too. Every selected operation must deliver through those nodes or have an explicit unmet explanation. Selector gaps are preserved automatically. Coverage and unmet_requirements are compiled locally. Reports cannot become populations.'
        planned=typed_request(planned,selected,scoped=True)
    manifest={'schema':VERSION,'catalog_identity':digest(catalog),'scope':scope,'selected_operations':sorted(ops),
        'loaded_scientific_reference_ids':sorted(reference_scope),'unloaded_scientific_reference_ids':sorted(all_references-reference_scope),
        'all_operation_meanings_visible_to_selector':True,'supplied_catalog_vocabulary_truncated':False,
        'source_vocabulary_deferred_to_planning':deferred,'all_supplied_source_vocabulary_in_plan':stream_scope is None or stream_scope==all_streams,
        'loaded_source_field_contracts':sorted(stream_scope) if stream_scope is not None else sorted(all_streams),
        'unloaded_source_field_contracts':sorted(all_streams-stream_scope) if stream_scope is not None else [],
        'selection_request_bytes':len(json.dumps(selector).encode()),'plan_request_bytes':len(json.dumps(planned).encode()),'plan_request_limit_bytes':PLAN_MAX_BYTES if typed else MAX_BYTES,
        'semantic_completeness_validated':False}
    if not ops or not bounded(planned,PLAN_MAX_BYTES if typed else MAX_BYTES):
        reason='No supported operator scope selected.' if not ops else 'Selected full contracts exceed the bounded request; this composition needs a narrower question or additional catalog paging.'
        result={'interpretation':reason,'nodes':[],'coverage':'unsupported',
            'unmet_requirements':[reason]+list(dict.fromkeys(s['unmet'] for s in scope['segments'] if s['unmet']))[:19]}
        return result,combine_receipts([first],manifest)
    def parse_scoped(raw):
        wire=value(raw)
        if typed:wire,ack=compile_requirements(wire,scope)
        else:ack=wire.pop('requirement_coverage',None)
        graph=parse({**raw,'content':[{'type':'text','text':json.dumps(wire)}]})
        if any(n['operation']=='reference_review' and n['parameters']['reference_id'] not in reference_scope for n in graph['nodes']):
            raise ValueError('Graph uses an unloaded scientific reference contract')
        if stream_scope is not None:
            def referenced_streams(value):
                found=set()
                if isinstance(value,dict):
                    for key,child in value.items():
                        if key=='stream' and isinstance(child,str):found.add(child)
                        elif key=='streams' and isinstance(child,list):found.update(x for x in child if isinstance(x,str))
                        else:found.update(referenced_streams(child))
                elif isinstance(value,list):
                    for child in value:found.update(referenced_streams(child))
                return found
            if referenced_streams([n.get('parameters',{}) for n in graph['nodes']])-stream_scope:raise ValueError('Graph uses an unloaded source-field contract')
        manifest['requirement_coverage']=coverage(graph,ack,scope,inherited_unmet=typed)
        return graph
    result,second=send(planned,parse=parse_scoped)
    return result,combine_receipts([first,second],manifest)


def serialize_index(index, schema_streams=None):
    """Keep the regular lossless serializer, with an explicit deferred-field note."""
    working=copy.deepcopy(index);note=working['cohort_fields']
    # Publish source names once, as empty field-contract placeholders. The
    # deferral declaration distinguishes these from genuinely fieldless sources.
    # Longitudinal stream factoring can then reference the same exact names.
    working['cohort_fields']={'record_fields':{s:[] for s in note['available_streams']}}
    working['deferred_cohort_fields']={**note,'available_streams':'See cohort record-field keys; field contents are deferred, not empty.'}
    return serialize(working,source_names_in_schema=schema_streams)
