"""Original cross-condition evidence tasks for the existing SQL/typed learners.

Synthetic documentary states are not medical indications or guideline thresholds.
No publisher text, protected questions or model-generated clinical truth enters
supervision. Python over received rows independently checks every SQL target.
"""
from collections import Counter,defaultdict
from dataclasses import dataclass
import json
from pathlib import Path
import random
import sqlite3
import time

from clinical_core.contracts import digest
from clinical_core.agere.clinical import adapter_learning as A,component_learning as C
from tools.training import adapter_tasks as T,healthcare_curriculum as H

VERSION='authored-evidence-analytics/1'


def source(path,variant):
    H.source(path,variant)
    rng=random.Random(92581+variant*137)
    with sqlite3.connect(path/'semantic.sqlite3') as db:
        db.executescript('''
          CREATE TABLE care_review(review_id TEXT,patient_id TEXT,condition TEXT,event_date TEXT,
            subject TEXT,assertion_status TEXT,medication_state TEXT,current_daily_dose REAL,
            documented_target_dose REAL,dose_unit TEXT,documented_reason TEXT,reason_status TEXT,
            rule_status TEXT,rule_edition TEXT,rule_verified INTEGER,required_input_status TEXT);
          CREATE TABLE care_work(patient_id TEXT,condition TEXT,program TEXT,referral_date TEXT,
            completion_date TEXT,enrollment_state TEXT,consent_state TEXT,coverage_status TEXT,device_state TEXT);
          CREATE TABLE care_contact(patient_id TEXT,condition TEXT,clinician_id TEXT,role TEXT,status TEXT,event_date TEXT);
        ''')
        patients=[r[0] for r in db.execute('SELECT patient_id FROM patient')]
        for i,pid in enumerate(patients):
            for j,condition in enumerate(H.CONDITIONS):
                for k,day in enumerate(('2029-12-01','2030-03-12','2030-05-12','2030-06-12')):
                    # Nonclinical arbitrary dose values and recorded goals: no
                    # drug-specific dose/indication is inferred from a source.
                    dose=rng.choice((None,2.,5.,10.));goal=rng.choice((None,5.,10.))
                    reason=rng.choice((None,'','dizziness recorded','patient preference recorded','awaiting laboratory result'))
                    row=(f'R-{i}-{j}-{k}',pid,condition,day,rng.choice(('patient','patient','family')),
                         rng.choice(('confirmed','confirmed','negated','uncertain')),
                         rng.choice(('active','held','stopped','unknown')),dose,goal,
                         rng.choice(('recorded_units_per_day','recorded_units_per_day','unknown')),
                         reason,rng.choice(('documented','not_documented','unassessed')),
                         rng.choice(('final','draft','superseded')),rng.choice(('edition-a','edition-b')),
                         rng.choice((0,1)),rng.choice(('complete','missing','unknown')))
                    db.execute('INSERT INTO care_review VALUES('+','.join('?' for _ in row)+')',row)
                row=(pid,condition,'support_review','2030-03-12',rng.choice((None,'2030-04-12','2030-06-12')),
                     rng.choice(('enrolled','referred','waitlisted','declined','unknown')),
                     rng.choice(('documented','declined','unknown')),rng.choice(('verified','not_eligible','unknown')),
                     rng.choice(('requested','shipped','delivered','canceled')))
                db.execute('INSERT INTO care_work VALUES(?,?,?,?,?,?,?,?,?)',row)
                for role in ('assigned','visit'):
                    row=(pid,condition,f'D{i%12}',role,rng.choice(('active','ended')) if role=='assigned' else rng.choice(('completed','canceled','scheduled')),
                         rng.choice(('2029-12-01','2030-03-12','2030-06-12')))
                    db.execute('INSERT INTO care_contact VALUES(?,?,?,?,?,?)',row)


def read(path):
    with sqlite3.connect((path/'semantic.sqlite3').as_uri()+'?mode=ro',uri=True) as db:
        db.row_factory=sqlite3.Row
        return {table:[dict(r) for r in db.execute('SELECT * FROM '+table)] for table in ('care_review','care_work','care_contact')}


@dataclass(frozen=True)
class Family:
    name:str
    table:str
    wording:str
    sql:str
    predicate:object
    split:str='fit'
    shape:str='count'
    grouped:str|None=None
    denominator:object=None


def families():
    below=lambda r:r['current_daily_dose'] is not None and r['documented_target_dose'] is not None and r['dose_unit']=='recorded_units_per_day' and r['current_daily_dose']<r['documented_target_dose']
    b="current_daily_dose<documented_target_dose AND dose_unit='recorded_units_per_day'"
    documented=lambda r:r['reason_status']=='documented' and bool((r['documented_reason'] or '').strip())
    d="reason_status='documented' AND TRIM(COALESCE(documented_reason,''))<>''"
    return [
      Family('confirmed-patient-evidence','care_review','How many distinct patients have confirmed patient-subject {condition} care_review records through {date}?',"subject='patient' AND assertion_status='confirmed'",lambda r:r['subject']=='patient' and r['assertion_status']=='confirmed'),
      Family('below-goal-with-reason','care_review','How many distinct patients have {condition} care_review records below the recorded dose goal, using recorded_units_per_day, with a documented nonempty reason through {date}?',b+' AND '+d,lambda r:below(r) and documented(r)),
      Family('below-goal-reason-absent','care_review','How many distinct patients have {condition} care_review records below the recorded dose goal, using recorded_units_per_day, with a missing or blank reason text through {date}?',b+" AND (documented_reason IS NULL OR TRIM(documented_reason)='')",lambda r:below(r) and not (r['documented_reason'] or '').strip()),
      Family('uncertain-is-not-negative','care_review','How many distinct patients have uncertain {condition} care_review records through {date}?',"assertion_status='uncertain'",lambda r:r['assertion_status']=='uncertain'),
      Family('family-subject-distinct','care_review','How many distinct patients have family-subject {condition} care_review records through {date}?',"subject='family'",lambda r:r['subject']=='family'),
      Family('missing-prerequisite-input','care_review','How many distinct patients have {condition} care_review records with missing required inputs through {date}?',"required_input_status='missing'",lambda r:r['required_input_status']=='missing'),
      Family('final-source-with-verification','care_review','How many distinct patients have {condition} care_review records with final rule_status and rule_verified=1 through {date}?',"rule_status='final' AND rule_verified=1",lambda r:r['rule_status']=='final' and r['rule_verified']==1),
      Family('reason-text-is-not-attestation','care_review','How many distinct patients have {condition} care_review records with nonempty reason text but reason_status not_documented through {date}?',"TRIM(COALESCE(documented_reason,''))<>'' AND reason_status='not_documented'",lambda r:bool((r['documented_reason'] or '').strip()) and r['reason_status']=='not_documented'),
      Family('documented-dose-goal-unknown','care_review','How many distinct patients have {condition} care_review records with missing documented_target_dose through {date}?',"documented_target_dose IS NULL",lambda r:r['documented_target_dose'] is None),
      Family('coverage-still-unknown','care_work','How many distinct patients have {condition} care_work records with unknown coverage_status and documented consent through {date}?',"coverage_status='unknown' AND consent_state='documented'",lambda r:r['coverage_status']=='unknown' and r['consent_state']=='documented'),
      Family('held-with-documented-reason','care_review','How many distinct patients have held {condition} care_review medications with a documented nonempty reason through {date}?',"medication_state='held' AND "+d,lambda r:r['medication_state']=='held' and documented(r),'calibration'),
      Family('explicit-negative-patients','care_review','Which patients have negated patient-subject {condition} care_review records through {date}?',"assertion_status='negated' AND subject='patient'",lambda r:r['assertion_status']=='negated' and r['subject']=='patient','calibration','patient_set'),
      Family('not-enrolled-does-not-mean-declined','care_work','How many distinct patients have referred {condition} care_work enrollment_state and unknown consent_state through {date}?',"enrollment_state='referred' AND consent_state='unknown'",lambda r:r['enrollment_state']=='referred' and r['consent_state']=='unknown','calibration'),
      Family('percent-documented-reasons','care_review','What percent of {condition} care_review records below the recorded dose goal, using recorded_units_per_day, have a documented nonempty reason through {date}?',b,documented,'calibration','value',denominator=below),
      Family('latest-observation-not-any-history','care_review','How many distinct patients have active medication_state in their latest {condition} care_review record through {date}?',"medication_state='active'",lambda r:r['medication_state']=='active','verification','latest'),
      Family('draft-review-population','care_review','Which patients have draft rule_status and complete required inputs in {condition} care_review records through {date}?',"rule_status='draft' AND required_input_status='complete'",lambda r:r['rule_status']=='draft' and r['required_input_status']=='complete','verification','patient_set'),
      Family('grouped-exception-evidence','care_review','For {condition} care_review records below the recorded dose goal, using recorded_units_per_day, count distinct patients by reason_status through {date}?',b,below,'verification','table','reason_status'),
      Family('referred-without-completed-service','care_work','Which patients have referred {condition} care_work enrollment_state and no completion by {date}?',"enrollment_state='referred' AND (completion_date IS NULL OR completion_date>'{date}')",lambda r:r['enrollment_state']=='referred','verification','patient_set'),
      Family('device-delivery-distinct-from-consent','care_work','How many distinct patients have delivered device_state with unknown consent_state in {condition} care_work through {date}?',"device_state='delivered' AND consent_state='unknown'",lambda r:r['device_state']=='delivered' and r['consent_state']=='unknown','verification'),
      Family('completed-contact-not-assignment','care_contact','How many distinct patients have completed visit-role {condition} care_contact records through {date}?',"role='visit' AND status='completed'",lambda r:r['role']=='visit' and r['status']=='completed','verification'),
    ]


def render(f,condition,cutoff,prefix=''):
    q=prefix+f.wording.format(condition=condition,date=cutoff)
    clock='referral_date' if f.table=='care_work' else 'event_date'
    where=f"condition='{condition}' AND {clock}<='{cutoff}'"
    relation=f.table
    if f.shape=='latest':
        relation=f"(SELECT *,ROW_NUMBER() OVER(PARTITION BY patient_id ORDER BY event_date DESC,review_id DESC) AS rn FROM care_review WHERE {where})"
        where='rn=1'
    predicate=f.sql.format(date=cutoff)
    if f.shape=='value':
        expression="100.0*SUM(CASE WHEN reason_status='documented' AND TRIM(COALESCE(documented_reason,''))<>'' THEN 1 ELSE 0 END)/NULLIF(COUNT(*),0) AS answer"
        acceptance={'returns':'value','columns':{'answer':{'unit':'percent','decimals':1}}}
    elif f.shape=='patient_set':
        expression='DISTINCT patient_id';acceptance={'returns':'patient_set','columns':{'patient_id':{'unit':'id'}},'keys':['patient_id']}
    else:
        expression='COUNT(DISTINCT patient_id) AS answer';acceptance={'returns':'count','columns':{'answer':{'unit':'count'}}}
    if f.grouped:
        expression=f.grouped+', '+expression;acceptance={'returns':'table','columns':{f.grouped:{'unit':'text'},'answer':{'unit':'count'}},'keys':[f.grouped]}
    sql=f'SELECT {expression} FROM {relation} WHERE {where} AND ({predicate})'
    if f.grouped:sql+=' GROUP BY '+f.grouped
    return q,sql,acceptance


def expected(f,received,condition,cutoff):
    clock='referral_date' if f.table=='care_work' else 'event_date'
    rows=[r for r in received[f.table] if r['condition']==condition and r[clock]<=cutoff]
    if f.shape=='latest':
        latest={}
        for r in rows:
            old=latest.get(r['patient_id'])
            if old is None or (r['event_date'],r['review_id'])>(old['event_date'],old['review_id']):latest[r['patient_id']]=r
        rows=list(latest.values())
    if f.denominator:
        rows=[r for r in rows if f.denominator(r)]
        return [{'answer':100*sum(f.predicate(r) for r in rows)/len(rows) if rows else None}]
    rows=[r for r in rows if f.predicate(r)]
    if f.name=='referred-without-completed-service':rows=[r for r in rows if r['completion_date'] is None or r['completion_date']>cutoff]
    if f.shape=='patient_set':return [{'patient_id':p} for p in sorted({r['patient_id'] for r in rows})]
    if f.grouped:
        groups=defaultdict(set)
        for r in rows:groups[r[f.grouped]].add(r['patient_id'])
        return [{f.grouped:key,'answer':len(values)} for key,values in sorted(groups.items())]
    return [{'answer':len({r['patient_id'] for r in rows})}]


def build(output,*,fit_per_family=48,calibration_per_family=12,verification_per_family=16):
    output=C.checked_path(output);output.mkdir(parents=True,exist_ok=False);started=time.monotonic()
    contexts=[];received=[]
    for variant in range(3):
        path=output/('source' if variant==0 else f'source-variant-{variant}');source(path,variant)
        contexts.append(T.Context(path));received.append(read(path))
    rows=[];oracles={};seen=set()
    prefixes=('','For care-team review, ','Using only the received evidence, ','For population planning, ')
    for f in families():
        count={'fit':fit_per_family,'calibration':calibration_per_family,'verification':verification_per_family}[f.split]
        produced=0
        for j in range(len(H.CONDITIONS)*3*len(prefixes)):
            condition=H.CONDITIONS[j%len(H.CONDITIONS)];cutoff=('2029-12-31','2030-03-31',H.AS_OF)[j//len(H.CONDITIONS)%3]
            q,sql,acceptance=render(f,condition,cutoff,prefixes[j//(len(H.CONDITIONS)*3)])
            if q in seen:continue
            seen.add(q);ident=digest([VERSION,f.name,q]);analysis={'steps':[{'name':'answer_rows','purpose':q,'sql':sql}],'final':'answer_rows','returns':acceptance['returns'],'requirements':[{'quote':q,'applied_in':['answer_rows']}],'unresolved':[],'workflow':[]}
            rows.append({'id':ident,'question':q,'target':{'analysis':analysis},'reference_sql':sql,'acceptance':acceptance,'family':'evidence/'+f.name,'split':f.split,'source_kind':'authored_engineering','source_digest':contexts[0].identity})
            oracles[ident]=[{'columns':list(acceptance['columns']),'rows':expected(f,r,condition,cutoff),'truncated':False} for r in received]
            produced+=1
            if produced==count:break
        if produced!=count:raise ValueError('Explicit study allocation exceeds distinct available questions')
    manifests={}
    for learner in ('typed','bottled'):
        tasks=[{**r,'target':r['target'] if learner=='typed' else r['reference_sql']} for r in rows]
        data=output/(learner+'-tasks.jsonl');data.write_text(''.join(json.dumps(r)+'\n' for r in tasks))
        spec={'schema':'approved-adapter-data/1','learner':learner,'source_identity':contexts[0].identity,'source_variants':[{'path':str(c.path),'source_identity':c.identity} for c in contexts[1:]],'training':{'iterations':120,'batch_size':4,'num_layers':4,'rank':8,'learning_rate':5e-5,'max_tokens':900},'files':[{'path':str(data),'sha256':A.file_hash(data)}],'provenance':'Agere-authored documentary analytics, independently verified Python/SQL/native targets on three source fixtures. No publisher text, medical thresholds, treatment-suitability labels, protected questions or generated clinical truth. Recorded dose goals are fictional source facts.'}
        path=output/(learner+'-manifest.json');path.write_text(json.dumps(spec,indent=2)+'\n');manifests[learner]=str(path)
    oracle_path=output/'independent-source-calculations.json';oracle_path.write_text(json.dumps(oracles)+'\n')
    receipt={'schema':VERSION,'questions_per_learner':len(rows),'families':len(families()),'conditions':list(H.CONDITIONS),'splits':dict(Counter(r['split'] for r in rows)),'family_splits':dict(Counter(f.split for f in families())),'source_fixtures':[{'path':str(c.path),'identity':c.identity,'patients':len(c.patients)} for c in contexts],'manifests':manifests,'independent_calculations':{'file':str(oracle_path),'sha256':A.file_hash(oracle_path)},'provider_calls':0,'clinical_qualification':False,'seconds':time.monotonic()-started}
    (output/'build.json').write_text(json.dumps(receipt,indent=2)+'\n');return receipt


def verify(output,learner):
    # The existing independent/native verifier accepts this shared manifest
    # format; no special clinical evaluator or answer-key scorer is added.
    return H.verify(output,learner)
