"""Trace every previously authored question to executable operators and remaining data needs."""
import json
from pathlib import Path
ROOT=Path(__file__).resolve().parent
MAP={
 'CKD':['patient_analysis','trend','Program'], 'HF':['medication','patient_analysis','IntegratedStack'],
 'SEP':['admission_episodes','crossfit_effect','risk_adjusted_facilities'],
 'BEN':['patient_analysis','Program'], 'PAL':['patient_analysis','Program'],
 'ADM':['admission_episodes','process_paths','crossfit_effect'],
 'SNF':['risk_adjusted_facilities','facility_shrinkage','allocate'],
 'PCP':['risk_adjusted_facilities','facility_shrinkage'], 'NET':['network_usage','patient_analysis'],
 'DOC':['coding_reconciliation'], 'FIN':['contract_score','claim_development','allocate'],
 'POL':['compile_document','Program'], 'DISC':['benjamini_hochberg','crossfit_effect','process_paths'],
 'LEARN':['ActionSpecialist','QuestionPlanner','route_search'], 'DATA':['Warehouse','extract_pdf','patient_graph'],
 'R-VALUE':['contract_score','crossfit_effect'], 'R-CAUSAL':['crossfit_effect'], 'R-ECON':['allocate','contract_score'],
 'R-ACCESS':['patient_analysis','Program'], 'R-RENAL':['trend','Program'], 'R-SERIOUS':['patient_analysis'],
 'R-PHARM':['medication','IntegratedStack'], 'R-BEHAV':['patient_analysis','Program'],
 'R-FAMILY':['patient_analysis'], 'R-POSTACUTE':['admission_episodes','risk_adjusted_facilities'],
 'R-SAFETY':['Program','patient_analysis'], 'R-RULES':['compile_document','Program'],
 'R-EQUITY':['risk_adjusted_facilities'], 'R-DATAINT':['Warehouse','extract_pdf','patient_graph'],
 'R-DISCOVERY':['process_paths','benjamini_hochberg'], 'R-COMPUTE':['route_search','total_cost']}
rows=[]
for file in ('config/questions.json','research/vbc-landscape/question_supplement.json'):
 source=json.loads((ROOT/file).read_text())
 for group in source['groups']:
  for q in group['questions']:
   rows.append({'id':q['id'],'question':q['question'],'group':group['id'],'source_file':file,
    'available_components':MAP.get(group['id'],[]),'status':'operator_components_available; full question requires its exact data and specification',
    'required_data':group.get('required_data',q.get('required_data',[])),
    'original_expected_output':q.get('expected_output'),
    'scope':'Component coverage is not proof that every subquestion is answered; patient facts, effect estimates and causal explanations are distinct'})
result={'synthetic_development':True,'questions':rows,'count':len(rows),'model_training':'QuestionPlanner uses the earlier analyst-authored domain curriculum. Clinical action heads use the separately versioned synthetic labels.'}
(ROOT/'artifacts/agere-full/question-inventory.json').write_text(json.dumps(result,indent=2))
print(json.dumps({'questions':len(rows),'groups':len(set(r['group'] for r in rows))}))
