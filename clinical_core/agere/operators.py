"""Source-grounded population operators. Observations, hypotheses and missing inputs stay distinct."""
from __future__ import annotations
import math
import re
from collections import Counter,defaultdict
from datetime import date,timedelta
import numpy as np
from scipy.stats import theilslopes
from .narrative import read as read_narrative, candidate, review_actions

VERSION='rich-operators/2'
MEDS=('bb','arni','mra','sglt2')
ALIASES={'creatinine':{'creatinine','serum creatinine','2160-0'},'egfr':{'egfr','33914-3','62238-1'},
         'potassium':{'potassium','2823-3'},'hba1c':{'hba1c','4548-4'}}

def ref(r,field=None):
    return {'record_id':r['record_id'],'source_table':r['source_table'],'event_date':r['event_date'],
            'available_date':r['available_date'],'field':field}

def latest(rows):
    # Unknown statement dates can be newer than any dated row: do not pick a latest value.
    return max(rows,key=lambda r:(r['event_date'],r['available_date'],r['record_id'])) if rows and all(r.get('event_date') for r in rows) else None

def labs(records,concept):
    result=[]
    for r in records:
        if not r.get('event_date') or r['source_table']!='labs' or str(r.get('test',r.get('code',''))).lower() not in ALIASES[concept]:continue
        try: value=float(r['value'])
        except (KeyError,ValueError,TypeError): continue
        if not math.isfinite(value):continue
        unit=r.get('unit','').lower().replace(' ','');conversion=None
        allowed={'egfr':{'ml/min/1.73m2','ml/min/1.73m²'},'potassium':{'mmol/l','meq/l'},
                 'hba1c':{'%'},'creatinine':{'mg/dl'}}
        if concept=='creatinine' and unit in {'umol/l','µmol/l','μmol/l'}:
            value/=88.4;unit='mg/dl';conversion='umol/L / 88.4'
        if unit not in allowed[concept]:continue
        result.append({'date':r['event_date'],'value':value,'unit':unit,'conversion':conversion,'evidence':ref(r,'value')})
    return sorted(result,key=lambda r:(r['date'],r['evidence']['record_id']))

def trend(series):
    bydate=defaultdict(list)
    for r in series:bydate[r['date']].append(r['value'])
    if len(bydate)<3:return {'status':'insufficient_distinct_dates','points':len(bydate)}
    ds=sorted(bydate);x=np.array([(date.fromisoformat(d)-date.fromisoformat(ds[0])).days for d in ds])
    y=np.array([np.median(bydate[d]) for d in ds]);slope,intercept,low,high=theilslopes(y,x)
    return {'status':'computed','method':'Theil-Sen; descriptive trend, not a diagnosis',
            'slope_per_year':float(slope*365.25),'slope_interval_per_year':[float(low*365.25),float(high*365.25)],
            'points':len(ds),'span_days':int(x[-1]),'unit':series[0]['unit'],
            'evidence':[r['evidence'] for r in series]}

def medication(records,subject,cutoff):
    rs=[r for r in records if r.get('drug_class')==subject and r.get('event_date')]
    supplies={};issues=[];orders=[];admins=[]
    transactions=defaultdict(list)
    for r in rs:
        if r['source_table']=='pharmacy_claims': transactions[r.get('dispense_id') or r['record_id']].append(r)
    reversed_ids=set()
    for key,group in transactions.items():
        newest=max((r['available_date'],r['event_date']) for r in group)
        states={r.get('transaction_type') for r in group if (r['available_date'],r['event_date'])==newest}
        if 'reversed' in states: reversed_ids.add(key)
    for r in rs:
        stream=r['source_table']
        if stream=='medication_orders':orders.append(ref(r));continue
        if stream=='medication_administration':admins.append(ref(r));continue
        if stream not in {'pharmacy_claims','medication_history'}:continue
        if r.get('transaction_type') in {'rejected','reversed'}:
            issues.append(ref(r));continue
        if r.get('status')!='dispensed':continue
        if stream=='pharmacy_claims' and r.get('dispense_id') in reversed_ids:continue
        days=r.get('days_supply')
        if not isinstance(days,(int,float)) or not math.isfinite(days) or days<=0:continue
        end=(date.fromisoformat(r['event_date'])+timedelta(days=int(days)-1)).isoformat()
        if end<cutoff:continue
        key=r.get('dispense_id') or r['record_id']
        entry=supplies.setdefault(key,{'start':r['event_date'],'end':end,'dose':r.get('dose'),
                                    'payment_channels':[],'evidence':[]})
        entry['evidence'].append(ref(r));entry['payment_channels'].append(r.get('payment_channel'))
    return {'subject':subject,'status':'supply_documented' if supplies else 'supply_not_observed',
            'supplies':list(supplies.values()),'order_evidence':orders,'administration_evidence':admins,
            'transaction_issues':issues,'ingestion_established':False,'missing_care_established':False,
            'max_tolerated_dose':'unknown_without_titration_and_tolerance_evidence'}

def clinical_facts(patient,records,cutoff):
    e=labs(records,'egfr');cr=labs(records,'creatinine')
    financial=latest([r for r in records if r['source_table'] in {'financial','financial_screens'}])
    narrative=read_narrative(records)
    mentions=narrative['mentions']
    ef=[m for m in mentions if m['concept']=='ef' and candidate(m)]
    goals=[m for m in mentions if m['concept']=='goals_of_care' and candidate(m)]
    barriers=[m for m in mentions if m['concept'] in {'financial_hardship','transport_barrier'} and candidate(m)]
    outside=[m for m in mentions if m['concept']=='outside_care' and candidate(m)]
    claims_dx=[]
    for r in records:
        if r['source_table']=='medical_claims':claims_dx+=r.get('dx',[])
        if r['source_table']=='problem_lists':claims_dx += [p.get('code','') for p in r.get('problems',[])]
    age=None
    if patient.get('birth_date'):
        dob=date.fromisoformat(patient['birth_date']);at=date.fromisoformat(cutoff)
        age=at.year-dob.year-((at.month,at.day)<(dob.month,dob.day))
    facts={'age':age,'latest_egfr':e[-1]['value'] if e else None,
           'difficulty_paying':financial.get('difficulty_paying_medical_bills') if financial else None,
           'egfr_below_60_persistent': None,
           'has_documented_nephrology':True if any(r['source_table']=='referrals' and 'nephro' in r.get('service','').lower() for r in records) else None}
    lows=[x for x in e if x['value']<60]
    if lows and e[-1]['value']<60 and (date.fromisoformat(lows[-1]['date'])-date.fromisoformat(lows[0]['date'])).days>=90:
        facts['egfr_below_60_persistent']=True
    if e and e[-1]['value']>=60:facts['egfr_below_60_persistent']=False
    return {'facts':facts,'egfr':e,'creatinine':cr,'narrative':narrative,'ef_candidates':ef,'documented_codes':sorted(set(claims_dx)),
            'goals_mentions':goals,'barrier_mentions':barriers,'outside_mentions':outside,
            'financial_evidence':ref(financial) if financial else None}

def patient_analysis(patient,records,cutoff,programs):
    clinical=clinical_facts(patient,records,cutoff)
    med=[medication(records,m,cutoff) for m in MEDS]
    evaluations=[p.evaluate(clinical['facts'],cutoff) for p in programs]
    flags=[]
    for p in evaluations:
        if p['status']=='criteria_met':flags.append({'kind':p['policy_id'],'status':'review_candidate','policy':p})
    et=trend(clinical['egfr']);ct=trend(clinical['creatinine'])
    if et['status']=='computed' and et['slope_interval_per_year'][1]<0:
        flags.append({'kind':'declining_egfr','status':'measurement_pattern','evidence':et['evidence']})
    if ct['status']=='computed' and ct['slope_interval_per_year'][0]>0:
        flags.append({'kind':'rising_creatinine','status':'measurement_pattern','evidence':ct['evidence']})
    if clinical['goals_mentions']:flags.append({'kind':'goals_of_care_context','status':'text_mentions','evidence':clinical['goals_mentions']})
    if clinical['barrier_mentions']:flags.append({'kind':'access_context','status':'text_mentions','evidence':clinical['barrier_mentions']})
    if clinical['outside_mentions']:flags.append({'kind':'outside_care_context','status':'text_mentions','evidence':clinical['outside_mentions']})
    unresolved=[]
    if any(e['upper_value']<=40 for e in clinical['ef_candidates']):
        flags.append({'kind':'reduced_ef_documentation','status':'text_candidate','evidence':clinical['ef_candidates']})
        unresolved=['Confirm current phenotype and echo date','Reconcile holds, exceptions and actual use',
                    'Retrieve titration history before judging maximum tolerated dose']
    narrative_actions=review_actions(clinical['narrative'])
    for action in narrative_actions:
        if action['kind']=='verify_financial_support_need' and clinical['facts']['difficulty_paying'] is False:
            action['structured_counterevidence']={'difficulty_paying':False,'evidence':clinical['financial_evidence']}
            action['reconciliation_required']=True
    return {'patient_id':patient['patient_id'],'synthetic':True,'clinical':clinical,'medications':med,
            'trends':{'egfr':et,'creatinine':ct},'policies':evaluations,'flags':flags,
            'hf_information_needs':unresolved,'narrative_review_actions':narrative_actions,
            'care_gap_from_absence':False}

def process_paths(patients):
    edges=Counter();lags=defaultdict(list)
    for p in patients.values():
        seq=sorted([r for r in p['records'] if r.get('event_date') and r['source_table'] in {'referrals','documents','procedure_orders','adt'}],
                   key=lambda r:(r['event_date'],r['record_id']))
        for a,b in zip(seq,seq[1:]):
            edge=(a['source_table'],b['source_table']);edges[edge]+=1
            lags[edge].append((date.fromisoformat(b['event_date'])-date.fromisoformat(a['event_date'])).days)
    return [{'from':a,'to':b,'observed_transitions':n,'median_days':float(np.median(lags[(a,b)])),
             'interpretation':'Observed adjacent events, not a causal or matched referral-completion relationship'} for (a,b),n in edges.most_common()]

def admission_episodes(records,cutoff,window_days=30):
    """ADT A01/A03 pairs, not inpatient labels invented from outpatient encounters."""
    admissions=sorted([r for r in records if r['source_table']=='adt' and r.get('event_date') and r.get('event') in ('A01','admission')],key=lambda r:r['event_date'])
    discharges=sorted([r for r in records if r['source_table']=='adt' and r.get('event_date') and r.get('event') in ('A03','discharge')],key=lambda r:r['event_date'])
    result=[];used=set()
    for a in admissions:
        match=next((d for d in discharges if d['record_id'] not in used and d['event_date']>=a['event_date'] and d['organization_id']==a['organization_id']),None)
        if not match:result.append({'admission':ref(a),'status':'no_observed_discharge'});continue
        used.add(match['record_id']);end=date.fromisoformat(match['event_date'])
        nxt=next((r for r in admissions if 0<(date.fromisoformat(r['event_date'])-end).days<=window_days),None)
        observed=(date.fromisoformat(cutoff)-end).days>=window_days
        result.append({'admission':ref(a),'discharge':ref(match),'readmission':ref(nxt) if nxt else None,
                       'status':'observed_readmission' if nxt else 'window_elapsed_source_coverage_unknown' if observed else 'right_censored',
                       'cause':'not_established','window_days':window_days})
    return result
