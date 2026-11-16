"""Reproducible, source-backed visual packet and adversarial probes. No paid calls."""
import hashlib,json,sqlite3,random
from pathlib import Path
from clinical_core.agere.operators import trend,labs,patient_analysis,medication
from clinical_core.agere.service import query
ROOT=Path(__file__).resolve().parent
BASE=ROOT/'artifacts/agere-full'; RUN=BASE/'warehouse-run-v4'; OUT=ROOT/'artifacts/agere-observatory'

def read(p):return json.loads(p.read_text())
def save(p,d):p.write_text(json.dumps(d,indent=2,allow_nan=False))

def build():
 OUT.mkdir(exist_ok=True)
 run=read(RUN/'run.json');audit=read(BASE/'heldout-audit-v1.json');verify=read(BASE/'verification.json')
 patients=[json.loads(x) for x in (RUN/'patients.jsonl').open()]
 cards=read(RUN/'insights.json');groups={c['id']:set(c['patient_ids']) for c in cards}
 renal=[p for p in patients if p['patient_id'] in groups['declining_egfr']]
 threshold_blind=[p for p in renal if p['clinical']['facts']['latest_egfr']>=30]
 renal.sort(key=lambda p:p['trends']['egfr']['slope_per_year'])
 # Select a displayed case with >=3 dates and currently above the single referral threshold.
 example=sorted(threshold_blind,key=lambda p:p['trends']['egfr']['slope_per_year'])[0]
 indexes={p['patient_id']:p for p in patients}
 examples={c['id']:indexes[c['patient_ids'][0]] for c in cards}
 examples['declining_egfr']=example
 db=sqlite3.connect(f'file:{BASE / "warehouse.sqlite3"}?mode=ro',uri=True)
 for p in examples.values():
  p['source_excerpt']=[]
  ids=[]
  if p['clinical']['financial_evidence']:ids.append(p['clinical']['financial_evidence']['record_id'])
  ids.extend(x['evidence']['record_id'] for x in p['clinical']['ef_candidates'][:1])
  ids.extend(x['evidence']['record_id'] for x in p['clinical']['goals_mentions'][:1])
  for rid in dict.fromkeys(ids):
   r=db.execute('SELECT payload FROM records WHERE release=? AND id=?',(run['release'],rid)).fetchone()
   if r:p['source_excerpt'].append(json.loads(r[0]))
 db.close()
 questions=[
 'Show kidney specialist review','Show declining eGFR','Show reduced EF documentation','Show financial assistance screen','Show goals of care context',
 'Find all HFrEF patients not on GDMT or max tolerated dose',
 'Find CKD patients without a nephrology visit',
 'Find patients whose creatinine is rising',
 'What SNFs do best for certain DRGs?',
 'Which PCPs have the best heart failure outcomes?',
 'Why were patients readmitted and which intervention will reduce sepsis admissions?',
 'Which patients qualify for the new CMS ACCESS program?',
 'Find patients going out of network and why',
 'Which locations can improve financial performance under downside risk contracts?',
 'Show financial assistance and explain why patients were readmitted',
 'Which patients are receiving futile care?',
 ]
 queries=[]
 for q in questions:
  result=query(RUN,q)
  result['cards']=[{k:v for k,v in c.items() if k!='patient_ids'} for c in result['cards']]
  queries.append(result)
 probes=[]
 def probe(name,expect,actual,passed,implication):
  probes.append({'name':name,'expected':expect,'observed':actual,'passed':bool(passed),'implication':implication})
 series=example['clinical']['egfr'];a=trend(series); shuffled=list(series);random.Random(42).shuffle(shuffled);b=trend(shuffled)
 probe('Reorder the renal timeline','Identical slope',b['slope_per_year'],a['slope_per_year']==b['slope_per_year'],'Sorting must not change the trajectory.')
 b=trend(series+series)
 probe('Duplicate every lab row','No extra independent measurement dates',b['points'],b['points']==a['points'] and b['slope_per_year']==a['slope_per_year'],'Repeated documentation must not strengthen evidence.')
 r={'record_id':'PROBE-1','patient_id':'SYNTHETIC-PROBE','source_table':'labs','event_date':'2026-09-01','available_date':'2026-09-02','test':'egfr','value':5,'unit':'unknown','synthetic':True}
 b=labs([r],'egfr');probe('Unsupported lab units','Exclude the value',len(b),not b,'Unknown units cannot drive a numerical clinical flag.')
 p=patient_analysis({'patient_id':'SYNTHETIC-PROBE'},[],'2026-09-30',[])
 probe('Missing financial screen','Unknown, not false',p['clinical']['facts']['difficulty_paying'],p['clinical']['facts']['difficulty_paying'] is None,'Missing information is not proof of absent need.')
 b=medication([{**r,'source_table':'medication_orders','drug_class':'mra'}],'mra','2026-09-30')
 probe('Medication order without a fill','No supply or missing-care conclusion',b['status'],b['status']=='supply_not_observed' and not b['missing_care_established'],'Order, dispensation and ingestion are different states.')
 for name,text in [('Negated low EF','Prior EF 30% was entered in error; current EF 60%.'),('Family-member low EF','The patient reports that her brother has EF 25%; her EF is 60%.')]:
  p=patient_analysis({'patient_id':'SYNTHETIC-PROBE'},[{**r,'source_table':'notes','text':text}],'2026-09-30',[])
  flagged=any(x['kind']=='reduced_ef_documentation' for x in p['flags'])
  probe(name,'No patient low-EF candidate',{'text':text,'low_ef_candidate':flagged},not flagged,'Native regex candidate extraction needs assertion, experiencer and temporal adjudication before promotion. Existing candidate label prevents calling this confirmed HFrEF.')
 probe('Creatinine question on this warehouse','Missing input, not an empty healthy cohort',queries[7]['status'],queries[7]['status']=='requires_explicit_operator_and_inputs','The warehouse contains eGFR but no creatinine results.')
 probe('Compound question with an unsupported clause','Explicitly partial answer',queries[14]['status'],queries[14]['status']=='partial_screening_results','The query interface now preserves the unsupported part instead of overclaiming completion.')
 data={'synthetic':True,'run_summary':run['summary'],'as_of':run['as_of'],'snapshot':run['snapshot'],'compute':run['compute'],
       'audit':audit,'verification':verify,'math':read(BASE/'mathematical-experiments.json'),
       'cards':[{k:v for k,v in c.items() if k!='patient_ids'} for c in cards],
       'examples':examples,'queries':queries,'stress_probes':probes,
       'cross_signal':{'declining_egfr':len(renal),'latest_egfr_at_least_30':len(threshold_blind),
                       'both_renal_screens':len(groups['declining_egfr']&groups['kidney_specialist_review']),
                       'interpretation':'Negative descriptive trajectories can be present above one referral-screen threshold. This is not a validated prediction of future deterioration.'},
       'scope':'Observed synthetic warehouse plus separate explicitly labeled adversarial fixtures; no oracle labels read and no API calls.',
       'source_files':[]}
 for p in [RUN/'run.json',RUN/'patients.jsonl',RUN/'insights.json',BASE/'heldout-audit-v1.json',BASE/'verification.json',BASE/'mathematical-experiments.json',ROOT/'clinical_core/agere/query_scope.py',Path(__file__)]:
  data['source_files'].append({'path':str(p.relative_to(ROOT)),'sha256':hashlib.sha256(p.read_bytes()).hexdigest()})
 save(OUT/'evidence.json',data)
 template=OUT/'template.html'
 if template.exists():
  html=template.read_text().replace('__DATA__',json.dumps(data,separators=(',',':')).replace('<','\\u003c'))
  (OUT/'index.html').write_text(html)
  (RUN/'observatory.html').write_text(html)
 print(json.dumps({'cross_signal':data['cross_signal'],'probes':[(x['name'],x['passed']) for x in probes], 'query_statuses':{s:sum(q['status']==s for q in queries) for s in set(q['status'] for q in queries)}}))
 return data
if __name__=='__main__':build()
