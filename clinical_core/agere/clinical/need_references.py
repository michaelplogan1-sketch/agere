"""Exact review-question references, with immutable, explicitly selected follow-ups.

Unit identity is source location, never endorsement of a question's premise.
Historical evidence-needs receipts and answer attachment engines are unchanged.
"""
from pathlib import Path
import hashlib,json,re
from ...contracts import digest
from . import need_candidates as candidates,stable_reconciliation as stable
from . import source_synthesis,record_context,question_context,evidence_answers

VERSION='review-question-references/1'
ALIASES=re.compile(r'(?<![\w:])(?:p[0-9]+:u[0-9]+|u[0-9]+|s[0-9]+)(?:…|\.\.\.)?(?![\w:]|…|\.\.\.)',re.I)
RECORDS=re.compile(r'(?<![\w-])WH[A-Z]*-R-[A-Za-z0-9-]+(?:…|\.\.\.)?(?![\w-]|…|\.\.\.)')

def identity():
 return digest([VERSION,hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),candidates.identity(),stable.identity()])

def exact_pattern(value):return r'(?<![\w-])'+re.escape(value)+r'(?![\w-]|…|\.\.\.)'

def resolve(need,answer,context,records):
 """Only saved pass/unit maps and literal complete record IDs can locate sources."""
 units={u['id']:u for u in context['model_context']['evidence']}
 manifest=stable.manifest(answer,context) if answer.get('passes') else {}
 if 'reference_manifest' in answer and manifest!=answer['reference_manifest']:raise ValueError('Saved reference manifest disagrees with source passes')
 by_id={r['record_id']:r for r in records}
 if len(by_id)!=len(records) or any(r['patient_id']!=answer['patient_id'] for r in records):raise ValueError('Distinct same-patient records required')
 text=need['question'];refs={};unresolved=[]
 stages={v.split('_',1)[0] for v in need['finding_ids']}
 local=int(next(iter(stages))[1:]) if len(stages)==1 and re.fullmatch(r'p[1-9][0-9]*',next(iter(stages))) else None
 named={int(v) for v in re.findall(r'\bpass\s+([1-9][0-9]*)\b',text,re.I)}
 qualified={int(v) for v in re.findall(r'\bp([1-9][0-9]*):u[0-9]+',text,re.I)}
 for match in ALIASES.finditer(text):
  token=match[0];name=token.lower();reason=None;basis='pass_qualified_unit'
  if token.endswith(('…','...')):reason='abbreviated_reference'
  elif name.startswith('s'):reason='source_alias_namespace_unresolved'
  elif name.startswith('u'):
   if local is None or (named|qualified)-{local}:reason='unit_alias_namespace_unresolved'
   else:name='p'+str(local)+':'+name;basis='own_source_pass_unit'
  item=manifest.get(name) if reason is None else None
  if reason is None and item is None:reason='unit_not_in_saved_manifest'
  if reason:
   unresolved.append({'mention':token,'start':match.start(),'end':match.end(),'reason':reason});continue
  unit=units[item['source_unit_id']];record=by_id.get(item['record_id'])
  if record is None:raise ValueError('Referenced source unit is no longer received for this patient')
  ref=refs.setdefault(name,{**item,'record_hash':digest(record),'unit_hash':digest(unit),'source_unit':unit,'basis':basis,'mentions':[]})
  ref['mentions'].append({'text':token,'start':match.start(),'end':match.end()})
 for rid,record in sorted(by_id.items()):
  mentions=[{'text':m[0],'start':m.start(),'end':m.end()} for m in re.finditer(exact_pattern(rid),text)]
  if mentions:refs['record:'+rid]={'name':'record:'+rid,'record_id':rid,'field':'','kind':'record','source_unit_id':None,'record_hash':digest(record),'basis':'literal_complete_record_id','mentions':mentions}
 for match in RECORDS.finditer(text):
  if match[0] not in by_id:unresolved.append({'mention':match[0],'start':match.start(),'end':match.end(),'reason':'abbreviated_reference' if match[0].endswith(('…','...')) else 'record_not_received_for_patient'})
 if len(refs)>40:raise ValueError('Reference lookup exceeds the explicit forty-reference bound')
 return {'references':list(refs.values()),'unresolved_mentions':sorted(unresolved,key=lambda r:r['start'])}

def prepare(path,request):
 evidence_answers.target(request)
 _,need,records,binding=candidates.input_state(path,**request)
 answer=source_synthesis.inspect(path,binding['answer_id']);context=source_synthesis.context(path,answer['context_id'])
 located=resolve(need,answer,context,records)
 result={'schema':VERSION,'implementation':identity(),'binding':binding,'context_hash':digest(context),
  'original_question':need['question'],**located,'status':'exact_references_located' if located['references'] else 'no_exact_references',
  'question_resolved':False,'question_premise_endorsed':False,'clinical_execution_authorized':False,'new_paid_api_calls':0,
  'scope':'Exact source location only. Abbreviations, unknown aliases and clinical meaning remain unresolved. Select references explicitly before creating a bounded follow-up.'}
 if candidates.input_state(path,**request)[3]!=binding:raise ValueError('Question sources changed during reference lookup')
 result['id']=digest(result);candidates.write(candidates.location(path,result['id'],'need-references'),result);return result

def inspect(path,key):
 r=json.loads(candidates.location(path,key,'need-references').read_text())
 if r['id']!=key or digest({k:v for k,v in r.items() if k!='id'})!=key or r['implementation']!=identity():raise ValueError('Question reference identity changed')
 if candidates.input_state(path,r['binding']['receipt_id'],r['binding']['need_id'])[3]!=r['binding']:raise ValueError('Question reference sources changed')
 answer=source_synthesis.inspect(path,r['binding']['answer_id'])
 if digest(source_synthesis.context(path,answer['context_id']))!=r['context_hash']:raise ValueError('Question reference context changed')
 return r

def select(path,request):
 if not isinstance(request,dict) or set(request)!={'reference_id','reference_names'}:raise ValueError('Exact reference receipt and selected reference names required')
 r=inspect(path,request['reference_id']);names=request['reference_names'];by_name={v['name']:v for v in r['references']}
 if not isinstance(names,list) or not 1<=len(names)<=40 or any(not isinstance(n,str) for n in names) or len(set(names))!=len(names) or set(names)-set(by_name):raise ValueError('Distinct available exact references required')
 b=r['binding'];_,_,records,_=candidates.input_state(path,b['receipt_id'],b['need_id'])
 selected=question_context.select_sources(records,sorted({by_name[n]['record_id'] for n in names}),b['snapshot']['as_of'])
 row=record_context.create(path,b['patient_id'],r['original_question'],selected['connected_record_ids'])
 if inspect(path,r['id'])!=r:raise ValueError('Question references changed during selection')
 result={'schema':VERSION,'implementation':identity(),'reference_id':r['id'],'reference_hash':digest(r),'binding':b,
  'original_question':r['original_question'],'selected_reference_names':sorted(names),'unselected_reference_names':sorted(set(by_name)-set(names)),
  'unresolved_mentions':r['unresolved_mentions'],'selection':selected,'context_id':row['id'],'context_hash':digest(row),
  'record_scope':row['record_scope'],'budget':row['budget'],'dispatch':row['dispatch'],'omissions':row['omissions'],
  'selection_scope':'operator_selected_exact_references_and_typed_links','selection_sufficiency':'unassessed','meaning_resolved':False,
  'question_premise_endorsed':False,'clinical_execution_authorized':False,'new_paid_api_calls':0}
 result['id']=digest(result);candidates.write(candidates.location(path,result['id'],'reference-contexts'),result);return result

def inspect_selection(path,key):
 r=json.loads(candidates.location(path,key,'reference-contexts').read_text())
 if r['id']!=key or digest({k:v for k,v in r.items() if k!='id'})!=key or r['implementation']!=identity():raise ValueError('Reference selection identity changed')
 if digest(inspect(path,r['reference_id']))!=r['reference_hash']:raise ValueError('Reference selection sources changed')
 if digest(record_context.inspect(path,r['context_id']))!=r['context_hash']:raise ValueError('Reference selection context changed')
 return r

def answer_binding(path,request):
 if not isinstance(request,dict) or set(request)!={'receipt_id','need_id','selection_kind','selection_id','answer_id'} or request['selection_kind']!='exact_references':raise ValueError('Exact reference selection and saved answer required')
 evidence_answers.target({k:request[k] for k in ('receipt_id','need_id')})
 selection=inspect_selection(path,request['selection_id']);b=selection['binding']
 if any(request[k]!=b[k] for k in ('receipt_id','need_id')):raise ValueError('Reference selection belongs to a different question')
 answer=source_synthesis.inspect(path,request['answer_id'])
 if answer.get('status')!='complete' or (len(answer.get('passes',[]))>1 and not answer.get('reconciliation',{}).get('groups')):raise ValueError('Complete saved interpretation required')
 if answer['context_id']!=selection['context_id'] or answer['patient_id']!=b['patient_id']:raise ValueError('Saved answer must use this exact context and patient')
 return selection,answer,{'selection_hash':digest(selection),'answer_hash':digest(answer),'context_id':selection['context_id']}

def attach(path,request):
 selection,answer,deps=answer_binding(path,request)
 needs=candidates.needs.prepare(path,answer['id'],[])
 r={'schema':VERSION,'implementation':identity(),**request,'dependencies':deps,'original_question':selection['original_question'],
  'followup_needs_id':needs['id'],'summary':{'findings':len(answer['findings']),'exact_citations':sum(len(f['citations']) for f in answer['findings']),'review_questions':len(answer['review_steps']),**needs['summary']},
  'status':'machine_interpretation_attached_for_review','requirement_resolved':False,'human_review_recorded':False,'clinical_execution_authorized':False,'new_paid_api_calls':0}
 if answer_binding(path,request)[2]!=deps:raise ValueError('Reference answer dependencies changed during attachment')
 r['id']=digest(r);candidates.write(candidates.location(path,r['id'],'reference-answers'),r);return r

def answers(path,request):
 """Additive listing: old attachment engine and receipts remain unchanged."""
 result=evidence_answers.inspect(path,request)
 for p in sorted((Path(path)/'reference-answers').glob('*.json')):
  r=json.loads(p.read_text())
  if any(r.get(k)!=request[k] for k in ('receipt_id','need_id')):continue
  errors=[]
  try:
   if r['id']!=p.stem or digest({k:v for k,v in r.items() if k!='id'})!=r['id'] or r['implementation']!=identity():raise ValueError('Reference answer identity changed')
   if answer_binding(path,{k:r[k] for k in ('receipt_id','need_id','selection_kind','selection_id','answer_id')})[2]!=r['dependencies']:raise ValueError('Reference answer dependencies changed')
   candidates.needs.inspect(path,r['followup_needs_id'])
  except (ValueError,KeyError,FileNotFoundError) as exc:errors.append(str(exc))
  result['answers'].append({**r,'current':not errors,'stale_reasons':errors})
 return result
