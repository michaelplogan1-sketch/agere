"""Two bounded live synthetic checks using the shared remaining authorization."""
import json
from pathlib import Path
from clinical_core.agere.gateway import BudgetLedger,PersistentGateway
from clinical_core.providers import EvidenceTask
from live_provider_pilot import load_backend_keys
ROOT=Path(__file__).resolve().parent
load_backend_keys(ROOT.parent/'insight-engine/.env')
jobs=json.loads((ROOT/'artifacts/agere-full/warehouse-run-v2/provider-jobs.json').read_text())
job=next(j for j in jobs if j['provider']=='jev');body=job['request'];state=body['state']
task=EvidenceTask(state['subject'],state['as_of'],body['questions']['reason']['criteria'],state['evidence'])
ledger=BudgetLedger(ROOT/'artifacts/agere-full/provider-budget.sqlite3',legacy_summary=ROOT/'artifacts/live-provider-pilot/summary.json')
gateway=PersistentGateway(ledger,allow_live=True);results=[]
try:
    for provider in ('jev','claude'):
        try:results.append({'provider':provider,'result':gateway.classify(provider,task)})
        except Exception as exc:results.append({'provider':provider,'error':type(exc).__name__,'http_status':getattr(exc,'code',None)})
    result={'synthetic':True,'patient_id':job['patient_id'],'results':results,'budget':ledger.receipt(),
            'scope':'Connection and typed-output check on one synthetic evidence packet; no accuracy comparison'}
    (ROOT/'artifacts/agere-full/live-check.json').write_text(json.dumps(result,indent=2));print(json.dumps(result,indent=2))
finally:ledger.close()
