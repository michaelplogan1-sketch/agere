"""Run the authorized synthetic-only matched provider pilot; never print credentials."""
from pathlib import Path
import json,os
from clinical_core.agere.gateway import BudgetLedger
from clinical_core.agere.clinical.model_reader import compare,score
ROOT=Path(__file__).resolve().parent
path=Path('/Users/mikel/insight-engine/.env')
if path.exists():
    for line in path.read_text().splitlines():
        if '=' not in line or line.lstrip().startswith('#'):continue
        key,value=line.split('=',1);key=key.strip().removeprefix('export ')
        if key in {'TYPESAFE_API_KEY','ANTHROPIC_API_KEY','PERPLEXITY_API_KEY'}:
            os.environ.setdefault(key,value.strip().strip('"').strip("'"))
out=ROOT/'artifacts/agere-clinical-reader-v1'
items=json.loads((out/'live-cases.json').read_text())
ledger=BudgetLedger(ROOT/'artifacts/agere-full/provider-budget.sqlite3',legacy_summary=ROOT/'artifacts/live-provider-pilot/summary.json')
try:
    results={};before=ledger.receipt()
    # Fixed two-call budget. No retries or budget reset; Perplexity remains prepared if its credential is absent.
    for provider in ('perplexity','jev','claude'):
        result=compare(items,provider,ledger,allow_live=provider!='perplexity')
        if provider=='perplexity' and not os.environ.get('PERPLEXITY_API_KEY'):result['status']='credential_missing'
        (out/(provider+'-result.json')).write_text(json.dumps(result,indent=2))
        results[provider]={'status':result['status'],'score':score(items,result),'seconds':result.get('seconds'),
                           'usage':result.get('usage'),'cost_estimate':result.get('cost_estimate')}
        print(json.dumps({'provider':provider,**results[provider]}),flush=True)
    (out/'comparison.json').write_text(json.dumps({'synthetic':True,'cases':len(items),'providers':results,'budget_before':before,'budget_after':ledger.receipt(),
      'facts_role':'Source-grounded extraction proposals are not promoted into decision authority',
      'comparison_caveat':'Same case evidence and decision questions; Claude additionally extracts facts, so its latency and token bill include extra work',
      'training_effect':'No held-out benchmark targets or provider responses used to train a model in this run'},indent=2))
finally:ledger.close()
