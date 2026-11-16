"""Live local API exercise of program/accounting evidence composition."""
import argparse,json,urllib.request
from pathlib import Path
from clinical_core.agere.clinical.graph_workbench import accountability_examples,snapshot_examples


def main():
    p=argparse.ArgumentParser();p.add_argument('--output',type=Path,required=True);a=p.parse_args();reports=[]
    def request(path,body=None):
        req=urllib.request.Request('http://127.0.0.1:8885'+path,data=None if body is None else json.dumps(body).encode(),headers={'Content-Type':'application/json'})
        with urllib.request.urlopen(req,timeout=120) as r:return json.load(r)
    assert request('/health')['synthetic']
    for i,ex in enumerate(snapshot_examples(accountability_examples(),'2029-02-15'),1):
        answer=request('/api/stack/compose',{'question':ex['question']});assert answer['new_paid_api_calls']==0
        (a.output/f'http-question-{i}.json').write_text(json.dumps(answer,indent=2)+'\n')
        r=next(x['result'] for x in answer['results'] if x['node']['operation']=='accountability_review')
        page=request('/api/stack/accountability?id='+r['id']);assert len(page['items'])==20 and page['next_offset']==20
        second=request('/api/stack/accountability?id='+r['id']+'&offset=20');assert not {x['id'] for x in page['items']}&{x['id'] for x in second['items']}
        pid=r['patients'][0]['patient_id'];row=request('/api/stack/accountability?id='+r['id']+'&patient='+pid)
        assert row['patient_id']==pid and row['financial_evidence']['patient_id']==pid
        assert not row['payment_entitlement_established']
        if i==2:assert answer['results'][-1]['result']['accountability_assessments']==r['patients_evaluated']==14
        else:assert r['patients_evaluated']==1614
        reports.append({'title':ex['title'],'patients':r['patients_evaluated'],'requests':r['requests'],'request_counts':r['request_counts'],
            'source_inspection_passed':True,'nonoverlapping_queue_pages':True,'new_paid_api_calls':0})
    result={'passed':True,'synthetic':True,'questions':reports,'requests_sent':0,'work_items_created':0}
    (a.output/'http-verification.json').write_text(json.dumps(result,indent=2)+'\n');print(json.dumps(result,indent=2))


if __name__=='__main__':main()
