"""Explicit recovery using saved responses; unknown failures remain blocked."""
from . import availability_source_stage as A, source_coverage_recovery as U, citation_context_recovery as C
from . import source_synthesis as S, care_capabilities as care

def install():
 C.install();U.install()

def state(path,context_id):
 install()
 row,bundle,links,packets,key=A.prepare(path,context_id)
 if A.exists(path,key):return A.inspect(path,key),None
 _,_,_,_,recovered=U.prepare(path,context_id)
 if U.exists(path,recovered):return U.inspect(path,recovered),None
 # A saved failure is required. This endpoint is not an alternative way to
 # trigger fresh synthesis or evade a blocked/pending outbound request.
 progress=A.progress(path,context_id)
 if progress.get('status')!='incomplete' or progress.get('error') not in U.RECOVERABLE_ERRORS:
  raise ValueError('No supported saved source-coverage failure; original route and retry restrictions remain')
 recoverable=[i for i,(body,_) in enumerate(packets,1) if U.recoverable(path,context_id,body,packets,row)]
 if not recoverable:raise ValueError('Saved response cannot be recovered without guessing')
 return None,{'source_passes':len(packets),'recoverable_saved_source_passes':recoverable,
  'request_bytes':[A.passes.request_bytes(body) for body,_ in packets],
  'scope':'Omitted coverage becomes explicitly system-unassessed; all original citation checks still apply.'}

def preview(path,request):
 care.exact(request,'context_id');parent,source=state(path,request['context_id'])
 reconciliation=C.preview(path,parent['id']) if parent else None
 if parent and not reconciliation['saved_answer_available']:
  old=C.legacy.preview(path,parent['id'])
  # A recovered source stage may proceed to its first reconciliation. An old
  # completed stage needs a diagnosed local citation recovery, never a blind retry.
  if parent['schema']!=U.VERSION and not reconciliation['recoverable_failed_groups']:
   raise ValueError('No supported saved citation failure to recover')
 return {'schema':'interpretation-recovery/1','context_id':request['context_id'],'source_recovery':source,
  'reconciliation':reconciliation,'original_failed_calls_preserved':True,'new_paid_api_calls':0,
  'clinical_meaning_validated':False,'clinical_execution_authorized':False,
  'scope':'Reuse saved responses locally; only unfinished valid requests may call the existing provider. No failed request is resent.'}

def run(path,request,ledger,*,allow_live=False):
 preview(path,request);parent,source=state(path,request['context_id']);source_calls=0;source_tokens=0
 if source:
  parent=U.synthesize(path,request['context_id'],ledger,allow_live=allow_live)
  source_calls=parent['new_paid_api_calls'];source_tokens=parent['new_token_count_calls']
 result=C.synthesize(path,parent['id'],ledger,allow_live=allow_live)
 return {**result,'new_paid_api_calls':source_calls+result['new_paid_api_calls'],
  'new_token_count_calls':source_tokens+result['new_token_count_calls'],
  'recovery_performed':True,'failed_requests_resent':0}
