"""Received medication evidence alongside exact documentary questions.

Additional source coverage never substitutes for a contract's specified account,
resolves a question, or establishes a current regimen. No provider calls.
"""
from collections import Counter
from pathlib import Path
import hashlib,json,sqlite3
from ...contracts import digest
from . import care_capabilities as C,evidence_portfolio as P,evidence_acquisition as A
from . import evidence_requirements as Q,record_context as RC,warehouse_interpretation as W
from . import medication_accounts as M,context_builder

VERSION='medication-source-review/1'
KIND='medication_source_review'
MEDICATION_STREAMS=tuple(sorted({s for s,v in W.STATEMENTS.items() if v[0]=='medication'}|set(M.STREAMS)|{'medication_reconciliation'}))
NARRATIVE_STREAMS=('notes','telephone_messages','imported_documents','chart_summaries','care_intake','discharge_plans','encounter_text_fragments')
BOUNDARY={
 'medication_orders':'Intended prescription/order; not administration or ingestion.',
 'medication_lists':'Historical list entry; event time and present use may be unknown.',
 'medication_reconciliation':'Dated reconciliation report. Array items are separately displayed; not a verified current regimen.',
 'pharmacy_dispenses':'Recorded dispensing; not confirmed ingestion or current directions.',
 'pharmacy_claims':'Claim/dispensing report; payment is not medication use.',
 'pharmacy_claim_transactions':'Adjudication event; not receipt or ingestion.',
 'pharmacy_history':'Pharmacy history account. Explicit original-source links are not independent corroboration.',
 'medication_administrations':'Administration account for its particular order and event; not longitudinal adherence.',
 'medication_followup_reports':'Dated use report for its named prescription; not observed ingestion.',
}
LIMITS=['Received coverage is incomplete outside this warehouse. No absent-care, nonadherence or current-regimen inference.',
 'Literal names are displayed separately. No ingredient, strength, formulation or therapeutic equivalence is inferred.',
 'The original contract, concept, source categories and result remain unchanged. Other medication evidence is not a substitute.',
 'Narrative records are available for explicit inspection; their clinical meaning and relevance are unassessed here.',
 'No clinical review, treatment selection, titration authority, patient consent, benefit eligibility or external action is established.']
REFERENCE={'title':'AHRQ MATCH medication reconciliation process','url':'https://www.ahrq.gov/patient-safety/settings/hospital/match/appendix/app-7.html',
 'checked_on':'2026-10-11','review_status':'machine_curated_unreviewed','scope':'Process reference: obtain and confirm medication history, compare with orders and have discrepancies reviewed. Not patient evidence or a titration protocol; currency at the future synthetic cutoff is unverified.'}


def identity():return digest([VERSION,hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),P.identity(),RC.identity(),W.identity()])

def source(path,request):
 C.exact(request,'portfolio_id patient_id');p=P.inspect(path,request['portfolio_id']);a=A.load(path,p['proposal_id'])
 patient=C.text(request['patient_id'],'patient ID',150)
 refs=[r for r in a['references'] if r['patient_id']==patient]
 if not refs:raise ValueError('Patient is not in this exact documentary portfolio')
 questions=[Q.inspect(path,r['receipt_id']) for r in refs]
 if not any(any(b['source'].get('relation','').startswith('medication') or b['source'].get('relation')=='reported_use' or any('medication' in s or 'pharmacy' in s for s in b['source'].get('streams',[])) for b in q['contract']['bindings'].values()) for q in questions):
  raise ValueError('Portfolio has no medication-source question for this patient')
 records,meta=RC.received(path,patient)
 binding={'portfolio_hash':digest(p),'proposal_id':a['id'],'proposal_hash':digest(a),'question_hashes':{q['id']:digest(q) for q in questions},
  'patient_id':patient,'received_fingerprint':digest(records),'source_digest':meta['source_digest'],'snapshot':meta['snapshot'],'implementation':identity()}
 return questions,records,binding

def account(r,path,fields,boundary):
 fields=context_builder.clean(fields)
 def leaves(value,at):
  if isinstance(value,dict):return [p for k,v in value.items() for p in leaves(v,[*at,k])]
  if isinstance(value,list):return [p for i,v in enumerate(value) for p in leaves(v,[*at,i])]
  return [at]
 return {'record_id':r['record_id'],'record_hash':digest(r),'patient_id':r['patient_id'],'source_table':r['source_table'],'field_path':path,
  'fields':fields,'source_date':r.get('event_date'),'available_date':r.get('available_date'),'source_system':r.get('source_system'),
  'evidence':[M.ref(r,p) for p in leaves(fields,path)],'meaning':boundary,'current_regimen_established':False}

def project(patient,records,asof):
 if len({r['record_id'] for r in records})!=len(records):raise ValueError('Duplicate received record ID')
 if any(r['patient_id']!=patient or r.get('synthetic') is not True or not W.native(r) or not W.visible(r,asof) for r in records):raise ValueError('Visible same-patient native synthetic evidence required')
 index={r['record_id']:r for r in records};meds=[r for r in records if r['source_table'] in MEDICATION_STREAMS]
 if len(meds)>400:raise ValueError('Medication evidence exceeds complete review bound; no records were dropped')
 rows=[];issues=[];links=[]
 for r in sorted(meds,key=lambda r:(r.get('event_date') or '',r['record_id'])):
  clean=context_builder.clean(r);fields={k:clean[k] for k in r['_warehouse_fields'] if k in clean}
  boundary=BOUNDARY.get(r['source_table'],'Dated documentary medication account; not verified current care.')
  rows.append(account(r,[],fields,boundary))
  if r['source_table']=='medication_reconciliation':
   entries=r.get('medications')
   if not isinstance(entries,list):issues.append({'record_id':r['record_id'],'field':'medications','reason':'medication_array_missing_or_invalid'})
   else:
    for i,item in enumerate(entries):
     if not isinstance(item,dict):issues.append({'record_id':r['record_id'],'field_path':['medications',i],'reason':'medication_item_invalid'});continue
     # Preserve exact literal dose/frequency/status and parent respondent separately.
     rows.append(account(r,['medications',i],item,'Reported medication item at this source date; actual ingestion and present directions remain unverified.'))
  for field,streams in W.LINK_FIELDS.items():
   if field not in r or r[field] is None:continue
   refs=r[field] if isinstance(r[field],list) else [r[field]]
   for pos,rid in enumerate(refs):
    target=index.get(rid) if isinstance(rid,str) else None
    reason='referenced_source_not_received' if target is None else 'wrong_linked_source_type' if target['source_table'] not in streams else None
    fp=[field,pos] if isinstance(r[field],list) else [field]
    item={'source':M.ref(r,fp),'target_record_id':rid,'basis':'explicit_source_reference','independent_corroboration':False}
    if reason:issues.append({**item,'reason':reason})
    else:links.append({**item,'target':M.ref(target,['record_id'])})
 narratives=[account(r,[],{k:context_builder.clean(r)[k] for k in r['_warehouse_fields'] if k in context_builder.clean(r)},'Received narrative/context account; medication relevance and meaning unassessed.') for r in records if r['source_table'] in NARRATIVE_STREAMS]
 if len(narratives)>200 or len(json.dumps([rows,narratives],ensure_ascii=False).encode())>1500000:raise ValueError('Complete local review exceeds display bound; narrow through source inspection instead')
 return {'medication_accounts':rows,'narrative_accounts':narratives,'source_counts':dict(Counter(r['source_table'] for r in meds)),
  'medication_records':len(meds),'nested_medication_items':sum(bool(r['field_path']) for r in rows),'narrative_records':len(narratives),
  'received_records_outside_review':len(records)-len(meds)-len(narratives),'source_links':links,'source_link_issues':issues}

def coverage(questions,records):
 counts=Counter(r['source_table'] for r in records);out=[]
 for q in questions:
  bindings=[]
  for name,b in q['contract']['bindings'].items():
   s=b['source'];relation=s.get('relation')
   if s.get('kind')=='native_account':
    streams=sorted(M.STREAMS if relation==M.RELATION else [t for t,v in W.STATEMENTS.items() if v[1]==relation])
    # Presence is only source coverage. Existing evaluated account result remains authoritative.
    state='requested_source_categories_received' if any(counts[t] for t in streams) else 'requested_source_categories_not_received'
    if relation==M.RELATION and not (counts['reported_medication_entries'] or counts['medication_review_decisions']):
     state='requested_item_account_roots_not_received'
   else:streams=s['streams'];state='interpretation_contract_requires_its_exact_concept_and_relation'
   evaluated=q.get('bindings',{}).get(name,{})
   bindings.append({'binding':name,'label':b['label'],'source_contract':s,'category_counts':{s:counts[s] for s in streams},'coverage_status':state,
    'original_result':evaluated.get('result'),'original_account_coverage':evaluated.get('state',{}).get('source_coverage')})
  out.append({'receipt_id':q['id'],'title':q['contract']['title'],'purpose':q['contract']['purpose'],'status':q['status'],
   'binding_coverage':bindings,'question_resolved_by_this_review':False})
 return out

def snapshot(path,request):
 questions,records,binding=source(path,request);r=project(request['patient_id'],records,binding['snapshot']['as_of'])
 extra=[{'record_id':x['record_id'],'source_table':x['source_table'],'reason':'Received category is outside the registered native statement mapping; literal items are exposed by this review only.'} for x in records if x['source_table'] in MEDICATION_STREAMS and x['source_table'] not in W.STATEMENTS]
 row={'schema':VERSION,'request':request,'patient_id':request['patient_id'],'as_of':binding['snapshot']['as_of'],'binding':binding,
  **r,'questions':coverage(questions,records),'additional_unmapped_native_records':extra,'reference':REFERENCE,'limits':LIMITS,
  'review_questions':['Confirm the complete current prescription, nonprescription and supplement history, including actual use and directions.',
   'Review each dated report, dispensing account and order separately; reconcile discrepancies with an appropriately authorized clinician.',
   'Confirm patient goals, service-specific consent, clinical appropriateness, safety information and any prescribing/titration protocol before action.',
   'Verify exact plan benefits, network/participation, service access and buyer responsibilities.'],
  'new_paid_api_calls':0,'clinical_execution_authorized':False,'original_questions_changed':False,'status':'received_medication_evidence_for_review'}
 row['id']=digest(row);return row

def database(path):
 db=sqlite3.connect(Path(path)/'medication-source-review.sqlite3',timeout=30)
 db.execute('CREATE TABLE IF NOT EXISTS reviews(id TEXT PRIMARY KEY,portfolio TEXT,patient TEXT,payload TEXT)')
 for op in ('UPDATE','DELETE'):db.execute(f"CREATE TRIGGER IF NOT EXISTS immutable_medication_review_{op} BEFORE {op} ON reviews BEGIN SELECT RAISE(ABORT,'Medication review history is immutable'); END")
 return db

def prepare(path,request):
 row=snapshot(path,request);db=database(path)
 try:
  with db:
   if source(path,request)[2]!=row['binding']:raise C.work.WorkConflict('Medication review sources changed during preparation')
   db.execute('INSERT OR IGNORE INTO reviews VALUES (?,?,?,?)',(row['id'],request['portfolio_id'],request['patient_id'],json.dumps(row,allow_nan=False)))
 finally:db.close()
 return row

def inspect(path,key):
 key=C.text(key,'medication review ID',64);db=database(path)
 try:raw=db.execute('SELECT payload FROM reviews WHERE id=?',(key,)).fetchone()
 finally:db.close()
 if raw is None:raise ValueError('Unknown medication source review')
 row=json.loads(raw[0])
 if digest({k:v for k,v in row.items() if k!='id'})!=key:raise ValueError('Medication review integrity changed')
 if source(path,row['request'])[2]!=row['binding']:raise C.work.WorkConflict('Medication review questions or received sources changed; prepare again')
 return row

def listing(path,request):
 C.exact(request,'portfolio_id patient_id');db=database(path)
 try:rows=[json.loads(r[0]) for r in db.execute('SELECT payload FROM reviews WHERE portfolio=? AND patient=? ORDER BY rowid',(request['portfolio_id'],request['patient_id']))]
 finally:db.close()
 if not rows:source(path,request)
 for r in rows:
  try:inspect(path,r['id']);r['current']=True;r['stale_reason']=None
  except (ValueError,KeyError,FileNotFoundError) as e:r['current']=False;r['stale_reason']=str(e)
 return {'reviews':rows,'clinical_execution_authorized':False}

def install():
 """Add a source origin while preserving all previous origin and plan identities."""
 if getattr(C.origin,'medication_review_adapter',False):return
 original_origin=C.origin;original_prepare=C.prepare
 def origin(path,request):
  if request.get('kind')!=KIND:return original_origin(path,request)
  C.exact(request,'kind id finding_ids')
  if request['finding_ids']!=[]:raise ValueError('Literal medication reviews have no model finding IDs')
  r=inspect(path,request['id'])
  return {'kind':KIND,'id':r['id'],'hash':digest(r),'patient_id':r['patient_id'],'review':r,
   'authority':'literal_received_medication_evidence_not_clinical_assessment'}
 def prepare_match(path,request):
  if request.get('origin',{}).get('kind')==KIND:
   r=inspect(path,request['origin']['id'])
   if request.get('need_tags')!=['medication_review']:raise ValueError('This documentary origin supports medication review only; treatment or titration indication is not established')
   if request.get('coverage',{}).get('as_of')!=r['as_of']:raise ValueError('Review and service decision must use the same source cutoff')
  return original_prepare(path,request)
 origin.medication_review_adapter=True;C.origin=origin;C.prepare=prepare_match
