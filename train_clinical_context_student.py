"""Generate, blind-check, and fit the authorized synthetic context specialist."""
from pathlib import Path
import json,os
from clinical_core.agere.gateway import BudgetLedger
from clinical_core.agere.clinical.context_student import generate_batch,verify_batch,fit
ROOT=Path(__file__).resolve().parent
OUT=ROOT/'artifacts/agere-context-student-v1';OUT.mkdir(exist_ok=True)
RECEIPTS=OUT/'receipts';RECEIPTS.mkdir(exist_ok=True)
for line in Path('/Users/mikel/insight-engine/.env').read_text().splitlines():
 if '=' in line and not line.lstrip().startswith('#'):
  k,v=line.split('=',1);k=k.strip().removeprefix('export ')
  if k in {'ANTHROPIC_API_KEY','TYPESAFE_API_KEY'}:os.environ.setdefault(k,v.strip().strip('"').strip("'"))
l=BudgetLedger(ROOT/'artifacts/agere-full/provider-budget.sqlite3')
try:
 before=l.receipt();generated=[];receipts=[]
 # Preserve completed training batches; smaller held-out batches reduce truncation risk.
 groups=[list(range(i,i+5)) for i in range(0,20,5)]+[[i] for i in range(20,30)]
 for indices in groups:
  i=indices[0]
  path=OUT/f'generated-{i:02d}.json'
  if path.exists():rows=json.loads(path.read_text())
  else:
   rows,receipt=generate_batch(indices,l,RECEIPTS);receipts.append(receipt);path.write_text(json.dumps(rows,indent=2))
  generated+=rows;print(json.dumps({'stage':'generation','families_complete':max(indices)+1,'examples':len(generated)}),flush=True)
 checked=[]
 for i in range(0,len(generated),12):
  path=OUT/f'verified-{i:03d}.json'
  if path.exists():rows=json.loads(path.read_text())
  else:
   rows,receipt=verify_batch(generated[i:i+12],l,RECEIPTS);receipts.append(receipt);path.write_text(json.dumps(rows,indent=2))
  checked+=rows;print(json.dumps({'stage':'blind_verification','examples':len(checked),'agreements':sum(x['machine_agreement'] for x in checked)}),flush=True)
 (OUT/'examples.jsonl').write_text(''.join(json.dumps(x)+'\n' for x in checked))
 card=fit(checked,OUT)
 (OUT/'execution.json').write_text(json.dumps({'synthetic':True,'budget_before':before,'budget_after':l.receipt(),'request_receipts':receipts,'model_weights_trained':True},indent=2))
 print(json.dumps({'stage':'trained','training_examples':card['training_examples'],'metrics':{k:{a:b for a,b in v.items() if a not in ('classification_report','errors')} for k,v in card['metrics'].items()},'budget':l.receipt()}),flush=True)
finally:l.close()
