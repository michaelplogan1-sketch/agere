"""Reuse bounded documentary logic to explain unresolved quoted-rule inputs.

Repeated requirement bindings share one source variable. Hypothetical assignments
are logical conditions, never predicted values or clinical acquisition priorities.
"""
from pathlib import Path
import hashlib
from ...contracts import digest
from ..policy import expression
from . import program_documents as D,evidence_resolution as E,evidence_requirements as R

VERSION='quoted-program-resolution/1'
LIMITS=['Internal source/specification review proposals; no requests are sent.',
 'Hypothetical values are not predictions, missing-care findings or estimates of clinical benefit.',
 'Completing a request does not establish its input value. Different source contracts may have additional dependencies not represented in these logical cases.',
 'An unreviewed mapping remains unverified even when the quoted predicate resolves.']


def identity():return digest([VERSION,hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),D.code_identity(),E.identity()])


def negate(x):return x['not'] if isinstance(x,dict) and set(x)=={'not'} else {'not':x}


class UnsupportedDomain(ValueError):pass


def boolean_expression(predicate,inputs):
 op,args=next(iter(predicate.items()))
 if op in ('all','any'):return {op:[boolean_expression(x,inputs) for x in args]}
 if op=='not':return negate(boolean_expression(args,inputs))
 name,want=args;value=inputs[name]
 if op in ('eq','ne') and type(want) is bool:
  return value if (want is True)==(op=='eq') else negate(value)
 if op=='in' and all(type(v) is bool for v in want):
  choices=[value if v else negate(value) for v in sorted(set(want))]
  return choices[0] if len(choices)==1 else {'any':choices}
 raise UnsupportedDomain('Conditional numeric/string comparison is not implemented; input values remain unresolved.')


def source_variables(package,row):
 variables={};values={};inputs={};groups={}
 for name,b in package['input_bindings'].items():
  if b.get('value_type','boolean')!='boolean':raise UnsupportedDomain('Conditional numeric/string domain is not implemented.')
  observed=row['inputs'][name]
  if b['kind']=='requirement':
   source={'kind':'requirement','contract':b['contract'],'version':b['version'],'hash':b['contract_hash']};value=observed['evidence']['value']
  elif b['kind']=='protocol':source={'kind':'protocol','protocol':b['protocol'],'hash':b['protocol_hash'],'field':b['field']};value=observed['value']
  else:source={'kind':'unbound','package':package['id'],'input':name};value=None
  key='v_'+digest(source);variables.setdefault(key,{'source':source,'input_names':[]})['input_names'].append(name)
  if key in values and values[key] is not value:raise ValueError('Shared source has inconsistent input values')
  values[key]=value;groups[name]=key
  if b['kind']=='requirement' and b['expected']=='contradicted':inputs[name]=negate(key)
  elif b['kind']=='requirement' and b['expected']=='known':inputs[name]={'any':[key,negate(key)]}
  else:inputs[name]=key
  if R.logic(inputs[name],values) is not observed['value']:raise ValueError('Quoted input differs from its source requirement')
 return variables,values,inputs,groups


def project(package,row):
 if row.get('schema')!=D.VERSION or row.get('synthetic') is not True or row['package_id']!=package['id']:raise ValueError('Matching synthetic quoted-rule receipt required')
 inputs={k:v['value'] for k,v in row['inputs'].items()};variables={};groups={};unsupported=None
 blocked=row['evaluation']['status'] in ('not_effective','semantic_changes_requested','document_not_received_by_cutoff')
 try:
  variables,values,expressions,groups=source_variables(package,row)
  logical=boolean_expression(package['spec']['predicate'],expressions)
  try:R.validate_expression(logical,set(values))
  except ValueError as exc:raise UnsupportedDomain('Expanded conditional expression exceeds the existing bounded resolver.') from exc
  resolution=E.conditional_sets(logical,values)
  if resolution['current_value'] is not expression(package['spec']['predicate'],inputs):raise ValueError('Quoted rule and conditional logic disagree')
  for case in resolution['cases']:
   facts={name:R.logic(expr,{**values,**case['binding_values']}) for name,expr in expressions.items()}
   if expression(package['spec']['predicate'],facts) is not case['contract_value']:raise ValueError('Conditional witness differs from the Program interpreter')
   case['resulting_input_values']=facts
 except UnsupportedDomain as exc:
  unsupported=str(exc);resolution={'complete':False,'cases':[],'status':'unsupported_conditional_domain','current_value':expression(package['spec']['predicate'],inputs),'reason':unsupported}
 if blocked:
  resolution={'complete':False,'cases':[],'status':'specification_blocked','current_value':None,'reason':row['evaluation']['status']}
 active_variables={key for c in resolution['cases'] for key in c['binding_values']}
 requests={};binding_requests={};upstream={}
 def add(scope,kind,detail,instruction,names=(),source_request=None):
  key=digest([VERSION,package['id'],row['patient_id'] if scope=='patient' else None,kind,detail])
  request=requests.setdefault(key,{'id':key,'scope':scope,'patient_id':row['patient_id'] if scope=='patient' else None,'package':package['id'],
   'kind':kind,'detail':detail,'instruction':instruction,'input_names':[],'source_request':source_request,'request_sent':False,'clinical_execution_authorized':False})
  for name in names:
   if name not in request['input_names']:request['input_names'].append(name)
   binding_requests.setdefault(name,set()).add(key)
  return key
 if row['review']['status']!='mapping_confirmed':
  add('specification','review_quoted_mapping',{'review':row['review']},'Review the source clauses, completeness and semantic mapping. Record a correction or a user-reported review; this does not establish clinical authority.')
 if blocked:add('specification','resolve_specification_blocker',{'status':row['evaluation']['status'],'effective_start':package['spec']['effective_start'],'effective_end':package['spec']['effective_end'],'received_on':package['provenance']['received_on']},'Resolve the specification version, source availability or requested mapping correction before using this simulation. Do not alter the historical package.')
 if unsupported and not blocked:add('specification','extend_conditional_domain',{'reason':unsupported},'Implement and qualify a typed conditional resolver for this domain; do not invent values or assume independent comparisons.')
 for name,b in package['input_bindings'].items():
  observed=row['inputs'][name]
  if observed['value'] is not None:continue
  relevant=groups.get(name) in active_variables
  if b['kind']=='unbound':
   add('specification','bind_rule_input',{'input':name,'value_type':b['value_type'],'reason':b['reason'],'quote':package['bindings'][name]},
    'Map this quoted criterion to a versioned source requirement or protocol, or document that it cannot be supported. A mapping does not supply a patient value.',[name])
  elif relevant and b['kind']=='requirement':
   native=E.project(observed['evidence']);upstream[name]=native
   for need in native['requests']:
    add('patient',need['kind'],{'requirement':observed['requirement_reference'],'source_detail':need['detail']},need['instruction'],[name],need)
  elif relevant:
   receipt=observed['evidence']
   add('patient','resolve_protocol_input',{'protocol':b['protocol'],'field':b['field'],'receipt_id':observed['protocol_receipt_id'],'receipt_hash':observed['receipt_hash'],
    'missing_inputs':receipt['missing_inputs'],'unimplemented_clinical_requirements':receipt.get('unimplemented_clinical_requirements',[])},
    'Review the pinned protocol sources, missing inputs and unimplemented criteria; no absence is converted to a negative.',[name])
  if relevant and not binding_requests.get(name):
   add('patient','clarify_mapped_input',{'input':name,'status':observed['status'],'quote':package['bindings'][name]},'Clarify the received input or its interpretation against the declared source contract.',[name])
 for case in resolution['cases']:
  names={name for key in case['binding_values'] for name in variables[key]['input_names']}
  case['request_ids']=sorted({k for name in names for k in binding_requests.get(name,())})
  case['source_values']=case.pop('binding_values');case['program_value']=case.pop('contract_value')
 for request in requests.values():
  relevant_cases=[c for c in resolution['cases'] if request['id'] in c['request_ids']]
  request['relevant_to_unresolved_predicate']=bool(relevant_cases)
  request['input_can_resolve_rule_alone']=any(len(c['source_values'])==1 for c in relevant_cases)
 return {'schema':VERSION,'synthetic':True,'patient_id':row['patient_id'],'package':package['id'],'program_receipt_id':row['id'],
  'program_receipt_hash':digest(row),'status':row['status'],'resolution':resolution,'variables':variables,'upstream_resolution':upstream,
  'requests':sorted(requests.values(),key=lambda r:(r['scope'],r['kind'],r['id'])),'limits':LIMITS,'clinical_eligibility_established':False}


def inspect(path,key):
 row=D.inspect(path,key)
 return project(D.load_package(path,row['package_id']),row)
