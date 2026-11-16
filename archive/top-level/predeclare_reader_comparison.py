import json,hashlib
from pathlib import Path
from clinical_core.agere.active import stack_path
from clinical_core.agere.clinical import semantic_reader as R,learning_cohorts as C,learning_queue as L,semantic_learning as S
from clinical_core.contracts import digest
out=Path('artifacts/agere-reader-comparison-v1');st=stack_path();key='8021f42a502aab70d962ecc3fce0fe3b4c1d706800e69e9135958bd071d5d02b'
# Authored before running any candidate on these selected targets. Relation, time
# and statement may be ambiguous; only declared unambiguous axes are scored.
rows=[
('medication','P-1008','WHC-R-2a8c05f88293a92fd81bb7','text','Did not increase Metformin today','medication metformin',{'relation':['dose_change'],'time':['current_at_source']},'Deferred increase is not a drug stop or a maximum tolerated dose.'),
('medication','P-1008','WHL-R-0acabe433ee7be8706c13c','text','No prescription was issued in this message.','prescription issuance',{'statement':['patient_negated'],'relation':['prescription_order'],'time':['current_at_source']},'Explicit non-issuance is not current medication use.'),
('respiratory','P-1002','WHL-R-0390c5a1864916fc60d50f','text','resting shortness of breath','resting dyspnea',{'statement':['patient_negated'],'relation':['clinical_observation'],'time':['current_at_source']},'Negation governs this current symptom report.'),
('respiratory','P-1002','WHL-R-0390c5a1864916fc60d50f','text','new breathing difficulty','breathing difficulty',{'statement':['hypothetical'],'relation':['clinical_observation']},'Return precautions do not affirm an observed symptom; time axis not adjudicated.'),
('cardiometabolic','P-1002','WHL-R-190cfd511099112e34dbb1','text','Office pressure 120/88 mmHg','blood pressure',{'statement':['patient_affirmed'],'relation':['clinical_observation'],'time':['current_at_source']},'Source reports this measurement, not current warehouse-cutoff state.'),
('cardiometabolic','P-1002','WHL-R-190cfd511099112e34dbb1','text','7.2 on 2027-01-28','HbA1c',{'statement':['patient_affirmed'],'relation':['clinical_observation'],'time':['historical']},'Explicit dated comparison measurement precedes the current account.'),
('functional','P-1004','WHL-R-3f77864673ae55d1a1e964','text','Timed rise/walk/turn test 19.2 seconds.','mobility measurement',{'statement':['patient_affirmed'],'relation':['clinical_observation'],'time':['current_at_source']},'Current reassessment measurement, not old source findings.'),
('functional','P-1004','WHL-R-3f77864673ae55d1a1e964','text','Discuss outpatient balance assessment and practical support.','balance assessment',{'relation':['care_plan']},'Recommendation does not establish completed care; remaining axes not adjudicated.'),
('appointment','P-3002','WHL-R-dee7767d1820065becc7d8','text','Requested an appointment.','appointment request',{'statement':['patient_affirmed'],'relation':['care_plan']},'Appointment request is not document request or completed visit; time not adjudicated.'),
('appointment','P-3002','WHR-7111ccce54e44a8d68dca111','plan_text','ACL reconstruction scheduled','ACL reconstruction',{'statement':['patient_affirmed'],'relation':['care_plan'],'time':['planned']},'Scheduled procedure is not evidence it occurred.'),
('payer','P-1008','WHF-R-3bd68e19addd8cad6f189d','reason','payer_updated_network_pricing_and_reprocessed_same_service','payer repricing',{'relation':['unknown']},'Financial repricing is outside this clinical relation taxonomy. It does not establish benefit eligibility.'),
('payer','P-1002','WHF-R-2275fb84ece44e1667a9fd','text','Questions about the insurance calculation may be directed to billing.','billing instruction',{'statement':['nonclinical'],'relation':['unknown']},'Administrative instruction, not a patient clinical account; source_coverage means data availability, not insurance.')]
meta=json.loads((st/'run.json').read_text());policy,lm=L.lineage('artifacts/agere-current-source-learning-v1/acquisition/lineage',meta);protection=S.protections();dataset=C.dataset(st,key)[2];families={r['patient_family'] for r in dataset};cases=[]
for i,(workflow,pid,rid,field,quote,concept,expected,rationale) in enumerate(rows):
 assert L.eligibility(pid,policy,protection) is None and policy['patient_families'][pid] not in families
 # Read only received text and exact offsets; no annotations or model outputs.
 import sqlite3
 with sqlite3.connect(st/'stack.sqlite3') as db:record=json.loads(db.execute('SELECT payload FROM records WHERE id=? AND patient=?',(rid,pid)).fetchone()[0])
 from clinical_core.agere.narrative_sources import text_fields
 text=next(f['text'] for f in text_fields(record) if f['field']==field);assert text.count(quote)==1
 start=text.index(quote);target={'patient_id':pid,'record_id':rid,'field':field,'start':start,'end':start+len(quote),'concept':concept}
 rr,tt,mm=R.source(st,target);packet=R.packet(rr,tt,target,mm['snapshot']['as_of'])
 assert packet['context_complete']
 cases.append({'id':f'{workflow}-{sum(c["workflow"]==workflow for c in cases)+1}','workflow':workflow,'target':target,'quote':quote,'source_hash':digest(record),'context_digest':digest(packet),'family':policy['patient_families'][pid],'expected_axes':expected,'rationale':rationale})
plan={'schema':'reader-comparison-development-plan/1','cohort_id':key,'roles':['cohort_lexical','cohort_windowed_minilm','kev','d1'],'cases':cases,'expectation_origin':'Assistant-authored engineering expectations from visible source text; NOT clinician-adjudicated labels. Undeclared axes are unscored.','selection':'12 purposive passages across six workflows and four original-training patient families, excluded from every partition of the compared cohort; no frozen/reserved/outer evaluation use. Not a prevalence or generalization sample.','training_or_tuning_allowed':False,'source_run':meta['source_run'],'source_digest':meta['source_digest'],'cohort_dataset_digest':digest(dataset),'policy_sha256':hashlib.sha256(Path('artifacts/agere-current-source-learning-v1/acquisition/lineage/policy.json').read_bytes()).hexdigest(),'protection_register':protection['register_sha256'],'metrics':['exact agreement with declared engineering axes','abstentions','contract or execution failures','same-evidence integrity','source overlap','cached replay and usage'],'inference_started':False}
p=out/'predeclaration.json';assert not p.exists();p.write_text(json.dumps(plan,indent=2)+'\n');print('Saved',len(cases),'cases;',sum(len(c['expected_axes']) for c in cases),'declared axes;',len({c['family'] for c in cases}),'families')
