"""Bind existing analytical receipts to the shared graph decision, never to eligibility.

Report outputs remain reports. Complete member evidence and unresolved inputs can
travel with the original question without becoming a cohort or a service need.
"""
from pathlib import Path
from collections import Counter
import hashlib,json,sqlite3,zlib,re
from ...contracts import digest
from . import measure_composition as M,financial_accounts as F,program_accountability as A,outcomes as O
from . import graph_criteria as C,program_documents as D,patient_input_index
from . import graph_observation as B

OPERATIONS=('measure_review','financial_accounts','accountability_review','outcome',B.OPERATION)
MODULES=dict(zip(OPERATIONS,(M,F,A,O,B)))
TITLES=dict(zip(OPERATIONS,('Documentary measure','Received financial accounts','Program accountability','Recorded outcomes','Record clocks and observation process')))


def identity():
    return digest([hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),M.identity(),F.identity(),A.identity(),O.code_identity(),B.identity()])


def key(op,prep,pid):
    return M.patient_id(prep,pid) if op=='measure_review' else MODULES[op].receipt_id(prep,pid)


def read(db,op,rid):
    table='patients' if op=='measure_review' else 'receipts'
    raw=db.execute('SELECT payload FROM '+table+' WHERE id=?',(rid,)).fetchone()
    if raw is None:raise ValueError('Analytical member receipt missing')
    return json.loads(zlib.decompress(raw[0]))


def status_value(op,row):
    if op==B.OPERATION:return row['status'],None
    if op=='measure_review':return row['status'],row['effective_numerator']
    if op=='outcome':return row['status'],row['recorded_label']
    if op=='accountability_review':return row['payment_readiness'],None
    status=('review_required' if row['unassigned_sources'] or any(a['status']!='reconciled_received_records' for a in row['accounts'])
            else 'reconciled_received_records' if row['accounts'] else 'no_received_accounts')
    return status,row['status_counts']


def attach(path,op,patients,parameters,prep,result,selection):
    ids=sorted(patients);db=MODULES[op].storage(path);refs=[]
    try:
        for pid in ids:
            rid=key(op,prep,pid);row=read(db,op,rid)
            if row['id']!=rid or row['patient_id']!=pid:raise ValueError('Analytical receipt patient mismatch')
            status,value=status_value(op,row)
            refs.append({'patient_id':pid,'receipt_id':rid,'receipt_digest':digest(row),'status':status,'value':value,
                'membership':O.membership(row,parameters['filter']) if op=='outcome' else None,
                'report_only':op!='outcome','care_account_counts':{},'review_topics':[]})
    finally:db.close()
    result.update(review_members=refs,input_membership=selection,population_digest=digest(ids),
                  member_status_counts=dict(Counter(r['status'] for r in refs)))
    return result


def dependencies(path,graph):
    nodes=[x for x in graph['results'] if x['node']['operation'] in OPERATIONS]
    if not nodes:return {}
    inputs=patient_input_index.snapshot(path)['patients'] if any(x['node']['operation']!=B.OPERATION for x in nodes) else {};result={}
    for item in nodes:
        node=item['node'];op=node['operation'];prep=MODULES[op].prepare(path,node['parameters'])
        result[node['id']]={'adapter':identity(),'patients':{m['patient_id']:{'receipt_id':key(op,prep,m['patient_id']),
            'raw_input_hash':prep['patient_sources'][m['patient_id']] if op==B.OPERATION else inputs.get(m['patient_id'],{}).get('protocol_hash')} for m in item['result']['review_members']}}
    return result


def summary(op,result):
    omitted={'review_members','patients','input_membership','processing_counts','financial_processing_counts','elapsed_seconds','input_fingerprints','cache_hit'}
    return {'title':TITLES[op],**{k:v for k,v in result.items() if k not in omitted}}


def source_refs(op,row,context):
    # The event ledger stores its primary hash beside, not inside, source.
    events=[{**e['source'],'record_hash':e['record_hash']} for e in row.get('evidence',{}).values()] if op=='outcome' else []
    return C.source_refs([row,context,*events])


def gaps(op,row,node):
    base={'node_id':node['id'],'receipt_id':row['id'],'request_id':None,'target_record_id':None,
          'parameters':node['parameters'],'requested_action':'review_versioned_analytical_input'}
    result=[]
    if op==B.OPERATION:
        result=[{**base,'status':'observation_process_unresolved','binding':q['id'],'question':q['question'],
            'conditional_effect':q['effect']} for q in B.QUESTIONS]
    elif op=='measure_review':
        for role in row['unresolved_components']:
            result.append({**base,'status':'measure_component_unresolved','binding':role,'component':row['components'][role],
                           'conditional_effect':'May change documentary denominator/numerator; no certified performance or payment inferred.'})
    elif op=='accountability_review':
        for req in row['requirements']:
            result.append({**base,'status':'accountability_input_requires_review','binding':req['id'],'scope':req['scope'],
                           'requirement':req,'conditional_effect':'Required for contract-specific assessment; not evidence of entitlement or attribution.'})
    elif op=='financial_accounts':
        for account in row['accounts']:
            if account['status']!='reconciled_received_records':
                result.append({**base,'status':'financial_account_requires_review','account_id':account['account_id'],
                    'source_record_ids':account['source_record_ids'],'account_status':account['status'],'next_step':account['next_step']})
        if row['unassigned_sources']:
            result.append({**base,'status':'financial_source_binding_unresolved','sources':row['unassigned_sources']})
    else:
        if row['recorded_label'] is None:
            result.append({**base,'status':'outcome_observation_unresolved','outcome_status':row['status'],
                'window':row['window'],'unresolved_endpoint_evidence':row['unresolved_endpoint_evidence']})
        if row['coverage']['status']=='unknown':
            result.append({**base,'status':'outcome_source_coverage_unknown','coverage':row['coverage'],
                'conditional_effect':'No matching received event is not proof of no event or complete follow-up.'})
    return result


def evidence(path,ref,node):
    op=node['operation'];pid=ref['patient_id'];prep=MODULES[op].prepare(path,node['parameters'])
    if key(op,prep,pid)!=ref['receipt_id']:raise ValueError('Analytical sources or specification changed')
    db=MODULES[op].storage(path)
    try:row=read(db,op,ref['receipt_id'])
    finally:db.close()
    if row['patient_id']!=pid or row['id']!=ref['receipt_id'] or digest(row)!=ref['receipt_digest']:
        raise ValueError('Analytical receipt integrity mismatch')
    context={}
    if op=='measure_review':
        docs={k:D.inspect(path,v['receipt_id']) for k,v in row['references'].items()}
        if any(digest(docs[k])!=v['receipt_hash'] or docs[k]['patient_id']!=pid for k,v in row['references'].items()):
            raise ValueError('Measure source receipt mismatch')
        context={'definition':node['parameters'],'source_receipts':docs}
    elif op=='accountability_review':
        finance=F.inspect(path,row['financial_receipt_id'])
        if finance['patient_id']!=pid or digest(finance)!=row['financial_receipt_hash']:raise ValueError('Accountability financial binding mismatch')
        context={'financial_evidence':finance}
    refs=source_refs(op,row,context)
    with sqlite3.connect((Path(path)/'stack.sqlite3').resolve().as_uri()+'?mode=ro',uri=True) as db:
        for rid,h in refs.items():
            raw=db.execute('SELECT patient,payload FROM records WHERE id=?',(rid,)).fetchone()
            if not raw or raw[0]!=pid or digest(json.loads(raw[1]))!=h:raise ValueError('Analytical source changed; rebuild affected analysis')
    finding={'node_id':node['id'],'operation':op,'receipt_id':row['id'],'receipt_digest':digest(row),
        'title':TITLES[op],'parameters':node['parameters'],'membership':ref['membership'],'report_only':ref['report_only'],
        'status':ref['status'],'value':ref['value'],'report':row,'report_context':context,'care_accounts':[],
        'review_topics':[],'authority':'observed_analytics_not_clinical_performance_or_attribution'}
    return finding,refs,gaps(op,row,node)


def financial_table(path,result,*,selector_contract=None,prepared=None):
    """Present complete cached accounts at the original source-record grain.

    Patient membership chooses input receipts; it does not select every account
    those patients own. A record-scoped input retains its original predicates,
    projects source identities, and binds them through the receipt's explicit
    account ownership. No accounting identity is re-computed or certified here.
    """
    from . import request_plan,semantic_analyst
    from .clinical_events import iso
    from sqlglot import parse_one,exp
    path=Path(path);mapping=F.mapping();prep=prepared or F.prepare(path,result['parameters'])
    members=result['review_members'];ids={m['patient_id']for m in members}
    if len(ids)!=len(members)or len(ids)!=result['patients_evaluated']:
        raise ValueError('Complete financial input membership required')
    selected=None;selection_query=None
    if selector_contract is not None:
        request_plan.assert_sealed(selector_contract)
        tree=parse_one(selector_contract['membership_query'],read='sqlite')
        source=(selector_contract.get('request')or{}).get('source',{}).get('table')
        origin=tree.args.get('from_').this if isinstance(tree,exp.Select)and tree.args.get('from_')else None
        catalog=request_plan.Catalog.load(path)
        if (not isinstance(origin,exp.Table)or origin.name.casefold()!=str(source).casefold()
            or not {'patient_id','record_id'}<=set(catalog.tables.get(source,[]))):
            raise ValueError('Record-scoped financial output requires the selected source record identities')
        alias=origin.alias_or_name
        tree.set('expressions',[exp.alias_(exp.column('patient_id',table=alias),'patient_id'),
                                exp.alias_(exp.column('record_id',table=alias),'record_id')])
        selection_query=tree.sql(dialect='sqlite')
        wh=semantic_analyst.SemanticWarehouse(path/'semantic.sqlite3')
        try:
            wh.define(prep['base']['snapshot']['as_of'])
            scope=wh.query(selection_query,as_of=prep['base']['snapshot']['as_of'],max_rows=None,max_chars=None)
            if scope.get('truncated'):raise ValueError('Incomplete financial source selection')
            selected={(r['patient_id'],r['record_id'])for r in scope['rows']}
        finally:wh.close()
        if any(pid not in ids or not isinstance(rid,str)or not rid for pid,rid in selected):
            raise ValueError('Financial source selector differs from the input population')
    rows=[];coverage=[];unassigned=[];bound=set();owners={};input_accounts=0
    db=F.storage(path)
    try:
        for member in members:
            pid=member['patient_id'];rid=member['receipt_id']
            if rid!=F.receipt_id(prep,pid):raise ValueError('Financial source or parameters changed')
            patient=read(db,'financial_accounts',rid)
            if patient['patient_id']!=pid or patient['id']!=rid or digest(patient)!=member['receipt_digest']:
                raise ValueError('Financial account receipt mismatch')
            coverage.append({'patient_id':pid,'receipt_id':rid,'status':member['status'],
                             'recorded_accounts':len(patient['accounts']),'unassigned_sources':len(patient['unassigned_sources'])})
            unassigned.extend({'patient_id':pid,'receipt_id':rid,**item}for item in patient['unassigned_sources'])
            for account in patient['accounts']:
                input_accounts+=1
                source_keys={(pid,x)for x in account['source_record_ids']}
                qualifying=source_keys if selected is None else source_keys&selected
                if selected is not None and not qualifying:continue
                if bound&qualifying:raise ValueError('Selected source has ambiguous financial account ownership')
                bound.update(qualifying)
                row={'patient_id':pid,'account_id':account['account_id'],'account_status':account['status'],
                     'currency':account['currency'],'receipt_id':rid,'receipt_digest':member['receipt_digest'],
                     **{'snapshot_'+field:(account['snapshot_amounts']or{}).get(field)for field in mapping['snapshot_amounts']},
                     'selected_snapshots':account['selected_snapshots'],'snapshot_count':account['snapshot_count'],
                     'source_count':account['source_count'],'equations':account['equations'],'issues':account['issues'],
                     'source_record_ids':account['source_record_ids'],
                     'qualifying_source_record_ids':sorted(x[1]for x in qualifying)if selected is not None else None,
                     'cash_comparisons':[{k:v for k,v in cash.items()if k not in ('included','excluded','unresolved')}
                         |{key+'_records':len(cash[key])for key in ('included','excluded','unresolved')}for cash in account['cash_comparisons']],
                     'first_reported_source_date':None,'last_reported_source_date':None,
                     'first_received_date':None,'last_received_date':None,'unknown_source_date_records':0,
                     'received_transactions':{t['stream']:{'by_currency':{},'unresolved_records':[],
                         'not_accepted_records':0,'unknown_event_date_records':0,'capture_complete':False}for t in mapping['transactions']}}
                index=len(rows);rows.append(row)
                for record in account['source_record_ids']:
                    if record in owners:raise ValueError('Financial source belongs to more than one presented account')
                    owners[record]=index
    finally:db.close()
    # These are observations needed for lifecycle/received-flow presentation,
    # not another execution of the financial source interpretation engine.
    seen=set();transactions={t['stream']:t for t in mapping['transactions']}
    with sqlite3.connect((path/'stack.sqlite3').resolve().as_uri()+'?mode=ro',uri=True)as db:
        for rid,pid,payload in db.execute('SELECT id,patient,payload FROM records WHERE id IN (SELECT value FROM json_each(?))',
                                         (json.dumps(list(owners)),)):
            row=rows[owners[rid]];raw=json.loads(payload);seen.add(rid)
            if pid!=row['patient_id']or raw['patient_id']!=pid:raise ValueError('Financial lifecycle source changed patient')
            for field,lo,hi in [('event_date','first_reported_source_date','last_reported_source_date'),
                                ('available_date','first_received_date','last_received_date')]:
                day=raw.get(field)
                if iso(day):
                    row[lo]=min(row[lo],day)if row[lo]else day;row[hi]=max(row[hi],day)if row[hi]else day
                elif field=='event_date':row['unknown_source_date_records']+=1
            rule=transactions.get(raw['source_table'])
            if rule is None:continue
            flow=row['received_transactions'][rule['stream']]
            if not iso(raw.get('event_date')):flow['unknown_event_date_records']+=1
            if rule['status_field']and raw.get(rule['status_field'])!=rule['accepted_status']:
                flow['not_accepted_records']+=1;continue
            currency=raw.get(mapping['currency_field'])
            if currency is None and rule['currency_from_account_if_unrecorded']:currency=row['currency']
            amount=raw.get(rule['amount_field']);reason=None
            if not isinstance(currency,str)or not re.fullmatch('[A-Z]{3}',currency):reason='unknown_transaction_currency'
            elif not F.integer(amount):reason='noninteger_or_missing_amount'
            elif amount<0 and not rule['allow_negative']:reason='unexpected_negative_amount'
            if reason:flow['unresolved_records'].append({'record_id':rid,'reason':reason});continue
            total=flow['by_currency'].setdefault(currency,{'known_received_cents':0,'records':0})
            total['known_received_cents']+=amount;total['records']+=1
    if seen!=set(owners):raise ValueError('Financial lifecycle source no longer received')
    unresolved=[{'patient_id':pid,'record_id':rid,'reason':'selected_source_has_no_unique_received_account'}
                for pid,rid in sorted((selected or set())-bound)]
    rows.sort(key=lambda r:(r['patient_id'],r['account_id']))
    scope={'schema':'financial-account-output-scope/1','input_patients':sorted(ids),'input_accounts':input_accounts,
           'presented_accounts':len(rows),'excluded_input_accounts':input_accounts-len(rows),
           'selection':'exact_source_record_ownership'if selected is not None else'all_accounts_for_input_patients',
           'selector_contract_id':selector_contract['id']if selector_contract else None,'selection_query':selection_query,
           'selected_source_records':len(selected)if selected is not None else None,'unresolved_selected_sources':unresolved,
           'entity_keys_digest':digest([(r['patient_id'],r['account_id'])for r in rows]),
           'patient_coverage':coverage,'unassigned_sources':unassigned,
           'source_map_digest':digest(mapping),'clinical_eligibility_established':False,
           'meaning':'Record predicates select account ownership; patient membership alone does not select accounts. Source dates are reported calendar dates, not established account opening/closing dates. Snapshot comparisons and all received transactions through available_by are separate; missing currency/dates and capture completeness remain unresolved.'}
    return {'rows':rows,'scope':scope}
