"""Real loopback HTTP lifecycle with an isolated disposable synthetic warehouse.

Only the warehouse/answer adapters are fixtures. The service routing, retirement
transactions, work engines and buyer-plan invalidation are production code.
"""
import copy,json,multiprocessing
from http.server import ThreadingHTTPServer
from pathlib import Path
from types import SimpleNamespace
from urllib.request import Request,urlopen
from urllib.error import HTTPError
from unittest.mock import patch

OUT=Path('artifacts/agere-answer-retirement-v1')

def child(queue):
    import test_answer_retirement as t
    import test_care_capabilities as b
    from clinical_core.agere import service
    from clinical_core.agere.clinical import care_capabilities as care,answer_retirement as r
    f=t.Retirement();f.setUp();buyer=b.CareCapabilities();buyer.setUp()
    meta=json.loads((f.root/'run.json').read_text());meta['cache_key_digest']='fixture';(f.root/'run.json').write_text(json.dumps(meta))
    care.save_portfolio(f.root,copy.deepcopy(buyer.save_request));req=copy.deepcopy(buyer.req)
    req['origin']={'kind':'assessment','id':f.item()['assessment_id'],'finding_ids':[]}
    plan=care.prepare(f.root,req);task=care.enqueue(f.root,{'plan_id':plan['id'],'option_id':plan['options'][0]['id'],'actor':'engineering fixture'})['task']
    def server(address,handler):
        result=ThreadingHTTPServer(address,handler);queue.put({'port':result.server_port,'item':f.item(),'task':task,'old_assessment':req['origin']['id']});return result
    stack=SimpleNamespace(path=f.root,retriever=SimpleNamespace(meta={'source_run':'fixture'}))
    try:
        with patch('clinical_core.agere.clinical.stack_runtime.Stack',return_value=stack),patch('clinical_core.agere.clinical.native_snapshot_cache.install'),patch('clinical_core.agere.clinical.native_snapshot_cache.prepare_vocabulary_index'),patch.object(service,'ThreadingHTTPServer',side_effect=server):
            service.serve(f.root,0,stack=f.root)
    finally:buyer.doCleanups();f.doCleanups()

def main():
    ctx=multiprocessing.get_context('spawn');queue=ctx.Queue();process=ctx.Process(target=child,args=(queue,));process.start()
    try:
        info=queue.get(timeout=30);base='http://localhost:'+str(info['port'])
        def api(route,body=None):
            with urlopen(Request(base+route,data=json.dumps(body).encode() if body is not None else None,headers={'Content-Type':'application/json'}),timeout=40) as response:return json.load(response)
        def rejected(route,body,code,text):
            try:api(route,body);raise AssertionError('Expected rejection')
            except HTTPError as error:assert error.code==code and text in error.read().decode()
        item=info['item'];state=api('/api/stack/work/answers?item='+item['id']);assert len(state['answers'])==2
        request=dict(item_id=item['id'],expected_revision=item['revision'],answer_ids=['a'*64],replacement_ids=['c'*64],reason='replaced_for_review',note='Disposable engineering fixture; not patient care')
        plan=api('/api/stack/work/answers/prepare',request);assert plan['downstream_tasks'][0]['task_id']==info['task']['id']
        before=api('/api/stack/work?item='+item['id']);assert before['item']==item
        apply=dict(plan_id=plan['id'],actor='engineering fixture',request_id='http-retirement')
        result=api('/api/stack/work/answers/apply',apply);assert result['item']['reassessment_required'] and result['item']['revision']==item['revision']+1
        assert api('/api/stack/work/answers/apply',apply)['idempotent_replay']
        after=api('/api/stack/work?item='+item['id']);assert after['history'][:-1]==before['history']
        active=api('/api/stack/work/answers?item='+item['id']);assert [a['answer_id'] for a in active['answers']]==['b'*64,'c'*64] and len(active['retirements'])==1
        rejected('/api/stack/work/answers/prepare',request,409,'changed')
        rejected('/api/stack/work/answers/apply',{**apply,'actor':'different'},409,'Request ID')
        rejected('/api/stack/capabilities/event',dict(task_id=info['task']['id'],expected_revision=info['task']['revision'],request_id='handoff',action='prepare_handoff',actor='engineering fixture',note='Fixture'),409,'retired machine context')
        value={'scope':'Disposable engineering warehouse; synthesis verification mocked only for fixture answers', 'http_lifecycle_passed':True,'prepared':plan,'applied':result,'active':active,'old_history_preserved':True,'dependent_buyer_handoff_rejected':True,'concurrent_and_request_reuse_rejected':True,'clinical_validation':False}
        (OUT/'http-fixture-verification.json').write_text(json.dumps(value,indent=2)+'\n');print('Disposable HTTP lifecycle passed')
    finally:process.terminate();process.join(timeout=10)
if __name__=='__main__':main()
