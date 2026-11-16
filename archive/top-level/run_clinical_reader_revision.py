from pathlib import Path
import json,os
from clinical_core.agere.gateway import BudgetLedger
from clinical_core.agere.clinical.model_reader import compare,requests,score
ROOT=Path(__file__).resolve().parent
for line in Path('/Users/mikel/insight-engine/.env').read_text().splitlines():
 if '=' in line and not line.lstrip().startswith('#'):
  key,value=line.split('=',1);key=key.strip().removeprefix('export ')
  if key=='ANTHROPIC_API_KEY':os.environ.setdefault(key,value.strip().strip('"').strip("'"))
out=ROOT/'artifacts/agere-clinical-reader-v1';items=json.loads((out/'live-cases.json').read_text())
ledger=BudgetLedger(ROOT/'artifacts/agere-full/provider-budget.sqlite3')
try:
 before=ledger.receipt();settled=ledger.settle_completed()
 (out/'reservation-settlement.json').write_text(json.dumps({'before':before,'settlements':settled,'after':ledger.receipt()},indent=2))
 body=requests(items,'claude');(out/'claude-revised-request.json').write_text(json.dumps(body,indent=2))
 result=compare(items,'claude',ledger,allow_live=True)
 (out/'claude-revised-result.json').write_text(json.dumps(result,indent=2))
 (out/'claude-revised-score.json').write_text(json.dumps(score(items,result),indent=2))
 (out/'budget-current.json').write_text(json.dumps(ledger.receipt(),indent=2))
 print(json.dumps({'status':result['status'],'error':result.get('error'),'diagnostic':result.get('diagnostic'),'score':score(items,result),'budget':ledger.receipt()}),flush=True)
finally:ledger.close()
