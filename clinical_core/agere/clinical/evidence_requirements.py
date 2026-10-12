"""Reusable, versioned documentary decision contracts over complete populations.

Adapter-specific concepts live in JSON; the evaluator knows no disease or payer.
The current catalog supplies internal review contracts, never clinical eligibility.
"""
from collections import Counter, defaultdict
from pathlib import Path
import hashlib
import json
import re
import sqlite3
import time
import zlib

from ...contracts import digest
from . import interpretation_state as interpretation, source_state, native_requirements

VERSION = 'evidence-requirement-composition/1'
CATALOG = Path(__file__).with_name('documentary_contracts.json')
FILTERS = ('all','supported','contradicted','unresolved')
LIMITS = [
    'These versioned contracts establish documentary review conditions, not disease, treatment, program eligibility or payment entitlement.',
    'Unreviewed interpretations, unavailable sources and unresolved dates remain unknown; they are not negative findings.',
    'Binding influence is a conditional logic calculation, not a probability, clinical benefit or monetary value of information.',
    'Source completeness is not established. A satisfied contract applies only to its declared documentary inputs and clocks.',
]


def logic(expr, values):
    if isinstance(expr,str): return values[expr]
    op, children=next(iter(expr.items()))
    if op=='not':
        value=logic(children,values)
        return None if value is None else not value
    results=[logic(e,values) for e in children]
    if op=='all': return False if False in results else None if None in results else True
    return True if True in results else None if None in results else False


def validate_expression(expr, names, depth=0):
    if depth>6: raise ValueError('Bounded requirement expression required')
    if isinstance(expr,str):
        if expr not in names: raise ValueError('Unknown requirement binding')
        return {expr}
    if not isinstance(expr,dict) or len(expr)!=1: raise ValueError('One explicit logical operator required')
    op, children=next(iter(expr.items()))
    if op=='not': return validate_expression(children,names,depth+1)
    if op not in ('all','any') or not isinstance(children,list) or not 1<=len(children)<=20:
        raise ValueError('Bounded all/any/not contract expression required')
    return set().union(*(validate_expression(c,names,depth+1) for c in children))


def validate_definition(spec):
    if not isinstance(spec,dict) or set(spec)!={'id','version','title','purpose','bindings','expression','supported_next_step'}:
        raise ValueError('Exact documentary contract definition required')
    for key in ('id','version','title','purpose','supported_next_step'):
        if not isinstance(spec[key],str) or not 1<=len(spec[key])<=1500: raise ValueError('Bounded contract '+key+' required')
    if not re.fullmatch('[a-z][a-z0-9_]{0,63}',spec['id']): raise ValueError('Contract identifier required')
    bindings=spec['bindings']
    if not isinstance(bindings,dict) or not 1<=len(bindings)<=20: raise ValueError('Bounded contract bindings required')
    for name,b in bindings.items():
        if not re.fullmatch('[a-z][a-z0-9_]{0,63}',name): raise ValueError('Binding identifier required')
        if set(b)!={'label','source','expected'} or not isinstance(b['label'],str) or not 1<=len(b['label'])<=500:
            raise ValueError('Explicit source binding required')
        if b['expected'] not in ('affirmed','negated'): raise ValueError('Explicit observed polarity required')
        if is_native(b):
            native_requirements.validate(b['source'])
        elif not isinstance(b['source'],dict) or set(b['source'])!={'streams','concept','relation','clock'}:
            raise ValueError('Exact binding source fields required')
        else:source_state.validate({**b['source'],'filter':'all'})
    if validate_expression(spec['expression'],set(bindings))!=set(bindings): raise ValueError('Unused contract bindings')
    return spec


def definitions():
    data=json.loads(CATALOG.read_text())
    if data.get('schema')!=VERSION or data.get('authority')!='internal_documentary_review':
        raise ValueError('Unsupported contract catalog authority')
    result={}
    for raw in data['contracts']:
        spec=validate_definition(raw)
        if spec['id'] in result: raise ValueError('Duplicate contract')
        result[spec['id']]={**spec,'authority':data['authority'],'hash':digest(spec)}
    return result


def definition(key):
    if not isinstance(key,str) or key not in definitions(): raise ValueError('Unknown documentary contract')
    return definitions()[key]


def validate(p):
    if not isinstance(p,dict) or set(p)!={'contract','filter'} or p['filter'] not in FILTERS:
        raise ValueError('Registered contract and documentary result filter required')
    definition(p['contract']); return dict(p)


def code_identity():
    return digest([VERSION,hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
                   hashlib.sha256(CATALOG.read_bytes()).hexdigest(),source_state.code_identity(),native_requirements.identity()])


def is_native(binding):
    return isinstance(binding.get('source'),dict) and binding['source'].get('kind')=='native_account'


def scope(spec):
    bindings=[b for b in spec['bindings'].values() if not is_native(b)]
    streams=sorted({s for b in bindings for s in b['source']['streams']})
    concepts=sorted({b['source']['concept'] for b in bindings})
    return {'streams':streams,'concepts':concepts,'filter':'all'}


def snapshot(path,p):
    spec=definition(validate(p)['contract'])
    snap=interpretation.snapshot(path,scope(spec)) if any(not is_native(b) for b in spec['bindings'].values()) else None
    relations=[b['source']['relation'] for b in spec['bindings'].values() if is_native(b)]
    native=native_requirements.snapshot(path,relations) if relations else None
    return {'identity':digest([code_identity(),spec['hash'],snap['identity'] if snap else None,native['identity'] if native else None]),
            'contract_hash':spec['hash'],'interpretation':snap,'native':native}


def receipt_id(snap,pid,*,code=None,interpretation_code=None):
    underlying=(source_state.receipt_id(snap['interpretation'],pid,interpretation_code=interpretation_code)
                if snap['interpretation'] else None)
    native=snap.get('native')
    native_source=[native['source_digest'],native['source_snapshot'],native['patient_hashes'].get(pid)] if native else None
    # Whole-population snapshot changes do not invalidate unrelated patients.
    return digest([code or code_identity(),snap['contract_hash'],pid,underlying,native_source])


def scoped_receipt(receipt, binding):
    """A union acquisition pass must not let an undeclared stream satisfy a binding."""
    source=binding['source']; streams=set(source['streams']); concept=source['concept']
    result=dict(receipt)
    result['effective_assertions']=[a for a in receipt['effective_assertions']
        if a['source']['source_table'] in streams and a['concept']==concept]
    result['source_groups']=[g for g in receipt['source_groups'] if g['target']['concept']==concept and
        receipt['sources'].get(g['target']['record_id'],{}).get('source_table') in streams]
    # Stale occurrences lack a verified source; conservatively retain them.
    present={s['source_table'] for s in receipt['sources'].values() if s['source_table'] in streams}
    result['coverage']={**receipt['coverage'],'streams_without_records':sorted(streams-present),
        'coverage_scope':'Unmapped-unit counts are conservative union-scope diagnostics, not binding-specific recall.'}
    return result


def evaluate(receipt,spec,*,native_report=None,patient_id=None,as_of=None):
    pid=receipt['patient_id'] if receipt else patient_id
    if receipt and patient_id is not None and pid!=patient_id:raise ValueError('Requirement patient mismatch')
    rows={};values={}
    for name,b in spec['bindings'].items():
        if is_native(b):state=native_requirements.project(native_report,b['source'],patient_id=pid)
        else:
            if receipt is None:raise ValueError('Required interpretation receipt missing')
            state=source_state.project(scoped_receipt(receipt,b),{**b['source'],'filter':'all'})
        value=(state['value']==b['expected']) if state['status']=='documented' else None
        values[name]=value
        rows[name]={'label':b['label'],'expected':b['expected'],'result':value,'state':state}
    value=logic(spec['expression'],values)
    status='supported' if value is True else 'contradicted' if value is False else 'unresolved'
    acquisition=[]
    for name,v in values.items():
        if v is not None: continue
        counterfactuals={str(x).lower():logic(spec['expression'],{**values,name:x}) for x in (False,True)}
        acquisition.append({'binding':name,'label':rows[name]['label'],
            'if_binding_resolved':counterfactuals,
            'can_resolve_alone':value is None and any(v is not None for v in counterfactuals.values()),
            'missing_streams':rows[name]['state']['source_coverage']['streams_without_records'],
            'steps':rows[name]['state']['next_evidence_steps'],
            'pending_accounts':rows[name]['state'].get('pending_accounts',[])})
    return {'schema':VERSION,'synthetic':True,'patient_id':pid,'as_of':receipt['as_of'] if receipt else as_of,
            'contract':spec,'status':status,'value':value,'bindings':rows,
            'interpretation_receipt_id':receipt['id'] if receipt else None,'acquisition':acquisition,
            'next_step':spec['supported_next_step'] if value is True else
                'Resolve the listed evidence requirements.' if value is None else
                'The declared documentary condition is contradicted; inspect the supporting accounts.',
            'clinical_eligibility_established':False,'limits':LIMITS}


def storage(path):
    db=sqlite3.connect(Path(path)/'evidence-requirements.sqlite3',timeout=30)
    db.execute('CREATE TABLE IF NOT EXISTS receipts(id TEXT PRIMARY KEY,patient TEXT NOT NULL,payload BLOB NOT NULL)')
    return db


def execute(path,patient_ids,parameters,*,prepared=None,preview_limit=6):
    started=time.perf_counter();p=validate(parameters);spec=definition(p['contract']);ids=sorted(set(patient_ids))
    if type(preview_limit) is not int or not 0<=preview_limit<=20: raise ValueError('Bounded requirement preview required')
    snap=prepared or snapshot(path,p)
    if snap['contract_hash']!=spec['hash'] or (snap['interpretation'] and snap['interpretation']['parameters']!=scope(spec)): raise ValueError('Requirement snapshot mismatch')
    base={'counts':{}};source=None;native_db=None
    if snap['interpretation']:
        _,base=interpretation.execute(path,ids,scope(spec),prepared=snap['interpretation'],preview_limit=0)
        source=interpretation.storage(path)
    if snap.get('native'):
        native_db=sqlite3.connect((Path(path)/'stack.sqlite3').resolve().as_uri()+'?mode=ro',uri=True)
        found={r[0] for r in native_db.execute('SELECT id FROM patients')}
        if set(ids)-found:
            native_db.close()
            if source:source.close()
            raise ValueError('Unknown native patient identity')
    out=storage(path);yes=set();unknown=set();statuses=Counter();counts=Counter();priority=defaultdict(Counter);preview=[];code=code_identity()
    interpretation_code=interpretation.code_identity()
    try:
        with out:
            for pid in ids:
                underlying=source_state.receipt_id(snap['interpretation'],pid,interpretation_code=interpretation_code) if snap['interpretation'] else None
                key=receipt_id(snap,pid,code=code,interpretation_code=interpretation_code);raw=out.execute('SELECT payload FROM receipts WHERE id=?',(key,)).fetchone()
                if raw: row=json.loads(zlib.decompress(raw[0]));counts['patient_cache_hits']+=1
                else:
                    receipt=None
                    if underlying:
                        raw=source.execute('SELECT payload FROM receipts WHERE id=?',(underlying,)).fetchone()
                        if raw is None: raise ValueError('Required interpretation receipt missing')
                        receipt=json.loads(zlib.decompress(raw[0]))
                    report=native_requirements.load(native_db,pid,snap['native']) if native_db else None
                    row=evaluate(receipt,spec,native_report=report,patient_id=pid,
                                 as_of=snap['native']['source_snapshot']['as_of'] if native_db else None)
                    row.update(id=key,code_identity=code)
                    if native_db:row['native_source']={'patient_hash':snap['native']['patient_hashes'].get(pid),
                        'snapshot':snap['native']['source_snapshot'],'source_digest':snap['native']['source_digest'],
                        'relations':snap['native']['relations']}
                    out.execute('INSERT INTO receipts VALUES (?,?,?)',(key,pid,zlib.compress(json.dumps(row,allow_nan=False).encode())))
                    counts['patient_contracts_computed']+=1
                statuses[row['status']]+=1
                for a in row['acquisition']:
                    priority[a['binding']]['patients_with_unresolved_binding']+=1
                    priority[a['binding']]['decisions_resolvable_by_this_binding_alone']+=a['can_resolve_alone']
                if p['filter']=='all' or row['status']==p['filter']: yes.add(pid);membership='matched'
                elif row['status']=='unresolved' and p['filter']!='unresolved': unknown.add(pid);membership='unknown'
                else: membership='not_matched'
                candidate={k:row[k] for k in ('id','patient_id','status','value','next_step')}
                candidate['membership']=membership
                preview=sorted(preview+[candidate],key=lambda r:(r['membership']!='matched',r['patient_id']))[:preview_limit]
        if snapshot(path,p)['identity']!=snap['identity']: raise ValueError('Contract or source review changed during evaluation; rerun')
    finally:
        if source:source.close()
        if native_db:native_db.close()
        out.close()
    return yes,unknown,{'schema':VERSION,'synthetic':True,'contract':spec,'identity':snap['identity'],
        'patients_evaluated':len(ids),'population_digest':digest(ids),'status_counts':dict(statuses),
        'processing_counts':dict(counts),'interpretation_processing_counts':base['counts'],
        'acquisition_priority':[{'binding':k,'label':spec['bindings'][k]['label'],**dict(v)} for k,v in sorted(priority.items(),key=lambda kv:(-kv[1]['decisions_resolvable_by_this_binding_alone'],kv[0]))],
        'patients':preview,'all_patient_receipts_persisted':True,'new_paid_api_calls':0,
        'elapsed_seconds':time.perf_counter()-started,'clinical_eligibility_established':False,'limits':LIMITS}


def inspect(path,key):
    if not isinstance(key,str) or not re.fullmatch('[a-f0-9]{64}',key): raise ValueError('Exact requirement receipt ID required')
    db=storage(path)
    try: raw=db.execute('SELECT payload FROM receipts WHERE id=?',(key,)).fetchone()
    finally: db.close()
    if raw is None: raise ValueError('Unknown requirement receipt')
    row=json.loads(zlib.decompress(raw[0]))
    if row['code_identity']!=code_identity() or row['contract']['hash']!=definition(row['contract']['id'])['hash']:
        raise ValueError('Requirement contract changed; rebuild')
    if row['interpretation_receipt_id']:interpretation.inspect(path,row['interpretation_receipt_id'])
    if row.get('native_source'):
        saved=row['native_source'];snap=native_requirements.snapshot(path,saved.get('relations'))
        if (snap['source_snapshot']!=saved['snapshot'] or snap['source_digest']!=saved['source_digest'] or
            snap['patient_hashes'].get(row['patient_id'])!=saved['patient_hash']):raise ValueError('Native source changed; rebuild requirement')
        native_requirements.inspect(path,row['patient_id'],saved['patient_hash'],saved.get('relations'))
    return row
