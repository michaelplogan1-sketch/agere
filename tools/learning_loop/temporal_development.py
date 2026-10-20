"""Explicit authored development questions; never a reserved or clinical benchmark.

No source discovery or corpus scan. Each expected result is specified before the
model runs and checked by separate reference SQL on this tiny received fixture.
Uses the existing offline trial/compiler/reviewer with no learned-shape capture.
"""
import argparse
import json
import os
from pathlib import Path
import sqlite3
import time

from . import prompt_optimizer as O
from clinical_core.agere.clinical import request_plan as R
from clinical_core.agere.clinical import sql_query


CASES = [
    ('access_latest', 'How many appointments scheduled in 2030 have a latest received status of no_show?',
     1, "SELECT count(*) FROM appointment WHERE final_status='no_show' AND scheduled_date BETWEEN '2030-01-01' AND '2030-12-31'"),
    ('access_history', 'How many appointments scheduled in 2030 ever had a no_show status, even if their latest status changed?',
     3, "SELECT count(*) FROM appointment WHERE ever_no_show=1 AND scheduled_date BETWEEN '2030-01-01' AND '2030-12-31'"),
    ('access_cancelled', 'How many appointments scheduled in 2030 have a latest received status of cancelled?',
     1, "SELECT count(*) FROM appointment WHERE final_status='cancelled' AND scheduled_date BETWEEN '2030-01-01' AND '2030-12-31'"),
    ('access_outcome', 'How many appointments scheduled in 2030 have the history-based outcome no_show?',
     2, "SELECT count(*) FROM appointment WHERE outcome='no_show' AND scheduled_date BETWEEN '2030-01-01' AND '2030-12-31'"),
    ('admission_readmit', 'How many inpatient stays admitted in 2029 were followed by a recorded readmission within 30 days?',
     1, "SELECT count(*) FROM stay WHERE type='inpatient' AND admit_date BETWEEN '2029-01-01' AND '2029-12-31' AND readmitted_30d=1"),
    ('admission_window', 'How many inpatient stays discharged in 2030 were admitted in 2029?',
     1, "SELECT count(*) FROM stay WHERE type='inpatient' AND admit_date BETWEEN '2029-01-01' AND '2029-12-31' AND discharge_date BETWEEN '2030-01-01' AND '2030-12-31'"),
    ('social_latest', 'How many patients have a positive latest dated transport screen as of the snapshot? Ignore older positive screens when a later negative screen exists.',
     1, "SELECT count(DISTINCT s.patient_id) FROM social_need s WHERE need='transport' AND positive=1 AND event_date=(SELECT max(t.event_date) FROM social_need t WHERE t.patient_id=s.patient_id AND t.need='transport' AND t.event_date<='2030-05-27')"),
    ('social_ever', 'How many patients ever had a positive transport screen in the received data?',
     2, "SELECT count(DISTINCT patient_id) FROM social_need WHERE need='transport' AND positive=1"),
    ('social_access', 'How many patients have a positive latest dated transport screen and an appointment whose latest status is no_show?',
     1, "SELECT count(DISTINCT s.patient_id) FROM social_need s WHERE need='transport' AND positive=1 AND event_date=(SELECT max(t.event_date) FROM social_need t WHERE t.patient_id=s.patient_id AND t.need='transport') AND EXISTS(SELECT 1 FROM appointment a WHERE a.patient_id=s.patient_id AND final_status='no_show')"),
    ('financial_service', 'What is the total allowed_cents for claims with service dates in 2029, including claims adjudicated in 2030?',
     35000, "SELECT sum(allowed_cents) FROM claim WHERE service_date BETWEEN '2029-01-01' AND '2029-12-31'"),
    ('financial_denial', 'How many claims have a latest status of paid and were ever denied?',
     1, "SELECT count(*) FROM claim WHERE final_status='paid' AND ever_denied=1"),
    ('provider_scope', 'What is the total allowed_cents for claims with service dates in 2029 among patients attributed to practice ORG1?',
     30000, "SELECT sum(c.allowed_cents) FROM claim c JOIN patient p USING(patient_id) WHERE service_date BETWEEN '2029-01-01' AND '2029-12-31' AND p.pcp_practice_id='ORG1'"),
    ('dose_stopped', 'How many patients have an active documented carvedilol regimen with a known daily dose in the last 180 days? A later stopped statement supersedes an older active statement; actual ingestion is not established.',
     1, None),
    ('dose_conflict', 'How many patients have conflicting doses in their latest same-day carvedilol statements in the last 180 days? Do not choose by record ID.',
     1, None),
    ('barrier_history', 'How many patients have an affirmed cost_access therapy-barrier mention dated in 2030? Exclude historical assertions; this counts mentions, not confirmed current contraindications.',
     1, "SELECT count(DISTINCT patient_id) FROM therapy_barrier WHERE assertion='affirmed' AND barrier_type='cost_access' AND event_date BETWEEN '2030-01-01' AND '2030-12-31'"),
    ('aco_population', 'How many patients attributed to practice ORG1 have a recorded inpatient stay admitted in 2029 and an allowed claim amount greater than zero with a service date in 2029?',
     2, "SELECT count(*) FROM patient p WHERE pcp_practice_id='ORG1' AND EXISTS(SELECT 1 FROM stay s WHERE s.patient_id=p.patient_id AND type='inpatient' AND admit_date BETWEEN '2029-01-01' AND '2029-12-31') AND EXISTS(SELECT 1 FROM claim c WHERE c.patient_id=p.patient_id AND allowed_cents>0 AND service_date BETWEEN '2029-01-01' AND '2029-12-31')"),
]


def recheck_named_metric(result, path):
    """The request permits supporting columns; accept only its named metric.

    Never use the first number or any coincidentally matching cell. Keep the
    original strict-format result so this verifier correction stays visible.
    """
    if result['id'] != 'financial_service' or 'query' not in result: return result
    shown = sql_query.execute(path, {'query': result['query'], 'returns': 'table'}, target=Path(path)/'semantic.sqlite3')
    rows = shown.get('rows') or []
    result['strict_scalar_passed'] = result['passed']
    result['returned_table'] = rows
    if (len(rows) == 1 and 'total_allowed_cents' in rows[0] and not shown.get('truncated')
            and result.get('review', {}).get('equivalent') is True
            and not result.get('review', {}).get('issues') and not result.get('unresolved') and not result.get('checks')):
        result['passed'] = rows[0]['total_allowed_cents'] == 35000
        result['verifier_note'] = 'Question permits extra columns; compare explicitly named total_allowed_cents, not scalar-only shape.'
    return result


def fixture(path):
    path = Path(path); path.mkdir(parents=True, exist_ok=True)
    target = path/'semantic.sqlite3'
    if target.exists(): raise ValueError('Use a new fixture directory; previous evidence is immutable')
    with sqlite3.connect(target) as db:
        db.executescript('''
        CREATE TABLE meta(key,value);
        INSERT INTO meta VALUES('version','authored-temporal/1'),('source_digest','authored-temporal/1'),('as_of','2030-05-27');
        CREATE TABLE patient(patient_id,birth_date,pcp_practice_id,payer_id);
        INSERT INTO patient VALUES('A','1960-01-01','ORG1','P1'),('B','1965-01-01','ORG1','P1'),('C','1970-01-01','ORG2','P2'),('D',NULL,'ORG2','P2');
        CREATE TABLE appointment(appointment_id,patient_id,scheduled_date,final_status,ever_no_show,kept,outcome);
        INSERT INTO appointment VALUES('a','A','2030-01-01','cancelled',1,0,'no_show'),('b','B','2030-01-01','no_show',1,0,'no_show'),('c','C','2030-01-01','completed',1,1,'kept');
        CREATE TABLE stay(patient_id,type,admit_date,discharge_date,readmitted_30d,los_days);
        INSERT INTO stay VALUES('A','inpatient','2029-12-30','2030-01-03',1,4),('B','inpatient','2029-05-01','2029-05-02',0,1),('C','observation','2029-06-01','2029-06-01',NULL,0);
        CREATE TABLE social_need(patient_id,need,positive,event_date,record_id);
        INSERT INTO social_need VALUES('A','transport',1,'2029-01-01','s1'),('A','transport',0,'2030-01-01','s2'),('B','transport',1,'2030-01-01','s3');
        CREATE TABLE claim(claim_id,patient_id,service_date,first_adjudication_date,allowed_cents,final_status,ever_denied);
        INSERT INTO claim VALUES('c1','A','2029-12-01','2030-01-01',10000,'paid',1),('c2','B','2029-05-01','2029-05-02',20000,'paid',0),('c3','C','2029-01-01','2029-01-02',5000,'denied',1),('c4','D','2030-01-01','2030-01-02',90000,'paid',0);
        CREATE TABLE therapy_barrier(patient_id,drug_class,barrier_type,assertion,event_date,record_id,method);
        INSERT INTO therapy_barrier VALUES('A','beta_blocker','cost_access','historical','2030-01-01','t1','rules'),('B','beta_blocker','cost_access','affirmed','2030-01-01','t2','rules');
        CREATE TABLE record(record_id,patient_id,stream,event_date,payload);
        ''')
        for rid,pid,day,status,amount in [('m1','A','2030-01-01','active','25 mg'),('m2','A','2030-02-01','stopped','25 mg'),
                ('m3','B','2030-01-01','active','6.25 mg'),('m4','C','2030-01-01','active','6.25 mg'),('m5','C','2030-01-01','active','25 mg')]:
            db.execute('INSERT INTO record VALUES(?,?,?,?,?)', (rid,pid,'medication_lists',day,json.dumps(
                dict(event_date=day,name='carvedilol',dose=amount,frequency='BID',list_status=status))))
        # Independent exact values above also reconcile with these literal source queries.
        for ident,_,expected,query in CASES:
            if not ident.startswith('dose_'):
                assert db.execute(query).fetchone()[0] == expected, ident
    return path


def run(path, out, model):
    out = Path(out); out.mkdir(parents=True, exist_ok=True)
    if (out/'results.json').exists(): raise ValueError('Do not overwrite a previous experiment')
    catalog, version, rows = R.Catalog.load(path), O.candidate(), []
    for ident, question, expected, reference in CASES:
        case = dict(id=ident,family=ident,split='train',provenance='authored_development',question=question,expected={'value':expected})
        start = time.monotonic()
        result = recheck_named_metric(O.trial(catalog,case,version,model), path)
        result.update(seconds=round(time.monotonic()-start,3), question=question, expected=expected,
                      reference_sql=reference if not ident.startswith('dose_') else None,
                      reference_basis='Hand-authored source rows and declared semantics; not independent clinical review')
        rows.append(result)
        (out/'results.json').write_text(json.dumps(dict(schema='authored-temporal-questions/1',trials=rows,score=O.score(rows)),indent=2)+'\n')
        print(json.dumps({k:result.get(k) for k in ('id','passed','actual','error','calls','seconds')}),flush=True)
    return rows


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--out',required=True)
    parser.add_argument('--execute',action='store_true')
    args=parser.parse_args()
    out=Path(args.out); path=fixture(out/'fixture')
    (out/'declared-cases.json').write_text(json.dumps(CASES,indent=2)+'\n')
    if not args.execute: return
    if not os.environ.get('ANTHROPIC_API_KEY'): raise ValueError('Existing hosted credentials required')
    from clinical_core.agere.gateway import BudgetLedger, PersistentGateway
    from clinical_core.agere.clinical.nl2sql import HostedModel
    receipts=out/'provider-receipts'; receipts.mkdir()
    ledger=BudgetLedger(out/'usage.sqlite3')
    try:
        model=HostedModel(ledger,receipts,model=PersistentGateway.MODELS['claude'],request_max_bytes=None)
        run(path,out,model)
    finally: ledger.close()


if __name__ == '__main__': main()
