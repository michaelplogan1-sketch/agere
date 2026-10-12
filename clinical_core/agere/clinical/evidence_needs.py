"""Locate requested evidence without pretending to resolve its clinical meaning.

Exact received-record references can be located deterministically. Free-text or
abbreviated references remain unbound; no probabilistic string match is promoted.
"""
from pathlib import Path
import hashlib
import json
import re
from ...contracts import digest
from . import source_synthesis, record_context, question_context
from .. import narrative_sources

VERSION = 'source-evidence-needs/1'


def identity(): return digest([VERSION,hashlib.sha256(Path(__file__).read_bytes()).hexdigest()])


def locate(records, answer, selectors):
    if not isinstance(selectors,list) or len(selectors)>40: raise ValueError('Bounded explicit evidence selectors required')
    by_id={r['record_id']:r for r in records}; selected=set(answer.get('record_scope',{}).get('record_ids',[]))
    if not selected:
        selected={c['source']['record_id'] for f in answer['findings'] for c in f['citations']}
        selected.update(answer.get('dependencies',{}).get('record_passes',{}))
    locations=answer.get('dependencies',{}).get('record_passes',{})
    found=[]
    for s in selectors:
        if not isinstance(s,dict) or set(s)!={'record_id','field'} or any(not isinstance(s[k],str) for k in s) or not 1<=len(s['record_id'])<=180 or len(s['field'])>200:
            raise ValueError('Exact record_id and optional field required')
        record=by_id.get(s['record_id']); fields={p['field'] for p in narrative_sources.text_fields(record)} if record else set()
        fields.update(k for k in (record or {}) if k not in narrative_sources.FORBIDDEN)
        status=('not_received_for_patient' if record is None else 'field_not_received' if s['field'] and s['field'] not in fields
                else 'received_field_not_transported' if s['field'] and s['record_id'] in selected and s['field'] not in answer.get('selected_fields',{}).get(s['record_id'],[])
                else 'available_in_selected_scope' if s['record_id'] in selected else 'received_outside_selected_scope')
        found.append({**s,'status':status,'source_passes':locations.get(s['record_id'],[]),
            'record_hash':digest(record) if record else None,'question_resolved':False,
            'clinical_meaning':'unassessed','care_not_received':False})
    return found


def prepare(path, answer_id, selectors=None):
    answer=source_synthesis.inspect(path,answer_id)
    records,meta=record_context.received(path,answer['patient_id']); known={r['record_id'] for r in records}
    # Include every original scope record, even when uncited in the single-pass answer.
    row=source_synthesis.context(path,answer['context_id'])
    fields={rid:set() for rid in row['model_context']['sources']}
    for unit in row['model_context']['evidence']:
        if unit['kind']=='narrative':fields[unit['source_ref']].add(unit['field'])
        else:fields[unit['source_ref']].update(unit['reported_fields'])
    scoped={**answer,'record_scope':{'record_ids':list(row['model_context']['sources'])},'selected_fields':fields}
    needs=[]
    for step in answer['review_steps']:
        literal=sorted(k for k in known if re.search(r'(?<![\w-])'+re.escape(k)+r'(?![\w-]|…|\.\.\.)',step['question']))
        step_selectors=[{'record_id':k,'field':''} for k in literal]
        aliases=[]
        # Only the question's own unambiguous source-pass namespace is automatic.
        # References in reconciled/global questions may refer back to another pass.
        stages={f.split('_',1)[0] for f in step['finding_ids']}
        if len(stages)==1 and re.fullmatch(r'p[1-9][0-9]*',next(iter(stages))):
            number=int(next(iter(stages))[1:])
            packet=next((p for p in answer.get('passes',[]) if p['pass_number']==number),None)
            named_passes={int(v) for v in re.findall(r'\bpass\s+([1-9][0-9]*)\b',step['question'],re.I)}
            if packet and not (named_passes-{number}):
                by_alias={v['unit']:v['source_unit_id'] for v in packet['result']['unit_reviews']}
                by_unit={u['id']:u for u in row['model_context']['evidence']}
                for alias in sorted(set(re.findall(r'\bu[1-9][0-9]*\b',step['question']))):
                    unit=by_unit.get(by_alias.get(alias))
                    if unit:
                        selector={'record_id':unit['source_ref'],'field':unit['field']}
                        if selector not in step_selectors:step_selectors.append(selector)
                        aliases.append({'namespace':'source_pass','pass_number':number,'alias':alias,
                            'source_unit_id':unit['id'],**selector})
        loc=locate(records,scoped,step_selectors)
        needs.append({'id':step['id'],'question':step['question'],'finding_ids':step['finding_ids'],
            'binding_basis':'exact_source_reference_or_source_pass_alias' if loc else 'unbound_question',
            'status':'evidence_located_meaning_unresolved' if loc else 'interpretation_unresolved',
            'retrieval_status':'exact_reference_lookup' if loc else 'not_run',
            'selectors':loc,'unit_alias_bindings':aliases,'semantic_resolution':'pending','clinical_authority':False})
    explicit=locate(records,scoped,[] if selectors is None else selectors)
    result={'schema':VERSION,'synthetic':True,'answer_id':answer_id,'answer_hash':digest(answer),
        'patient_id':answer['patient_id'],'snapshot':meta['snapshot'],'patient_source_fingerprint':digest(records),
        'implementation':identity(),'needs':needs,'explicit_selectors':explicit,
        'summary':{'proposed_questions':len(needs),'questions_with_exact_located_records':sum(bool(n['selectors']) for n in needs),
            'unbound_questions':sum(not n['selectors'] for n in needs)},
        'scope':'Locating a source does not establish that it answers a question. Unbound language, retrieval not run, unavailable evidence and absent care are distinct.',
        'new_paid_api_calls':0,'clinical_execution_authorized':False}
    if digest(record_context.received(path,answer['patient_id'])[0])!=result['patient_source_fingerprint']:
        raise ValueError('Evidence changed while locating needs')
    result['id']=digest(result);target=Path(path)/'evidence-needs'/(result['id']+'.json');target.parent.mkdir(exist_ok=True)
    target.write_text(json.dumps(result,indent=2));return result


def inspect(path,key):
    if not isinstance(key,str) or not re.fullmatch('[a-f0-9]{64}',key):raise ValueError('Exact evidence-needs receipt required')
    r=json.loads((Path(path)/'evidence-needs'/(key+'.json')).read_text())
    if r['id']!=key or digest({k:v for k,v in r.items() if k!='id'})!=key or r['implementation']!=identity():raise ValueError('Evidence-needs identity changed')
    if digest(source_synthesis.inspect(path,r['answer_id']))!=r['answer_hash'] or digest(record_context.received(path,r['patient_id'])[0])!=r['patient_source_fingerprint']:
        raise ValueError('Evidence-needs sources changed')
    return r


def context_for_need(path,receipt_id,need_id):
    r=inspect(path,receipt_id)
    if need_id=='explicit':question=source_synthesis.inspect(path,r['answer_id'])['question'];selectors=r['explicit_selectors']
    else:
        n=next((n for n in r['needs'] if n['id']==need_id),None)
        if n is None:raise ValueError('Unknown evidence need')
        question=n['question'];selectors=n['selectors']
    if not selectors or any(s['status'] in ('not_received_for_patient','field_not_received','received_field_not_transported') for s in selectors):
        raise ValueError('Exact available source references required; no inferred binding or silent selector removal')
    records,meta=record_context.received(path,r['patient_id'])
    selected=question_context.select_sources(records,sorted({s['record_id'] for s in selectors}),meta['snapshot']['as_of'])
    row=record_context.create(path,r['patient_id'],question,selected['connected_record_ids'])
    result={'schema':VERSION,'need_receipt_id':receipt_id,'need_id':need_id,'answer_id':r['answer_id'],'selection':selected,
        'context_id':row['id'],'record_scope':row['record_scope'],'budget':row['budget'],
        'dispatch':row['dispatch'],'meaning_resolved':False,'new_paid_api_calls':0}
    result['id']=digest(result)
    target=Path(path)/'evidence-followups'/(result['id']+'.json');target.parent.mkdir(exist_ok=True)
    target.write_text(json.dumps(result,indent=2));return result
