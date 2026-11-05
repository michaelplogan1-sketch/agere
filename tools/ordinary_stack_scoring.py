"""Independent received-row arithmetic for the fixed ordinary-question study.

This study tool is never imported by the answerer. It reads the existing semantic
snapshot with plain SQLite and aggregates in Python, without product planners,
metric macros, model judgments or answers from either arm. It tests arithmetic
over that received representation, not the correctness of source normalization.
"""
import argparse
from collections import Counter,defaultdict
from datetime import date,datetime,timedelta,timezone
import hashlib
import json
import math
from pathlib import Path
import sqlite3
import sys

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
RUNTIME=ROOT/'artifacts/agere-full-stack-rerun-2026-10-30/focused/runtime/stack'
CURRICULUM=ROOT/'config/agere/ordinary_question_curriculum.json'
SCORING=ROOT/'config/agere/ordinary_question_scoring.json'


def fingerprint(value):
    return hashlib.sha256(json.dumps(value,sort_keys=True,separators=(',',':'),allow_nan=False).encode()).hexdigest()


def file_sha(path):return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def rubric(case,config=None):
    config=config or json.loads(SCORING.read_text())
    return {**config['common_checks'],**config['cases'][case]['checks']}


def compare_rows(expected,observed,keys):
    """Compare every normalized cell; presentation mapping is separately retained.

    Normalization must copy actual output values with explicit field/unit paths.
    This function does not ask a model to grade itself or silently map measures.
    """
    def indexed(rows):
        index={}
        for row in rows:
            if any(k not in row for k in keys):raise ValueError('Missing comparison group key')
            key=json.dumps([row[k]for k in keys],sort_keys=True)
            if key in index:raise ValueError('Duplicate comparison group')
            index[key]=row
        return index
    left,right=indexed(expected),indexed(observed);faults=[];checked=0
    def equal(a,b):
        if type(a)in(int,float)and type(b)in(int,float):return math.isfinite(a)and math.isfinite(b)and math.isclose(a,b,rel_tol=0,abs_tol=1e-8)
        return type(a)is type(b)and a==b
    def cells(a,b,path):
        nonlocal checked
        if isinstance(a,dict):
            if not isinstance(b,dict):faults.append({'path':path,'expected':a,'observed':b});return
            for field in a:
                if field not in b:faults.append({'path':path+[field],'reason':'missing_field'})
                else:cells(a[field],b[field],path+[field])
            for field in set(b)-set(a):faults.append({'path':path+[field],'reason':'extra_unassessed_field'})
        else:
            checked+=1
            if not equal(a,b):faults.append({'path':path,'expected':a,'observed':b})
    for key in left.keys()&right.keys():cells(left[key],right[key],[key])
    missing=sorted(left.keys()-right.keys());extra=sorted(right.keys()-left.keys())
    return {'schema':'independent-cell-reconciliation/1','state':'passed'if not faults and not missing and not extra else'failed',
        'expected_rows':len(left),'observed_rows':len(right),'cells_checked':checked,
        'missing_groups':missing,'extra_groups':extra,'cell_faults':faults,'clinical_qualification':False}


def score_review(case,review,config=None):
    """Require evidence for each engineering disposition; clinical truth separate."""
    config=config or json.loads(SCORING.read_text());checks=rubric(case,config);result={}
    if set(review)-set(checks):raise ValueError('Unrecognized review checks cannot be silently ignored')
    for key in checks:
        observation=review.get(key)or{};state=observation.get('state','unassessed')
        if state not in config['states']:raise ValueError('Unknown review disposition')
        evidence=observation.get('evidence')or[]
        if state!='unassessed'and not evidence:raise ValueError('A decided check needs inspectable evidence: '+key)
        for ref in evidence:
            path=(ROOT/ref['path']).resolve()
            if not path.is_relative_to(ROOT)or file_sha(path)!=ref['sha256']:raise ValueError('Changed or foreign review evidence')
        result[key]={'state':state,'reason':observation.get('reason'),'evidence':evidence}
    counts=Counter(r['state']for r in result.values())
    return {'case':case,'engineering_state':'failed'if counts['failed']else'unassessed'if counts['unassessed']else'passed',
        'checks':result,'counts':dict(counts),'clinical_qualification':False,
        'independent_qualification_dependency':config['cases'][case].get('clinical_qualification'),
        'unrecognized_review_keys':sorted(set(review)-set(checks))}


def freeze_protocol(references):
    from clinical_core.agere.local_release import code_identity
    from clinical_core.agere.deliverables.answer_context import source_identity
    curriculum=json.loads(CURRICULUM.read_text());config=json.loads(SCORING.read_text())
    ids={c['id']for c in curriculum['cases']}
    if set(config['cases'])!=ids or len(ids)!=25:raise ValueError('All fixed25cases require explicit scoring')
    if {a for c in curriculum['cases']for a in c['areas']}!=set(range(1,11)):raise ValueError('Alltenacceptanceareas required')
    source=source_identity(RUNTIME);entries={};native={}
    for case in curriculum['cases']:
        cid=case['id'];definition=config['cases'][cid]
        entry={'kind':case['kind'],'question_sha256':fingerprint(case['question']),
            'required_checks':list(rubric(cid,config)),
            'clinical_qualification':definition.get('clinical_qualification','Not established by engineering checks'),
            'execution':definition.get('execution')or case.get('execution')or'One declared run per arm; no automatic retry.'}
        if definition['native_reference']:
            path=references/(definition['native_reference']+'.json');reference=json.loads(path.read_text())
            if(reference['case']!=cid or reference['source']['source_digest']!=source['source_digest']
                or reference['source']['as_of']!=source['as_of']or reference['curriculum_sha256']!=file_sha(CURRICULUM)):
                raise ValueError('Reference belongs to another question/source/cutoff')
            native[cid]=reference
            entry['native_reference']={'path':str(path.relative_to(ROOT)),'sha256':file_sha(path)}
        entries[cid]=entry
    if native['Q16']['reference']['totals']['appointment_demand_2029']!=native['Q15']['reference']['totals']['appointments']:
        raise ValueError('Capacity and appointment references silently use different demand universes')
    return {'schema':'ordinary-study-protocol/1','prepared_at':datetime.now(timezone.utc).isoformat(),
        'source':source,'product_code_digest':fingerprint(code_identity()),'curriculum_sha256':file_sha(CURRICULUM),
        'scoring_sha256':file_sha(SCORING),'runner_sha256':file_sha(Path(__file__).with_name('ordinary_stack_comparison.py')),
        'evaluator_sha256':file_sha(__file__),'cases':entries,'native_reference_cases':sorted(native),
        'order':curriculum['comparison']['order'],'qualification':'No clinical/model accuracy or superiority established. Clinical narrative checks need source inspection; missing clinical adjudication remains separate from engineering acceptance.',
        'existing_pilot':'Q03 ordinary pilot retained separately, not repeated or pooled into frozen metrics.',
        'state':'declared_scoring_frozen_for_execution','provider_calls':0}


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


def q04(records):
    groups=defaultdict(list)
    for r in records:
        if in_year(r.get('event_date'),2029):groups[r['stream']].append(r)
    rows=[]
    for stream,group in groups.items():
        available=[day(r.get('available_date'))for r in group]
        lag=[(a-day(r['event_date'])).days for r,a in zip(group,available)if a is not None]
        receipts=[day(r.get('warehouse_receipt_date'))for r in group]
        rows.append({'source_family':stream,'current_received_versions':len(group),
            'reported_available_by_2029_end':sum(a is not None and a<=date(2029,12,31)for a in available),
            'reported_available_later':sum(a is not None and a>date(2029,12,31)for a in available),
            'reported_availability_unknown':sum(a is None for a in available),
            'availability_lag_days_sum':sum(lag),'availability_lag_n':len(lag),
            'availability_precedes_event':sum(n<0 for n in lag),
            'explicit_warehouse_receipt_known':sum(d is not None for d in receipts),
            'explicit_warehouse_receipt_unknown':sum(d is None for d in receipts)})
    return {'rows':ordered(rows,['source_family']),'totals':{'current_received_versions':sum(len(g)for g in groups.values())},
        'definition':'Complete current received-version clock inventory by source family for events occurring in2029; a declared clinical subset can be checked against its exact families without assuming all warehouse streams are clinical. Family semantic classification needs separate source review. available_date is reported availability, not independent proof of physical warehouse ingestion. Only explicit warehouse receipt fields establish that clock. Current versions alone cannot reconstruct superseded observations available in2029; earlier-state completeness needs received version history. No clock imputation.'}


def exposure(member_months,events,year,kind):
    months=[r for r in member_months if str(r.get('month',''))[:4]==str(year)]
    by_key={};groups=defaultdict(lambda:{'member_months':0,'people':set(),'eligible_events':0,'unmatched_events':0})
    for row in months:
        key=(row['patient_id'],row['month'][:7])
        if key in by_key:raise ValueError('Received member-month grain is not unique')
        by_key[key]=row;g=groups[row.get('attribution_practice_id')];g['member_months']+=1;g['people'].add(row['patient_id'])
    for event in events:
        if event.get('kind')!=kind or not in_year(event.get('event_date'),year):continue
        member=by_key.get((event['patient_id'],event['event_date'][:7]));g=groups[member.get('attribution_practice_id')if member else None]
        g['eligible_events'if member else'unmatched_events']+=1
    rows=[]
    for practice,g in groups.items():
        n=g['member_months'];rows.append({'attribution_practice_id':practice,'member_months':n,
            'exposed_people':len(g['people']),'eligible_events':g['eligible_events'],'events_without_received_member_month':g['unmatched_events'],
            'eligible_events_per_1000_member_months':1000*g['eligible_events']/n if n else None})
    return {'rows':ordered(rows,['attribution_practice_id']),
        'totals':{'member_months':len(months),'exposed_people':len({r['patient_id']for r in months}),
            'eligible_events':sum(g['eligible_events']for g in groups.values()),'events_without_received_member_month':sum(g['unmatched_events']for g in groups.values())},
        'definition':'Historical received unique patient-month exposure and contemporaneous attribution. Unknown practice is retained. Events with no received member-month are reported separately, never divided by known exposure or dropped from full totals. No present-survival filter, case-mix model or causal ranking.'}


def q12(adjudications,claims,as_of):
    by_id={r['payer_claim_id']:r for r in claims};groups=defaultdict(list);excluded=Counter()
    cutoff=day(as_of)
    for row in adjudications:
        service=day(row.get('service_from'))
        if service is None or service.year not in(2028,2029):continue
        claim=by_id.get(row.get('payer_claim_id'))
        if claim is not None and claim.get('duplicate_submission')==1:excluded['identified_duplicate_submission']+=1;continue
        available=day(row.get('available_date'));adjudicated=day(row.get('adjudication_date'))
        if available is None or adjudicated is None:excluded['unknown_adjudication_or_availability_clock']+=1;continue
        if available>cutoff or adjudicated>cutoff:excluded['after_snapshot']+=1;continue
        lag=(adjudicated.year-service.year)*12+adjudicated.month-service.month
        groups[service.strftime('%Y-%m'),lag].append(row)
    rows=[]
    for(month,lag),group in groups.items():
        amounts=sums(group,'paid_cents');rows.append({'service_month':month,'adjudication_lag_months':lag,
            'adjudication_events':len(group),**amounts})
    return {'rows':ordered(rows,['service_month','adjudication_lag_months']),
        'totals':{'adjudication_events':sum(len(g)for g in groups.values()),
            'observed_sum_cents':sum(r['observed_sum_cents']for r in rows),'missing_values':sum(r['missing_values']for r in rows)},
        'excluded':dict(excluded),'as_of':as_of,
        'definition':'Received payer-only full adjudication history; signed paid_cents (including reversals/recoupments), service-month origin, adjudication-date development and receipt cutoff. Identified duplicate claim submissions excluded. Replacement/original financial events retained rather than dropping their historical payments with final claim-count flags. This is not a count of final current claims, cash receipts, an ultimate-payment estimate or evidence of complete runout. Unified/provider-claim histories are a separately labeled alternative requiring their own reconciliation.'}


ACCOUNT_FIELDS=('billed_cents','contractual_adjustment_cents','self_pay_discount_cents','payer_expected_cents',
    'payer_received_cents','patient_responsibility_cents','received_cents','refunded_cents','balance_cents',
    'patient_credit_cents','provider_pending_cents')


def q13(records):
    snapshots=defaultdict(list);transactions=defaultdict(list);active=set();unlinked=[]
    for r in records:
        account=r.get('account_id');pid=r.get('patient_id');currency=r.get('currency')
        if not account or not pid or not currency:
            unlinked.append({'record_id':r.get('record_id'),'stream':r.get('source_table'),'reason':'missing_account_patient_or_currency'});continue
        key=(account,pid,currency)
        if in_year(r.get('event_date'),2029):active.add(key)
        if r.get('source_table')=='patient_billing_accounts':snapshots[key].append(r)
        elif(r.get('source_table'),r.get('status'))in[('patient_payments','received'),('patient_refunds','issued')]:
            if type(r.get('amount_cents'))is not int or r['amount_cents']<0:
                unlinked.append({'record_id':r.get('record_id'),'reason':'invalid_nonnegative_integer_cent_transaction'})
            else:transactions[key].append(r)
    rows=[]
    for key in sorted(active):
        history=snapshots[key]
        if not history:
            unlinked.append({'account_id':key[0],'patient_id':key[1],'reason':'no_received_account_snapshot'});continue
        # Registered account event timestamp, then record ID; never add snapshots.
        latest=max(history,key=lambda r:(r.get('event_time')or r.get('event_date')or'',r['record_id']))
        cutoff=(latest.get('event_time')or latest.get('event_date')or'')[:16]
        included=[r for r in transactions[key]if (r.get('event_time')or r.get('event_date')or'')[:16]<=cutoff]
        paid=[r for r in included if r['source_table']=='patient_payments'];refunds=[r for r in included if r['source_table']=='patient_refunds']
        values={f:latest.get(f)for f in ACCOUNT_FIELDS}
        def residual(coefficients):
            return sum(v*values[k]for k,v in coefficients.items())if all(type(values[k])is int for k in coefficients)else None
        rows.append({'account_id':key[0],'patient_id':key[1],'currency':key[2],'snapshot_record_id':latest['record_id'],
            'snapshot_date':latest.get('event_date'),'snapshot_event_time':latest.get('event_time'),**values,
            'patient_balance_residual_cents':residual({'patient_responsibility_cents':1,'received_cents':-1,'refunded_cents':1,'balance_cents':-1,'patient_credit_cents':1}),
            'billed_allocation_residual_cents':residual({'billed_cents':1,'contractual_adjustment_cents':-1,'self_pay_discount_cents':-1,'payer_expected_cents':-1,'patient_responsibility_cents':-1,'provider_pending_cents':-1}),
            'patient_payment_events_through_snapshot':sums(paid,'amount_cents'),
            'patient_refund_events_through_snapshot':sums(refunds,'amount_cents')})
    return {'rows':rows,'totals':{'received_activity_accounts':len(active),'accounts_with_snapshot':len(rows),
        'nonzero_patient_residuals':sum(r['patient_balance_residual_cents']not in(None,0)for r in rows),
        'nonzero_billed_residuals':sum(r['billed_allocation_residual_cents']not in(None,0)for r in rows)},'unresolved_links':unlinked,
        'definition':'Accounts with recorded account/payment/refund activity during2029; latest received account statement through snapshot, not summed historical stocks. This declared activity definition is not proof of every economically open account. Patient/account/currency binding is exact. Accepted received payments/issued refunds through account event minute are separate flows, not payer remittances. Registered patient-balance and billed-allocation identities in integer cents; missing amounts stay unknown. Differences are source reconciliation issues, not debt or fraud.'}


def q16(records):
    durations=[];n=0;missing=0
    for r in records:
        if not in_year(r.get('scheduled_date'),2029):continue
        n+=1;value=r.get('duration_minutes')
        if type(value)not in(int,float)or value<=0:missing+=1
        else:durations.append(value)
    return {'rows':[{'appointment_demand_2029':n,'scenario_multiplier':1.1,'scenario_demand':n*1.1,
        'received_positive_duration_n':len(durations),'missing_or_invalid_duration_n':missing,
        'observed_duration_minutes_sum':sum(durations),'mean_observed_duration_minutes':sum(durations)/len(durations)if durations else None}],
        'totals':{'appointment_demand_2029':n,'received_positive_duration_n':len(durations)},
        'definition':'Comparable demand declared as received2029 appointment records, not arrivals per staffed hour. Direct duration_minutes only; absent durations/staffing/arrival-distribution/utilization target block a numeric staffing or queue-wait claim. The10% multiplier is a scenario, not an estimated forecast. No default service time, FTE hours or queueing distribution.'}


def q17(events,member_months):
    by_key={(r['patient_id'],r['month'][:7])for r in member_months};counts=Counter();unknown=Counter();exposure_n=Counter()
    if len(by_key)!=len(member_months):raise ValueError('Received member-month grain is not unique')
    for r in member_months:
        if str(r.get('month',''))[:4]in('2028','2029'):exposure_n[r['month'][:7]]+=1
    for r in events:
        if r.get('kind')!='ed_visit'or not any(in_year(r.get('event_date'),y)for y in(2028,2029)):continue
        month=r['event_date'][:7]
        (counts if(r['patient_id'],month)in by_key else unknown)[month]+=1
    rows=[]
    for y in(2028,2029):
        for m in range(1,13):
            month=f'{y}-{m:02d}';n=exposure_n[month]
            rows.append({'month':month,'member_months':n,'eligible_ed_events':counts[month],
                'ed_without_received_exposure':unknown[month],'eligible_ed_per_1000_member_months':1000*counts[month]/n if n else None})
    total_n=sum(exposure_n.values());total_events=sum(counts.values())
    return {'rows':rows,'totals':{'member_months':total_n,'eligible_ed_events':total_events,
        'ed_without_received_exposure':sum(unknown.values()),'stable_rate_per_1000_member_months':1000*total_events/total_n if total_n else None},
        'definition':'Monthly historical received member-month exposure and received ED events, all24calendar months retained. No present-survival filter. No received exposure means an unknown rate, not observed zero. Stable pooled exposure-weighted baseline is arithmetic only; change detection must state its registered method, assumptions, multiplicity and source-clock limitations. This reference supplies no claimed true change point or causal explanation.'}


def q18(events,spans,deaths,as_of):
    by_patient=defaultdict(list);enrollment=defaultdict(list);death={r['patient_id']:day(r.get('death_date'))for r in deaths}
    for r in events:
        if r.get('kind')=='inpatient'and day(r.get('event_date'))is not None:by_patient[r['patient_id']].append(r)
    for r in spans:enrollment[r['patient_id']].append(r)
    rows=[]
    for pid,group in by_patient.items():
        group.sort(key=lambda r:(r['event_date'],r.get('record_id')or''));index=group[0]
        if not in_year(index['event_date'],2028):continue
        if sum(r['event_date']==index['event_date']for r in group)>1:
            rows.append({'patient_id':pid,'index_admit_date':index['event_date'],'status':'ambiguous_first_admission'});continue
        end=day(index.get('end_date'));cutoffs=[(day(as_of),'snapshot')]
        if end is None:
            rows.append({'patient_id':pid,'index_admit_date':index['event_date'],'index_discharge_date':None,'status':'unknown_index_discharge'});continue
        if end<day(index['event_date']):
            rows.append({'patient_id':pid,'index_admit_date':index['event_date'],'index_discharge_date':end.isoformat(),'status':'invalid_index_discharge'});continue
        if death.get(pid)is not None:cutoffs.append((death[pid],'received_death'))
        active=[s for s in enrollment[pid]if day(s.get('coverage_start'))is not None and day(s['coverage_start'])<=end
            and(s.get('coverage_end')is None or day(s.get('coverage_end'))is not None and end<=day(s['coverage_end']))]
        # Use the union of contiguous received coverage, not the first span's end.
        coverage_end=None
        if active and all(s.get('coverage_end')is not None for s in active):
            coverage_end=max(day(s['coverage_end'])for s in active)
            while True:
                more=[s for s in enrollment[pid]if day(s.get('coverage_start'))is not None and day(s['coverage_start'])<=coverage_end+timedelta(days=1)
                    and(s.get('coverage_end')is None or day(s.get('coverage_end'))is not None and day(s['coverage_end'])>coverage_end)]
                if not more:break
                if any(s.get('coverage_end')is None for s in more):coverage_end=None;break
                coverage_end=max(day(s['coverage_end'])for s in more)
            if coverage_end is not None:cutoffs.append((coverage_end,'received_coverage_end'))
        cutoff,basis=min(cutoffs);next_event=next((r for r in group[1:]if day(r['event_date'])>=end),None)
        next_day=day(next_event['event_date'])if next_event else None
        status='invalid_followup_order'if cutoff<end else'eligibility_unknown'if not active else'next_observed_admission'if next_day and next_day<=cutoff else'censored_received_followup'
        observed=next_day if status=='next_observed_admission'else cutoff
        rows.append({'patient_id':pid,'index_admit_date':index['event_date'],'index_discharge_date':end.isoformat(),
            'next_received_admit_date':next_day.isoformat()if next_day else None,'censor_date':cutoff.isoformat(),
            'censor_basis':basis,'status':status,'days':(observed-end).days if status not in('invalid_followup_order','eligibility_unknown')else None})
    return {'rows':ordered(rows,['patient_id']),'totals':dict(Counter(r['status']for r in rows)),
        'definition':'First recorded unified inpatient admission must occur in2028; no present-survival filter. Follow from received discharge to next received admission, or earliest snapshot/death/contiguous received enrollment end. Same-day distinct next admission is zero elapsed days. Missing index discharge or absent index eligibility stays explicit. Received-source censoring is not proof of complete external-event capture, and death is a competing event rather than a routine independent censoring assumption for clinical risk.'}


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
        elif case=='Q04':
            query="SELECT record_id,stream,event_date,available_date,payload FROM record WHERE event_date>='2029-01-01' AND event_date<'2030-01-01'"
            rows=[]
            for r in db.execute(query):
                payload=json.loads(r['payload'])
                rows.append({k:r[k]for k in('record_id','stream','event_date','available_date')}|{'warehouse_receipt_date':payload.get('warehouse_receipt_date')or payload.get('warehouse_received_date')})
            inputs={'records':rows};result=q04(**inputs)
        elif case in('Q11','Q17'):
            inputs={'member_months':read_rows(db,'SELECT patient_id,month,attribution_practice_id FROM member_month'),
                    'events':read_rows(db,'SELECT record_id,patient_id,kind,event_date FROM utilization_event')}
            result=exposure(**inputs,year=2029,kind='inpatient')if case=='Q11'else q17(**inputs)
        elif case=='Q12':
            inputs={'adjudications':read_rows(db,'SELECT payer_claim_id,record_id,claim_status,adjudication_date,available_date,service_from,paid_cents FROM payer_adjudication'),
                    'claims':read_rows(db,'SELECT payer_claim_id,duplicate_submission FROM payer_claim')}
            result=q12(**inputs,as_of=meta['as_of'])
        elif case in('Q13','Q16'):
            streams=('patient_billing_accounts','patient_payments','patient_refunds')if case=='Q13'else('appointments',)
            records=[json.loads(r['payload'])|{'record_id':r['record_id'],'patient_id':r['patient_id'],'event_date':r['event_date'],'source_table':r['stream']}for r in db.execute('SELECT record_id,patient_id,stream,event_date,payload FROM record WHERE stream IN('+','.join('?'for _ in streams)+')',streams)]
            if case=='Q16':
                # Canonical appointment includes all normalized source feeds;
                # raw current_record appointments alone omit another feed.
                durations={}
                for r in db.execute("SELECT payload FROM current_record WHERE stream='appointments'"):
                    p=json.loads(r['payload']);durations[p.get('appointment_id')]=p.get('duration_minutes')
                records=[dict(r)|{'duration_minutes':durations.get(r['appointment_id'])}for r in db.execute('SELECT appointment_id,scheduled_date FROM appointment')]
            inputs={'records':records};result=q13(**inputs)if case=='Q13'else q16(**inputs)
        elif case=='Q18':
            inputs={'events':read_rows(db,"SELECT patient_id,kind,event_date,end_date,record_id FROM utilization_event WHERE kind='inpatient'"),
                'spans':read_rows(db,'SELECT patient_id,coverage_start,coverage_end FROM enrollment_span'),
                'deaths':read_rows(db,'SELECT patient_id,death_date FROM death')};result=q18(**inputs,as_of=meta['as_of'])
        else:raise ValueError('No independent arithmetic reference implemented for '+case)
        return {'schema':'ordinary-independent-reference/1','case':case,'source':{k:meta.get(k)for k in('source_digest','as_of','version','postprocessing_identity')},
            'input_digest':fingerprint(inputs),'input_counts':{k:len(v)for k,v in inputs.items()},
            'reference':result,'qualification':'Arithmetic over received semantic rows only. Not clinical adjudication, raw adapter qualification, complete requested narrative scoring or either arm answer.'}
    finally:db.close()


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--case',choices=['Q01','Q02','Q03','Q04','Q11','Q12','Q13','Q15','Q16','Q17','Q18'])
    parser.add_argument('--freeze',action='store_true')
    parser.add_argument('--references',type=Path)
    parser.add_argument('--output',required=True,type=Path)
    args=parser.parse_args();output=args.output.resolve()
    if not output.is_relative_to(ROOT/'artifacts')or not any(p.startswith('agere-ordinary-scoring')for p in output.parts):
        raise ValueError('References must be in a dedicated agere-ordinary-scoring artifact directory, denied to both study arms.')
    if output.exists():raise ValueError('Preserve the existing reference; do not overwrite an evaluated definition.')
    if args.freeze:
        if not args.references or args.case:raise ValueError('Freeze requires references and no single case')
        result=freeze_protocol(args.references.resolve());output.parent.mkdir(parents=True,exist_ok=True)
        output.write_text(json.dumps(result,indent=2,allow_nan=False)+'\n')
        print(json.dumps({'state':result['state'],'cases':len(result['cases']),'native_references':len(result['native_reference_cases']),'provider_calls':0}));return
    if not args.case:raise ValueError('Reference preparation requires a case')
    source=RUNTIME/'semantic.sqlite3';before=source.stat();result=prepare(args.case,source);after=source.stat()
    assert(before.st_size,before.st_mtime_ns)==(after.st_size,after.st_mtime_ns)
    result.update(prepared_at=datetime.now(timezone.utc).isoformat(),provider_calls=0,source_unchanged=True,
        curriculum_sha256=hashlib.sha256(CURRICULUM.read_bytes()).hexdigest(),
        evaluator_sha256=hashlib.sha256(Path(__file__).read_bytes()).hexdigest())
    output.parent.mkdir(parents=True,exist_ok=True);output.write_text(json.dumps(result,indent=2,allow_nan=False)+'\n')
    print(json.dumps({'case':args.case,'output':str(output),'rows':len(result['reference']['rows']),
        'totals':result['reference']['totals'],'provider_calls':0}))


if __name__=='__main__':main()
