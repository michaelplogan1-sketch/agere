from pathlib import Path
import json,time
from clinical_core.agere.active import stack_path
from clinical_core.agere.clinical import appointment_followthrough as A,appointment_qualification as Q
out=Path('artifacts/agere-appointment-followthrough-v1');start=time.monotonic()
r=A.prepare(stack_path(),{'patient_ids':None});Q.Q.write(out/'population-summary.json',A.summary(r));print(json.dumps({'phase':'observation','id':r['id'],'patients':len(r['patients']),'states':r['status_counts'],'labels':r['label_counts'],'seconds':time.monotonic()-start}),flush=True)
m=Q.export_dataset(stack_path(),r['id'],out/'dataset');print(json.dumps({'phase':'dataset','seconds':time.monotonic()-start}),flush=True)
b=Q.build(stack_path(),'artifacts/agere-current-source-learning-v1/acquisition/lineage',out/'dataset',out/'learning');print(json.dumps({'phase':'qualification','status':b['status'],'counts':b['counts'],'source_qualification':b['source_qualification'],'seconds':time.monotonic()-start}),flush=True)
s=Q.attach(stack_path(),out/'learning');Q.Q.write(out/'runtime-qualification.json',s);print(json.dumps({'phase':'attached','current':s['current'],'seconds':time.monotonic()-start}),flush=True)
