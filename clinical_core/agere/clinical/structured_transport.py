"""Lossless explicit structured-field transport, preserving historical contexts."""
from pathlib import Path
import copy,hashlib,json,os,re,tempfile
from ...contracts import digest
from . import record_context as original,reference_scope_work as scope,context_builder as context
VERSION='structured-source-transport/1'
TARGET_BYTES=3500
DECLARATION='Structured units may be fragments of one received record. The field json-pointer-map identifies a canonical JSON object mapping exact paths in the original structured unit to their complete values. Sibling fragments can occur in other passes. They are not independent confirmations. Narrative units are unchanged. Container structure and exact reconstruction are locally verified; clinical meaning is unvalidated.'

def identity():return digest([VERSION,TARGET_BYTES,hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),scope.identity(),original.identity()])
def pointer(path):return ''.join('/'+str(k).replace('~','~0').replace('/','~1') for k in path)
def partition(value,maximum=TARGET_BYTES):
 containers=[];parts=[]
 def visit(value,path):
  if len(path)>64:raise ValueError('Structured nesting exceeds transport depth')
  if len(context.encode(value))<=maximum or not isinstance(value,(dict,list)) or not value:
   parts.append({'path':path,'value':value});return
  if isinstance(value,dict):
   containers.append({'path':path,'kind':'object','keys':list(value)})
   for key in value:visit(value[key],path+[key])
  else:
   containers.append({'path':path,'kind':'array','length':len(value)})
   for i,item in enumerate(value):visit(item,path+[i])
 visit(value,[])
 return {'containers':containers,'parts':parts}
def restore(split):
 # Typed paths distinguish numeric object keys from array positions.
 containers={digest(c['path']):c for c in split['containers']};parts={digest(p['path']):p for p in split['parts']};used=set()
 if len(containers)!=len(split['containers']) or len(parts)!=len(split['parts']) or set(containers)&set(parts):raise ValueError('Duplicate/overlapping structured paths')
 def visit(path):
  key=digest(path);used.add(key)
  if key in parts:return parts[key]['value']
  if key not in containers:raise ValueError('Structured path missing')
  node=containers[key]
  if node['kind']=='object':return {k:visit(path+[k]) for k in node['keys']}
  if node['kind']=='array':return [visit(path+[i]) for i in range(node['length'])]
  raise ValueError('Invalid structured container')
 value=visit([])
 if used!=set(containers)|set(parts):raise ValueError('Unreachable structured paths')
 return value

def transform(parent):
 units=[];manifest=[]
 for unit in parent['model_context']['evidence']:
  if unit['kind']!='structured' or len(context.encode(unit['reported_fields']))<=TARGET_BYTES:
   units.append(copy.deepcopy(unit));continue
  split=partition(unit['reported_fields'])
  if context.encode(restore(split))!=context.encode(unit['reported_fields']):raise ValueError('Structured reconstruction mismatch')
  fragments=[];groups=[];pending=[]
  for part in split['parts']:
   trial={pointer(v['path']):v['value'] for v in pending+[part]}
   if pending and len(context.encode(trial))>TARGET_BYTES:groups.append(pending);pending=[]
   pending.append(part)
  if pending:groups.append(pending)
  for group in groups:
   fields={pointer(part['path']):copy.deepcopy(part['value']) for part in group}
   if len(fields)!=len(group):raise ValueError('Ambiguous structured paths')
   fragment={**copy.deepcopy(unit),'field':'json-pointer-map','reported_fields':fields,
    'structured_fragment':{'parent_unit_id':unit['id'],
      'paths':[{'path':part['path'],'json_pointer':pointer(part['path']),'value_hash':digest(part['value'])} for part in group],
      'parent_value_hash':digest(unit['reported_fields']),'map_hash':digest(fields),'fragments_in_parent':len(groups)}}
   fragment['id']=digest([VERSION,unit['id'],fragment['structured_fragment']]);units.append(fragment)
   fragments.append({'id':fragment['id'],**fragment['structured_fragment']})
  manifest.append({'parent_unit_id':unit['id'],'source_ref':unit['source_ref'],'parent_value_hash':digest(unit['reported_fields']),
   'containers':split['containers'],'fragments':fragments,'reconstructed_exactly':True})
 row=copy.deepcopy(parent);row.pop('id',None)
 row.update(schema=VERSION,identity=identity(),original_context_id=parent['id'],original_context_hash=digest(parent),structured_transport={'schema':VERSION,'original_units':len(parent['model_context']['evidence']),'transport_units':len(units),'parents':manifest,'lossless':True,'narrative_units_unchanged':True,'meaning_validated':False})
 row['model_context']['evidence']=units
 row['missing_evidence']['record_scope']['structured_transport']=DECLARATION
 row['model_context']['missing_evidence']=row['missing_evidence']
 packet=row['model_context'];header={**packet,'sources':{},'evidence':[]}
 inline={**packet,'sources':{},'evidence':[{k:v for k,v in u.items() if k!='source_ref'}|packet['sources'][u['source_ref']] for u in units]}
 row['budget'].update(used=len(context.encode(packet)),header_required=len(context.encode(header)),
   candidate_units=len(units),selected_units=len(units),protected_units=sum(bool(u['protected_reasons']) for u in units),
   inline_provenance_equivalent_bytes=len(context.encode(inline)))
 if row['budget']['used']>row['parameters']['max_bytes']:raise ValueError('Complete structured transport context exceeds declared context bound')
 row['id']=digest(row)
 return row

def location(path,key):
 if not isinstance(key,str) or not re.fullmatch('[a-f0-9]{64}',key):raise ValueError('Exact context ID required')
 return Path(path)/'structured-contexts'/(key+'.json')
def exists(path,key):return location(path,key).exists()
def create(path,original_id):
 parent=_original_verified(path,original_id)
 if parent.get('schema')==VERSION:raise ValueError('Context is already structured-field transport')
 row=transform(parent)
 if not row['structured_transport']['parents']:raise ValueError('No structured units need partitioning')
 if digest(_original_inspect(path,original_id))!=row['original_context_hash']:raise ValueError('Original context changed during structured transport')
 target=location(path,row['id']);target.parent.mkdir(exist_ok=True)
 # Unique temporary files make identical concurrent preparation safe.
 with tempfile.NamedTemporaryFile(mode='w',dir=target.parent,prefix='.transport-',delete=False) as stream:
  temporary=Path(stream.name);json.dump(row,stream,indent=2,allow_nan=False)
 try:os.replace(temporary,target)
 finally:temporary.unlink(missing_ok=True)
 return row

def inspect(path,key):
 row=json.loads(location(path,key).read_text());scope.verified(row)
 if row['identity']!=identity():raise ValueError('Structured transport contract changed')
 parent=_original_inspect(path,row['original_context_id'])
 if digest(parent)!=row['original_context_hash'] or transform(parent)!=row:raise ValueError('Structured source transport no longer matches original evidence')
 return row

def prepare_selection(path,request):
 scope.care.exact(request,'selection_id');base=_scope_inspect_selection(path,request['selection_id'])
 if base.get('schema')==VERSION:raise ValueError('Selection already uses structured transport')
 row=create(path,base['context_id'])
 result={**base,'schema':VERSION,'implementation':identity(),'original_selection_id':base['id'],'original_selection_hash':digest(base),
   'context_id':row['id'],'context_hash':digest(row),'structured_transport':row['structured_transport'],
   'status':'source_context_prepared','dispatch':row['dispatch'],'omissions':row['omissions']}
 result.pop('id')
 if _scope_inspect_selection(path,base['id'])!=base:raise ValueError('Reference selection changed during structured transport')
 return scope.save(path,'selections',base['target']['target'],result)
def inspect_selection(path,key):
 row=scope.load(path,'selections',key)
 if row.get('schema')!=VERSION:return _scope_inspect_selection(path,key)
 if row['implementation']!=identity():raise ValueError('Structured selection contract changed')
 if digest(_scope_inspect_selection(path,row['original_selection_id']))!=row['original_selection_hash']:raise ValueError('Original selection dependencies changed')
 if digest(inspect(path,row['context_id']))!=row['context_hash']:raise ValueError('Structured selection context changed')
 return row

_original_exists=original.exists
_original_inspect=original.inspect
_original_verified=original.verified
_scope_inspect_selection=scope.inspect_selection

def install():
 if getattr(original.inspect,'structured_transport',False):return
 def inspect_any(path,key):return inspect(path,key) if exists(path,key) else _original_inspect(path,key)
 def exists_any(path,key):return exists(path,key) or _original_exists(path,key)
 inspect_any.structured_transport=True;original.inspect=inspect_any;original.exists=exists_any
 scope.inspect_selection=inspect_selection
