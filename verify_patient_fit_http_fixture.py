"""Real HTTP on a disposable engineering fixture; no live clinical reviews."""
import json,multiprocessing
from datetime import datetime,timedelta,timezone
from http.server import ThreadingHTTPServer
from types import SimpleNamespace
from pathlib import Path
from urllib.request import Request,urlopen
from urllib.error import HTTPError
from unittest.mock import patch

OUT=Path('artifacts/agere-patient-fit-v1')
def child(queue):
    import test_patient_fit_answers as fixture
    from clinical_core.agere import service
    from clinical_core.agere.clinical import patient_fit_work as work
    from clinical_core.agere.clinical import internal_execution as execution
    f=fixture.Answers();f.setUp()
    meta=json.loads((f.path/'run.json').read_text());meta['cache_key_digest']='fixture';(f.path/'run.json').write_text(json.dumps(meta))
    def credential(role):return execution.provision(f.path,{'principal_id':'Disposable HTTP engineering fixture','identity_kind':'synthetic_test',
        'buyer_id':'buyer1','service_id':'pharmacy','roles':[role],'expires_at':(datetime.now(timezone.utc)+timedelta(hours=1)).isoformat()})['token']
    worker=credential('packet_worker');reviewer=credential('packet_reviewer')
    def server(address,handler):
        result=ThreadingHTTPServer(address,handler)
        queue.put({'port':result.server_port,'request':f.req,'task_id':f.task['id'],'revision':f.task['revision'],'worker':worker,'reviewer':reviewer});return result
    stack=SimpleNamespace(path=f.path,retriever=SimpleNamespace(meta={'source_run':'fixture'}))
    try:
        with patch('clinical_core.agere.clinical.stack_runtime.Stack',return_value=stack),patch('clinical_core.agere.clinical.native_snapshot_cache.install'),patch('clinical_core.agere.clinical.native_snapshot_cache.prepare_vocabulary_index'),patch.object(service,'ThreadingHTTPServer',side_effect=server):
            service.serve(f.path,0,stack=f.path)
    finally:f.doCleanups()
def main():
    ctx=multiprocessing.get_context('spawn');queue=ctx.Queue();process=ctx.Process(target=child,args=(queue,));process.start()
    try:
        info=queue.get(timeout=30);base='http://localhost:'+str(info['port'])
        def api(route,body=None,token=None):
            headers={'Content-Type':'application/json'}
            if token:headers['Authorization']='Bearer '+token
            with urlopen(Request(base+route,data=json.dumps(body).encode() if body is not None else None,headers=headers),timeout=40) as response:return json.load(response)
        before=api('/api/stack/work-state?id='+info['task_id'])
        request=info['request'];result=api('/api/stack/patient-fit/answer',request)
        assert result==api('/api/stack/patient-fit/answer',request)
        view=api('/api/stack/patient-fit/answers',{'target':request['target']});assert len(view['answers'])==1 and view['answers'][0]['current']
        assert not result['current_patient_consent_established'] and not result['human_review_recorded']
        try:api('/api/stack/patient-fit/answer',{**request,'requirement_resolved':True});raise AssertionError('Unexpected authority accepted')
        except HTTPError as exc:assert exc.code==400
        assert before==api('/api/stack/work-state?id='+info['task_id'])
        run=api('/api/stack/internal-execution/run',{'task_id':info['task_id'],'expected_revision':info['revision'],'request_id':'fixture-run'},info['worker'])['run']
        review=api('/api/stack/internal-execution/review',{'run_id':run['id'],'expected_revision':0,'request_id':'fixture-review','disposition':'needs_more_evidence',
            'note':'Disposable engineering fixture, not a clinical review','follow_up_on':datetime.now(timezone.utc).date().isoformat()},info['reviewer'])
        followreq={'run_id':run['id'],'review_revision':1,'target':request['target'],'record_ids':['r1'],'note':'Disposable fixture source follow-up','request_id':'fixture-followup'}
        try:api('/api/stack/packet-followup/prepare',followreq);raise AssertionError('Missing credential accepted')
        except HTTPError as exc:assert exc.code==403
        follow=api('/api/stack/packet-followup/prepare',followreq,info['reviewer'])
        returned=api('/api/stack/packet-followup/answer',{'task_id':info['task_id'],'followup_id':follow['id'],'answer_id':request['answer_id']})
        assert not returned['requirement_resolved']
        assert api('/api/stack/packet-followup/answers?id='+info['task_id'])['answers'][0]['current']
        report={'status':'passed','scope':'Disposable synthetic engineering fixture; source-context and machine-answer adapters are fixtures. HTTP routes, target binding, immutable persistence and guards are actual code.',
            'exact_answer_attachment':result,'view':view,'old_packet_unchanged':True,'authority_injection_rejected':True,
            'scoped_packet_followup':follow,'packet_answer_return':returned,'missing_credential_rejected':True,'clinical_validation':False,'live_model_output_tested':False}
        (OUT/'http-fixture-verification.json').write_text(json.dumps(report,indent=2)+'\n');print('Disposable HTTP answer-return lifecycle passed')
    finally:process.terminate();process.join(timeout=10)
if __name__=='__main__':main()
