"""Dated dispense evidence and transaction reconciliation, not inferred adherence."""
from collections import defaultdict
from datetime import date,timedelta
import math
from ...contracts import digest
from .clinical_events import iso

VERSION='medication-source-reconciliation/1'

def reference(r):
    return {'record_id':r['record_id'],'record_hash':digest(r),'source_table':r['source_table'],
            'source_system':r.get('source_system'),'event_date':r.get('event_date'),
            'available_date':r.get('available_date'),'recorded_date':r.get('recorded_date'),
            'drug':r.get('drug'),'dose':r.get('dose'),'dispense_id':r.get('dispense_id'),
            'prescription_id':r.get('prescription_id'),'fill_date':r.get('fill_date'),
            'dispense_date':r.get('dispense_date'),'days_supply':r.get('days_supply'),
            'status':r.get('status'),'transaction_type':r.get('transaction_type')}

def transaction_state(claims):
    if not claims:return {'state':'no_claim_transactions','basis':None}
    # Recorded transaction time orders adjudication; availability only determines visibility.
    if any(not iso(r.get('recorded_date')) for r in claims):
        return {'state':'unknown_transaction_order','basis':'Missing explicit recorded date'}
    latest=max(r['recorded_date'] for r in claims)
    states={r.get('transaction_type') for r in claims if r['recorded_date']==latest}
    state=next(iter(states)) if len(states)==1 and states<= {'paid','reversed','rejected'} else 'conflicting_latest_transactions'
    if state=='rejected' and any(r.get('transaction_type')=='paid' for r in claims):
        state='rejection_after_paid_unresolved'
    return {'state':state,'latest_recorded_date':latest,'basis':'Explicit recorded transaction date; intraday ordering unavailable',
            'evidence':[reference(r) for r in claims]}

def reconcile(records,subject,as_of):
    rows=[r for r in records if r.get('drug_class')==subject]
    groups=defaultdict(list);orders=[];admins=[];issues=[];excluded=[]
    for r in rows:
        stream=r['source_table']
        if stream=='medication_orders':orders.append(reference(r))
        elif stream=='medication_administration':admins.append(reference(r))
        elif stream in ('pharmacy_claims','medication_history'):
            # Cross-source agreement uses supplied dispense identities, never fuzzy merging.
            groups[r.get('dispense_id') or 'unlinked:'+r['record_id']].append(r)
            if stream=='pharmacy_claims' and r.get('transaction_type') in ('rejected','reversed'):issues.append(reference(r))
    lifecycle=[];current=[];historical=[];conflicts=[]
    for identifier,rs in sorted(groups.items()):
        claims=[r for r in rs if r['source_table']=='pharmacy_claims'];tx=transaction_state(claims)
        candidates=[]
        for r in rs:
            if r.get('status')!='dispensed':continue
            if r['source_table']=='pharmacy_claims' and r.get('transaction_type')!='paid':continue
            field='fill_date' if r['source_table']=='pharmacy_claims' else 'dispense_date'
            day=r.get(field);days=r.get('days_supply');reason=None
            if not iso(day):reason='explicit_dispense_date_not_observed_or_invalid'
            elif day>as_of:reason='future_dispense'
            if type(days) not in (int,float) or not math.isfinite(days) or days<=0 or days!=int(days) or days>3650:
                reason=reason or 'invalid_or_missing_integer_days_supply'
            if reason:excluded.append({'reason':reason,'evidence':reference(r)});continue
            try:end=(date.fromisoformat(day)+timedelta(days=int(days)-1)).isoformat()
            except OverflowError:excluded.append({'reason':'invalid_supply_date_range','evidence':reference(r)});continue
            candidates.append({'start':day,'end':end,'days_supply':int(days),'drug':r.get('drug'),
                               'dose':r.get('dose'),'payment_channel':r.get('payment_channel'),
                               'source_table':r['source_table'],'evidence':reference(r)})
        signatures={(c['start'],c['end'],c['drug'],c['dose']) for c in candidates}
        reason=None
        if len(signatures)>1:reason='same_dispense_identity_has_conflicting_dates_drug_or_dose'
        if claims and tx['state']!='paid':
            if any(c['source_table']=='medication_history' for c in candidates):reason=reason or 'history_and_claim_adjudication_disagree'
            elif tx['state'] not in ('reversed','rejected'):reason=reason or 'claim_transaction_state_unresolved'
            candidates=[]
        item={'dispense_id':identifier,'transaction':tx,'source_evidence':[reference(r) for r in rs],
              'date_basis':'Explicit fill_date/dispense_date; source dates are not substituted',
              'status':'unresolved' if reason else tx['state'] if tx['state'] in ('reversed','rejected') else 'no_usable_dispense_evidence'}
        if reason:
            item['reason']=reason;conflicts.append(item)
        elif candidates:
            one=candidates[0]
            item.update(status='current_documented_supply' if one['end']>=as_of else 'historical_documented_supply',
                        start=one['start'],end=one['end'],days_supply=one['days_supply'],drug=one['drug'],dose=one['dose'],
                        payment_channels=sorted({c['payment_channel'] for c in candidates if c['payment_channel']}),
                        evidence=[c['evidence'] for c in candidates])
            (current if item['status']=='current_documented_supply' else historical).append(item)
        lifecycle.append(item)
    taking=[reference(r) for r in rows if r.get('medication_taking_observed') is True]
    return {'schema':VERSION,'subject':subject,'as_of':as_of,
            'status':'supply_documented' if current else 'supply_unresolved' if conflicts or excluded else 'supply_not_observed',
            'supplies':current,'historical_supplies':historical,'dispense_lifecycle':lifecycle,
            'source_disagreements':conflicts,'excluded_dispense_observations':excluded,
            'order_evidence':orders,'administration_evidence':admins,'transaction_issues':issues,
            'explicit_taking_observations':taking,'ingestion_established':False,'missing_care_established':False,
            'max_tolerated_dose':'unknown_without_titration_and_tolerance_evidence',
            'limitations':['Recorded transaction dates cannot order same-day contradictory transactions.',
                           'A pharmacy/history dispense is not proof of ingestion or current prescribed therapy.',
                           'Overlapping fills are preserved, not stockpiled or summed into additional days.',
                           'A missing dispense ID cannot establish cross-source identity.',
                           'No inferred indication, contraindication, appropriate hold or optimal tolerated dose.']}
