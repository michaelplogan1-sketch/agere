"""Bounded model extraction on actual synthetic warehouse records, preserving packet selection."""
from pathlib import Path
import json,os,sqlite3
from clinical_core.agere.gateway import BudgetLedger
from clinical_core.agere.clinical.model_reader import compare,requests
ROOT=Path(__file__).resolve().parent;RUN=ROOT/'artifacts/agere-clinical-workflows-v2';OUT=ROOT/'artifacts/agere-clinical-reader-v1'
examples=json.loads((RUN/'examples.json').read_text());meta=json.loads((RUN/'run.json').read_text())
selection=[('heart_failure','current_phenotype_unresolved'),('heart_failure','insufficient_phenotype_evidence'),
 ('kidney','referral_context_review'),('kidney','trajectory_review'),('affordability','assistance_needs_review'),('affordability','latest_report_denies_difficulty')]
db=sqlite3.connect((ROOT/'artifacts/agere-full/warehouse.sqlite3').as_uri()+'?mode=ro',uri=True)
cases=[];bindings={}
for i,(domain,status) in enumerate(selection):
 pid=examples[domain][status]['patient_id']
 rs=[json.loads(row[0]) for row in db.execute('SELECT payload FROM records WHERE release=? AND patient=? AND event<=? AND arrival<=? AND identity=? ORDER BY event,id',
      (meta['snapshot']['release'],pid,meta['snapshot']['as_of'],meta['snapshot']['as_of'],'matched'))]
 # Use source records referenced by the already correction-filtered fact ledger, never independently revive superseded records.
 allowed={e['record_id'] for f in examples[domain][status]['facts'] for e in f['evidence']}
 rs=[r for r in rs if r['record_id'] in allowed]
 notes=[r for r in rs if r['source_table']=='notes' and r.get('text')]
 chosen=notes[-1:]
 if domain=='kidney':
  labs=[r for r in rs if r['source_table']=='labs' and r.get('test')=='egfr']
  chosen+=([labs[0],labs[len(labs)//2],labs[-1]] if len(labs)>2 else labs)
 if domain=='affordability':chosen+=[r for r in rs if r['source_table']=='financial'][-1:]
 if domain=='heart_failure':chosen+=[r for r in rs if r['source_table']=='medication_orders'][-1:]
 evidence=[];source_map={};seen=set()
 for r in chosen:
  if r['record_id'] in seen:continue
  seen.add(r['record_id']);eid=f'w{i}_e{len(evidence)}'
  if r['source_table']=='notes':text=r['text'];field='text'
  else:
   keys=('test','value','unit','difficulty_paying_medical_bills','drug_class','drug','dose','status')
   text=json.dumps({k:r[k] for k in keys if k in r},sort_keys=True);field='observed_fields_json_projection'
  evidence.append({'id':eid,'note_id':r['record_id'],'date':r['event_date'],'text':text})
  source_map[eid]={'patient_id':pid,'record_id':r['record_id'],'field':field,'event_date':r['event_date'],'available_date':r['available_date']}
 if domain=='heart_failure':
  question='What do the supplied records establish about a CURRENT patient EF at or below 40%?'
  options={'yes':'An explicitly current patient EF at or below 40% is documented','no':'An explicitly current patient EF above 40% is documented','unknown':'EF is missing, historical only, about another person, or contradictory'}
 elif domain=='kidney':
  question='What direction do the dated eGFR observations document? Use values only for eGFR with the same units.'
  options={'yes':'Every later supplied value is lower than the earlier value','no':'The supplied sequence is not consistently decreasing','unknown':'Insufficient comparable dated eGFR observations'}
 else:
  question='What do the latest available patient reports establish about difficulty paying medical bills?'
  options={'yes':'The latest report explicitly reports difficulty paying','no':'The latest report explicitly denies difficulty paying','unknown':'Missing report or unresolved same-date conflict'}
 cid='warehouse_'+str(i);bindings[cid]={'patient_id':pid,'domain':domain,'source_map':source_map,'selection':'Latest note plus bounded relevant structured records; not a complete chart review'}
 cases.append({'case_id':cid,'synthetic':True,'as_of':meta['snapshot']['as_of'],'question':question,'options':options,'evidence':evidence})
db.close()
(OUT/'warehouse-cases.json').write_text(json.dumps(cases,indent=2));(OUT/'warehouse-bindings.json').write_text(json.dumps(bindings,indent=2))
body=requests(cases,'claude');(OUT/'warehouse-request.json').write_text(json.dumps(body,indent=2))
for line in Path('/Users/mikel/insight-engine/.env').read_text().splitlines():
 if '=' in line and not line.lstrip().startswith('#'):
  k,v=line.split('=',1)
  if k.strip().removeprefix('export ')=='ANTHROPIC_API_KEY':os.environ.setdefault('ANTHROPIC_API_KEY',v.strip().strip('"').strip("'"))
l=BudgetLedger(ROOT/'artifacts/agere-full/provider-budget.sqlite3')
try:
 result=compare(cases,'claude',l,allow_live=True)
 (OUT/'warehouse-result.json').write_text(json.dumps(result,indent=2));(OUT/'budget-current.json').write_text(json.dumps(l.receipt(),indent=2))
 overlay=[]
 for a in result.get('answers',[]):overlay.append({**bindings[a['case_id']],'model_result':a,'decision_authority':False,'review_status':'unreviewed_model_proposal'})
 (RUN/'model-proposals.json').write_text(json.dumps({'synthetic':True,'source':'bounded live Claude warehouse pilot','patients':overlay,'applied_to_deterministic_workflow':False},indent=2))
 print(json.dumps({'status':result['status'],'error':result.get('error'),'diagnostic':result.get('diagnostic'),'cases':len(cases),'source_grounded_facts':sum(len(a['extracted_facts']) for a in result.get('answers',[])),'budget':l.receipt()}),flush=True)
finally:l.close()
