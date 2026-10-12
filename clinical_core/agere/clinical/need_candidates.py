"""Bounded local discovery for unresolved questions, separate from need binding.

Ranked passages are candidates. Explicitly selected received records and their
typed-reference closure can enter the existing interpretation path. No discovery
or interpretation here resolves a documentary or clinical requirement.
"""
from collections import Counter
from pathlib import Path
import hashlib
import json
import re
from ...contracts import digest
from . import evidence_needs as needs, source_synthesis, record_context, question_context
from . import warehouse_retrieval as retrieval

VERSION='need-candidate-acquisition/1'


def identity():
    return digest([VERSION,hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
        hashlib.sha256(Path(question_context.__file__).read_bytes()).hexdigest(),question_context.native.identity()])


def location(path,key,kind='need-candidates'):
    if not isinstance(key,str) or not re.fullmatch('[a-f0-9]{64}',key):raise ValueError('Exact candidate receipt ID required')
    return Path(path)/kind/(key+'.json')


def write(target,result):
    target.parent.mkdir(exist_ok=True)
    if target.exists():
        if json.loads(target.read_text())!=result:raise ValueError('Candidate receipt identity collision')
    else:
        temporary=target.with_suffix('.tmp');temporary.write_text(json.dumps(result,indent=2,allow_nan=False));temporary.replace(target)


def input_state(path,receipt_id,need_id):
    receipt=needs.inspect(path,receipt_id)
    need=next((n for n in receipt['needs'] if n['id']==need_id),None)
    if need is None:raise ValueError('Select a proposed question belonging to this exact evidence-needs receipt')
    answer=source_synthesis.inspect(path,receipt['answer_id'])
    context=source_synthesis.context(path,answer['context_id'])
    records,meta=record_context.received(path,receipt['patient_id'])
    if digest(records)!=receipt['patient_source_fingerprint']:raise ValueError('Patient evidence changed during candidate preparation')
    binding={'receipt_id':receipt_id,'receipt_hash':digest(receipt),'need_id':need_id,'answer_id':answer['id'],
        'answer_hash':digest(answer),'patient_id':receipt['patient_id'],'patient_source_fingerprint':digest(records),
        'snapshot':meta['snapshot'],'original_record_ids':sorted(context['model_context']['sources'])}
    return receipt,need,records,binding


def search_contract(retriever):
    return {'code':hashlib.sha256(Path(retrieval.__file__).read_bytes()).hexdigest(),
        'source_digest':retriever.meta['source_digest'],'encoder':retriever.encoder.identity,
        'reranker':retriever.reranker.identity if retriever.reranker else None,
        'annotation':getattr(retriever,'annotation_identity',None)}


def prepare(path,request,retriever):
    if not isinstance(request,dict) or set(request)!={'receipt_id','need_id','query'}:raise ValueError('Exact need receipt, question and discovery query required')
    query=request['query']
    if not isinstance(query,str) or not 1<=len(query.strip())<=1500:raise ValueError('Bounded discovery query required')
    query=query.strip();_,need,records,binding=input_state(path,request['receipt_id'],request['need_id'])
    contract=search_contract(retriever)
    key=digest([identity(),binding,query,contract]);target=location(path,key)
    if target.exists():return {**inspect(path,key),'cache_hit':True}
    found=retriever.search(query,patient=binding['patient_id'],limit=8)
    if len(found['evidence'])>8:raise ValueError('Retrieval exceeded declared passage bound')
    by_id={r['record_id']:r for r in records};candidates={}
    original=set(binding['original_record_ids'])
    for rank,hit in enumerate(found['evidence'],1):
        source=hit['source'];record=by_id.get(source['record_id'])
        if record is None or source['patient_id']!=binding['patient_id'] or source['record_hash']!=digest(record):
            raise ValueError('Candidate source changed or belongs to another patient')
        start,end=source['start'],source['end']
        if type(start) is not int or type(end) is not int or not 0<=start<end or not any(
                p['field']==source['field'] and end<=len(p['text']) and p['text'][start:end]==hit['text'] for p in retrieval.representations(record)):
            raise ValueError('Candidate passage does not match its received source span')
        candidate=candidates.setdefault(record['record_id'],{'record_id':record['record_id'],'source_table':record['source_table'],
            'record_hash':digest(record),'available_date':record.get('available_date'),
            'original_scope_status':'available_in_original_scope' if record['record_id'] in original else 'received_outside_original_scope',
            'passages':[],'semantic_relevance':'unassessed','binds_need':False})
        candidate['passages'].append({'rank':rank,'text':hit['text'],'source':source})
    candidate_ids=set(candidates)
    result={'schema':VERSION,'id':key,'implementation':identity(),'binding':binding,'query':query,'original_question':need['question'],
        'search_contract':contract,'candidates':list(candidates.values()),'context_id':None,
        'summary':{'ranked_passages':len(found['evidence']),'candidate_records':len(candidates),
            'outside_original_scope':len(candidate_ids-original),'already_in_original_scope':len(candidate_ids&original),
            'received_records_not_returned':len(records)-len(candidates)},
        'retrieval':{k:found[k] for k in ('method','candidate_passages','cache_hit','elapsed_seconds')},
        'unsearched_or_unreturned_evidence':'No completeness guarantee; an unreturned record is not evidence of absent care.',
        'unreturned_categories':dict(sorted(Counter(r['source_table'] for r in records if r['record_id'] not in candidate_ids).items())),
        'status':'candidates_for_selection' if candidates else 'no_candidates_returned',
        'need_binding_changed':False,'semantic_resolution':'pending','retrieval_recall_assessed':False,
        'clinical_execution_authorized':False,'new_paid_api_calls':0,
        'scope':'Locally ranked candidate passages for this patient. Choose explicit records before preparing interpretation. Query refinement does not change the original requirement.'}
    if input_state(path,binding['receipt_id'],binding['need_id'])[3]!=binding:raise ValueError('Evidence changed during candidate search')
    result['result_digest']=digest(result);write(target,result);return {**result,'cache_hit':False}


def inspect(path,key):
    result=json.loads(location(path,key).read_text())
    if result['id']!=key or result['implementation']!=identity() or result['result_digest']!=digest({k:v for k,v in result.items() if k!='result_digest'}):
        raise ValueError('Candidate receipt identity or integrity changed')
    current=input_state(path,result['binding']['receipt_id'],result['binding']['need_id'])[3]
    if current!=result['binding']:raise ValueError('Candidate input dependencies changed')
    if digest([identity(),current,result['query'],result['search_contract']])!=key:raise ValueError('Candidate request identity changed')
    return result


def select(path,request):
    if not isinstance(request,dict) or set(request)!={'candidate_id','record_ids'}:raise ValueError('Exact candidate receipt and selected records required')
    result=inspect(path,request['candidate_id']);ids=request['record_ids']
    if not isinstance(ids,list) or not ids or any(not isinstance(r,str) for r in ids) or len(set(ids))!=len(ids):raise ValueError('Distinct explicit candidate records required')
    if set(ids)-{r['record_id'] for r in result['candidates']}:raise ValueError('Selected record is not in this candidate receipt')
    binding=result['binding'];_,_,records,current=input_state(path,binding['receipt_id'],binding['need_id'])
    selection=question_context.select_sources(records,ids,binding['snapshot']['as_of'])
    context=record_context.create(path,binding['patient_id'],result['query'],selection['connected_record_ids'])
    if input_state(path,binding['receipt_id'],binding['need_id'])[3]!=current:raise ValueError('Sources changed during candidate selection')
    report={'schema':VERSION,'implementation':identity(),'candidate_id':result['id'],'candidate_hash':digest(result),
        'receipt_id':binding['receipt_id'],'need_id':binding['need_id'],'answer_id':binding['answer_id'],
        'patient_id':binding['patient_id'],'original_question':result['original_question'],'query':result['query'],
        'selection':selection,'context_id':context['id'],'record_scope':context['record_scope'],'budget':context['budget'],
        'dispatch':context['dispatch'],'omissions':context['omissions'],'snapshot':binding['snapshot'],
        'need_binding_changed':False,'meaning_resolved':False,'selection_sufficiency':'unassessed',
        'clinical_execution_authorized':False,'new_paid_api_calls':0}
    report['id']=digest(report);write(location(path,report['id'],'candidate-contexts'),report);return report


def inspect_selection(path,key):
    result=json.loads(location(path,key,'candidate-contexts').read_text())
    if result['id']!=key or digest({k:v for k,v in result.items() if k!='id'})!=key or result['implementation']!=identity():raise ValueError('Candidate selection identity changed')
    if digest(inspect(path,result['candidate_id']))!=result['candidate_hash']:raise ValueError('Candidate selection dependencies changed')
    context=record_context.inspect(path,result['context_id'])
    if context['record_scope']!=result['record_scope'] or context['patient_id']!=result['patient_id']:raise ValueError('Candidate context changed')
    return result
