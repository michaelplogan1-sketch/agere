"""Literal comparisons over exact received values, linked to original questions.

Equal source strings do not establish independent corroboration, authorship,
patient identity, therapeutic equivalence, clinical meaning or completed care.
"""
from pathlib import Path
import hashlib,json,math,sqlite3
from ...contracts import digest
from . import need_references as refs,evidence_dispositions as dispositions

VERSION='literal-source-comparison/1'
MAX_VALUE_BYTES=262144

def identity():
 return digest([VERSION,hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),refs.identity(),dispositions.identity()])

def scalar(value):
 if value is not None and type(value) not in (str,bool,int,float):raise ValueError('Received scalar field required')
 if type(value) is float and not math.isfinite(value):raise ValueError('Finite literal numeric value required')
 raw=json.dumps(value,ensure_ascii=False,allow_nan=False).encode()
 if len(raw)>MAX_VALUE_BYTES:raise ValueError('Complete field exceeds comparison byte bound; choose an exact source unit instead')
 return {'type':'null' if value is None else {str:'string',bool:'boolean',int:'integer',float:'number'}[type(value)],
         'value_hash':digest(value),'encoded_bytes':len(raw)}

def state(path,key):
 r=refs.inspect(path,key);records,_=refs.record_context.received(path,r['binding']['patient_id'])
 if digest(records)!=r['binding']['patient_source_fingerprint']:raise ValueError('Reference comparison sources changed')
 return r,{v['name']:v for v in r['references']},{v['record_id']:v for v in records}

def selected_record(names,records,name):
 if not isinstance(name,str) or name not in names:raise ValueError('Exact located reference name required')
 ref=names[name];record=records.get(ref['record_id'])
 if record is None or digest(record)!=ref['record_hash']:raise ValueError('Exact reference source changed')
 return ref,record

def preview(path,request):
 if not isinstance(request,dict) or set(request)!={'reference_id','reference_names'}:raise ValueError('Exact receipt and two selected references required')
 wanted=request['reference_names']
 if not isinstance(wanted,list) or len(wanted)!=2 or any(not isinstance(v,str) for v in wanted) or len(set(wanted))!=2:raise ValueError('Two distinct located reference names required')
 r,names,records=state(path,request['reference_id']);items=[]
 for name in wanted:
  ref,record=selected_record(names,records,name);fields=[]
  for field,value in sorted(dispositions.field_values(record).items()):
   try:meta=scalar(value)
   except ValueError as exc:
    fields.append({'field':field,'comparable':False,'reason':str(exc)});continue
   fields.append({'field':field,**meta,'comparable':True,'value_preview':value if meta['encoded_bytes']<=2000 else None,'preview_omitted':meta['encoded_bytes']>2000})
  unit=ref.get('source_unit')
  items.append({'reference_name':name,'record_id':ref['record_id'],'record_hash':ref['record_hash'],
     'default_field':ref['field'] if ref['kind']=='narrative' else None,'fields':fields,
     'source_unit_text_available':bool(unit and unit['kind']=='narrative'),'source_unit_field':unit['field'] if unit and unit['kind']=='narrative' else None})
 if refs.inspect(path,r['id'])!=r:raise ValueError('Reference sources changed during preview')
 return {'schema':VERSION,'reference_id':r['id'],'original_question':r['original_question'],'sources':items,'new_paid_api_calls':0,
         'scope':'Select exact scalar fields or submitted narrative units. No normalization, unit conversion or semantic inference.'}

def operand(names,records,selector):
 if not isinstance(selector,dict) or set(selector)!={'reference_name','scope','field'} or selector['scope'] not in ('received_field','source_unit') or not isinstance(selector['field'],str):raise ValueError('Exact received-field or source-unit selector required')
 ref,record=selected_record(names,records,selector['reference_name']);values=dispositions.field_values(record)
 field=selector['field']
 if field not in values:raise ValueError('Field not received or not allowed; missing is not a null value')
 value=values[field];span=None
 if selector['scope']=='source_unit':
  unit=ref.get('source_unit')
  if not unit or unit['kind']!='narrative' or field!=unit['field'] or type(value) is not str:raise ValueError('Exact narrative unit required')
  start,end=unit['start'],unit['end']
  if type(start) is not int or type(end) is not int or not 0<=start<end<=len(value) or value[start:end]!=unit['text'] or digest(unit)!=ref['unit_hash']:raise ValueError('Narrative unit does not match its received field span')
  span={'start':start,'end':end,'complete_received_field':start==0 and end==len(value)};value=unit['text']
 elif isinstance(value,str):span={'start':0,'end':len(value),'complete_received_field':True}
 return {'selector':selector,'record_id':record['record_id'],'record_hash':digest(record),'source_table':record['source_table'],
  'source_date':record.get('event_date'),'arrival_date':record.get('available_date'),'value':value,**scalar(value),'span':span}

def compare_values(left,right):
 """Type-preserving literal equality; difference windows never replace full values."""
 a,b=left['value'],right['value'];equal=type(a) is type(b) and a==b
 result={'literal_equal':equal,'same_value_type':type(a) is type(b),'observed_values_on_both_sides':a is not None and b is not None,
  'status':'null_value_present' if a is None or b is None else 'equal_literal_values' if equal else 'different_literal_values',
  'normalization_applied':False,'unit_conversion_applied':False}
 if type(a) is str and type(b) is str:
  prefix=0
  while prefix<min(len(a),len(b)) and a[prefix]==b[prefix]:prefix+=1
  suffix=0
  while suffix<min(len(a),len(b))-prefix and a[len(a)-1-suffix]==b[len(b)-1-suffix]:suffix+=1
  windows=[]
  if not equal:
   for text in (a,b):
    start=max(0,prefix-60);end=min(len(text),prefix+160)
    windows.append({'start':start,'end':end,'text':text[start:end],'omitted_before':start,'omitted_after':len(text)-end})
  result['text']={'left_characters':len(a),'right_characters':len(b),'common_prefix_characters':prefix,'common_suffix_characters':suffix,
     'difference_ranges':None if equal else {'left':[prefix,len(a)-suffix],'right':[prefix,len(b)-suffix]},
     'first_difference_windows':windows,'offset_unit':'Unicode code points','complete_values_compared':True}
 return result

def database(path):
 db=sqlite3.connect(Path(path)/'reference-comparisons.sqlite3',timeout=30)
 db.executescript('''CREATE TABLE IF NOT EXISTS comparisons(id TEXT PRIMARY KEY,receipt TEXT,need TEXT,payload TEXT NOT NULL);
 CREATE INDEX IF NOT EXISTS comparison_question ON comparisons(receipt,need);
 CREATE TRIGGER IF NOT EXISTS immutable_comparison_update BEFORE UPDATE ON comparisons BEGIN SELECT RAISE(ABORT,'Literal comparisons are immutable'); END;
 CREATE TRIGGER IF NOT EXISTS immutable_comparison_delete BEFORE DELETE ON comparisons BEGIN SELECT RAISE(ABORT,'Literal comparisons are immutable'); END;''')
 return db

def prepare(path,request):
 if not isinstance(request,dict) or set(request)!={'reference_id','left','right'}:raise ValueError('Exact reference receipt and two scalar selectors required')
 r,names,records=state(path,request['reference_id']);left=operand(names,records,request['left']);right=operand(names,records,request['right'])
 same=left['record_id']==right['record_id'] and left['selector']['field']==right['selector']['field'] and left['span']==right['span']
 result={'schema':VERSION,'implementation':identity(),'reference_id':r['id'],'reference_hash':digest(r),'binding':r['binding'],
   'original_question':r['original_question'],'left':left,'right':right,'comparison':compare_values(left,right),'same_source_location':same,
   'original_unresolved_mentions':r['unresolved_mentions'],'question_resolved':False,'question_premise_endorsed':False,
   'clinical_execution_authorized':False,'human_review_recorded':False,'new_paid_api_calls':0,
   'scope':'Only the selected literal values are compared. Equality does not establish authorship, independent corroboration, patient identity, clinical equivalence or resolution of the original question. Null is a reported null, not a known clinical value.'}
 if refs.inspect(path,r['id'])!=r:raise ValueError('Reference sources changed during comparison')
 result['id']=digest(result);db=database(path)
 try:
  with db:db.execute('INSERT OR IGNORE INTO comparisons VALUES (?,?,?,?)',(result['id'],r['binding']['receipt_id'],r['binding']['need_id'],json.dumps(result,allow_nan=False)))
  if json.loads(db.execute('SELECT payload FROM comparisons WHERE id=?',(result['id'],)).fetchone()[0])!=result:raise ValueError('Literal comparison identity collision')
 finally:db.close()
 return result

def verify(path,r):
 if digest({k:v for k,v in r.items() if k!='id'})!=r['id'] or r['implementation']!=identity():raise ValueError('Literal comparison identity changed')
 if digest(refs.inspect(path,r['reference_id']))!=r['reference_hash']:raise ValueError('Literal comparison sources changed')
 return r

def listing(path,request):
 refs.evidence_answers.target(request);db=database(path)
 try:rows=[json.loads(v[0]) for v in db.execute('SELECT payload FROM comparisons WHERE receipt=? AND need=? ORDER BY rowid',(request['receipt_id'],request['need_id']))]
 finally:db.close()
 if not rows:refs.candidates.input_state(path,**request)
 result=[]
 for row in rows:
  errors=[]
  try:verify(path,row)
  except (ValueError,KeyError,FileNotFoundError) as exc:errors.append(str(exc))
  result.append({**row,'current':not errors,'stale_reasons':errors})
 return {'schema':VERSION,**request,'comparisons':result,'question_resolved':False,'new_paid_api_calls':0}
