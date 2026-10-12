"""Read-only answer-coverage compatibility for literal medication work origins."""
from pathlib import Path
import hashlib
from ...contracts import digest
from . import care_capabilities as C,care_readiness as R,medication_source_review as M,synthesis_work_availability as old

def prepare(path,task_id):
 task=R.task(path,task_id);plan=C.inspect_plan(path,task['plan_id']);origin=plan['origin']
 if origin['kind']!=M.KIND:return old.prepare(path,task_id)
 if task['patient_id']!=plan['patient_id'] or task['patient_id']!=origin['patient_id']:raise ValueError('Medication work patient changed')
 review=M.inspect(path,origin['id'])
 if digest(review)!=origin['hash']:raise ValueError('Medication work source review changed')
 result={'schema':'medication-work-availability/1','task_id':task_id,'task_revision':task['revision'],'plan_id':plan['id'],
  'patient_id':task['patient_id'],'answers':[],'source_review_id':review['id'],
  'source_inventory':{k:review[k] for k in ('medication_records','nested_medication_items','narrative_records','received_records_outside_review')},
  'scope':'Literal received medication review only. No saved model interpretation is attached to this origin.',
  'clinical_meaning_validated':False,'clinical_execution_authorized':False,'human_review_established':False,
  'new_paid_api_calls':0,'external_actions':0,'code_identity':hashlib.sha256(Path(__file__).read_bytes()).hexdigest()}
 if R.task(path,task_id)!=task:raise ValueError('Medication task changed while reading coverage')
 result['id']=digest(result);return result
