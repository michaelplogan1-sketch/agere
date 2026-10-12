"""Conditional evidence sets and concrete source-review requests.

This is bounded Boolean reasoning over documentary contracts, not a model of
care, information availability, clinical benefit or missingness probability.
"""
import hashlib
from pathlib import Path
from ...contracts import digest
from . import evidence_requirements as requirements

VERSION='evidence-resolution/1'


def identity():
    return digest([VERSION,hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),requirements.code_identity()])


class ResolutionBudgetExceeded(ValueError):pass


def conditional_sets(expression,values,*,maximum_cases=256,maximum_operations=40000):
    """Inclusion-minimal sufficient assignments under three-valued logic.

    Shared bindings stay shared: incompatible assignments never join. Superset
    pruning is logical implication only; it does not rank acquisition cost.
    If the bound is exceeded, return no cases and explicitly report incompleteness.
    """
    requirements.validate_expression(expression,set(values))
    if any(v is not None and type(v) is not bool for v in values.values()):raise ValueError('Boolean or unknown binding values required')
    if type(maximum_cases) is not int or not 1<=maximum_cases<=256 or type(maximum_operations) is not int or not 1<=maximum_operations<=40000:
        raise ValueError('Bounded resolution computation required')
    operations=0
    def tick():
        nonlocal operations
        operations+=1
        if operations>maximum_operations:raise ResolutionBudgetExceeded()
    def prune(rows):
        kept=[]
        for row in sorted(set(rows),key=lambda r:(len(r),sorted(r))):
            if not any(existing<=row for existing in kept):kept.append(row)
            tick()
            if len(kept)>maximum_cases:raise ResolutionBudgetExceeded()
        return kept
    def join(left,right):
        rows=[]
        for a in left:
            for b in right:
                tick();merged=a|b
                if len(dict(merged))==len(merged):rows.append(merged)
        return prune(rows)
    def solve(expr,target):
        if isinstance(expr,str):
            value=values[expr]
            return [frozenset([(expr,target)])] if value is None else [frozenset()] if value is target else []
        op,children=next(iter(expr.items()))
        if op=='not':return solve(children,not target)
        conjunctive=(op=='all') is target
        result=[frozenset()] if conjunctive else []
        for child in children:
            rows=solve(child,target)
            result=join(result,rows) if conjunctive else prune([*result,*rows])
        return result
    current=requirements.logic(expression,values)
    base={'schema':VERSION,'current_value':current,'unknown_bindings':sorted(k for k,v in values.items() if v is None),
          'meaning':'Conditional binding assignments sufficient to resolve this documentary expression; outcomes are hypothetical, not predicted.',
          'probability_or_clinical_benefit_estimated':False}
    try:
        cases=[]
        for target in (False,True):
            for row in solve(expression,target):
                assignments=dict(sorted(row))
                # Independent evaluation of every generated witness.
                if requirements.logic(expression,{**values,**assignments}) is not target:raise ValueError('Invalid evidence-resolution witness')
                cases.append({'contract_value':target,'binding_values':assignments,'unresolved_bindings_to_resolve':len(assignments)})
        return {**base,'complete':True,'cases':sorted(cases,key=lambda r:(r['unresolved_bindings_to_resolve'],r['contract_value'],sorted(r['binding_values'].items()))),
                'status':'already_resolved' if current is not None else 'conditional_evidence_sets','operations':operations}
    except ResolutionBudgetExceeded:
        return {**base,'complete':False,'cases':[],'status':'expression_budget_exceeded','operations':operations}


def project(row):
    if row.get('schema')!=requirements.VERSION or row.get('synthetic') is not True:raise ValueError('Synthetic documentary receipt required')
    spec=row['contract'];requests={};binding_requests={}
    def add(name,kind,detail,instruction):
        binding=spec['bindings'][name]
        key=digest([VERSION,row['patient_id'],binding['source'],kind,detail])
        item=requests.setdefault(key,{'id':key,'patient_id':row['patient_id'],'kind':kind,'detail':detail,'instruction':instruction,
            'source_contract':binding['source'],'bindings':[],'binding_impacts':[],'requirement_receipt_id':row['id'],
            'can_resolve_contract_alone':False,
            'required_to_resolve_contract':row['value'] is None,
            'request_sent':False,'clinical_execution_authorized':False})
        if name not in item['bindings']:item['bindings'].append(name)
        binding_requests.setdefault(name,[]).append(key)
    for need in row['acquisition']:
        name=need['binding'];state=row['bindings'][name]['state'];source=spec['bindings'][name]['source']
        if requirements.is_native(spec['bindings'][name]):
            if not state['source_coverage']['requested_account_observed']:
                add(name,'locate_source_account',{'relation':source['relation'],'literal_scope':source['where'],
                    'native_report_available':state['source_coverage']['native_report_available']},
                    'Locate or map the requested source account. Its absence here does not establish absent care.')
            for account in state['pending_accounts']:
                base={k:account[k] for k in ('entity','statement_ids','account_status')}
                for dependency in account['source_dependencies']:
                    add(name,'resolve_source_reference',{**base,'dependency':dependency},
                        'Verify this referenced record, its patient, source type and availability; retrieve it if not received.')
                for field in account['missing_fields']:
                    add(name,'clarify_literal_field',{**base,'field':field},'Obtain or clarify this literal field; do not replace an unknown value with false.')
                if account['account_status']=='undated':
                    add(name,'verify_statement_date',base,'Establish the source statement date; arrival time cannot choose a clinically current account.')
                if account['account_status']=='conflicting':
                    add(name,'reconcile_competing_accounts',base,'Review the competing accounts and their provenance without majority voting.')
                if account['scope'] is None:
                    add(name,'clarify_account_scope',{**base,'literal_scope':source['where']},'Resolve which requested source entity these accounts describe.')
                if not account['source_dependencies'] and not account['missing_fields'] and account['account_status']=='documented_source_statement' and account['scope'] is True:
                    add(name,'interpret_literal_predicate',{**base,'conditions':source['conditions']},'Clarify the literal value and its type; the declared predicate is unresolved.')
        else:
            for stream in need['missing_streams']:
                add(name,'locate_received_source',{'stream':stream},'Locate the declared source or document its capture limitations; no received row does not establish no care.')
            for pending in state.get('pending_assertions',[]):
                add(name,'review_source_interpretation',{'assertion':pending,'clock':source['clock']},
                    'Review the source wording, subject, polarity, relation and declared clock against the requested binding.')
        if not binding_requests.get(name):
            add(name,'clarify_source_state',{'status':state['status'],'steps':need['steps']},'Resolve the listed source interpretation, date, conflict or coverage requirement.')
        for key in set(binding_requests[name]):
            requests[key]['binding_impacts'].append({'binding':name,'if_binding_resolved':need['if_binding_resolved']})
            requests[key]['can_resolve_contract_alone']|=need['can_resolve_alone']
    resolution=conditional_sets(spec['expression'],{name:b['result'] for name,b in row['bindings'].items()})
    for case in resolution['cases']:
        case['request_ids']=sorted({key for name in case['binding_values'] for key in binding_requests.get(name,[])})
    return {'schema':VERSION,'synthetic':True,'patient_id':row['patient_id'],'requirement_receipt_id':row['id'],
        'contract_id':spec['id'],'contract_hash':spec['hash'],'source_as_of':row['as_of'],'code_identity':identity(),
        'resolution':resolution,'requests':sorted(requests.values(),key=lambda r:(not r['can_resolve_contract_alone'],r['kind'],r['id'])),
        'limits':['Requests are internal evidence-review proposals; none were sent.',
                  'Several requests can support one binding; completing a request does not establish its truth.',
                  'Minimal sets describe logic, not care priority, acquisition cost or an optimal intervention.',
                  'Clinical eligibility, source completeness and actual care are not established.']}


def inspect(path,key):
    return project(requirements.inspect(path,key))
