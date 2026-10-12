"""Selected component mappings preserve unrelated option and delivery identities."""
from contextvars import ContextVar
from functools import wraps
from pathlib import Path
import hashlib,json
from ...contracts import digest
from . import care_option_delivery as D,supplemental_options as S
VERSION='option-component-registry/1'
CATALOG=Path(__file__).with_name('functional_delivery_components.json')
_SELECTED=ContextVar('selected_component_mapping',default=None)

def identity():return digest([VERSION,hashlib.sha256(Path(__file__).read_bytes()).hexdigest()])

def mapping(c):
 marker=c['option_evidence'].get('registry_entry')
 if not marker or marker.get('implementation')!=S.identity():return None
 entries=[b for b in S.bundles() if b['reference']['registry_entry']==marker]
 if len(entries)!=1:raise ValueError('Exact supplemental evidence bundle unavailable')
 data=json.loads(CATALOG.read_text())
 if data['schema']!=VERSION:raise ValueError('Versioned selected component mappings required')
 found=[m for m in data['mappings'] if m['bundle_key']==marker['key'] and m['bundle_version']==marker['version'] and m['reference_doi']==c['reference']['citation']['doi'] and m['option_evidence_key']==c['option_evidence']['key']]
 if len(found)!=1:raise ValueError('No unique component mapping for this selected evidence bundle')
 m=found[0];keys=[x['key'] for x in m['components']];alternatives={a['key'] for a in c['alternatives']}
 if len(keys)!=len(set(keys)) or set(m['alternatives'])!=alternatives:raise ValueError('Distinct components and complete alternative coverage required')
 for cs in m['alternatives'].values():
  if not cs or len(cs)!=len(set(cs)) or not set(cs)<=set(keys):raise ValueError('Nonempty distinct known components required')
 for component in m['components']:
  if not component['label'] or not component['checks']:raise ValueError('Labeled component review checks required')
 return {**m,'registry_identity':identity(),'selected_bundle':marker}

def install():
 S.install()
 if getattr(D.snapshot,'component_registry',False):return
 original_inputs=D.inputs;original_identity=D.identity;original_snapshot=D.snapshot
 def inputs(path,key):
  c=D.X.comparison(path,key);m=mapping(c)
  return (c,m,D.C.care.inspect_plan(path,c['plan_id'])) if m is not None else original_inputs(path,key)
 def selected_identity():
  m=_SELECTED.get()
  return digest([original_identity(),identity(),m]) if m is not None else original_identity()
 @wraps(original_snapshot)
 def snapshot(path,request):
  D.C.care.exact(request,'comparison_id alternative_key component_bindings coordination_note')
  c=D.X.comparison(path,request['comparison_id']);m=mapping(c);token=_SELECTED.set(m)
  try:return original_snapshot(path,request)
  finally:_SELECTED.reset(token)
 snapshot.component_registry=True
 D.inputs=inputs;D.identity=selected_identity;D.snapshot=snapshot
