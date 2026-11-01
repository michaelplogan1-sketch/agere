"""Deterministic investigator state: native results remain complete and queryable.

This changes the actor's prompt representation, not its evidence or scope. No
model-generated summary, source truncation or learned clinical assertion is used.
"""
import json
from ...contracts import digest

VERSION = 'execution-state/5'


def progress_evidence(calls,candidate=None):
    """Native dependency identity, independent of temporary names and retries.

    Final tables and bound prose name their actual dependency views. Unused
    follow-up queries are retained in receipts but cannot reset a failed review.
    Unbound scalar callers retain the complete native evidence set.
    """
    from .harness_improvement import semantic_result
    import re
    pattern=re.compile(r'\bagere_result_\d+\b')
    def refs(value):
        if isinstance(value,str):return set(pattern.findall(value))
        if isinstance(value,dict):return set().union(*(refs(v)for v in value.values()))if value else set()
        if isinstance(value,list):return set().union(*(refs(v)for v in value))if value else set()
        return set()
    by_view={(c.get('result')or{}).get('result_view'):c for c in calls if isinstance(c.get('result'),dict)}
    wanted=refs({k:(candidate or{}).get(k)for k in('table_from_tool','computed_prose','headline','facts','value')})if isinstance(candidate,dict)else set()
    selected=set();todo=list(wanted)
    while todo:
        name=todo.pop()
        if name in selected:continue
        selected.add(name);call=by_view.get(name)
        if call:todo.extend(refs(call.get('input'))-selected)
    names={};observed=[]
    def stable(value,key=None):
        if isinstance(value,dict):return{k:stable(v,k)for k,v in value.items()if k not in('result_view','actor_callback_id','owned_run_recovery','request_hash','attempt_id')}
        if isinstance(value,list):return[stable(v)for v in value]
        if isinstance(value,str)and key in('sql','query','executed_query','base','result','candidate_result_view','table_from_tool'):
            return pattern.sub(lambda m:names.get(m.group(),m.group()),value)
        return value
    for call in calls:
        value=stable(semantic_result({k:call.get(k)for k in('name','input','result')}))
        ident=digest(value);out=call.get('result')or{}
        name=out.get('result_view')if isinstance(out,dict)else None
        if name:names[name]='native_'+ident
        if not wanted or name in selected:observed.append(ident)
    return sorted(set(observed))


def draft_state(candidate):
    """Reference an unverified draft without copying its native patient table."""
    state={'last_draft':{k:v for k,v in candidate.items()if k not in (
        'table','sources','evidence_text','system_one_judgments','table_binding','investigation_review','verification','limitations')},
        'last_draft_digest':digest(candidate)}if isinstance(candidate,dict)else{'last_draft':candidate,'last_draft_digest':digest(candidate)}
    if not isinstance(candidate,dict):return state
    # Present unchanged compiled fields in their editable native form. This is
    # only prompt state: the compiler must resolve every binding again.
    draft=state['last_draft'];draft.pop('computed_prose',None)
    for proof in candidate.get('computed_prose')or[]:
        if not isinstance(proof,dict)or not all(k in proof for k in ('field','template','bindings','rendered')):continue
        field=proof['field'];target=draft;key=field
        if field.startswith('facts.'):
            target=draft.get('facts');key=field[len('facts.'):]
            if not isinstance(target,dict):continue
            # Never mutate the saved candidate through its nested facts object.
            target=draft['facts']=dict(target)
        elif field!='headline':continue
        if target.get(key)==proof['rendered']:
            target[key]={'template':proof['template'],'bindings':proof['bindings']}
    state['last_draft_prose_instruction']='Return unchanged quantitative fields as these template/bindings objects verbatim. Any edited sentence needs a new template; every binding is rechecked against current executed cells.'
    binding=candidate.get('table_binding')
    if isinstance(binding,dict):
        state['last_draft_table_binding']={k:v for k,v in binding.items()if k!='query'}
        if'query'in binding:state['last_draft_table_binding']['native_query_digest']=digest(binding['query'])
    for key in ('verification','limitations'):
        if key in candidate:state['last_draft'][key]=candidate[key]
    if isinstance(candidate.get('table'),list):
        state['last_draft_rows']=len(candidate['table'])
        state['last_draft_table_instruction']='The last draft table is preserved natively. Reconstruct it only from the matching executed result_view; do not infer omitted values.'
    return state


def recovery_context(replay,tools):
    """Keep complete recovery feedback queryable; send native draft references.

    Owned recovery is still unfinished work, never an accepted learned answer.
    This handles the draft before it can become nested original-request text.
    """
    meta={k:v for k,v in replay.items()if k not in ('calls','final_answer')}
    feedback=meta.get('downstream_review_feedback')
    if not feedback or tools.semantic is None:return meta
    native_digest=digest(feedback)
    raw=json.dumps(feedback,default=str,allow_nan=False)
    columns=['feedback_json','feedback_digest'];rows=[[raw,native_digest]]
    existing=next((v for v in tools.executed_results.values()if v.get('kind')=='unverified_recovery_feedback'and v.get('feedback_digest')==native_digest),None)
    if existing is not None:
        if existing.get('rows')!=[dict(zip(columns,rows[0]))]:
            raise ValueError('Recovery feedback native view changed')
    else:
        existing=tools.retain_result({'columns':columns,'rows':[dict(zip(columns,rows[0]))],
            'truncated':False,'kind':'unverified_recovery_feedback','feedback_digest':native_digest})
    name=existing['result_view']
    reference={'result_view':name,'json_column':'feedback_json','feedback_digest':native_digest,
        'instruction':'Full original feedback and draft remain queryable with query_sql/json_extract. Draft values are unverified; bind any final answer to current executed results and complete verification.'}
    def project(value):
        if isinstance(value,list):return[project(v)for v in value]
        if not isinstance(value,dict):return value
        return{k:({'native_feedback':reference,**draft_state(v)}if k=='unverified_retained_draft'else project(v))for k,v in value.items()}
    meta['downstream_review_feedback']=project(feedback)
    meta['downstream_review_native']=reference
    return meta


def review_reference(feedback,tools):
    """Keep complete structured review feedback queryable, never a new fact.

    A source review can repeat record lists in its prose and in every field
    judgment. Its exact receipt belongs in the existing native result store;
    copying both representations into an actor prompt can exceed service input
    context even when the patient evidence already stays native.
    """
    if not isinstance(feedback,dict) or not isinstance(feedback.get('support'),dict):return None
    if not feedback['support'].get('packets'):return None
    if getattr(tools,'semantic',None) is None or not callable(getattr(tools,'retain_result',None)):return None
    ident=digest(feedback)
    row={'feedback_json':json.dumps(feedback,default=str,allow_nan=False),'feedback_digest':ident}
    existing=next((v for v in tools.executed_results.values()
        if v.get('kind')=='unverified_review_feedback' and v.get('feedback_digest')==ident),None)
    if existing is not None:
        if existing.get('columns')!=list(row) or existing.get('rows')!=[row] or existing.get('truncated'):
            raise ValueError('Review feedback native view changed')
    else:
        existing=tools.retain_result({'columns':list(row),'rows':[row],
            'truncated':False,'kind':'unverified_review_feedback','feedback_digest':ident})
    return {'result_view':existing['result_view'],'json_column':'feedback_json','feedback_digest':ident,
        'guidance_path':'$.feedback','packets_path':'$.support.packets',
        'instruction':'The complete exact reviewer receipt, repair prose and every claim/source binding are retained here. '
            'Use query_sql with json_extract/json_each to inspect the relevant packet and claim_feedback entries. '
            'Packet IDs are opaque labels; use only explicit patient_id bindings. '
            'This is unverified reviewer feedback, not patient evidence or a clinical truth label. '
            'Unknown feedback is an unresolved dependency, not a proven source fault. '
            'Retain unaffected native work and uncertainty; verify any final answer against the current source receipts.'}


def documentation_references(out):
    loaded = out.get('loaded_documentation') or {}
    return {'identity': loaded.get('identity'),
            'sections': [{k: section[k] for k in ('section_id', 'sha256', 'title')}
                         for section in loaded.get('sections', [])]}


def loaded_documentation(calls, identity):
    """One exact copy of each actually loaded section of the current contract."""
    import hashlib
    sections = {}
    if identity is None:return None
    for call in calls:
        loaded = (call.get('result') or {}).get('loaded_documentation') if isinstance(call.get('result'), dict) else None
        if not loaded or loaded.get('identity') != identity:continue
        for section in loaded.get('sections', []):
            if hashlib.sha256(section['documentation_text'].encode()).hexdigest() != section['sha256']:
                raise ValueError('Loaded documentation digest changed')
            key = section['section_id']
            if key in sections and sections[key] != section:
                raise ValueError('Conflicting documentation sections under one installed identity')
            sections[key] = section
    return {'identity': identity, 'sections': list(sections.values()),
            'instruction': 'These are complete first-party documentation sections already loaded from native SQL. '
                'Use their exact definitions; do not fetch them again unless different sections or a changed contract are needed. '
                'They are documentation, never patient evidence.'} if sections else None


def catalog(calls):
    entries=[]
    for index,call in enumerate(calls):
        out=call.get('result')
        entry={'call':index,'tool':call.get('name'),'input_digest':digest(call.get('input')),
               'result_digest':digest(out)}
        if not isinstance(out,dict):
            entry['observation']=out
        elif out.get('error'):
            entry.update(error=out['error'],failed_input=call.get('input'))
        else:
            rows=out.get('rows')
            if not isinstance(rows,list):rows=next((out[k]for k in ('table','documents','passages')if isinstance(out.get(k),list)),None)
            if isinstance(rows,list)and out.get('result_view'):
                entry.update(result_view=out['result_view'],columns=out.get('columns')or
                    list(dict.fromkeys(k for r in rows if isinstance(r,dict)for k in r)),
                    rows_computed=len(rows),truncated=bool(out.get('truncated')))
                from .semantic_analyst import collection_manifest
                collections=collection_manifest(rows)
                if collections:entry['structured_collection_manifest']=collections
                # Small scalar/aggregate results are useful exact observations;
                # patient/free-text tables stay native and queryable by view.
                if out.get('loaded_documentation'):
                    entry['documentation_sections'] = documentation_references(out)
                elif not any(isinstance(r,dict)and'patient_id'in r for r in rows) and len(json.dumps(rows,default=str))<2500:
                    entry['computed_rows']=rows
                for key in ('coverage','method','record_ids_filter','accounting','assembly','source_scope','field_patient_queries','exhaustive_reason','scope_warnings',
                            'native_sql_contract','read_views','incomplete_dependencies','registered_domain_functions'):
                    if key in out:
                        if key=='coverage'and isinstance(out[key],list):
                            entry['coverage']={'patients':len(out[key]),'received_documents':sum(r.get('received_documents',0)for r in out[key]),
                                'unique_whole_documents':sum(r.get('unique_whole_documents',0)for r in out[key]),
                                'inventory_whole_documents':sum(r.get('inventory_whole_documents',r.get('unique_whole_documents',0))for r in out[key]),
                                'source_scope':out.get('source_scope'),'chart_wide_absence_established':False}
                        else:entry[key]=out[key]
                if isinstance(rows,list)and any(isinstance(r,dict)and'review_json'in r for r in rows):
                    fields={}
                    for row in rows:
                        try:found=json.loads(row.get('review_json')or'{}')
                        except (ValueError,TypeError):continue
                        for key,values in found.items():
                            if isinstance(values,list):
                                v=fields.setdefault(key,{'patients_with_findings':0,'findings':0,'json_path':'$.'+key})
                                v['patients_with_findings']+=bool(values);v['findings']+=len(values)
                    entry['narrative_field_manifest']=fields
            else:
                # Non-table contracts/instructions must remain visible. They
                # are not silently summarized by a language model or dropped.
                entry['observation']=out
        entries.append(entry)
    return entries


def messages(request,calls,*,recent_results=(),feedback=None,candidate=None,documentation_identity=None,documentation_in_system=False,feedback_native=None):
    from .semantic_analyst import actor_result
    def recent(call):
        out = call.get('result')
        if isinstance(out, dict) and out.get('loaded_documentation'):
            out = {k: v for k, v in out.items() if k not in ('rows', 'loaded_documentation')}
            out['documentation_sections'] = documentation_references(call['result'])
        return {'name': call.get('name'), 'input': call.get('input'), 'result': actor_result(out)}
    state={'schema':VERSION,'original_request':request,'executed_results':catalog(calls),
        'latest_results':[recent(c) for c in recent_results],
        'instruction':'This is the current deterministic execution state, not a new question or a clinical summary. '
            'All native receipts are retained. SQL result_views retain every computed row; a reading preview does not limit native membership. '
            'A truncated producer or incomplete_dependencies cannot support an accepted calculation or population scope. '
            'Read any earlier result by query_sql; use compose_table to join already executed results without regenerating SQL. '
            'A missing preview is not missing evidence. Preserve original scope and all requested outputs. '
            'Avoid repeating successful work; correct unresolved errors and current review feedback. '
            'Return table_from_tool naming the complete composed result; never transcribe patient tables.'}
    loaded = loaded_documentation(calls, documentation_identity)
    if loaded is not None:
        state['loaded_documentation'] = ({'identity':loaded['identity'],
            'sections':[{k:r[k]for k in ('section_id','title','sha256')}for r in loaded['sections']],
            'instruction':'The exact loaded first-party sections appear in the cacheable system documentation block; these are references to that complete text, not patient evidence.'}
            if documentation_in_system else loaded)
    if feedback is not None:
        # Operational reviewer receipts remain native; preserve the exact current
        # repair instructions without resending all model probabilities/costs.
        if isinstance(feedback,dict):
            if feedback_native is not None and feedback_native.get('feedback_digest')!=digest(feedback):
                raise ValueError('Review feedback reference does not match the current receipt')
            visible=('status','reason','repair_kind') if feedback_native is not None else ('status','feedback','reason','repair_kind')
            state['current_review_feedback']={k:feedback[k]for k in visible if k in feedback}
            state['current_review_native_digest']=digest(feedback)
            if feedback_native is not None:state['current_review_feedback']['native_feedback']=feedback_native
            coverage=feedback.get('coverage')or{}
            if coverage:
                state['current_review_feedback']['coverage']={k:coverage[k]for k in ('label','reasons','reject')if k in coverage}
            support=feedback.get('support')or{}
            fields=('id','patient_id','label','reason') if feedback_native is not None else ('id','patient_id','label','reason','claim_feedback')
            pending=[]
            for packet in support.get('packets',[]):
                if packet.get('label')=='supported':continue
                item={k:packet[k]for k in fields if k in packet}
                if feedback_native is not None:item['claim_feedback_entries']=len(packet.get('claim_feedback')or[])
                pending.append(item)
            if pending:state['current_review_feedback']['unresolved_dependencies']=pending
            if support.get('failed_packet_ids'):
                state['current_review_feedback']['failed_dependency_ids']=support['failed_packet_ids']
        else:state['current_review_feedback']=feedback
    if candidate is not None:
        state.update(draft_state(candidate))
    return [{'role':'user','content':json.dumps(state,default=str,allow_nan=False)}]
