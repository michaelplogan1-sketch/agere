"""Versioned declarative program/measure compiler. No eval, prose execution or silent nulls."""
from datetime import date
from ..contracts import digest

OPS={'eq','ne','lt','le','gt','ge','in'}

def expression(node, facts):
    if not isinstance(node,dict) or len(node)!=1: raise ValueError('One operator per expression')
    op,value=next(iter(node.items()))
    if op in {'all','any'}:
        if not isinstance(value,list) or not value: raise ValueError('Empty predicate')
        results=[expression(x,facts) for x in value]
        if op=='all': return False if False in results else (None if None in results else True)
        return True if True in results else (None if None in results else False)
    if op=='not':
        result=expression(value,facts);return None if result is None else not result
    if op not in OPS or not isinstance(value,list) or len(value)!=2: raise ValueError('Unsupported predicate')
    key,want=value
    if key not in facts or facts[key] is None: return None
    got=facts[key]
    try:
        if op=='eq': return got==want
        if op=='ne': return got!=want
        if op=='lt': return got<want
        if op=='le': return got<=want
        if op=='gt': return got>want
        if op=='ge': return got>=want
        if op=='in': return got in want
    except TypeError: return None

def keys(node):
    op,value=next(iter(node.items()))
    if op in {'all','any'}: return set().union(*(keys(n) for n in value))
    if op=='not': return keys(value)
    return {value[0]}

class Program:
    def __init__(self,spec):
        for field in ('id','version','effective_start','effective_end','source','predicate','purpose'):
            if field not in spec: raise ValueError('Missing policy '+field)
        start=date.fromisoformat(spec['effective_start']);end=date.fromisoformat(spec['effective_end'])
        if end<start: raise ValueError('Reversed policy dates')
        expression(spec['predicate'],{})
        self.spec=spec;self.hash=digest(spec);self.inputs=keys(spec['predicate'])
    def evaluate(self,facts,as_of):
        date.fromisoformat(as_of)
        if not self.spec['effective_start']<=as_of<=self.spec['effective_end']:
            return {'status':'not_effective','policy_hash':self.hash,'missing':[]}
        missing=sorted(k for k in self.inputs if facts.get(k) is None)
        result=expression(self.spec['predicate'],facts)
        return {'status':'criteria_met' if result is True else 'criteria_not_met' if result is False else 'unknown',
                'missing':missing,'policy_hash':self.hash,'policy_id':self.spec['id'],
                'purpose':self.spec['purpose'],'source':self.spec['source']}

def contract_score(spec,sites):
    """Compute contract-defined dollars from numerator/denominator, never inferred savings."""
    out=[]
    for site in sites:
        rows=[]
        for m in spec['measures']:
            v=site.get('measures',{}).get(m['id'])
            if not v or v.get('denominator') is None or v.get('numerator') is None:
                rows.append({'measure':m['id'],'status':'missing_counts'});continue
            n,d=v['numerator'],v['denominator']
            if not 0<=n<=d or d<=0: raise ValueError('Invalid measure counts')
            rate=n/d;target=m['target'];direction=m['direction']
            if direction not in {'higher','lower'} or not 0<=target<=1: raise ValueError('Invalid contract rule')
            gap=max(0,target-rate) if direction=='higher' else max(0,rate-target)
            rows.append({'measure':m['id'],'status':'computed','rate':rate,'gap':gap,
                         'opportunities':int(__import__('math').ceil(gap*d-1e-10)),
                         'illustrative_contract_value_usd':gap*d*m['value_per_gap_closed_usd']})
        out.append({'site':site['site'],'measures':rows,'causal_savings_estimate':None})
    return out


def compile_document(document,spec,bindings):
    """Compile an extracted specification only when each referenced field has an exact source quote.

    Binding verifies quotation, not semantic equivalence. A model can propose this
    object; it cannot execute code or declare its own quotation semantically correct.
    """
    program=Program(spec)
    if not isinstance(document,str) or not document.strip():raise ValueError('Empty policy source')
    if set(bindings)!=program.inputs:raise ValueError('Every predicate input needs source grounding')
    for field,b in bindings.items():
        start,end=b.get('start'),b.get('end')
        if type(start)!=int or type(end)!=int or not 0<=start<end<=len(document):raise ValueError('Invalid clause span')
        if document[start:end]!=b.get('quote'):raise ValueError('Policy quote differs from document')
    return {'compiled_specification':spec,'policy_hash':program.hash,'document_hash':digest(document),
            'clause_bindings':bindings,'semantic_equivalence':'Requires rule-level verification; exact quotes alone are not proof',
            'base_model_retraining_required':False}
