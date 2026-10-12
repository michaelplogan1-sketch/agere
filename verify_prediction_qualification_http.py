"""Actual local question -> qualification -> exact received-source inspection."""
import json
from pathlib import Path
import time
from clinical_core.agere.gateway import BudgetLedger
from verify_domain_programs_http import api,snapshot

OUT=Path('artifacts/agere-prediction-qualification-v1')
def save(name,value):(OUT/(name+'.json')).write_text(json.dumps(value,indent=2)+'\n')


def main():
    ledger=BudgetLedger(Path('artifacts/agere-full/provider-budget.sqlite3')).receipt();work=snapshot();save('ledger-before',ledger);save('work-before',work)
    started=time.perf_counter();status=api('/api/stack/prediction-qualification');elapsed=time.perf_counter()-started;save('http-status',status)
    assert status['all_current'] and len(status['reports'])==5 and not status['clinical_prediction_authorized']
    assert api('/api/stack/outcome-learning')==status
    results=[]
    for i in range(1,6):
        ex=json.loads((OUT/f'question-{i}.json').read_text());started=time.perf_counter();answer=api('/api/stack/compose',{'question':ex['question']});duration=time.perf_counter()-started;save(f'http-question-{i}',answer)
        final=answer['results'][-1]['result'];report=next(r for r in status['reports'] if r['dataset_identity']==final['identity'])
        assert final['patients_evaluated']==report['counts']['patients_assessed']==1614 and report['status']=='not_fitted'
        eligible=next(e for group,examples in report['source_qualification']['examples'].items() if group!='excluded' for e in examples)
        endpoint='/api/stack/baseline' if report['task']['adapter']=='index_day' else '/api/stack/decision-landmarks'
        evidence=api(endpoint+'?id='+eligible['baseline_receipt_id']);save(f'http-evidence-{i}',evidence)
        assert evidence['patient_id']==eligible['patient_id']
        results.append({'contract':report['task']['contract'],'question_seconds':duration,'dataset_identity':final['identity'],'report_id':report['id'],'patient_source_verified':eligible['patient_id'],'source_receipt_id':eligible['baseline_receipt_id']})
        print(json.dumps(results[-1]),flush=True)
    assert ledger==BudgetLedger(Path('artifacts/agere-full/provider-budget.sqlite3')).receipt() and work==snapshot()
    save('http-verification',{'passed':True,'status_seconds':elapsed,'tasks':results,'ledger_unchanged':True,'functional_work_unchanged':True});print('All five question, protected qualification and source inspection paths passed.',flush=True)


if __name__=='__main__':main()
