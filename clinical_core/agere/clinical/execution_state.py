"""Deterministic investigator state: native results remain complete and queryable.

This changes the actor's prompt representation, not its evidence or scope. No
model-generated summary, source truncation or learned clinical assertion is used.
"""
import json
from ...contracts import digest

VERSION = 'execution-state/9'
OPERATIONAL_FEEDBACK_AUTHORITIES = frozenset({'unverified_review_feedback'})


def operational_feedback_result(result):
    """Recognize only complete producer declarations, never row content/names."""
    dependencies = result.get('incomplete_dependencies') if isinstance(result, dict) else None
    return (isinstance(dependencies, list) and bool(dependencies) and all(
        isinstance(item, dict) and item.get('authority') in OPERATIONAL_FEEDBACK_AUTHORITIES
        for item in dependencies))


def review_limitation_view(text, feedback, reference):
    """Project only a bound prior review inside otherwise unchanged prose."""
    if not isinstance(text, str) or not isinstance(feedback, dict) or not isinstance(reference, dict):
        return text
    original = feedback.get('feedback')
    if (reference.get('feedback_digest') != digest(feedback) or not reference.get('result_view') or
            not isinstance(original, str) or not original or original not in text):
        return text
    guidance = review_guidance(original)
    if guidance == original:
        return text
    return {'prose': text.replace(original, guidance), 'original_text_digest': digest(text),
            'authority': 'unverified_review_feedback', 'native_review_reference': reference,
            'meaning': 'Only embedded JSON in the exact prior machine review is referenced. All limitation prose survives; this is not clinical source evidence.'}


def progress_evidence(calls,candidate=None,*,first_call=0):
    """Native dependency identity, independent of temporary names and retries.

    Final tables and bound prose name their actual dependency views. Unused
    follow-up queries are retained in receipts but cannot reset a failed review.
    Unbound scalar callers retain the complete native evidence set. A tool
    batch may select its own calls while resolving references against all
    preceding receipts; revisiting a changed observation is still progress.
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
    wanted=refs({k:(candidate or{}).get(k)for k in('table_from_tool','computed_prose','row_prose','headline','facts','value')})if isinstance(candidate,dict)else set()
    selected=set();todo=list(wanted)
    while todo:
        name=todo.pop()
        if name in selected:continue
        selected.add(name);call=by_view.get(name)
        if call:todo.extend(refs(call.get('input'))-selected)
    names={};observed=[]
    def stable(value,key=None):
        if isinstance(value,dict):
            table=isinstance(value.get('rows'),list)and isinstance(value.get('columns'),list)
            # Reserved transport names inside business cells are source data.
            return{k:(v if table and k in('rows','columns')else stable(v,k))for k,v in value.items()
                   if k not in('result_view','actor_callback_id','owned_run_recovery','request_hash','attempt_id')}
        if isinstance(value,list):return[stable(v)for v in value]
        if isinstance(value,str)and key in('sql','query','executed_query','base','result','candidate_result_view','table_from_tool'):
            return pattern.sub(lambda m:names.get(m.group(),m.group()),value)
        return value
    for index,call in enumerate(calls):
        value=stable(semantic_result({k:call.get(k)for k in('name','input','result')}))
        ident=digest(value);out=call.get('result')or{}
        name=out.get('result_view')if isinstance(out,dict)else None
        if name:names[name]='native_'+ident
        if index>=first_call and (not wanted or name in selected):observed.append(ident)
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
    if isinstance(candidate.get('row_prose'),dict):
        state['localized_prose_repair']={
            'draft_digest':state['last_draft_digest'],
            'instruction':'To change only existing patient/entity prose, return {"repair_draft":{"draft_digest":"the exact digest here","rows":[{"key":{original entity keys},"fields":{existing prose field:replacement}}]}}. '
                'Send only changed fields. Code retains every other row, source and basis, then runs the normal complete compiler and review. '
                'This cannot change native values, entity keys or source bases. For other changes return the complete answer contract.'}
    return state


def apply_prose_repair(proposal,candidate):
    """Merge an exact-draft prose edit atomically; confer no evidence authority."""
    import copy
    if not isinstance(proposal,dict) or set(proposal)!={'repair_draft'}:
        raise ValueError('A prose repair must contain only repair_draft')
    repair=proposal['repair_draft']
    if not isinstance(repair,dict) or set(repair)!={'draft_digest','rows'}:
        raise ValueError('repair_draft requires draft_digest and rows')
    if not isinstance(candidate,dict) or repair['draft_digest']!=digest(candidate):
        raise ValueError('Prose repair does not match the current draft; use its current digest or return a complete answer')
    spec=candidate.get('row_prose')
    if not isinstance(spec,dict) or set(spec)!={'keys','rows'} or not isinstance(spec['rows'],list):
        raise ValueError('Current draft has no keyed prose to repair')
    keys=spec['keys']
    if not isinstance(keys,list) or not keys or any(not isinstance(k,str)for k in keys) or len(set(keys))!=len(keys):
        raise ValueError('Current draft has invalid prose keys; return a complete answer')
    def identity(key):
        if (not isinstance(key,dict) or set(key)!=set(keys)
                or any(type(v)not in(str,int,float,bool)for v in key.values())):
            raise ValueError('Prose repair must use the exact existing entity keys')
        return digest(key)
    existing={}
    for row in spec['rows']:
        if not isinstance(row,dict) or not isinstance(row.get('fields'),dict):
            raise ValueError('Current draft has invalid prose rows; return a complete answer')
        ident=identity(row.get('key'))
        if ident in existing:raise ValueError('Current draft has duplicate entity keys; return a complete answer')
        existing[ident]=row
    edits=repair['rows']
    if not isinstance(edits,list) or not edits:raise ValueError('Prose repair needs changed fields')
    changes={}
    for edit in edits:
        if not isinstance(edit,dict) or set(edit)!={'key','fields'}:
            raise ValueError('Each prose edit requires only key and fields; bases and native values cannot be patched')
        ident=identity(edit['key']);fields=edit['fields']
        if ident not in existing:raise ValueError('Prose repair cannot add or rename an entity')
        if ident in changes:raise ValueError('Prose repair repeats an entity')
        if not isinstance(fields,dict) or not fields or not set(fields)<=set(existing[ident]['fields']):
            raise ValueError('Prose repair may replace only existing prose fields')
        if any(not isinstance(v,(str,dict))for v in fields.values()):
            raise ValueError('Prose fields require text or a computed template; deletion is not supported')
        changes[ident]={k:v for k,v in fields.items()if existing[ident]['fields'][k]!=v}
    if not any(changes.values()):raise ValueError('Prose repair contains no changed fields; inspect the unresolved dependency')
    merged=copy.deepcopy(candidate)
    # Restore the existing numeric templates, not their rendered numeric text.
    # Sources and all complete rows stay in the native candidate, not the patch.
    editable=draft_state(candidate)['last_draft']
    merged.update(copy.deepcopy(editable))
    for name in ('computed_prose','investigation_review','verification'):
        merged.pop(name,None)
    for row in merged['row_prose']['rows']:
        if identity(row['key'])in changes:row['fields'].update(copy.deepcopy(changes[identity(row['key'])]))
    return merged


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
            'truncated':False,'kind':'unverified_recovery_feedback','feedback_digest':native_digest,
            'incomplete_dependencies':[{'authority':'unverified_review_feedback',
                'reason':'Retained unfinished feedback/draft is operational state, not source evidence for an answer.'}]})
    name=existing['result_view']
    reference={'result_view':name,'json_column':'feedback_json','feedback_digest':native_digest,
        'instruction':'Full original feedback and draft remain queryable with query_sql/json_extract. Draft values are unverified; bind any final answer to current executed results and complete verification.'}
    def project(value,path='$'):
        if isinstance(value,list):return[project(v,path+'['+str(i)+']')for i,v in enumerate(value)]
        if not isinstance(value,dict):return value
        out={}
        for key,item in value.items():
            child=path+'.'+json.dumps(key)
            if key=='unverified_retained_draft':
                draft=draft_state(item)
                # Finished server responses can carry complete verifier JSON
                # inside limitation strings. Preserve the prose and retain all
                # embedded structured data at its exact original native path.
                if isinstance(item,dict) and isinstance(item.get('limitations'),list):
                    draft['last_draft']['limitations']=[review_guidance(text,json_path=child+'."limitations"['+str(i)+']')
                        for i,text in enumerate(item['limitations'])]
                out[key]={'native_feedback':reference,**draft}
            else:out[key]=project(item,child)
        return out
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
        # Operational state must neither consume clinical result sequence
        # numbers nor populate the source-record quotation index.
        from .backstop_agent import define_result
        name='agere_review_feedback_'+ident
        existing={'columns':list(row),'rows':[row],'result_view':name,
            'truncated':False,'kind':'unverified_review_feedback','feedback_digest':ident,
            'incomplete_dependencies':[{'result_view':name,'authority':'unverified_review_feedback',
                'reason':'Reviewer feedback is queryable operational state, not source evidence for an answer.'}]}
        tools.result_sources[name]=define_result(tools.semantic,name,list(row),[list(row.values())],receipt=existing)
        tools.executed_results[name]=existing
    return {'result_view':existing['result_view'],'json_column':'feedback_json','feedback_digest':ident,
        'guidance_path':'$.feedback','packets_path':'$.support.packets',
        'guidance':review_guidance(feedback.get('feedback')),
        'instruction':'The complete exact reviewer receipt, repair prose and every claim/source binding are retained here. '
            'Use query_sql with json_extract/json_each to inspect the relevant packet, field_review and claim_feedback entries. '
            'Packet IDs are opaque labels; use only explicit patient_id bindings. '
            'This is unverified reviewer feedback, not patient evidence or a clinical truth label. '
            'Unknown feedback is an unresolved dependency, not a proven source fault. '
            'Retain unaffected native work and uncertainty; verify any final answer against the current source receipts.'}


def review_guidance(text,*,json_path='$.feedback'):
    """Preserve all repair prose; reference only embedded JSON data literals.

    References include exact character offsets into the retained original text.
    No prefix, word count, model summary or output ceiling is used. A replacement
    is used only when shorter than that JSON literal; small guidance is exact.
    """
    if not isinstance(text,str):return text
    decoder=json.JSONDecoder();parts=[];cursor=0;index=0
    while index<len(text):
        if text[index] not in '[{':index+=1;continue
        try:value,end=decoder.raw_decode(text,index)
        except ValueError:index+=1;continue
        marker="[Exact JSON in native_feedback: substr(json_extract(feedback_json,'"+json_path.replace("'","''")+"'),"+str(index+1)+','+str(end-index)+')]'
        if isinstance(value,(list,dict)) and len(marker)<end-index:
            parts.extend([text[cursor:index],marker]);cursor=end
        index=end
    return ''.join([*parts,text[cursor:]])


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
                # The row handle preserves values, but cannot reconstruct the
                # executed source/filter contract after conversation compaction.
                # Carry the executor's actual SQL, never infer it from a proposed
                # input. Portable receipts inline native rows for independent
                # replay; their declared_executed_query is the exact scoped SQL
                # that ran before that serialization, not a model summary.
                if call.get('name')=='query_sql' and isinstance(out.get('executed_query'),str):
                    declared=out.get('declared_executed_query')
                    entry['executed_query']=declared if isinstance(declared,str) else out['executed_query']
                    if isinstance(declared,str) and declared!=out['executed_query']:
                        entry['portable_executable_provenance']={
                            'query_digest':digest(out['executed_query']),
                            'characters':len(out['executed_query']),
                            'scope':'Complete portable SQL with embedded native rows remains in the execution receipt. '
                                'executed_query is the exact scoped statement that ran against read_views; no source acceptance is inferred.'}
                from .semantic_analyst import collection_manifest,complete_actor_reading,actor_result
                if complete_actor_reading(out):
                    # These are explicitly requested readings, not previews or
                    # model summaries. Retain them after later tools/feedback so
                    # interpretation does not require rereading the same rows.
                    entry['complete_reading']=actor_result(out)
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
    from .semantic_analyst import actor_result,complete_actor_reading
    def recent(call):
        out = call.get('result')
        if complete_actor_reading(out):
            return {'name':call.get('name'),'input':call.get('input'),
                    'result':{'result_view':out.get('result_view'),
                              'complete_reading_in':'executed_results; exact rows retained there once'}}
        if isinstance(out, dict) and out.get('loaded_documentation'):
            out = {k: v for k, v in out.items() if k not in ('rows', 'loaded_documentation')}
            out['documentation_sections'] = documentation_references(call['result'])
        return {'name': call.get('name'), 'input': call.get('input'), 'result': actor_result(out)}
    state={'schema':VERSION,'original_request':request,'executed_results':catalog(calls),
        'latest_results':[recent(c) for c in recent_results],
        'instruction':'This is the current deterministic execution state, not a new question or a clinical summary. '
            'All native receipts are retained. SQL result_views retain every computed row; a reading preview does not limit native membership. '
            'Each executed_query in executed_results is the exact successful SQL receipt for that view; inspect it for source, filter, period and aggregation scope without rerunning it. '
            'A truncated producer or incomplete_dependencies cannot support an accepted calculation or population scope. '
            'Read any earlier result by query_sql; read_all_rows=true supplies the complete selected rows for interpretation. '
            'Explicit complete_reading entries remain here across turns; do not query them again merely to see their values. '
            'Use compose_table to join already executed results without regenerating SQL. '
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
            if feedback_native is not None:
                state['current_review_feedback']['native_feedback']=feedback_native
                if 'feedback'in feedback:state['current_review_feedback']['feedback']=feedback_native['guidance']
            coverage=feedback.get('coverage')or{}
            if coverage:
                state['current_review_feedback']['coverage']={k:coverage[k]for k in ('label','reasons','reject')if k in coverage}
            support=feedback.get('support')or{}
            if support.get('field_review_states'):
                state['current_review_feedback']['field_review_states']=support['field_review_states']
            fields=('id','patient_id','label','reason') if feedback_native is not None else ('id','label','reason','claim_feedback')
            pending=[]
            for packet in support.get('packets',[]):
                if packet.get('label')=='supported'and packet.get('decided_by'):continue
                item={k:packet[k]for k in fields if k in packet}
                if not packet.get('decided_by'):
                    item['label']='unknown'
                    if packet.get('label')!='unknown':item['observed_label']=packet.get('label')
                if feedback_native is not None:item['claim_feedback_entries']=len(packet.get('claim_feedback')or[])
                if isinstance(packet.get('field_review'),dict):
                    item['field_review']={k:packet['field_review'][k]for k in (
                        'schema','authority','packet_digest','manifest_digest','expected_count','supported_count',
                        'unresolved_count','fault_count','all_fields_supported','remaining_obligation',
                        'complete_dependency_accepted','clinical_qualification')if k in packet['field_review']}
                pending.append(item)
            if pending:state['current_review_feedback']['unresolved_dependencies']=pending
            if support.get('failed_packet_ids'):
                state['current_review_feedback']['failed_dependency_ids']=support['failed_packet_ids']
        else:state['current_review_feedback']=feedback
    if candidate is not None:
        state.update(draft_state(candidate))
    return [{'role':'user','content':json.dumps(state,default=str,allow_nan=False)}]
