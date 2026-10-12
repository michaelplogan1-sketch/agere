"""Run only the authorized Perplexity arm of the frozen synthetic reader pilot.

No credentials enter stdout or result artifacts. Preserve the HTTP response before
semantic parsing so a contract failure can be investigated without a paid retry.
"""
from pathlib import Path
import io,json,os,urllib.request
from clinical_core.agere.gateway import BudgetLedger
from clinical_core.agere.clinical.model_reader import compare,score

ROOT=Path(__file__).resolve().parent
OUT=ROOT/'artifacts/agere-clinical-reader-v1'

def main():
    for line in Path('/Users/mikel/insight-engine/.env').read_text().splitlines():
        if '=' not in line or line.lstrip().startswith('#'):continue
        k,v=line.split('=',1)
        if k.strip().removeprefix('export ')=='PERPLEXITY_API_KEY':
            os.environ['PERPLEXITY_API_KEY']=v.strip().strip('"').strip("'")
    if not os.environ.get('PERPLEXITY_API_KEY'):raise ValueError('Perplexity credential missing')
    cases=json.loads((OUT/'live-cases.json').read_text())
    if not all(c.get('synthetic') is True for c in cases):raise ValueError('Synthetic-only pilot')
    def capture(request,timeout):
        with urllib.request.urlopen(request,timeout=timeout) as response:
            raw=response.read()
            safe=raw.decode().replace(os.environ['PERPLEXITY_API_KEY'],'[REDACTED]')
            (OUT/'perplexity-http-response.json').write_text(safe)
            (OUT/'perplexity-http-receipt.json').write_text(json.dumps({
                'http_status':response.status,'request_id':response.headers.get('x-request-id'),
                'endpoint':'https://api.perplexity.ai/v1/decisions','synthetic':True},indent=2))
        return io.BytesIO(raw)
    previous=OUT/'perplexity-result.json'
    archive=OUT/'perplexity-pre-credential-result.json'
    if previous.exists() and not archive.exists():archive.write_bytes(previous.read_bytes())
    ledger=BudgetLedger(ROOT/'artifacts/agere-full/provider-budget.sqlite3')
    try:
        before=ledger.receipt()
        result=compare(cases,'perplexity',ledger,allow_live=True,transport=capture)
        previous.write_text(json.dumps(result,indent=2))
        comparison=score(cases,result)
        summary={'synthetic':True,'status':result['status'],'model':result.get('model'),
                 'score':comparison,'usage':result.get('usage'),'cost_estimate':result.get('cost_estimate'),
                 'seconds':result.get('seconds',result.get('original_seconds')),
                 'error':result.get('error'),'diagnostic':result.get('diagnostic'),
                 'budget_before':before,'budget_after':ledger.receipt(),
                 'decision_authority':False,'references_modified':False}
        (OUT/'perplexity-live-summary.json').write_text(json.dumps(summary,indent=2))
        (OUT/'budget-current.json').write_text(json.dumps(ledger.receipt(),indent=2))
        print(json.dumps(summary,indent=2))
    finally:ledger.close()

if __name__=='__main__':main()
