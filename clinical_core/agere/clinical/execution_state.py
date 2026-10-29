"""Deterministic investigator state: native results remain complete and queryable.

This changes the actor's prompt representation, not its evidence or scope. No
model-generated summary, source truncation or learned clinical assertion is used.
"""
import json
from ...contracts import digest

VERSION = 'execution-state/2'


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
                # Small scalar/aggregate results are useful exact observations;
                # patient/free-text tables stay native and queryable by view.
                if not any(isinstance(r,dict)and'patient_id'in r for r in rows) and len(json.dumps(rows,default=str))<2500:
                    entry['computed_rows']=rows
                for key in ('coverage','method','record_ids_filter','accounting','assembly'):
                    if key in out:
                        if key=='coverage'and isinstance(out[key],list):
                            entry['coverage']={'patients':len(out[key]),'received_documents':sum(r.get('received_documents',0)for r in out[key]),
                                'unique_whole_documents':sum(r.get('unique_whole_documents',0)for r in out[key])}
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


def messages(request,calls,*,recent_results=(),feedback=None,candidate=None):
    from .semantic_analyst import actor_result
    state={'schema':VERSION,'original_request':request,'executed_results':catalog(calls),
        'latest_results':[{'name':c.get('name'),'input':c.get('input'),'result':actor_result(c.get('result'))}for c in recent_results],
        'instruction':'This is the current deterministic execution state, not a new question or a clinical summary. '
            'All native receipts are retained. Every result_view contains its complete observed rows unless marked truncated. '
            'Read any earlier result by query_sql; use compose_table to join already executed results without regenerating SQL. '
            'A missing preview is not missing evidence. Preserve original scope and all requested outputs. '
            'Avoid repeating successful work; correct unresolved errors and current review feedback. '
            'Return table_from_tool naming the complete composed result; never transcribe patient tables.'}
    if feedback is not None:
        # Operational reviewer receipts remain native; preserve the exact current
        # repair instructions without resending all model probabilities/costs.
        if isinstance(feedback,dict):
            state['current_review_feedback']={k:feedback[k]for k in ('status','feedback','reason','repair_kind')if k in feedback}
            state['current_review_native_digest']=digest(feedback)
            coverage=feedback.get('coverage')or{}
            if coverage:
                state['current_review_feedback']['coverage']={k:coverage[k]for k in ('label','reasons','reject')if k in coverage}
            support=feedback.get('support')or{}
            pending=[{k:r[k]for k in ('id','label','reason')if k in r}for r in support.get('packets',[])if r.get('label')!='supported']
            if pending:state['current_review_feedback']['unresolved_dependencies']=pending
            if support.get('failed_packet_ids'):
                state['current_review_feedback']['failed_dependency_ids']=support['failed_packet_ids']
        else:state['current_review_feedback']=feedback
    if candidate is not None:
        state['last_draft']={k:v for k,v in candidate.items()if k not in ('table','sources','evidence_text','system_one_judgments','table_binding','investigation_review','verification','limitations')}if isinstance(candidate,dict)else candidate
        state['last_draft_digest']=digest(candidate)
        if isinstance(candidate,dict)and isinstance(candidate.get('table_binding'),dict):
            binding=candidate['table_binding']
            state['last_draft_table_binding']={k:v for k,v in binding.items()if k!='query'}
            if 'query'in binding:state['last_draft_table_binding']['native_query_digest']=digest(binding['query'])
        if isinstance(candidate,dict):
            # Keep exact uncertainty/limitations; only prior reviewer operational
            # receipts and expanded executable provenance are native references.
            for key in ('verification','limitations'):
                if key in candidate:state['last_draft'][key]=candidate[key]
        if isinstance(candidate,dict)and isinstance(candidate.get('table'),list):
            state['last_draft_rows']=len(candidate['table'])
            state['last_draft_table_instruction']='The last draft table is preserved natively. Reconstruct it only from the matching executed result_view; do not infer omitted values.'
    return [{'role':'user','content':json.dumps(state,default=str,allow_nan=False)}]
