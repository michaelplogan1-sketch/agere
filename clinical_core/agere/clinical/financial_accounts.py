"""Reconcile financial stocks and flows without summing repeated snapshots."""
from collections import Counter,defaultdict
from datetime import datetime,timezone
from pathlib import Path
import hashlib,json,re,sqlite3,time,zlib
from ...contracts import digest
from .clinical_events import iso
from . import patient_input_index as P

VERSION='financial-account-reconciliation/1'
LIMITS=[
 'Synthetic received-source reconciliation; no contract settlement, realized savings, collectability or hardship inference.',
 'Account snapshots, adjudication decisions and actual cash reports are different quantities. Snapshots are never summed across time.',
 'Cash comparisons use received transactions through the selected source timestamp, inclusive. Within-minute order is not established.',
 'A difference may reflect timing, missing records or inconsistent accounts. It is not established debt, fraud or lost revenue.',
 'Claims/remittances and source organization labels do not establish payment, provider attribution or program outcomes.',
 'The present snapshot is not a complete historical reconstruction of corrections and source capture.',
]


def mapping():
    m=json.loads(Path(__file__).with_name('financial_account_maps.json').read_text())
    if m['schema']!='financial-account-map/1':raise ValueError('Registered financial source map required')
    for rule in m['identities']:
        if any(k not in m['snapshot_amounts'] or type(v) is not int for k,v in rule['coefficients'].items()):raise ValueError('Integer mapped accounting identity required')
    return m


def identity():return digest([P.identity(),*[hashlib.sha256(Path(p).read_bytes()).hexdigest() for p in (__file__,Path(__file__).with_name('financial_account_maps.json'))]])


def validate(p,asof):
    if not isinstance(p,dict) or set(p)!={'available_by'} or not iso(p['available_by']) or p['available_by']>asof:raise ValueError('Explicit financial receipt cutoff required')
    return dict(p)


def time_key(r):
    day=r.get('event_date');value=r.get('event_time')
    if not iso(day):return None
    if value is None:return day,None
    if not isinstance(value,str):return None
    try:
        stamp=datetime.fromisoformat(value.replace('Z','+00:00'))
        if stamp.tzinfo is None or stamp.date().isoformat()!=day:return None
        utc=stamp.astimezone(timezone.utc)
        return utc.date().isoformat(),utc.isoformat()
    except (TypeError,ValueError):return None


def ref(r):
    return {'record_id':r['record_id'],'patient_id':r['patient_id'],'source_table':r['source_table'],
        'record_hash':digest(r),'source_date':r.get('event_date'),'source_time':r.get('event_time'),
        'arrival_date':r.get('available_date')}


def integer(v):return type(v) is int


def project(pid,records,cutoff,m=None):
    m=m or mapping();byid={};byaccount=defaultdict(list);bindings=defaultdict(list);unassigned=[];ignored=Counter()
    relevant={m['snapshot_stream'],*[t['stream'] for t in m['transactions']],*m['context_streams']}
    for r in records:
        if r['patient_id']!=pid or r.get('synthetic') is not True or r.get('identity_status')!='matched':raise ValueError('Matched same-patient synthetic finance sources required')
        if r['record_id'] in byid:raise ValueError('Duplicate source identity')
        byid[r['record_id']]=r
    for r in records:
        if r['source_table'] not in relevant:continue
        if r.get('_warehouse_source',{}).get('adapter')!=m['adapter']:unassigned.append({'reason':'unmapped_source_adapter','source':ref(r)});continue
        if not iso(r.get('available_date')):unassigned.append({'reason':'unknown_receipt_date','source':ref(r)});continue
        if r['available_date']>cutoff:ignored['not_received_by_cutoff']+=1;continue
        account=r.get(m['account_field'])
        if not isinstance(account,str) or not account:
            parents=[]
            for field in m['account_inheritance_links']:
                rid=r.get(field);parent=byid.get(rid) if isinstance(rid,str) else None
                if parent and parent['source_table'] in m['links'][field] and iso(parent.get('available_date')) and parent['available_date']<=cutoff and isinstance(parent.get(m['account_field']),str) and parent[m['account_field']]:
                    parents.append((field,parent))
            accounts={parent[m['account_field']] for field,parent in parents}
            if len(accounts)!=1:unassigned.append({'reason':'no_unique_explicit_account_binding','source':ref(r)});continue
            account=next(iter(accounts))
            bindings[account].extend({'origin':ref(r),'field':field,'target':ref(parent),'account_id':account} for field,parent in parents)
        byaccount[account].append(r)
    output=[]
    for account,rows in sorted(byaccount.items()):
        snapshots=[r for r in rows if r['source_table']==m['snapshot_stream']];issues=[];links=[]
        for r in rows:
            for field,streams in m['links'].items():
                if not r.get(field):continue
                target=byid.get(r[field]) if isinstance(r[field],str) else None;reason=None
                if not isinstance(r[field],str):reason='invalid_source_reference'
                elif target is None:reason='linked_record_not_received'
                elif not iso(target.get('available_date')) or target['available_date']>cutoff:reason='linked_record_unavailable_at_cutoff'
                elif streams and target['source_table'] not in streams:reason='linked_record_wrong_source_type'
                elif target.get(m['account_field']) and target[m['account_field']]!=account:reason='linked_record_different_account'
                if reason:issues.append({'reason':reason,'record_id':r['record_id'],'field':field,'target_record_id':r[field]})
                else:links.append({'origin':ref(r),'field':field,'target':ref(target)})
        dated=[r for r in snapshots if time_key(r) and time_key(r)[0]<=cutoff]
        current=[];currency=None;amounts=None;equations=[];cash=[]
        if len(dated)!=len(snapshots):issues.append({'reason':'unresolved_or_future_snapshot_clock','sources':[ref(r) for r in snapshots if r not in dated]})
        if dated:
            latest=max(time_key(r)[0] for r in dated);same=[r for r in dated if time_key(r)[0]==latest]
            if all(time_key(r)[1] for r in same):
                stamp=max(time_key(r)[1] for r in same);current=[r for r in same if time_key(r)[1]==stamp]
            else:current=same
            values=[{k:r.get(k) for k in [*m['snapshot_amounts'],m['currency_field'],'status','claim_id','care_record_id','organization_id']} for r in current]
            if len({digest(v) for v in values})!=1:issues.append({'reason':'conflicting_latest_snapshot'});current=[]
        if len(dated)!=len(snapshots):current=[]
        if len({r.get('organization_id') for r in snapshots})>1:
            issues.append({'reason':'snapshot_account_organization_conflict'});current=[]
        if current:
            r=current[0];currency=r.get(m['currency_field'])
            if not isinstance(currency,str) or not re.fullmatch('[A-Z]{3}',currency):issues.append({'reason':'unknown_snapshot_currency'});currency=None
            amounts={k:r.get(k) for k in m['snapshot_amounts']}
            if not all(integer(v) for v in amounts.values()):issues.append({'reason':'noninteger_or_missing_snapshot_amount'})
            for rule in m['identities']:
                valid=all(integer(amounts.get(k)) for k in rule['coefficients'])
                residual=sum(amounts[k]*v for k,v in rule['coefficients'].items()) if valid else None
                equations.append({'id':rule['id'],'coefficients':rule['coefficients'],'residual_cents':residual,
                    'status':'unresolved' if residual is None else 'balanced' if residual==0 else 'difference'})
            snapshot_time=time_key(r)
            for t in m['transactions']:
                included=[];excluded=[];unresolved=[]
                for tx in rows:
                    if tx['source_table']!=t['stream']:continue
                    if t['status_field'] and tx.get(t['status_field'])!=t['accepted_status']:
                        excluded.append({'source':ref(tx),'reason':'not_recorded_as_accepted_cash'});continue
                    tk=time_key(tx)
                    if tk is None:unresolved.append({'source':ref(tx),'reason':'unknown_transaction_clock'});continue
                    if tk[0]>snapshot_time[0] or (tk[0]==snapshot_time[0] and tk[1] and snapshot_time[1] and tk[1]>snapshot_time[1]):
                        excluded.append({'source':ref(tx),'reason':'after_selected_snapshot'});continue
                    if tk[0]==snapshot_time[0] and (not tk[1] or not snapshot_time[1]):
                        unresolved.append({'source':ref(tx),'reason':'same_day_order_unresolved'});continue
                    unit=tx.get(m['currency_field']);basis='explicit_transaction_currency'
                    if unit is None and t['currency_from_account_if_unrecorded']:
                        unit=currency;basis='inherited_from_explicit_account_binding'
                    if currency is None or unit!=currency:unresolved.append({'source':ref(tx),'reason':'transaction_currency_unresolved_or_different'});continue
                    if not integer(tx.get(t['amount_field'])):unresolved.append({'source':ref(tx),'reason':'noninteger_or_missing_transaction_amount'});continue
                    if tx[t['amount_field']]<0 and not t['allow_negative']:
                        unresolved.append({'source':ref(tx),'reason':'unexpected_negative_transaction_amount'});continue
                    included.append({'source':ref(tx),'field':t['amount_field'],'amount_cents':tx[t['amount_field']],
                        'currency':unit,'currency_basis':basis})
                total=sum(x['amount_cents'] for x in included);expected=amounts.get(t['snapshot_field'])
                delta=expected-total if integer(expected) else None
                status='unresolved' if unresolved or delta is None else 'agrees_with_received_transactions' if delta==0 else 'differs_from_received_transactions'
                cash.append({'stream':t['stream'],'snapshot_field':t['snapshot_field'],'snapshot_reported_cents':expected,
                    'received_transactions_cents':total,'difference_cents':delta,'status':status,'included':included,'excluded':excluded,'unresolved':unresolved,
                    'capture_complete':False})
        status='no_received_snapshot' if not snapshots else 'unresolved_snapshot' if not current else 'review_required' if issues or any(e['status']!='balanced' for e in equations) or any(c['status']!='agrees_with_received_transactions' for c in cash) else 'reconciled_received_records'
        output.append({'account_id':account,'status':status,'currency':currency,'snapshot_amounts':amounts,
            'selected_snapshots':[ref(r) for r in current],'snapshot_count':len(snapshots),'source_count':len(rows),
            'source_record_ids':[r['record_id'] for r in rows],'equations':equations,'cash_comparisons':cash,
            'links':links,'account_binding_evidence':bindings[account],'issues':issues,'documentary_context_counts':dict(Counter(r['source_table'] for r in rows if r['source_table'] in m['context_streams'])),
            'next_step':'Resolve missing or conflicting snapshot dates/timestamps before selecting a balance.' if status=='unresolved_snapshot' else 'Obtain the account snapshot associated with these received source reports.' if status=='no_received_snapshot' else 'Inspect source timing, account linkage and missing transaction reports before interpreting the difference.' if status!='reconciled_received_records' else 'Received amounts agree under this accounting contract; care, hardship and payment-contract outcomes remain unestablished.'})
    return {'schema':VERSION,'synthetic':True,'patient_id':pid,'available_by':cutoff,'accounts':output,
        'unassigned_sources':unassigned,'ignored_counts':dict(ignored),'status_counts':dict(Counter(r['status'] for r in output)),
        'clinical_action_authorized':False,'limits':LIMITS}


def prepare(path,p):
    meta=json.loads((Path(path)/'run.json').read_text())
    if meta.get('synthetic') is not True:raise ValueError('Synthetic warehouse required')
    p=validate(p,meta['snapshot']['as_of']);inputs=P.snapshot(path)
    base={'code_identity':identity(),'parameters':p,'snapshot':meta['snapshot'],'source_digest':meta['source_digest']}
    # Records hash only: unrelated machine-analysis changes do not rerun finance.
    patients={pid:row['baseline_components'][1] for pid,row in inputs['patients'].items()}
    return {'base':base,'patients':patients,'identity':digest([base,patients]),'processing':inputs['processing']}


def receipt_id(prep,pid):return digest([prep['base'],pid,prep['patients'][pid]])


def storage(path):
    db=sqlite3.connect(Path(path)/'financial-accounts.sqlite3',timeout=60)
    db.execute('CREATE TABLE IF NOT EXISTS receipts(id TEXT PRIMARY KEY,patient TEXT,payload BLOB)');return db


def execute(path,patient_ids,p,*,prepared=None,preview_limit=6):
    started=time.perf_counter();prep=prepared or prepare(path,p)
    if validate(p,prep['base']['snapshot']['as_of'])!=prep['base']['parameters']:raise ValueError('Financial preparation mismatch')
    ids=sorted(set(patient_ids))
    if set(ids)-set(prep['patients']):raise ValueError('Unknown financial patient')
    if type(preview_limit) is not int or not 0<=preview_limit<=20:raise ValueError('Bounded financial preview required')
    src=sqlite3.connect((Path(path)/'stack.sqlite3').resolve().as_uri()+'?mode=ro',uri=True);db=storage(path)
    counts=Counter();reuse=Counter();equations=Counter();cash=Counter();issue_counts=Counter();totals={};previews=[];unassigned=0
    try:
        src.execute('BEGIN')
        with db:
            for pid in ids:
                key=receipt_id(prep,pid);old=db.execute('SELECT payload FROM receipts WHERE id=?',(key,)).fetchone()
                if old:row=json.loads(zlib.decompress(old[0]));reuse['patient_cache_hits']+=1
                else:
                    raws=[r[0] for r in src.execute('SELECT payload FROM records WHERE patient=? ORDER BY id',(pid,))]
                    if digest([digest(r) for r in raws])!=prep['patients'][pid]:raise ValueError('Financial sources changed during assembly')
                    row=project(pid,[json.loads(r) for r in raws],p['available_by']);row.update(id=key,base=prep['base'])
                    db.execute('INSERT INTO receipts VALUES (?,?,?)',(key,pid,zlib.compress(json.dumps(row,allow_nan=False).encode())))
                    reuse['patients_computed']+=1;reuse['source_records_loaded']+=len(raws)
                counts.update(row['status_counts']);unassigned+=len(row['unassigned_sources'])
                for account in row['accounts']:
                    issue_counts.update({x['reason'] for x in account['issues']})
                    equations.update(e['id']+':'+e['status'] for e in account['equations'])
                    cash.update(e['stream']+':'+e['status'] for e in account['cash_comparisons'])
                    if account['currency'] and account['snapshot_amounts'] and all(integer(v) for v in account['snapshot_amounts'].values()):
                        total=totals.setdefault(account['currency'],Counter());total.update(account['snapshot_amounts']);total['accounts']+=1
                previews=sorted(previews+[{'id':key,'patient_id':pid,'accounts':len(row['accounts']),'status_counts':row['status_counts'],'unassigned_sources':len(row['unassigned_sources'])}],
                    key=lambda r:(not r['status_counts'].get('review_required'),-r['accounts'],r['patient_id']))[:preview_limit]
        return {'schema':VERSION,'synthetic':True,'parameters':p,'identity':prep['identity'],'patients_evaluated':len(ids),
            'accounts_evaluated':sum(counts.values()),'account_status_counts':dict(counts),'equation_status_counts':dict(equations),'cash_comparison_counts':dict(cash),
            'selected_snapshot_totals_by_currency':totals,'totals_meaning':'One unambiguous received snapshot per account with complete integer amounts; these are reported stocks, not summed cash flows or validated totals.',
            'accounts_with_issue_counts':dict(issue_counts),'issue_counts_overlap':True,'unassigned_source_count':unassigned,'patients':previews,'processing_counts':dict(reuse),'input_fingerprints':prep['processing'],
            'new_paid_api_calls':0,'clinical_action_authorized':False,'elapsed_seconds':time.perf_counter()-started,'limits':LIMITS}
    finally:src.close();db.close()


def inspect(path,key):
    if not isinstance(key,str) or not re.fullmatch('[a-f0-9]{64}',key):raise ValueError('Exact financial receipt required')
    db=storage(path)
    try:r=db.execute('SELECT payload FROM receipts WHERE id=?',(key,)).fetchone()
    finally:db.close()
    if not r:raise ValueError('Unknown financial receipt')
    row=json.loads(zlib.decompress(r[0]));prep=prepare(path,row['base']['parameters'])
    if key!=receipt_id(prep,row['patient_id']):raise ValueError('Financial evidence changed; recompute required')
    return row
