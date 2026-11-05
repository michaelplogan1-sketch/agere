"""Independent received-row arithmetic for the fixed ordinary-question study.

This study tool is never imported by the answerer. It reads the existing semantic
snapshot with plain SQLite and aggregates in Python, without product planners,
metric macros, model judgments or answers from either arm. It tests arithmetic
over that received representation, not the correctness of source normalization.
"""
import argparse
from collections import Counter,defaultdict
from datetime import date,datetime,timezone
import hashlib
import json
from pathlib import Path
import sqlite3

ROOT=Path(__file__).resolve().parents[1]
RUNTIME=ROOT/'artifacts/agere-full-stack-rerun-2026-10-30/focused/runtime/stack'
CURRICULUM=ROOT/'config/agere/ordinary_question_curriculum.json'


def fingerprint(value):
    return hashlib.sha256(json.dumps(value,sort_keys=True,separators=(',',':'),allow_nan=False).encode()).hexdigest()


def day(value):
    try:return date.fromisoformat(value[:10])
    except (ValueError,TypeError):return None


def in_year(value,year):
    d=day(value)
    return d is not None and d.year==year


def ordered(rows,keys):
    return sorted(rows,key=lambda row:json.dumps([row[k]for k in keys],sort_keys=True))


def q01(events,spans):
    """One discharge, one payer-set group; overlapping payers never fan out."""
    by_patient=defaultdict(list)
    for span in spans:by_patient[span['patient_id']].append(span)
    counts=Counter();diagnostics=Counter()
    for event in events:
        end=day(event.get('end_date'))
        if event.get('kind')!='inpatient'or end is None or end.year not in(2028,2029):continue
        active=[s for s in by_patient[event['patient_id']]if day(s.get('coverage_start'))is not None
            and day(s['coverage_start'])<=end and(s.get('coverage_end')is None or day(s['coverage_end'])is not None and end<=day(s['coverage_end']))]
        payers=tuple(sorted({s['payer_id']for s in active if s.get('payer_id')}))
        unknown_payer=any(not s.get('payer_id')for s in active)
        status='unknown'if not active or not payers else'conflicting_or_multiple'if len(payers)>1 or unknown_payer else'received'
        counts[(payers,status,end.year)]+=1;diagnostics[status]+=1
    groups=sorted({(payers,status)for payers,status,year in counts})
    rows=[]
    for payers,status in groups:
        prior=counts[payers,status,2028];current=counts[payers,status,2029]
        rows.append({'payer_ids':list(payers),'payer_assignment':status,'discharges_2028':prior,
            'discharges_2029':current,'absolute_change':current-prior,
            'percentage_change':100*(current-prior)/prior if prior else None})
    return {'rows':rows,'totals':{'discharges_2028':sum(n for(*_,year),n in counts.items()if year==2028),
        'discharges_2029':sum(n for(*_,year),n in counts.items()if year==2029)},'payer_assignment_counts':dict(diagnostics),
        'definition':'Unified received inpatient utilization events, discharge-year scope. Enrollment intervals inclusive at discharge; no present-survival or present-enrollment filter. A missing span stays unknown, not excluded. Multiple payer assignments remain one explicit payer-set group, not duplicated discharges. Percent undefined for zero prior count.'}


def sums(rows,field):
    values=[r[field]for r in rows if r.get(field)is not None]
    return {'observed_sum_cents':sum(values),'received_values':len(values),'missing_values':len(rows)-len(values)}


def q02(claims,allocations):
    """Amounts are different lifecycles, with explicit cash-cohort attribution."""
    by_id={r['claim_id']:r for r in claims}
    cohort=[r for r in claims if day(r.get('service_date'))is not None
            and date(2029,1,1)<=day(r['service_date'])<date(2029,7,1)]
    by_payer=defaultdict(list)
    for row in cohort:by_payer[row.get('payer')].append(row)
    rows=[]
    for payer,group in by_payer.items():
        row={'payer':payer,'claims':len(group)}
        for field in('allowed_cents','net_payer_paid_cents','net_payer_cash_cents'):
            for key,value in sums(group,field).items():row[field+'__'+key]=value
        rows.append(row)
    ids={r['claim_id']for r in cohort};cash=defaultdict(list)
    for allocation in allocations:
        cid=allocation.get('claim_id')
        key='service_cohort'if cid in ids else'other_received_service_cohort'if cid in by_id else'unlinked_claim'
        cash[key].append({'value':allocation.get('allocated_cents')})
    return {'rows':ordered(rows,['payer']),
        'cash_allocation_attribution':{k:{'allocation_records':len(v),**sums(v,'value')}for k,v in sorted(cash.items())},
        'totals':{'claims':len(cohort),**{field:sums(cohort,field)for field in('allowed_cents','net_payer_paid_cents','net_payer_cash_cents')}},
        'definition':'Unified claim relation, H1-2029 service dates, current received amounts through snapshot. Allowed/adjudicated net paid are not cash. Cash allocations are separately received posting events; allocations for other or unknown claims do not belong to this service cohort. NULL is a missing amount, not observed zero. Source-normalization and financial/clinical truth remain separate from arithmetic reconciliation.'}


def los_summary(values):
    received=[v for v in values if v is not None]
    return {'received_n':len(received),'missing_n':len(values)-len(received),
        'zero_n':sum(v==0 for v in received),'negative_n':sum(v<0 for v in received),
        'sum_days':sum(received),'min_days':min(received)if received else None,
        'max_days':max(received)if received else None,'mean_days':sum(received)/len(received)if received else None}


def q03(stays):
    groups=defaultdict(list)
    for row in stays:
        if row.get('type')=='inpatient'and in_year(row.get('admit_date'),2029):groups[row.get('organization_id')].append(row)
    rows=[];all_rows=[]
    for org,group in groups.items():
        received=[r for r in group if r.get('discharge_date')is not None]
        def metrics(records):
            recorded=[];elapsed=[]
            for r in records:
                start,end=day(r.get('admit_date')),day(r.get('discharge_date'))
                recorded.append(r.get('los_days')if start and end else None)
                elapsed.append((end-start).days if start and end else None)
            return {'recorded_los_days':los_summary(recorded),'calendar_date_difference':los_summary(elapsed)}
        row={'organization_id':org,'admissions':len(group),'discharge_received':len(received),
             'discharge_absent':len(group)-len(received),**metrics(received)}
        rows.append(row);all_rows.extend(group)
    received=[r for r in all_rows if r.get('discharge_date')is not None]
    return {'rows':ordered(rows,['organization_id']),
        'totals':{'admissions':len(all_rows),'discharge_received':len(received),'discharge_absent':len(all_rows)-len(received),
            'recorded_los_days':los_summary([r.get('los_days')if day(r.get('admit_date'))and day(r.get('discharge_date'))else None for r in received]),
            'calendar_date_difference':los_summary([(day(r['discharge_date'])-day(r['admit_date'])).days if day(r.get('admit_date'))and day(r.get('discharge_date'))else None for r in received])},
        'definition':'Received stay rows, type=inpatient, 2029 admission. Both recorded los_days and calendar discharge-minus-admit differences are independently reconciled. The unchanged question does not uniquely select one: score a correctly identified measure against its own reference. A silent switch of definitions fails semantic scoring. Missing dates never become zero. Absent discharges are outside observed-LOS summaries.'}


def q15(appointments):
    counts=Counter();providers=set();unknown=Counter()
    for r in appointments:
        if not in_year(r.get('scheduled_date'),2029):continue
        provider=r.get('clinician_id');month=r['scheduled_date'][:7];outcome=r.get('outcome')
        providers.add(provider);counts[provider,month,outcome]+=1
        if outcome not in('kept','cancelled','no_show','scheduled'):unknown[outcome]+=1
    groups={(p,m)for p,m,outcome in counts};rows=[]
    for p,m in groups:
        row={'clinician_id':p,'month':m,'appointments':sum(n for(p2,m2,_),n in counts.items()if p2==p and m2==m)}
        for outcome in('kept','cancelled','no_show','scheduled'):
            row[outcome]=counts[p,m,outcome]
        row['other_or_unknown']=row['appointments']-sum(row[k]for k in('kept','cancelled','no_show','scheduled'))
        rows.append(row)
    return {'rows':ordered(rows,['clinician_id','month']),
        'totals':{'appointments':sum(counts.values()),'providers_including_unknown':len(providers),
            **{o:sum(n for(*_,outcome),n in counts.items()if outcome==o)for o in('kept','cancelled','no_show','scheduled')},
            'other_or_unknown':sum(unknown.values())},
        'definition':'One received appointment at its scheduled month, grouped by received clinician_id, including NULL. Installed outcome resolves historical versions: raw final_status=booked can have outcome kept/no_show/cancelled, and is not automatically scheduled. All received appointment records are counted once; unknown outcomes remain separate. Counts describe observed variation, not causes.'}


def read_rows(db,query):return [dict(r)for r in db.execute(query)]


def prepare(case,source):
    """Only simple source projections run in SQLite; arithmetic is above."""
    db=sqlite3.connect(source.resolve().as_uri()+'?mode=ro',uri=True);db.row_factory=sqlite3.Row
    try:
        meta=dict(db.execute('SELECT key,value FROM meta'))
        if case=='Q01':
            inputs={'events':read_rows(db,"SELECT patient_id,kind,event_date,end_date,record_id FROM utilization_event WHERE kind='inpatient'"),
                    'spans':read_rows(db,'SELECT patient_id,payer_id,coverage_start,coverage_end FROM enrollment_span')}
            result=q01(**inputs)
        elif case=='Q02':
            inputs={'claims':read_rows(db,'SELECT claim_id,patient_id,payer,service_date,allowed_cents,net_payer_paid_cents,net_payer_cash_cents FROM claim'),
                'allocations':[{'record_id':r['record_id'],**json.loads(r['payload'])}for r in db.execute("SELECT record_id,payload FROM record WHERE stream='payer_cash_allocations'")]}
            result=q02(**inputs)
        elif case=='Q03':
            inputs={'stays':read_rows(db,"SELECT patient_id,organization_id,type,admit_date,discharge_date,los_days FROM stay WHERE type='inpatient'")};result=q03(**inputs)
        elif case=='Q15':
            inputs={'appointments':read_rows(db,'SELECT appointment_id,patient_id,clinician_id,scheduled_date,final_status,outcome,kept FROM appointment')};result=q15(**inputs)
        else:raise ValueError('No independent arithmetic reference implemented for '+case)
        return {'schema':'ordinary-independent-reference/1','case':case,'source':{k:meta.get(k)for k in('source_digest','as_of','version','postprocessing_identity')},
            'input_digest':fingerprint(inputs),'input_counts':{k:len(v)for k,v in inputs.items()},
            'reference':result,'qualification':'Arithmetic over received semantic rows only. Not clinical adjudication, raw adapter qualification, complete requested narrative scoring or either arm answer.'}
    finally:db.close()


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--case',required=True,choices=['Q01','Q02','Q03','Q15'])
    parser.add_argument('--output',required=True,type=Path)
    args=parser.parse_args();output=args.output.resolve()
    if not output.is_relative_to(ROOT/'artifacts')or not any(p.startswith('agere-ordinary-scoring')for p in output.parts):
        raise ValueError('References must be in a dedicated agere-ordinary-scoring artifact directory, denied to both study arms.')
    if output.exists():raise ValueError('Preserve the existing reference; do not overwrite an evaluated definition.')
    source=RUNTIME/'semantic.sqlite3';before=source.stat();result=prepare(args.case,source);after=source.stat()
    assert(before.st_size,before.st_mtime_ns)==(after.st_size,after.st_mtime_ns)
    result.update(prepared_at=datetime.now(timezone.utc).isoformat(),provider_calls=0,source_unchanged=True,
        curriculum_sha256=hashlib.sha256(CURRICULUM.read_bytes()).hexdigest(),
        evaluator_sha256=hashlib.sha256(Path(__file__).read_bytes()).hexdigest())
    output.parent.mkdir(parents=True,exist_ok=True);output.write_text(json.dumps(result,indent=2,allow_nan=False)+'\n')
    print(json.dumps({'case':args.case,'output':str(output),'rows':len(result['reference']['rows']),
        'totals':result['reference']['totals'],'provider_calls':0}))


if __name__=='__main__':main()
