"""Authored healthcare analytics practice, independently checked against received rows.

Offline training support only. These synthetic records and documentary questions
do not define medical treatment standards, clinical eligibility or causal effects.
No private corpus, legacy question store, answer key, provider or network is used.
"""
from __future__ import annotations
from collections import Counter, defaultdict
from dataclasses import dataclass
from datetime import date, timedelta
import json
from pathlib import Path
import random
import sqlite3
import time

from clinical_core.contracts import digest
from clinical_core.agere.clinical import component_learning as C, adapter_learning as A
from tools.training import adapter_tasks as T

AS_OF = '2030-05-27'
VERSION = 'authored-healthcare-analytics/2'
CONDITIONS = ('diabetes','heart failure','copd','asthma','obesity','coronary artery disease','hypertension','hyperlipidemia','cancer','prediabetes','depression')


def source(path, variant=0):
    path=C.checked_path(path);path.mkdir(parents=True,exist_ok=False)
    db=sqlite3.connect(path/'semantic.sqlite3')
    db.executescript('''
      CREATE TABLE meta(key,value);
      CREATE TABLE patient(patient_id TEXT,birth_date TEXT,sex_reported TEXT,coverage_type TEXT,payer_id TEXT,language TEXT,zip_code TEXT,death_date TEXT);
      CREATE TABLE condition(patient_id TEXT,concept TEXT,event_date TEXT,assertion_status TEXT,note_id TEXT);
      CREATE TABLE measurement(patient_id TEXT,concept TEXT,event_date TEXT,value REAL,unit TEXT);
      CREATE TABLE medication(patient_id TEXT,drug TEXT,state TEXT,current_daily_dose_mg REAL,documented_goal_mg REAL,documented_reason TEXT,event_date TEXT);
      CREATE TABLE medication_event(event_id TEXT,patient_id TEXT,drug TEXT,state TEXT,event_date TEXT,daily_dose_mg REAL);
      CREATE TABLE stay(stay_id TEXT,patient_id TEXT,type TEXT,diagnosis TEXT,admission_date TEXT,discharge_date TEXT,hospital TEXT);
      CREATE TABLE appointment(patient_id TEXT,scheduled_date TEXT,final_status TEXT,specialty TEXT,modality TEXT,wait_days INTEGER,clinician_id TEXT);
      CREATE TABLE clinician(clinician_id TEXT,specialty TEXT,organization TEXT);
      CREATE TABLE care_team(patient_id TEXT,clinician_id TEXT,role TEXT,status TEXT,start_date TEXT,end_date TEXT);
      CREATE TABLE claim(claim_id TEXT,patient_id TEXT,service_date TEXT,allowed_cents INTEGER,paid_cents INTEGER,refund_cents INTEGER,final_status TEXT,provider_id TEXT);
      CREATE TABLE adjudication(claim_id TEXT,patient_id TEXT,event_date TEXT,status TEXT,paid_cents INTEGER);
      CREATE TABLE pharmacy_claim(patient_id TEXT,service_date TEXT,drug TEXT,drug_class TEXT,paid_cents INTEGER,reversal_cents INTEGER,days_supply INTEGER,status TEXT);
      CREATE TABLE social_screen(patient_id TEXT,need TEXT,status TEXT,event_date TEXT);
      CREATE TABLE service_enrollment(patient_id TEXT,program TEXT,status TEXT,documented_eligible INTEGER,event_date TEXT);
      CREATE TABLE rpm(patient_id TEXT,event_date TEXT,device TEXT,value REAL);
      CREATE TABLE device_order(patient_id TEXT,device TEXT,status TEXT,event_date TEXT);
      CREATE TABLE contract(contract_id TEXT,payer_id TEXT,budget_cents INTEGER,shared_savings_fraction REAL);
      CREATE TABLE member_month(patient_id TEXT,month TEXT,contract_id TEXT);
      CREATE TABLE capacity(payer_id TEXT,month TEXT,specialty TEXT,demand_visits INTEGER,available_visits INTEGER);
      CREATE TABLE outreach(patient_id TEXT,event_date TEXT,program TEXT,outcome TEXT);
      CREATE TABLE patient_outcome(patient_id TEXT,concept TEXT,event_date TEXT,baseline_function REAL,followup_function REAL,pain_score REAL);
      CREATE TABLE care_activity(patient_id TEXT,concept TEXT,event_date TEXT,phase TEXT,minutes REAL,capacity_cost_cents_per_minute REAL);
    ''')
    db.executemany('INSERT INTO meta VALUES(?,?)',[('as_of',AS_OF),('source_digest',VERSION+f'/variant-{variant}')])
    rng=random.Random(49317+variant*991)
    counts=(48,31,57);n=counts[variant]
    def put(table,rows):
        if rows:db.executemany('INSERT INTO '+table+' VALUES('+','.join('?' for _ in rows[0])+')',rows)
    clinicians=[(f'D{i}',('primary_care','cardiology','endocrinology','pulmonology')[i%4],f'ORG{i%3}') for i in range(12)]
    put('clinician',clinicians)
    drugs=('carvedilol','metformin','lisinopril','apixaban')
    for i in range(n):
        pid=f'AUTHORED-{i:03}';payer=f'P{(i+variant)%4}'
        put('patient',[(pid,None if i%13==0 else f'{1940+(i*7)%64}-04-12','F' if (i+variant)%2 else 'M',('medicare','commercial','medicaid')[i%3],payer,('English','Spanish','Vietnamese')[i%3],f'ZIP{i%5}', '2029-11-04' if (i+variant)%17==0 else None)])
        for year in (2028,2029,2030):
            end_month=5 if year==2030 else 12
            for month in range(1,end_month+1):
                day=f'{year}-{month:02}-12';key=f'{i}-{year}-{month}'
                for concept in CONDITIONS:
                    if rng.random()<.45:
                        status=rng.choice(('confirmed','confirmed','negated','uncertain','historical'))
                        put('condition',[(pid,concept,day,status,'N-'+key+'-'+concept)])
                for concept,unit,center in [('hba1c','percent',7),('egfr','mL/min/1.73m2',55),('weight','kg',80)]:
                    if rng.random()<.58:
                        value=None if rng.random()<.12 else round(center+rng.uniform(-center*.4,center*.4),2)
                        put('measurement',[(pid,concept,day,value,unit)])
                        if (i+month)%9==0:put('measurement',[(pid,concept,day,value,unit)])
                if month%3==i%3:
                    kind=rng.choice(('inpatient','observation'));admit=f'{year}-{month:02}-02';discharge=f'{year}-{month:02}-05'
                    put('stay',[(key,pid,kind,rng.choice(('heart_failure','copd','other')),admit,discharge,f'H{i%3}')])
                    if rng.random()<.3:put('stay',[(key+'R',pid,'inpatient','other',f'{year}-{month:02}-20',f'{year}-{month:02}-23',f'H{i%3}')])
                status=rng.choice(('completed','completed','no_show','canceled','scheduled'))
                put('appointment',[(pid,day,status,rng.choice(('primary_care','cardiology','endocrinology','pulmonology')),rng.choice(('in_person','video','telephone')),rng.randrange(1,61),f'D{i%12}')])
                amount=rng.randrange(1000,250000);paid=amount-rng.randrange(0,500);refund=rng.randrange(0,paid//2+1) if i%5==0 else 0
                final=rng.choice(('paid','paid','denied'));claim='C-'+key
                put('claim',[(claim,pid,day,amount,paid if final=='paid' else 0,refund,final,f'D{i%12}')])
                put('adjudication',[(claim,pid,day,'denied' if i%3==0 else final,0 if i%3==0 else paid)])
                if i%3==0:put('adjudication',[(claim,pid,f'{year}-{month:02}-18',final,paid if final=='paid' else 0)])
                drug=drugs[(i+month)%4];gross=rng.randrange(500,40000);rev=gross if (i+month+variant)%7==0 else 0
                put('pharmacy_claim',[(pid,day,drug,('beta_blocker','diabetes','ace_inhibitor','anticoagulant')[(i+month)%4],gross,rev,rng.choice((30,60,90)),'reversed' if rev else 'paid')])
                for need in ('transport','food','housing','digital'):
                    put('social_screen',[(pid,need,rng.choice(('confirmed','absent','unassessed','declined')),day)])
                for program in ('heart_failure_support','diabetes_support','transport_support'):
                    put('outreach',[(pid,day,program,rng.choice(('reached','not_reached','declined','unknown')))])
                put('member_month',[(pid,f'{year}-{month:02}',f'K{payer[-1]}')])
                if month in (2,5):
                    concept=CONDITIONS[(i+month+variant)%len(CONDITIONS)];baseline=rng.randrange(20,80)
                    put('patient_outcome',[(pid,concept,day,baseline,None if i%7==0 else baseline+rng.randrange(-10,20),None if i%9==0 else rng.randrange(0,11))])
                    for phase in ('initial','followup','rehabilitation'):
                        put('care_activity',[(pid,concept,day,phase,rng.randrange(5,100),None if (i+month)%11==0 else rng.randrange(50,500))])
        for j,drug in enumerate(drugs):
            state=rng.choice(('active','active','held','stopped'));dose=float((j+1)*5);goal=dose*2 if (i+j)%3 else dose
            reason=rng.choice(('documented dizziness','patient preference','pending labs',None))
            put('medication',[(pid,drug,state,dose,goal,reason,'2030-05-12')])
            put('medication_event',[(f'E{i}-{j}-1',pid,drug,'active','2029-01-12',dose/2),(f'E{i}-{j}-2',pid,drug,state,'2030-05-12',dose)])
        for role in ('primary_care','cardiology'):
            doctor=next(c[0] for c in clinicians if c[1]==role and (int(c[0][1:])//4)==i%3)
            state=rng.choice(('assigned','assigned','referred','ended'))
            put('care_team',[(pid,doctor,role,state,'2028-01-01','2030-04-01' if state=='ended' else None)])
        for program in ('heart_failure_support','diabetes_support','transport_support'):
            put('service_enrollment',[(pid,program,rng.choice(('enrolled','referred','waitlisted','declined','not_enrolled')),rng.choice((1,0,None)),'2030-04-12')])
        for offset in range(30):
            if rng.random()<.6:
                day=(date.fromisoformat(AS_OF)-timedelta(days=offset)).isoformat()
                put('rpm',[(pid,day,'bluetooth_scale',round(70+i/2+rng.random(),2))])
                if offset%5==0:put('rpm',[(pid,day,'bluetooth_scale',round(70+i/2+rng.random(),2))])
        for device in ('bluetooth_scale','tablet','zoll_patch'):
            put('device_order',[(pid,device,rng.choice(('requested','shipped','delivered','canceled')),'2030-05-12')])
    for p in range(4):
        put('contract',[(f'K{p}',f'P{p}',(1400000+p*100000)*(variant+1),.5)])
        for year in (2028,2029,2030):
            for month in range(1,6 if year==2030 else 13):
                for specialty in ('primary_care','cardiology'):
                    put('capacity',[(f'P{p}',f'{year}-{month:02}',specialty,rng.randrange(20,100),rng.randrange(15,95))])
    db.commit();db.close()


class Rows:
    """Independent reference arithmetic reads actual fixture rows, not generator state."""
    def __init__(self,path):
        with sqlite3.connect((Path(path)/'semantic.sqlite3').as_uri()+'?mode=ro',uri=True) as db:
            db.row_factory=sqlite3.Row
            self.tables={name:[dict(r) for r in db.execute('SELECT * FROM '+name)] for name, in db.execute("SELECT name FROM sqlite_master WHERE type='table' AND name!='meta'").fetchall()}
        self.patients={r['patient_id']:r for r in self.tables['patient']}
    def get(self,table):
        if table=='patient':return self.tables[table]
        return [{**self.patients.get(r.get('patient_id'),{}),**r} for r in self.tables[table]]


@dataclass(frozen=True)
class Family:
    domain: str
    name: str
    table: str
    question: object
    sql_filter: object
    predicate: object
    metric: str = 'patients'
    field: str | None = None
    groups: tuple = ()
    unit: str = 'count'
    extra_sql: str = ''
    rows: object = None

    def render(self,p):
        columns={g:{'unit':'text'} for g in self.groups}
        expression={'patients':'COUNT(DISTINCT t.patient_id)','records':'COUNT(*)',
            'sum':f'SUM(t.{self.field})','average':f'AVG(t.{self.field})',
            'percent':f'100.0*SUM(t.{self.field})/NULLIF(COUNT(*),0)',
            'yes_no':'CASE WHEN COUNT(*)>0 THEN 1 ELSE 0 END',
            'list':None}.get(self.metric,self.metric)
        alias='patient_id' if self.metric=='list' else 'answer'
        if isinstance(expression,Formula):expression=str(expression).format_map(p)
        fields=', '.join([*('t.'+g for g in self.groups), f'{expression} AS {alias}'] if expression else ['DISTINCT t.patient_id'])
        relation=self.table+' t'
        if self.table!='patient' and self.table not in ('capacity','contract'):relation+=' JOIN patient p ON p.patient_id=t.patient_id'
        where=self.sql_filter(p)
        question=self.question(p)
        if p.get('cohort_year') is not None and self.domain!='vbc':
            where+=f" AND {'t' if self.table=='patient' else 'p'}.birth_date<'{p['cohort_year']}-01-01'"
            question+=f" Restrict the population to patients born before January 1, {p['cohort_year']}."
        sql='SELECT '+fields+' FROM '+relation+' '+self.extra_sql+' WHERE '+where
        if self.groups:sql+=' GROUP BY '+', '.join('t.'+g for g in self.groups)
        columns[alias]={'unit':self.unit}
        if self.unit in ('number','percent','dollars'):columns[alias]['decimals']=1 if self.unit=='percent' else 2
        keys=list(self.groups)
        returns='table' if keys else 'patient_set' if self.metric=='list' else 'count' if self.unit=='count' else 'value'
        if self.metric=='list':columns={'patient_id':{'unit':'id'}};keys=['patient_id']
        return question,sql,{'returns':returns,'columns':columns,'keys':keys}

    def expected(self,received,p):
        rows=self.rows(received,p) if self.rows else received.get(self.table)
        rows=[r for r in rows if self.predicate(r,p)]
        if p.get('cohort_year') is not None and self.domain!='vbc':
            rows=[r for r in rows if r.get('birth_date') is not None and r['birth_date']<f"{p['cohort_year']}-01-01"]
        if self.metric=='list':return [{'patient_id':x} for x in sorted({r['patient_id'] for r in rows})]
        groups=defaultdict(list)
        if self.groups:
            for r in rows:groups[tuple(r[g] for g in self.groups)].append(r)
        else:groups[()]=rows
        result=[]
        for key,batch in groups.items():
            values=[r[self.field] for r in batch if self.field and r[self.field] is not None]
            if self.metric=='patients':value=len({r['patient_id'] for r in batch})
            elif self.metric=='records':value=len(batch)
            elif self.metric=='sum':value=sum(values) if values else None
            elif self.metric=='average':value=sum(values)/len(values) if values else None
            elif self.metric=='percent':value=100*sum(values)/len(batch) if batch else None
            elif self.metric=='yes_no':value=int(bool(batch))
            else:value=self.metric.python(batch,received,p)
            result.append({**dict(zip(self.groups,key)),'answer':value})
        return result


class Formula(str):
    def __new__(cls,sql,python):
        result=super().__new__(cls,sql);result.python=python;return result


def params(j):
    return {'payer':f'P{j%4}','year':2028+(j//4)%3,'cohort_year':None if j<96 else 1950+((j//96)%5)*10,'concept':CONDITIONS[j%len(CONDITIONS)],
        'drug':('carvedilol','metformin','lisinopril','apixaban')[(j//5)%4],
        'program':('heart_failure_support','diabetes_support','transport_support')[(j//2)%3],
        'sex':'F' if j%2 else 'M','coverage':('medicare','commercial','medicaid')[(j//4)%3],
        'language':('English','Spanish','Vietnamese')[(j//3)%3],'birth_year':1950+j%48,
        'threshold':5+j%12,'days':(7,14,30,60)[(j//3)%4],
        'specialty':('primary_care','cardiology','endocrinology','pulmonology')[(j//5)%4],
        'device':('bluetooth_scale','tablet','zoll_patch')[(j//3)%3],
        'need':('transport','food','housing','digital')[(j//2)%4]}


def families():
    result=[]
    def add(domain,name,table,q,where,pred,**kw):result.append(Family(domain,name,table,q,where,pred,**kw))
    payer=lambda p: f"p.payer_id='{p['payer']}'"
    in_year=lambda p,col: f"t.{col}>='{p['year']}-01-01' AND t.{col}<'{p['year']+1}-01-01'"
    same=lambda r,p:r['payer_id']==p['payer']
    year=lambda r,p,col:r[col] is not None and r[col][:4]==str(p['year'])
    # Eight independent question structures per domain: five fit, one calibration,
    # two verification. Membership is fixed before any model outputs are seen.
    for name,q,where,pred,kw in [
      ('living',lambda p:f"Are there any living recorded patients belonging to payer {p['payer']}? Answer yes or no.",lambda p:f"t.payer_id='{p['payer']}' AND t.death_date IS NULL",lambda r,p:same(r,p) and r['death_date'] is None,{'metric':'yes_no','unit':'yes_no'}),
      ('sex',lambda p:f"Count patients with sex reported as {p['sex']} for payer {p['payer']}.",lambda p:f"t.payer_id='{p['payer']}' AND t.sex_reported='{p['sex']}'",lambda r,p:same(r,p) and r['sex_reported']==p['sex'],{}),
      ('coverage',lambda p:f"How many patients with {p['coverage']} coverage are recorded for payer {p['payer']}?",lambda p:f"t.payer_id='{p['payer']}' AND t.coverage_type='{p['coverage']}'",lambda r,p:same(r,p) and r['coverage_type']==p['coverage'],{}),
      ('birth-before',lambda p:f"List patients for payer {p['payer']} born before January 1, {p['birth_year']}.",lambda p:f"t.payer_id='{p['payer']}' AND t.birth_date<'{p['birth_year']}-01-01'",lambda r,p:same(r,p) and r['birth_date'] is not None and r['birth_date']<f"{p['birth_year']}-01-01",{'metric':'list'}),
      ('language',lambda p:f"Count patients whose recorded language is {p['language']} for payer {p['payer']}.",lambda p:f"t.payer_id='{p['payer']}' AND t.language='{p['language']}'",lambda r,p:same(r,p) and r['language']==p['language'],{}),
      ('zip-breakdown',lambda p:f"Count patients for payer {p['payer']} by zip_code.",lambda p:f"t.payer_id='{p['payer']}'",same,{'groups':('zip_code',)}),
      ('unknown-birth',lambda p:f"How many patients for payer {p['payer']} have no recorded birth date?",lambda p:f"t.payer_id='{p['payer']}' AND t.birth_date IS NULL",lambda r,p:same(r,p) and r['birth_date'] is None,{}),
      ('coverage-share',lambda p:f"What percent of patients for payer {p['payer']} have {p['coverage']} coverage?",lambda p:f"t.payer_id='{p['payer']}'",same,{'metric':Formula("100.0*SUM(t.coverage_type='{coverage}')/NULLIF(COUNT(*),0)",lambda b,d,p:100*sum(r['coverage_type']==p['coverage'] for r in b)/len(b) if b else None),'unit':'percent'})]:add('patients',name,'patient',q,where,pred,**kw)
    for name,status in [('confirmed','confirmed'),('negated','negated'),('uncertain','uncertain'),('historical','historical')]:
        add('conditions',name,'condition',lambda p,s=status:f"How many patients for payer {p['payer']} have {p['concept']} documented as {s} during {p['year']}?",lambda p,s=status:payer(p)+f" AND t.concept='{p['concept']}' AND t.assertion_status='{s}' AND "+in_year(p,'event_date'),lambda r,p,s=status:same(r,p) and r['concept']==p['concept'] and r['assertion_status']==s and year(r,p,'event_date'))
    add('conditions','confirmed-sex','condition',lambda p:f"Count {p['sex']} patients for payer {p['payer']} with confirmed {p['concept']} during {p['year']}.",lambda p:payer(p)+f" AND p.sex_reported='{p['sex']}' AND t.concept='{p['concept']}' AND t.assertion_status='confirmed' AND "+in_year(p,'event_date'),lambda r,p:same(r,p) and r['sex_reported']==p['sex'] and r['concept']==p['concept'] and r['assertion_status']=='confirmed' and year(r,p,'event_date'))
    add('conditions','document-count','condition',lambda p:f"How many condition documentation rows for {p['concept']} were received for payer {p['payer']} during {p['year']}? Keep repeated documentation rows for the same patient.",lambda p:payer(p)+f" AND t.concept='{p['concept']}' AND "+in_year(p,'event_date'),lambda r,p:same(r,p) and r['concept']==p['concept'] and year(r,p,'event_date'),metric='records')
    add('conditions','assertion-breakdown','condition',lambda p:f"Count documentation rows for {p['concept']} by assertion_status for payer {p['payer']} during {p['year']}.",lambda p:payer(p)+f" AND t.concept='{p['concept']}' AND "+in_year(p,'event_date'),lambda r,p:same(r,p) and r['concept']==p['concept'] and year(r,p,'event_date'),metric='records',groups=('assertion_status',))
    add('conditions','absence-is-not-negative','condition',lambda p:f"List patients for payer {p['payer']} with an explicit negated {p['concept']} assertion during {p['year']}; missing documentation does not count as a negative.",lambda p:payer(p)+f" AND t.concept='{p['concept']}' AND t.assertion_status='negated' AND "+in_year(p,'event_date'),lambda r,p:same(r,p) and r['concept']==p['concept'] and r['assertion_status']=='negated' and year(r,p,'event_date'),metric='list')
    for name,concept,kind in [('a1c-records','hba1c','records'),('egfr-patients','egfr','patients'),('weight-average','weight','average'),('a1c-average','hba1c','average'),('null-result','hba1c','null'),('a1c-threshold','hba1c','threshold'),('egfr-threshold','egfr','below'),('concept-breakdown',None,'group')]:
        add('measurements',name,'measurement',
            lambda p,c=concept,k=kind:(f"For payer {p['payer']} during {p['year']}, "+{'records':f"count all {c} measurement rows, including repeat rows.",'patients':f"count distinct patients with a recorded {c} measurement.",'average':f"report the average recorded {c} value; omit null values.",'null':f"count {c} rows with an unknown value.",'threshold':f"list patients with any recorded {c} value greater than {p['threshold']}.",'below':f"list patients with any recorded {c} value below {p['threshold']*5}.",'group':'count measurement rows by concept.'}[k]),
            lambda p,c=concept,k=kind:payer(p)+' AND '+in_year(p,'event_date')+(f" AND t.concept='{c}'" if c else '')+(' AND t.value IS NULL' if k=='null' else f" AND t.value>{p['threshold']}" if k=='threshold' else f" AND t.value<{p['threshold']*5}" if k=='below' else ''),
            lambda r,p,c=concept,k=kind:same(r,p) and year(r,p,'event_date') and (c is None or r['concept']==c) and (r['value'] is None if k=='null' else r['value'] is not None and r['value']>p['threshold'] if k=='threshold' else r['value'] is not None and r['value']<p['threshold']*5 if k=='below' else True),
            metric='average' if kind=='average' else 'patients' if kind=='patients' else 'list' if kind in ('threshold','below') else 'records',field='value' if kind=='average' else None,groups=('concept',) if kind=='group' else (),unit='number' if kind=='average' else 'count')
    for name,state,reason in [('active','active',None),('held-reason','held',True),('stopped','stopped',None),('below-goal','active','below'),('below-goal-unknown-reason','active','unknown'),('recorded-reason','active',True),('state-breakdown',None,None),('dose-average','active','average')]:
        def medwhere(p,s=state,r=reason):return payer(p)+f" AND t.drug='{p['drug']}'"+(f" AND t.state='{s}'" if s else '')+(' AND t.documented_reason IS NOT NULL' if r is True else ' AND t.current_daily_dose_mg<t.documented_goal_mg' if r=='below' else ' AND t.current_daily_dose_mg<t.documented_goal_mg AND t.documented_reason IS NULL' if r=='unknown' else '')
        def medpred(row,p,s=state,r=reason):return same(row,p) and row['drug']==p['drug'] and (not s or row['state']==s) and (row['documented_reason'] is not None if r is True else row['current_daily_dose_mg']<row['documented_goal_mg'] if r=='below' else row['current_daily_dose_mg']<row['documented_goal_mg'] and row['documented_reason'] is None if r=='unknown' else True)
        add('medications',name,'medication',lambda p,n=name,s=state:f"For payer {p['payer']} and {p['drug']}, "+{'active':'count patients with active medication documentation.','held-reason':'count patients with held medication and a documented reason.','stopped':'count patients whose medication is documented stopped.','below-goal':'count active-medication patients below their documented per-patient dose goal; use the recorded goal.','below-goal-unknown-reason':'count active-medication patients below the recorded per-patient dose goal with no documented reason.','recorded-reason':'list active-medication patients with a documented reason.','state-breakdown':'count medication rows by state.','dose-average':'give the average recorded current_daily_dose_mg among active medications.'}[n],medwhere,medpred,metric='average' if reason=='average' else 'list' if name=='recorded-reason' else 'records' if name=='state-breakdown' else 'patients',field='current_daily_dose_mg' if reason=='average' else None,groups=('state',) if name=='state-breakdown' else (),unit='number' if reason=='average' else 'count')
    for name,kind,diagnosis,metric,groups in [('inpatient','inpatient',None,'records',()),('observation','observation',None,'records',()),('hf-admissions',None,'heart_failure','records',()),('admitted-patients','inpatient',None,'patients',()),('hospital-breakdown',None,None,'records',('hospital',)),('type-breakdown',None,None,'records',('type',)),('length-of-stay','inpatient',None,'los',()),('hf-patient-list','inpatient','heart_failure','list',())]:
        add('utilization',name,'stay',lambda p,n=name:f"For payer {p['payer']} with admission dates during {p['year']}, "+{'inpatient':'count inpatient admission rows.','observation':'count observation stay rows.','hf-admissions':'count stays with heart_failure as the recorded diagnosis.','admitted-patients':'count distinct patients with an inpatient admission.','hospital-breakdown':'count stays by hospital.','type-breakdown':'count stays by type.','length-of-stay':'report average inpatient length of stay in days, from admission_date to discharge_date.','hf-patient-list':'list patients with an inpatient admission recorded for heart_failure.'}[n],
            lambda p,k=kind,d=diagnosis:payer(p)+' AND '+in_year(p,'admission_date')+(f" AND t.type='{k}'" if k else '')+(f" AND t.diagnosis='{d}'" if d else ''),
            lambda r,p,k=kind,d=diagnosis:same(r,p) and year(r,p,'admission_date') and (not k or r['type']==k) and (not d or r['diagnosis']==d),metric=Formula('AVG(julianday(t.discharge_date)-julianday(t.admission_date))',lambda b,d,p:sum((date.fromisoformat(r['discharge_date'])-date.fromisoformat(r['admission_date'])).days for r in b)/len(b) if b else None) if metric=='los' else metric,groups=groups,unit='number' if metric=='los' else 'count')
    for name,status,specialty,mode in [('completed','completed',None,'records'),('no-show-percent',None,None,'share'),('canceled','canceled',None,'records'),('specialty-kept','completed',True,'patients'),('wait-time',None,True,'average'),('modality',None,None,'group'),('status',None,None,'status'),('no-show-list','no_show',None,'list')]:
        add('appointments',name,'appointment',lambda p,n=name:f"For payer {p['payer']} with appointment dates during {p['year']}, "+{'completed':'count completed appointment rows.','no-show-percent':'what percent were no_show among completed or no_show appointments? Exclude canceled and scheduled appointments from the denominator.','canceled':'count canceled appointment rows.','specialty-kept':f"count distinct patients with a completed {p['specialty']} appointment.",'wait-time':f"give average recorded wait_days for {p['specialty']} appointments.",'modality':'count appointments by modality.','status':'count appointments by final_status.','no-show-list':'list patients with a no_show appointment.'}[n],
            lambda p,s=status,sp=specialty,m=mode:payer(p)+' AND '+in_year(p,'scheduled_date')+(f" AND t.final_status='{s}'" if s else '')+(f" AND t.specialty='{p['specialty']}'" if sp else '')+(" AND t.final_status IN ('completed','no_show') AND t.final_status NOT IN ('canceled','scheduled')" if m=='share' else ''),
            lambda r,p,s=status,sp=specialty,m=mode:same(r,p) and year(r,p,'scheduled_date') and (not s or r['final_status']==s) and (not sp or r['specialty']==p['specialty']) and (m!='share' or r['final_status'] in ('completed','no_show')),
            metric=Formula("100.0*SUM(t.final_status='no_show')/NULLIF(COUNT(*),0)",lambda b,d,p:100*sum(r['final_status']=='no_show' for r in b)/len(b) if b else None) if mode=='share' else 'average' if mode=='average' else 'patients' if mode=='patients' else 'list' if mode=='list' else 'records',
            field='wait_days' if mode=='average' else None,groups=('modality',) if mode=='group' else ('final_status',) if mode=='status' else (),unit='percent' if mode=='share' else 'number' if mode=='average' else 'count')
    for name,role,status,mode in [('assigned-pcp','primary_care','assigned','patients'),('assigned-cardiology','cardiology','assigned','patients'),('referral-only','cardiology','referred','patients'),('ended-pcp','primary_care','ended','list'),('pcp-panel','primary_care','assigned','group'),('team-status','cardiology',None,'status'),('referred-pcp','primary_care','referred','list'),('assigned-cardiology-panel','cardiology','assigned','group')]:
        add('providers',name,'care_team',lambda p,ro=role,s=status,m=mode:f"For payer {p['payer']}, "+(f"count documented {ro} relationships by status." if m=='status' else f"count patients by clinician_id with {s} {ro} relationships." if m=='group' else f"list patients with {s} {ro} relationships." if m=='list' else f"count distinct patients with {s} {ro} relationships."),
            lambda p,ro=role,s=status:payer(p)+f" AND t.role='{ro}'"+(f" AND t.status='{s}'" if s else ''),
            lambda r,p,ro=role,s=status:same(r,p) and r['role']==ro and (not s or r['status']==s),metric='list' if mode=='list' else 'records' if mode=='status' else 'patients',groups=('clinician_id',) if mode=='group' else ('status',) if mode=='status' else ())
    money=lambda field:Formula(f'SUM(t.{field})/100.0',lambda b,d,p:sum(r[field] for r in b)/100 if b else None)
    for name,table,mode in [('current-claims','claim','records'),('adjudication-rows','adjudication','records'),('final-denials','claim','denied'),('allowed-dollars','claim','allowed_cents'),('net-paid-dollars','claim','net'),('paid-provider','claim','provider'),('ever-denied','claim','ever'),('refund-dollars','claim','refund_cents')]:
        def claimwhere(p,t=table,m=mode):return payer(p)+' AND '+in_year(p,'event_date' if t=='adjudication' else 'service_date')+(" AND t.final_status='denied'" if m=='denied' else '')+(" AND EXISTS(SELECT 1 FROM adjudication a WHERE a.claim_id=t.claim_id AND a.status='denied')" if m=='ever' else '')
        def claimrows(d,p,m=mode,t=table):
            rows=d.get(t)
            if m=='ever':
                denied={r['claim_id'] for r in d.tables['adjudication'] if r['status']=='denied'}
                rows=[r for r in rows if r['claim_id'] in denied]
            return rows
        metric=money(mode) if mode in ('allowed_cents','refund_cents') else Formula('SUM(t.paid_cents-t.refund_cents)/100.0',lambda b,d,p:sum(r['paid_cents']-r['refund_cents'] for r in b)/100 if b else None) if mode=='net' else money('paid_cents') if mode=='provider' else 'records'
        add('claims',name,table,lambda p,n=name:f"For payer {p['payer']} during {p['year']}, "+{'current-claims':'count current claim rows using service_date, counting each claim once.','adjudication-rows':'count all received adjudication rows using event_date, including repeated decisions for one claim.','final-denials':'count current claims with final_status denied using service_date.','allowed-dollars':'report total allowed dollars using service_date.','net-paid-dollars':'report net paid dollars using service_date, subtracting refund_cents from paid_cents.','paid-provider':'report total paid dollars by provider_id, using service_date for the period.','ever-denied':'count current claims using service_date that have any denied adjudication; count each claim once.','refund-dollars':'report total refund dollars using service_date.'}[n],claimwhere,lambda r,p,t=table,m=mode:same(r,p) and year(r,p,'event_date' if t=='adjudication' else 'service_date') and (m!='denied' or r['final_status']=='denied'),metric=metric,groups=('provider_id',) if mode=='provider' else (),unit='dollars' if isinstance(metric,Formula) else 'count',rows=claimrows)
    for name,mode in [('gross-paid','gross'),('net-paid','net'),('paid-rows','paid'),('days-supply','days'),('class-spend','group'),('reversed-percent','reversed'),('drug-patients','drug'),('reversal-dollars','reversal')]:
        formula=money('paid_cents') if mode in ('gross','group') else money('reversal_cents') if mode=='reversal' else Formula('SUM(t.paid_cents-t.reversal_cents)/100.0',lambda b,d,p:sum(r['paid_cents']-r['reversal_cents'] for r in b)/100 if b else None) if mode=='net' else Formula("100.0*SUM(t.status='reversed')/NULLIF(COUNT(*),0)",lambda b,d,p:100*sum(r['status']=='reversed' for r in b)/len(b) if b else None) if mode=='reversed' else 'sum' if mode=='days' else 'patients' if mode=='drug' else 'records'
        add('pharmacy',name,'pharmacy_claim',lambda p,n=name:f"For payer {p['payer']} with pharmacy service dates during {p['year']}, "+{'gross-paid':'report gross paid dollars before reversals.','net-paid':'report net paid dollars after subtracting reversal_cents.','paid-rows':'count pharmacy rows with status paid.','days-supply':'sum recorded days_supply for status paid rows.','class-spend':'report gross paid dollars by drug_class.','reversed-percent':'report the percent of received rows with status reversed.','drug-patients':f"count distinct patients with a row for {p['drug']}.",'reversal-dollars':'report total reversal dollars.'}[n],
            lambda p,m=mode:payer(p)+' AND '+in_year(p,'service_date')+(" AND t.status='paid'" if m in ('paid','days') else '')+(f" AND t.drug='{p['drug']}'" if m=='drug' else ''),
            lambda r,p,m=mode:same(r,p) and year(r,p,'service_date') and (m not in ('paid','days') or r['status']=='paid') and (m!='drug' or r['drug']==p['drug']),metric=formula,field='days_supply' if mode=='days' else None,groups=('drug_class',) if mode=='group' else (),unit='percent' if mode=='reversed' else 'dollars' if mode in ('gross','net','group','reversal') else 'count')
    for name,status,mode in [('confirmed','confirmed','patients'),('unassessed','unassessed','patients'),('absent','absent','patients'),('declined','declined','patients'),('status-breakdown',None,'group'),('confirmed-list','confirmed','list'),('confirmed-share',None,'share'),('screening-rows',None,'records')]:
        add('social',name,'social_screen',lambda p,n=name,s=status:f"For payer {p['payer']} with {p['need']} screening dates during {p['year']}, "+{'confirmed':f'count distinct patients with status {s}.','unassessed':f'count distinct patients with status {s}.','absent':f'count distinct patients with status {s}.','declined':f'count distinct patients with status {s}.','status-breakdown':'count screening rows by status.','confirmed-list':'list patients with status confirmed.','confirmed-share':'report the percent of screening rows confirmed among confirmed or absent rows; exclude unassessed and declined rows.','screening-rows':'count all screening rows, including repeated screenings for one patient.'}[n],
            lambda p,s=status,m=mode:payer(p)+f" AND t.need='{p['need']}' AND "+in_year(p,'event_date')+(f" AND t.status='{s}'" if s else '')+(" AND t.status IN ('confirmed','absent')" if m=='share' else ''),
            lambda r,p,s=status,m=mode:same(r,p) and year(r,p,'event_date') and r['need']==p['need'] and (not s or r['status']==s) and (m!='share' or r['status'] in ('confirmed','absent')),
            metric=Formula("100.0*SUM(t.status='confirmed')/NULLIF(COUNT(*),0)",lambda b,d,p:100*sum(r['status']=='confirmed' for r in b)/len(b) if b else None) if mode=='share' else 'list' if mode=='list' else 'records' if mode in ('records','group') else 'patients',groups=('status',) if mode=='group' else (),unit='percent' if mode=='share' else 'count')
    for name,status,eligible,mode in [('enrolled','enrolled',None,'patients'),('referred','referred',None,'patients'),('eligible-not-enrolled',None,1,'eligible'),('waitlisted','waitlisted',None,'patients')]:
        add('services',name,'service_enrollment',lambda p,n=name,s=status:f"For payer {p['payer']} and program {p['program']}, "+('count patients explicitly documented eligible whose status is not enrolled.' if n=='eligible-not-enrolled' else f'count distinct patients with documented status {s}.'),
            lambda p,s=status,e=eligible:payer(p)+f" AND t.program='{p['program']}'"+(f" AND t.status='{s}'" if s else '')+(" AND t.documented_eligible=1 AND t.status!='enrolled'" if e else ''),
            lambda r,p,s=status,e=eligible:same(r,p) and r['program']==p['program'] and (not s or r['status']==s) and (not e or r['documented_eligible']==1 and r['status']!='enrolled'))
    for name,device,mode in [('rpm-patient-days','bluetooth_scale','days'),('rpm-patients','bluetooth_scale','patients'),('device-pending',None,'pending'),('device-delivered',None,'delivered')]:
        table='rpm' if mode in ('days','patients') else 'device_order'
        add('services',name,table,lambda p,m=mode:f"For payer {p['payer']}, "+(f"count distinct patient-days with bluetooth_scale readings during {p['year']}; repeated readings on one day count once." if m=='days' else f"count distinct patients with bluetooth_scale readings during {p['year']}." if m=='patients' else f"list patients with a requested {p['device']} that is not yet shipped." if m=='pending' else f"count distinct patients with a delivered {p['device']}."),
            lambda p,m=mode:payer(p)+(f" AND t.device='bluetooth_scale' AND "+in_year(p,'event_date') if m in ('days','patients') else f" AND t.device='{p['device']}' AND t.status='{'requested' if m=='pending' else 'delivered'}'"+(" AND t.status NOT IN ('shipped','delivered')" if m=='pending' else "")),
            lambda r,p,m=mode:same(r,p) and (r['device']=='bluetooth_scale' and year(r,p,'event_date') if m in ('days','patients') else r['device']==p['device'] and r['status']==('requested' if m=='pending' else 'delivered')),
            metric=Formula("COUNT(DISTINCT t.patient_id||':'||t.event_date)",lambda b,d,p:len({(r['patient_id'],r['event_date']) for r in b})) if mode=='days' else 'list' if mode=='pending' else 'patients')
    spend_sql="(SELECT SUM(c.paid_cents-c.refund_cents)/100.0 FROM claim c JOIN patient pp ON pp.patient_id=c.patient_id WHERE pp.payer_id='{payer}' AND c.service_date>='{year}-01-01' AND c.service_date<'{next_year}-01-01')"
    def net_spend(d,p):
        rows=[r for r in d.get('claim') if same(r,p) and year(r,p,'service_date')]
        return sum(r['paid_cents']-r['refund_cents'] for r in rows)/100 if rows else None
    def budget(d,p):return next(r['budget_cents']/100 for r in d.tables['contract'] if r['payer_id']==p['payer'])
    for name,mode in [('member-months','months'),('pmpm','pmpm'),('budget-difference','difference'),('scenario-share','share')]:
        formula='records' if mode=='months' else Formula(spend_sql+'/NULLIF(COUNT(*),0)',lambda b,d,p:net_spend(d,p)/len(b) if b and net_spend(d,p) is not None else None) if mode=='pmpm' else Formula('(MAX(k.budget_cents)/100.0-'+spend_sql+')'+('*MAX(k.shared_savings_fraction)' if mode=='share' else ''),lambda b,d,p,m=mode:(budget(d,p)-net_spend(d,p))*(.5 if m=='share' else 1) if b and net_spend(d,p) is not None else None)
        add('vbc',name,'member_month',lambda p,m=mode:f"For payer {p['payer']} during {p['year']}, "+{'months':'count received member-month rows.','pmpm':'report net medical paid dollars per received member-month, subtracting refunds.','difference':'report received contract budget dollars minus observed net medical paid dollars after refunds.','share':'calculate the arithmetic contract scenario: received budget dollars minus observed net medical paid dollars after refunds, multiplied by shared_savings_fraction. Label this as an arithmetic contract scenario.'}[m],
            lambda p:payer(p)+f" AND t.month>='{p['year']}-01' AND t.month<'{p['next_year']}-01'",lambda r,p:same(r,p) and r['month'][:4]==str(p['year']),metric=formula,unit='count' if mode=='months' else 'dollars',extra_sql='JOIN contract k ON k.contract_id=t.contract_id')
    for name,mode in [('capacity-demand','demand'),('capacity-gap','gap')]:
        add('vbc',name,'capacity',lambda p,m=mode:f"For payer {p['payer']} in {p['year']}, "+(f"sum demand_visits for {p['specialty']} capacity rows." if m=='demand' else f"sum available_visits minus demand_visits for {p['specialty']} capacity rows."),
            lambda p:f"t.payer_id='{p['payer']}' AND t.month>='{p['year']}-01' AND t.month<'{p['next_year']}-01' AND t.specialty='{p['specialty']}'",lambda r,p:same(r,p) and r['month'][:4]==str(p['year']) and r['specialty']==p['specialty'],metric='sum' if mode=='demand' else Formula('SUM(t.available_visits-t.demand_visits)',lambda b,d,p:sum(r['available_visits']-r['demand_visits'] for r in b) if b else None),field='demand_visits' if mode=='demand' else None,unit='count' if mode=='demand' else 'number')
    for name,mode in [('outreach-contact-share','share'),('outreach-unknown','unknown')]:
        add('vbc',name,'outreach',lambda p,m=mode:f"For payer {p['payer']} and {p['program']} during {p['year']}, "+('report the percent of outreach attempts reached among reached, not_reached or declined outcomes; exclude unknown outcomes.' if m=='share' else 'count outreach attempts with outcome unknown.'),
            lambda p,m=mode:payer(p)+f" AND t.program='{p['program']}' AND "+in_year(p,'event_date')+(" AND t.outcome IN ('reached','not_reached','declined')" if m=='share' else " AND t.outcome='unknown'"),lambda r,p,m=mode:same(r,p) and r['program']==p['program'] and year(r,p,'event_date') and (r['outcome'] in ('reached','not_reached','declined') if m=='share' else r['outcome']=='unknown'),metric=Formula("100.0*SUM(t.outcome='reached')/NULLIF(COUNT(*),0)",lambda b,d,p:100*sum(r['outcome']=='reached' for r in b)/len(b) if b else None) if mode=='share' else 'records',unit='percent' if mode=='share' else 'count')
    for name,mode in [('reported-outcome','reported'),('missing-outcome','missing'),('function-change','change'),('pain-average','pain')]:
        add('outcomes_value',name,'patient_outcome',lambda p,m=mode:f"For payer {p['payer']} with {p['concept']} outcome records during {p['year']}, "+{'reported':'count records with a known followup_function score.','missing':'count records with an unknown followup_function score.','change':'report the average followup_function minus baseline_function score among complete paired records. Higher function scores denote better function on this recorded scale.','pain':'report the average known pain_score.'}[m],
            lambda p,m=mode:payer(p)+f" AND t.concept='{p['concept']}' AND "+in_year(p,'event_date')+(" AND t.followup_function IS NOT NULL" if m=='reported' else " AND t.followup_function IS NULL" if m=='missing' else " AND t.followup_function IS NOT NULL AND t.baseline_function IS NOT NULL" if m=='change' else ''),
            lambda r,p,m=mode:same(r,p) and r['concept']==p['concept'] and year(r,p,'event_date') and (r['followup_function'] is not None if m=='reported' else r['followup_function'] is None if m=='missing' else r['followup_function'] is not None and r['baseline_function'] is not None if m=='change' else True),
            metric=Formula('AVG(t.followup_function-t.baseline_function)',lambda b,d,p:sum(r['followup_function']-r['baseline_function'] for r in b)/len(b) if b else None) if mode=='change' else 'average' if mode=='pain' else 'records',field='pain_score' if mode=='pain' else None,unit='number' if mode in ('change','pain') else 'count')
    for name,mode in [('recorded-activity-cost','cost'),('activity-phases','phase'),('unknown-cost-rate','missing'),('activity-minutes','minutes')]:
        add('outcomes_value',name,'care_activity',lambda p,m=mode:f"For payer {p['payer']} and {p['concept']} care activities during {p['year']}, "+{'cost':'sum the known activity costs in dollars: minutes times capacity_cost_cents_per_minute divided by 100. Report only recorded known costs.','phase':'sum known activity costs in dollars by phase, using minutes times capacity_cost_cents_per_minute divided by 100.','missing':'count activity rows with an unknown capacity_cost_cents_per_minute; Keep missing values explicit.','minutes':'sum recorded activity minutes.'}[m],
            lambda p,m=mode:payer(p)+f" AND t.concept='{p['concept']}' AND "+in_year(p,'event_date')+(" AND t.capacity_cost_cents_per_minute IS NULL" if m=='missing' else ''),
            lambda r,p,m=mode:same(r,p) and r['concept']==p['concept'] and year(r,p,'event_date') and (m!='missing' or r['capacity_cost_cents_per_minute'] is None),
            metric=Formula('SUM(t.minutes*t.capacity_cost_cents_per_minute)/100.0',lambda b,d,p:sum(r['minutes']*r['capacity_cost_cents_per_minute'] for r in b if r['minutes'] is not None and r['capacity_cost_cents_per_minute'] is not None)/100 if any(r['minutes'] is not None and r['capacity_cost_cents_per_minute'] is not None for r in b) else None) if mode in ('cost','phase') else 'sum' if mode=='minutes' else 'records',field='minutes' if mode=='minutes' else None,groups=('phase',) if mode=='phase' else (),unit='dollars' if mode in ('cost','phase') else 'count')
    return result


def build(output,*,fit_per_family=80,calibration_per_family=8,verification_per_family=8,learners=('typed','bottled')):
    for n in (fit_per_family,calibration_per_family,verification_per_family):
        if type(n) is not int or n<1:raise ValueError('Choose explicit positive per-family study sizes')
    if not learners or any(x not in ('typed','bottled') for x in learners):raise ValueError('This curriculum has typed and bottled code targets')
    output=C.checked_path(output);output.mkdir(parents=True,exist_ok=False)
    for variant in range(3):source(output/('source' if variant==0 else f'source-variant-{variant}'),variant)
    contexts=[T.Context(output/name) for name in ('source','source-variant-1','source-variant-2')]
    received=[Rows(c.path) for c in contexts];definitions=families();domains=Counter(f.domain for f in definitions)
    assert len(definitions)==104 and all(n==8 for n in domains.values()),domains
    counters=Counter();rows=[];oracles={};seen=set();duplicates=0;start=time.monotonic()
    prefixes=('','Please tell me: ','For our analytics review, ','Using the received data, ','For this report, ','I need to know: ','For the clinical analytics team, ','Please calculate: ')
    for f in definitions:
        index=counters[f.domain];counters[f.domain]+=1
        split='fit' if index<5 else 'calibration' if index==5 else 'verification'
        count={'fit':fit_per_family,'calibration':calibration_per_family,'verification':verification_per_family}[split]
        produced=0;j=0
        # Search a finite, explicit set of combinations. Duplicate wording is
        # discarded, never relabeled as more independent examples.
        for j in range(count*24):
            p=params(j+index*3);p['next_year']=p['year']+1
            question,sql,acceptance=f.render(p);question=prefixes[(j//12)%len(prefixes)]+question
            if question in seen:duplicates+=1;continue
            seen.add(question);family=f.domain+'/'+f.name;ident=digest([VERSION,family,question])
            analysis={'steps':[{'name':'answer_rows','purpose':question,'sql':sql}],'final':'answer_rows','returns':acceptance['returns'],'requirements':[{'quote':question,'applied_in':['answer_rows']}],'unresolved':[],'workflow':[]}
            rows.append({'id':ident,'question':question,'target':{'analysis':analysis},'reference_sql':sql,'acceptance':acceptance,'family':family,'split':split,'source_kind':'authored_engineering','source_digest':contexts[0].identity})
            oracles[ident]=[{'columns':list(acceptance['columns']),'rows':f.expected(d,p),'truncated':False} for d in received]
            produced+=1
            if produced==count:break
        if produced!=count:raise ValueError(f'Insufficient unique combinations for {family}: {produced}/{count}')
    manifests={}
    for learner in learners:
        tasks=[{**r,'target':r['target'] if learner=='typed' else r['reference_sql']} for r in rows]
        data=output/(learner+'-tasks.jsonl');data.write_text(''.join(json.dumps(r)+'\n' for r in tasks))
        spec={'schema':'approved-adapter-data/1','learner':learner,'source_identity':contexts[0].identity,
            'source_variants':[{'path':str(c.path),'source_identity':c.identity} for c in contexts[1:]],
            'training':{'iterations':len([r for r in tasks if r['split']=='fit'])//2,'batch_size':2,'num_layers':4,'rank':8,'learning_rate':5e-5,'max_tokens':900},
            'files':[{'path':str(data),'sha256':A.file_hash(data)}],
            'provenance':'New independently authored healthcare analytics structures on explicit authored source rows. SQL reference calculations are independently checked with Python over received rows on three different fixtures. No clinical treatment standard, private corpus or legacy answer-key supervision.'}
        path=output/(learner+'-manifest.json');path.write_text(json.dumps(spec,indent=2)+'\n');manifests[learner]=str(path)
    oracle_path=output/'independent-source-calculations.json';oracle_path.write_text(json.dumps(oracles)+'\n')
    receipt={'schema':VERSION,'domains':dict(domains),'families':len(definitions),'questions_per_learner':len(rows),'splits':dict(Counter(r['split'] for r in rows)),
        'family_splits':{s:len({r['family'] for r in rows if r['split']==s}) for s in ('fit','calibration','verification')},
        'duplicates_discarded':duplicates,'manifests':manifests,'independent_calculations':{'file':str(oracle_path),'sha256':A.file_hash(oracle_path)},
        'source_fixtures':[{'path':str(c.path),'identity':c.identity,'patients':len(c.patients)} for c in contexts],
        'provider_calls':0,'clinical_qualification':False,'seconds':time.monotonic()-start}
    (output/'build.json').write_text(json.dumps(receipt,indent=2)+'\n');return receipt


def verify(output,learner='typed'):
    output=C.checked_path(output);build=json.loads((output/'build.json').read_text());entry=build['independent_calculations']
    oracle_path=C.checked_path(entry['file'])
    if A.file_hash(oracle_path)!=entry['sha256']:raise ValueError('Independent source calculations changed')
    oracles=json.loads(oracle_path.read_text());spec,by,_=T.load(output/(learner+'-manifest.json'))
    contexts=[T.Context(s['path']) for s in build['source_fixtures']];failures=[];count=0;start=time.monotonic()
    if any(c.identity!=s['identity'] for c,s in zip(contexts,build['source_fixtures'])):raise ValueError('Authored source fixture changed after independent calculations')
    for split,rows in by.items():
        for r in rows:
            text=r['target'] if isinstance(r['target'],str) else json.dumps(r['target'])
            for i,ctx in enumerate(contexts):
                actual=ctx.query(r['reference_sql']);ok,reason=T.compare(actual,oracles[r['id']][i],r['acceptance'])
                native=ctx.assess(learner,r,text)
                if not ok or not native['acceptable']:failures.append({'task':r['id'],'family':r['family'],'question':r['question'],'fixture':i,'independent':reason,'native':native})
            count+=1
    receipt={'schema':'independent-healthcare-curriculum-verification/1','learner':learner,'cases':count,'source_fixtures':len(contexts),
        'acceptable':count-len({r['task'] for r in failures}),'failures':failures,'seconds':time.monotonic()-start,'provider_calls':0,'clinical_qualification':False,
        'basis':'Python calculations from raw received rows, reference SQL and native compiled targets agree. This is supervision verification, not language-model accuracy.'}
    (output/(learner+'-native-verification.json')).write_text(json.dumps(receipt,indent=2)+'\n');return receipt
