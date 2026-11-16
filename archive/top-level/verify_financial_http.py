"""Exercise whole-population and clinical-to-financial composition via local API."""
import argparse,json,urllib.request
from pathlib import Path
from clinical_core.agere.clinical.graph_workbench import financial_examples,snapshot_examples


def main():
    p=argparse.ArgumentParser();p.add_argument('--output',required=True,type=Path);a=p.parse_args();reports=[]
    def get(url):
        with urllib.request.urlopen('http://127.0.0.1:8885'+url,timeout=120) as r:return json.load(r)
    assert get('/health')['synthetic']
    for i,ex in enumerate(snapshot_examples(financial_examples(),'2029-02-15'),1):
        req=urllib.request.Request('http://127.0.0.1:8885/api/stack/compose',data=json.dumps({'question':ex['question']}).encode(),headers={'Content-Type':'application/json'})
        with urllib.request.urlopen(req,timeout=120) as r:answer=json.load(r)
        (a.output/f'http-question-{i}.json').write_text(json.dumps(answer,indent=2)+'\n')
        detail=answer['results'][-1]['result'];assert answer['new_paid_api_calls']==0
        if i==1:assert detail['patients_evaluated']==1614
        else:
            upstream=answer['results'][-2]['result'];assert detail['patients_evaluated']==upstream['counts']['matched']==14
            assert detail['processing_counts'].get('patients_computed',0)==0
        receipt=get('/api/stack/financial-accounts?id='+detail['patients'][0]['id']);assert receipt['patient_id']==detail['patients'][0]['patient_id']
        reports.append({'title':ex['title'],'patients':detail['patients_evaluated'],'accounts':detail['accounts_evaluated'],
            'account_status_counts':detail['account_status_counts'],'receipt_patient':receipt['patient_id'],
            'processing':detail['processing_counts'],'source_inspection_passed':True,'new_paid_api_calls':0})
    result={'passed':True,'synthetic':True,'questions':reports,'no_treatment_or_financial_authority':True}
    (a.output/'http-verification.json').write_text(json.dumps(result,indent=2)+'\n');print(json.dumps(result,indent=2))


if __name__=='__main__':main()
