"""Typed documentary predicates over native source accounts, never care decisions.

The native adapter supplies source meanings and account identities. This projector
combines literal conditions on those accounts without collapsing different drugs,
referrals, billing accounts or documents into a single latest patient fact.
"""
from collections import defaultdict
from pathlib import Path
import hashlib,json,re,sqlite3
from ...contracts import digest
from . import warehouse_interpretation as native
from . import cohorts
from . import medication_accounts
from .cohorts import _condition,compare,truth_all,truth_any

VERSION='native-documentary-requirements/1'


def fields():
    result=defaultdict(set)
    for _,relation,names in native.STATEMENTS.values():result[relation].update(names)
    result[medication_accounts.RELATION].update(medication_accounts.FIELDS)
    return dict(result)


def validate(source):
    if not isinstance(source,dict) or set(source)!={'kind','relation','where','conditions','quantifier'} or source['kind']!='native_account':
        raise ValueError('Explicit native account relation, scope, conditions and quantifier required')
    allowed=fields().get(source['relation'])
    if not allowed or source['quantifier'] not in ('any','all'):raise ValueError('Unmapped native relation or quantifier')
    for name in ('where','conditions'):
        rows=source[name]
        minimum=0 if name=='where' else 1
        if not isinstance(rows,list) or not minimum<=len(rows)<=8:raise ValueError('Bounded native conditions required')
        for c in rows:
            if not isinstance(c,dict) or not isinstance(c.get('field'),str):raise ValueError('Literal field condition required')
            parts=c['field'].split('.')
            if not 1<=len(parts)<=4 or parts[0] not in allowed or any(not re.fullmatch('[A-Za-z][A-Za-z0-9_]*',s) for s in parts):
                raise ValueError('Unmapped native source field path')
            _condition(c,{c['field']})
    return source


def identity():
    return digest([VERSION,native.identity(),medication_accounts.identity(),cohorts.code_identity(),hashlib.sha256(Path(__file__).read_bytes()).hexdigest()])


def value_at(values,field):
    value=values
    for key in field.split('.'):
        if not isinstance(value,dict) or key not in value:return None
        value=value[key]
    return value


def conditions(statement,clauses):
    return truth_all([compare(value_at(statement['values'],c['field']),c['op'],c['value']) for c in clauses])


def project(report,source,*,patient_id=None):
    p=validate(source);pid=patient_id or (report or {}).get('patient_id')
    if not isinstance(pid,str):raise ValueError('Exact native patient identity required')
    if report is not None and (report.get('patient_id')!=pid or report.get('synthetic') is not True or report.get('schema')!=native.VERSION):raise ValueError('Same-patient synthetic account report required')
    statements={s['id']:s for s in (report or {}).get('statements',[])}
    if len(statements)!=len((report or {}).get('statements',[])):raise ValueError('Duplicate native statement identity')
    if any(s['patient_id']!=pid or any(e['patient_id']!=pid for e in s['evidence']) for s in statements.values()):
        raise ValueError('Cross-patient native source evidence')
    evaluated=[];selected=[];pending=[];excluded=[];values=[];steps=[]
    for state in (report or {}).get('states',[]):
        if state['relation']!=p['relation']:continue
        ids=state['selected_statement_ids']
        if any(i not in statements for i in ids):raise ValueError('Native account references a missing statement')
        rows=[statements[i] for i in ids]
        if any(r['relation']!=p['relation'] or r['entity']!=state['entity'] for r in rows):raise ValueError('Native account identity mismatch')
        # Scope all selected reports together. Never prefilter away a competing
        # same-date statement or an undated report to create a clean latest fact.
        scope_values=[conditions(r,p['where']) for r in rows]
        scope=False if scope_values and all(v is False for v in scope_values) else True if scope_values and all(v is True for v in scope_values) else None
        if scope is False:
            excluded.append({'entity':state['entity'],'reason':'selected_accounts_outside_literal_scope','statement_ids':ids});continue
        tests=[conditions(r,p['conditions']) for r in rows]
        result=(tests[0] if tests and all(v is tests[0] for v in tests) else None)
        if scope is None or state['status']!='documented_source_statement':result=None
        values.append(result)
        item={'entity':state['entity'],'account_status':state['status'],'selected_source_date':state['selected_source_date'],
              'statement_ids':ids,'prior_statement_ids':state['prior_statement_ids'],'scope':scope,'result':result,
              'source_dependencies':[x for r in rows for x in r['link_issues']],
              'missing_fields':sorted({c['field'] for r in rows for c in (*p['where'],*p['conditions']) if value_at(r['values'],c['field']) is None})}
        evaluated.append(item)
        if result is None:pending.append(item)
        for row in rows:
            selected.append({'id':row['id'],'patient_id':pid,'relation':row['relation'],'entity':row['entity'],
                'authority':'literal_native_source_account','evidence':{'value':row['values'],'fields':row['evidence']},
                'linked_sources':row['linked_sources'],'link_issues':row['link_issues'],'source_date':row['source_date'],
                'clinical_truth_established':False,
                **({'reconciliation':row['reconciliation']} if 'reconciliation' in row else {})})
    # Empty sets are unknown, including universal predicates. An absent account
    # is never evidence that care was absent or a request was completed.
    result=(truth_any(values) if p['quantifier']=='any' else truth_all(values)) if values else None
    if not values:steps.append('Obtain or map the requested source account; no matching account does not establish absent care.')
    if pending:steps.append('Resolve the marked account timing, competing values, literal scope or source links before treating those accounts as resolved.')
    if any(x['account_status']=='conflicting' for x in pending):steps.append('Reconcile competing accounts without majority voting or silently dropping a different value.')
    return {'schema':VERSION,'synthetic':True,'patient_id':pid,'parameters':p,
        'status':'documented' if result is not None else 'unresolved_interpretation',
        'value':'affirmed' if result is True else 'negated' if result is False else None,
        'predicate_result':result,'quantifier_scope':'Selected native source accounts; any/all does not quantify all actual care.',
        'account_results':evaluated,'selected_assertions':selected,'pending_accounts':pending,'excluded_accounts':excluded,
        'source_coverage':{'streams_without_records':[],'units_without_in_scope_assertions':0,
            'native_report_available':report is not None,'requested_account_observed':bool(evaluated),
            'care_capture_complete':False},'next_evidence_steps':steps,
        'current_clinical_state_established':False,'clinical_eligibility_established':False}


def snapshot(path,relations=None):
    from .analytic_registry import code_identity
    path=Path(path);meta=json.loads((path/'run.json').read_text());op=path/'operators.json'
    if meta.get('synthetic') is not True:raise ValueError('Synthetic source required')
    available=op.exists() and 'warehouse_interpretation' in json.loads(op.read_text()).get('operators',{})
    if available and json.loads(op.read_text())['code_identity']!=code_identity():raise ValueError('Native account source build is stale')
    hashes={}
    db=sqlite3.connect((path/'stack.sqlite3').resolve().as_uri()+'?mode=ro',uri=True)
    try:
        if available:
            hashes={pid:hashlib.sha256(raw.encode()).hexdigest() for pid,raw in db.execute("SELECT patient,payload FROM analyses WHERE operator='warehouse_interpretation' ORDER BY patient")}
    finally:db.close()
    medication=medication_accounts.snapshot(path) if medication_accounts.RELATION in (relations or []) else None
    base_hashes=dict(hashes)
    if medication:
        hashes={pid:digest([base_hashes.get(pid),medication['patient_hashes'].get(pid),medication['terminology_hash']])
                for pid in set(base_hashes)|set(medication['patient_hashes'])}
    return {'identity':digest([identity(),meta['source_digest'],meta['snapshot'],hashes]),
            'available':available,'patient_hashes':hashes,'base_patient_hashes':base_hashes,'medication':medication,
            'relations':sorted(set(relations or [])),
            'source_snapshot':meta['snapshot'],'source_digest':meta['source_digest']}


def load(db,pid,prepared):
    expected=prepared['patient_hashes'].get(pid)
    if expected is None:return None
    base=prepared.get('base_patient_hashes',prepared['patient_hashes']).get(pid)
    report=None
    if base:
        row=db.execute("SELECT payload FROM analyses WHERE operator='warehouse_interpretation' AND patient=?",(pid,)).fetchone()
        if row is None or hashlib.sha256(row[0].encode()).hexdigest()!=base:raise ValueError('Native source accounts changed during evaluation')
        report=json.loads(row[0])
    if prepared.get('medication'):
        items=medication_accounts.load(db,pid,prepared['medication'],prepared['source_snapshot']['as_of'])
        report=report or {'schema':native.VERSION,'synthetic':True,'patient_id':pid,'statements':[],'states':[]}
        report['statements']+=items['statements'];report['states']+=items['states']
    return report


def inspect(path,pid,expected_hash,relations=None):
    prepared=snapshot(path,relations)
    if prepared['patient_hashes'].get(pid)!=expected_hash:raise ValueError('Native source accounts changed during evaluation')
    db=sqlite3.connect((Path(path)/'stack.sqlite3').resolve().as_uri()+'?mode=ro',uri=True)
    try:
        report=load(db,pid,prepared) if expected_hash else None
        records={}
        for statement in (report or {}).get('statements',[]):
            for ev in [*statement['evidence'],*(link['target'] for link in statement['linked_sources'])]:
                if ev['record_id'] not in records:
                    row=db.execute('SELECT payload FROM records WHERE patient=? AND id=?',(pid,ev['record_id'])).fetchone()
                    if row is None:raise ValueError('Native evidence no longer received')
                    records[ev['record_id']]=json.loads(row[0])
                record=records[ev['record_id']]
                value=medication_accounts.value_at(record,ev['field_path']) if 'field_path' in ev else record.get(ev['field'])
                if digest(record)!=ev['record_hash'] or value!=ev['value']:raise ValueError('Native evidence field or hash changed')
        return report
    finally:db.close()
