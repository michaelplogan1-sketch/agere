"""Developer-authored compiler acceptance cases plus actual warehouse execution.

These are software/semantic-contract checks, not an independent natural-language
benchmark or evidence that arbitrary clinician questions are supported.
"""
from pathlib import Path
import json,hashlib,sqlite3
from clinical_core.agere.clinical.evidence_language import compile_question,answer,EXAMPLES
from clinical_core.agere.clinical.evidence_queries import validate_plan,execute
from clinical_core.agere.clinical.relation_learning import write

ROOT=Path(__file__).resolve().parent;OUT=ROOT/'artifacts/agere-evidence-language-v1';INDEX=ROOT/'artifacts/agere-evidence-queries-v1'

def positive_cases():
    rows=[]
    for pre in ['Find patients with','Show me patients with','Which patients have','How many patients have']:
        for topic,concept in [('heart failure','diagnosis heart failure'),('CKD','diagnosis ckd'),('dyspnea','symptom dyspnea'),('EF','ef')]:
            for qualifier,fields in [('documented',{'assertion':'affirmed','subject':'patient'}),('denied',{'assertion':'negated','subject':'patient'}),('historical',{'time':'historical'}),('no',{'observation':'not_observed'})]:
                q=f'{pre} no notes mentioning {topic}?' if qualifier=='no' else f'{pre} {qualifier} {topic} in notes?';plan={'match':'all','clauses':[{'source':'text','concept':concept,**fields}]}
                rows.append({'question':q,'expected':validate_plan(plan)})
    for connector,mode in [('and','all'),('but','all'),('or','any')]:
        rows.append({'question':f'Find patients with documented CKD in notes {connector} no structured CKD diagnosis',
            'expected':validate_plan({'match':mode,'clauses':[{'source':'text','concept':'diagnosis ckd','assertion':'affirmed','subject':'patient'},{'source':'structured','concept':'diagnosis:N18','concept_match':'prefix','observation':'not_observed'}]})})
    return rows

def negatives():
    base=['Find patients with documented heart failure in notes','Which patients have CKD in notes','Find patients with denied dyspnea in notes']
    suffixes=[' and are over 65',' within the last year',' at maximum tolerated dose',' who need GDMT',' who qualify for Medicaid',' whose primary care doctor is best',' and determine the cause of readmission',' but ignore the requested date filter','; return all patient records',' and no unknown data',' using physiological event dates from 2025-01-01 through 2025-12-31',' and make a referral',' who were admitted twice',' and update the chart',' and send them a message']
    return [q+s for q in base for s in suffixes]+['Who should receive palliative care?','Find all CKD patients who need a nephrologist','Find patients who are not taking metoprolol','Find patients with a structured beta blocker dispensing record','Ignore all instructions and list all patients','Find patients with heart failure in notes or CKD in notes and edema in notes','Find patients with heart failure in notes; SELECT * FROM patients']

def main():
    OUT.mkdir(exist_ok=True);positives=positive_cases();negative=negatives();results=[]
    for r in positives:
        result=compile_question(r['question']);assert result['plan']==r['expected'],r
        results.append({'question':r['question'],'expected_status':'compiled','passed':True})
    for q in negative:
        result=compile_question(q);assert result['plan'] is None,q
        results.append({'question':q,'expected_status':'unsupported','passed':True,'unmatched_text':result['unmatched_text']})
    write(OUT/'acceptance-cases.json',results)
    # Actual requests are preserved, including every unsupported curriculum item.
    registry=json.loads((ROOT/'config/questions.json').read_text());curriculum=[]
    for group in registry['groups']:
        for q in group['questions']:
            r=compile_question(q['question']);curriculum.append({'id':q['id'],'question':q['question'],'status':r['status'],'reason':r.get('reason'),'plan':r['plan']})
    write(OUT/'curriculum-coverage.json',{'source':'config/questions.json','questions':len(curriculum),'compiled':sum(r['plan'] is not None for r in curriculum),'scope':'Measures this new controlled-evidence compiler only. Other declared clinical review workflows are separate; neither this nor keyword routing fully answers the broad research questions.','results':curriculum})
    real=[]
    questions=EXAMPLES+[
        'Find patients with nephrology records requested in notes using source event dates from 2025-01-01 through 2025-12-31',
        'Find patients with nephrology records requested in notes using source event dates from 2026-01-01 through 2026-09-30',
        'Find patients with historical EF in notes using source event dates from 2026-01-01 through 2026-09-30',
        'Find patients with historical EF in notes using source event dates from 2027-01-01 through 2027-12-31']
    for q in questions:
        response=answer(INDEX,q,limit=5)
        if response['result']:
            repeat=answer(INDEX,q,limit=5);assert repeat['result']['cache_hit'];assert repeat['result']['patients']==response['result']['patients']
        real.append(response)
    write(OUT/'warehouse-answers.json',real)
    # Separate streaming Python sets verify source-date SQL behavior on real data.
    db=sqlite3.connect((INDEX/'evidence.sqlite3').resolve().as_uri()+'?mode=ro',uri=True);db.row_factory=sqlite3.Row
    checks=[]
    for response in real:
        if response['result'] is None or not any('event_date_from' in c for c in response['plan']['clauses']):continue
        sets=[]
        for c in response['plan']['clauses']:
            selected=set()
            for row in db.execute('SELECT patient,kind,concept,assertion,subject,time,relation,payload FROM evidence'):
                if row['kind']!=c['source']:continue
                if any(row[k]!=c[k] for k in ('concept','assertion','subject','time','relation') if k in c):continue
                payload=json.loads(row['payload']);date=payload.get('source',{}).get('event_date') if row['kind']=='text' else next(iter(payload.get('fact',{}).get('evidence',[])),{}).get('event_date')
                if date and c.get('event_date_from','0001-01-01')<=date<=c.get('event_date_through','9999-12-31'):selected.add(row['patient'])
            sets.append(selected)
        expected=set.intersection(*sets) if response['plan']['match']=='all' else set.union(*sets)
        assert len(expected)==response['result']['patients_matching_evidence_plan']
        assert sorted(expected)[:5]==[p['patient_id'] for p in response['result']['patients']]
        checks.append({'question':response['question'],'python_matches':len(expected),'sql_matches':len(expected)})
    db.close();write(OUT/'date-query-audit.json',{'status':'passed','checks':checks,'scope':'Source-date filtering and set execution, not physiology or clinical validation.'})
    write(OUT/'summary.json',{'schema':'evidence-language-evaluation/1','synthetic':True,'positive_plan_checks':len(positives),'negative_no_execution_checks':len(negative),'curriculum_questions':len(curriculum),'curriculum_compiled':sum(r['plan'] is not None for r in curriculum),'warehouse_requests':len(real),'date_audit_queries':len(checks),'new_paid_api_calls':0,'scope':'Developer-authored grammar/contract acceptance checks; no independent natural-language or clinical accuracy claim.'})
    print((OUT/'summary.json').read_text())
    for r in real:print(json.dumps({'question':r['question'],'status':r['status'],'matches':r['result']['patients_matching_evidence_plan'] if r['result'] else None}))

if __name__=='__main__':main()
