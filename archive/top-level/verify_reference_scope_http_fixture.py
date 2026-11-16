"""Real route/persistence lifecycle; explicitly disposable machine/source fixtures."""
import json,multiprocessing
from pathlib import Path
from http.server import ThreadingHTTPServer
from types import SimpleNamespace
from urllib.request import Request,urlopen
from urllib.error import HTTPError
from unittest.mock import patch
OUT=Path('artifacts/agere-reference-scope-v1')
def child(queue):
 import test_reference_scope_work as fixture
 from clinical_core.agere import service
 from clinical_core.agere.clinical import reference_scope_work as scope
 f=fixture.Scope();f.setUp();selection=scope.select(f.path,f.selection_request);request=f.prepare_answer(selection)
 meta=json.loads((f.path/'run.json').read_text());meta['cache_key_digest']='fixture';(f.path/'run.json').write_text(json.dumps(meta))
 def server(address,handler):
  result=ThreadingHTTPServer(address,handler);queue.put({'port':result.server_port,'request':request,'preview_request':f.request,'selection_request':f.selection_request,'target':f.target,'task_id':f.task['id']});return result
 stack=SimpleNamespace(path=f.path,retriever=SimpleNamespace(meta={'source_run':'fixture'}))
 try:
  with patch('clinical_core.agere.clinical.stack_runtime.Stack',return_value=stack),patch('clinical_core.agere.clinical.native_snapshot_cache.install'),patch('clinical_core.agere.clinical.native_snapshot_cache.prepare_vocabulary_index'),patch.object(service,'ThreadingHTTPServer',side_effect=server):service.serve(f.path,0,stack=f.path)
 finally:f.doCleanups()
def main():
 ctx=multiprocessing.get_context('spawn');queue=ctx.Queue();p=ctx.Process(target=child,args=(queue,));p.start()
 try:
  info=queue.get(timeout=30);base='http://localhost:'+str(info['port'])
  def api(route,body=None):
   with urlopen(Request(base+route,data=json.dumps(body).encode() if body is not None else None,headers={'Content-Type':'application/json'}),timeout=50) as r:return json.load(r)
  before=api('/api/stack/work-state?id='+info['task_id'])
  preview=api('/api/stack/reference-scope/preview',info['preview_request']);assert preview['added_record_ids']==['o','pkt']
  selected=api('/api/stack/reference-scope/select',info['selection_request']);assert selected['id']==info['request']['selection_id']
  answer=api('/api/stack/reference-scope/answer',info['request']);assert answer==api('/api/stack/reference-scope/answer',info['request'])
  view=api('/api/stack/reference-scope/list',{'target':info['target']});assert len(view['answers'])==1 and view['answers'][0]['current']
  assert not answer['requirement_resolved'] and not answer['human_review_recorded']
  rejected=0
  for route,body in [('/api/stack/reference-scope/answer',{**info['request'],'human_review_recorded':True}),('/api/stack/reference-scope/select',{**info['selection_request'],'record_ids':['o','pkt']})]:
   try:api(route,body);raise AssertionError('Invalid request succeeded')
   except HTTPError as e:assert e.code==400;rejected+=1
  assert before==api('/api/stack/work-state?id='+info['task_id'])
  result={'status':'passed','scope':'Disposable engineering fixture; source-context and saved-machine-answer adapters are fixture data. Real HTTP routes, target/selection dependencies and immutable SQLite persistence.',
  'answer':answer,'current_history':view,'invalid_requests_rejected':rejected,'old_packet_unchanged':True,'new_human_reviews':0,'clinical_validation':False,'live_model_output_tested':False}
  (OUT/'http-fixture-verification.json').write_text(json.dumps(result,indent=2)+'\n');print(json.dumps({'status':'passed','rejections':rejected,'exact_answer_return':True}))
 finally:p.terminate();p.join(timeout=10)
if __name__=='__main__':main()
