"""Disposable authenticated HTTP packet lifecycle with source/model fixtures."""
import json,multiprocessing
from pathlib import Path
from http.server import ThreadingHTTPServer
from types import SimpleNamespace
from urllib.request import Request,urlopen
from urllib.error import HTTPError
from unittest.mock import patch
OUT=Path('artifacts/agere-reference-scope-v1')
def child(queue):
 import test_reference_scope_packets as fixture
 from clinical_core.agere import service
 f=fixture.Packets();f.setUp();meta=json.loads((f.path/'run.json').read_text());meta['cache_key_digest']='fixture';(f.path/'run.json').write_text(json.dumps(meta))
 def server(address,handler):
  result=ThreadingHTTPServer(address,handler);queue.put({'port':result.server_port,'request':f.req,'task_id':f.task['id'],'reviewer':f.bearer(f.reviewer),'worker':f.bearer(),'answer_id':f.output['id'],'target':f.target});return result
 stack=SimpleNamespace(path=f.path,retriever=SimpleNamespace(meta={'source_run':'fixture'}))
 try:
  with patch('clinical_core.agere.clinical.stack_runtime.Stack',return_value=stack),patch('clinical_core.agere.clinical.native_snapshot_cache.install'),patch('clinical_core.agere.clinical.native_snapshot_cache.prepare_vocabulary_index'),patch.object(service,'ThreadingHTTPServer',side_effect=server):service.serve(f.path,0,stack=f.path)
 finally:f.doCleanups()
def main():
 ctx=multiprocessing.get_context('spawn');q=ctx.Queue();p=ctx.Process(target=child,args=(q,));p.start()
 try:
  info=q.get(timeout=30);base='http://localhost:'+str(info['port'])
  def api(route,body=None,bearer=None):
   headers={'Content-Type':'application/json'}
   if bearer:headers['Authorization']=bearer
   with urlopen(Request(base+route,data=json.dumps(body).encode() if body is not None else None,headers=headers),timeout=60) as r:return json.load(r)
  before=api('/api/stack/work-state?id='+info['task_id']);rejected=0
  for credential in (None,info['worker']):
   try:api('/api/stack/reference-packet/prepare',info['request'],credential);raise AssertionError('Unscoped credential accepted')
   except HTTPError as e:assert e.code==403;rejected+=1
  follow=api('/api/stack/reference-packet/prepare',info['request'],info['reviewer']);assert api('/api/stack/reference-packet/prepare',info['request'],info['reviewer'])['idempotent_replay']
  answer=api('/api/stack/reference-packet/answer',{'followup_id':follow['id'],'answer_id':info['answer_id']});assert not answer['requirement_resolved']
  view=api('/api/stack/reference-packet/list',{'task_id':info['task_id']});assert view['followups'][0]['current'] and view['answers'][0]['current']
  original=api('/api/stack/reference-scope/list',{'target':info['target']});assert len(original['answers'])==1 and original['answers'][0]['id']==answer['question_answer_id']
  assert before==api('/api/stack/work-state?id='+info['task_id'])
  result={'status':'passed','scope':'Disposable synthetic engineering fixture; real HTTP authentication, scoped authorization, selection/packet dependencies and immutable stores. Source-context and machine-output fixtures; no clinical validation.',
   'followup':follow,'answer':answer,'view':view,'returned_to_original_question':True,'original_packet_unchanged':True,'credentials_rejected':rejected,'external_actions':0,'live_model_output_tested':False}
  assert info['reviewer'][7:] not in json.dumps(result)
  (OUT/'packet-http-fixture-verification.json').write_text(json.dumps(result,indent=2)+'\n');print(json.dumps({'status':'passed','credentials_rejected':rejected,'answer_returned_to_packet_and_question':True}))
 finally:p.terminate();p.join(timeout=10)
if __name__=='__main__':main()
