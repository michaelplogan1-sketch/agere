"""Question entry point uses exact local programs; no inference is requested."""
import json
from pathlib import Path
from verify_domain_programs_http import api,write,snapshot,OUT
from clinical_core.agere.gateway import BudgetLedger

def main():
    ledger=BudgetLedger(Path('artifacts/agere-full/provider-budget.sqlite3')).receipt();before=snapshot();results=[]
    for domain,count in [('respiratory',100),('behavioral',24)]:
        question='Prepare '+domain+' source-review work'
        result=api('/api/stack/compose',{'question':question});write(domain+'-question',result)
        assert result['planner_receipt']['route']=='local_compiler' and result['planner_receipt']['recipe']=='exact_domain_source_review'
        assert result['new_paid_api_calls']==0 and result['results'][-1]['result']['counts']['candidates']==count
        assert result['results'][-1]['result']['synthesis_assessments']==1
        replay=api('/api/stack/compose',{'question':question});assert replay['cache_hit'] and replay['new_paid_api_calls']==0
        results.append({'domain':domain,'question':question,'route':result['planner_receipt']['route'],'candidates':count,'saved_answers_retained':1,'cache_replay':True,'new_paid_api_calls':0})
        print(json.dumps(results[-1]),flush=True)
    assert snapshot()==before
    assert BudgetLedger(Path('artifacts/agere-full/provider-budget.sqlite3')).receipt()==ledger
    write('question-verification',results)
if __name__=='__main__':main()
