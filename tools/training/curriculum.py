"""New, source-bound analytics task curriculum; no benchmark or answer-store input.

This exercises reusable interpretation/SQL behavior across authored data and a
versioned public terminology snapshot. It does not create clinical treatment truth.
"""
import argparse
from collections import Counter
import hashlib
import json
from pathlib import Path
import sqlite3
import time
from clinical_core.contracts import digest
from clinical_core.agere.clinical import component_learning as C,adapter_learning as A
from tools.training import adapter_tasks as T


def source(path,reference=None,*,variant=False):
    path=C.checked_path(path);path.mkdir(parents=True,exist_ok=False)
    db=sqlite3.connect(path/'semantic.sqlite3')
    db.executescript('''CREATE TABLE meta(key,value);INSERT INTO meta VALUES('as_of','2030-05-27'),('source_digest','authored-analytics-curriculum-v1');
      CREATE TABLE patient(patient_id,birth_date,sex_reported,coverage_type,payer_id);
      CREATE TABLE claim(claim_id,patient_id,payer,service_date,allowed_cents,ever_denied);
      CREATE TABLE appointment(patient_id,scheduled_date,kept,final_status);
      CREATE TABLE drug_reference(display_name,strength_form,rxcui,source_sha256);''')
    for i in range(1,18 if variant else 25):
        db.execute('INSERT INTO patient VALUES(?,?,?,?,?)',(f'AUTHORED-{i}',f'{1940+i}-05-27','F' if (i+int(variant))%2 else 'M','medicare' if (i+int(variant))%3 else 'commercial',f'P{i%4}'))
        for year in range(2010,2030):
            db.execute('INSERT INTO claim VALUES(?,?,?,?,?,?)',(f'C-{i}-{year}',f'AUTHORED-{i}',f'P{i%4}',f'{year}-03-01',i*10237+year+int(variant)*10000,i%3==int(variant)))
            if variant and i%2:
                db.execute('INSERT INTO claim VALUES(?,?,?,?,?,?)',(f'EXTRA-{i}-{year}',f'AUTHORED-{i}',f'P{i%4}',f'{year}-06-01',i*7111+year,i%3==int(variant)))
            kept=(i+int(variant))%(5 if variant else 4)!=0
            db.execute('INSERT INTO appointment VALUES(?,?,?,?)',(f'AUTHORED-{i}',f'{year}-04-01',kept,'completed' if kept else 'no_show'))
    provenance=None
    if reference:
        reference=C.checked_path(reference);spec=json.loads(reference.read_text());data=A.checked_child(reference.parent,spec['data'])
        if A.file_hash(data)!=spec['sha256']:raise ValueError('Public reference snapshot changed')
        rows=json.loads(data.read_text())
        db.executemany('INSERT INTO drug_reference VALUES(?,?,?,?)',[(r['display_name'],r['strength_form'],r['rxcui'],r['source_sha256']) for r in rows]);provenance=spec
    db.commit();db.close();return provenance


def partition(family):
    number=int(digest(['agere-authored-analytics-curriculum-v1',family]),16)/(16**64)
    return 'fit' if number<.6 else 'calibration' if number<.8 else 'verification'


def templates():
    count={'unit':'count'};money={'unit':'dollars','decimals':2};percent={'unit':'percent','decimals':1}
    return [
      ('patient-sex',lambda x:(f'How many recorded patients have sex_reported {x["sex"]}?',f"SELECT COUNT(DISTINCT patient_id) AS patients FROM patient WHERE sex_reported='{x['sex']}'",{'patients':count},[])),
      ('patient-coverage',lambda x:(f'How many recorded patients have {x["coverage"]} coverage?',f"SELECT COUNT(DISTINCT patient_id) AS patients FROM patient WHERE coverage_type='{x['coverage']}'",{'patients':count},[])),
      ('patient-payer',lambda x:(f'How many received patients belong to payer {x["payer"]}?',f"SELECT COUNT(DISTINCT patient_id) AS patients FROM patient WHERE payer_id='{x['payer']}'",{'patients':count},[])),
      ('patient-birth-year',lambda x:(f'List patients born before January 1, {x["birth_year"]}.',f"SELECT patient_id FROM patient WHERE birth_date<'{x['birth_year']}-01-01'",{'patient_id':{'unit':'id'}},['patient_id'])),
      ('claim-year-count',lambda x:(f'How many received claims have service dates during {x["year"]}?',f"SELECT COUNT(*) AS claims FROM claim WHERE service_date>='{x['year']}-01-01' AND service_date<'{x['year']+1}-01-01'",{'claims':count},[])),
      ('claim-denial-share',lambda x:(f'What percent of received claims in {x["year"]} have ever been denied?',f"SELECT 100.0*SUM(ever_denied)/NULLIF(COUNT(*),0) AS denied_percent FROM claim WHERE service_date>='{x['year']}-01-01' AND service_date<'{x['year']+1}-01-01'",{'denied_percent':percent},[])),
      ('claim-payer-cost',lambda x:(f'How many dollars were allowed for payer {x["payer"]} in {x["year"]}?',f"SELECT SUM(allowed_cents)/100.0 AS allowed_dollars FROM claim WHERE payer='{x['payer']}' AND service_date>='{x['year']}-01-01' AND service_date<'{x['year']+1}-01-01'",{'allowed_dollars':money},[])),
      ('claim-spend-average',lambda x:(f'What was the average allowed amount in dollars for claims in {x["year"]}?',f"SELECT AVG(allowed_cents)/100.0 AS average_dollars FROM claim WHERE service_date>='{x['year']}-01-01' AND service_date<'{x['year']+1}-01-01'",{'average_dollars':money},[])),
      ('claim-threshold-yesno',lambda x:(f'Were any received claims for payer {x["payer"]} allowed more than {x["amount"]} dollars?',f"SELECT EXISTS(SELECT 1 FROM claim WHERE payer='{x['payer']}' AND allowed_cents>{x['amount']*100}) AS any_claim",{'any_claim':{'unit':'yes_no'}},[])),
      ('claim-denial-breakdown',lambda x:(f'Count claims ever denied in {x["year"]}, grouped by payer.',f"SELECT payer,COUNT(*) AS claims FROM claim WHERE ever_denied=1 AND service_date>='{x['year']}-01-01' AND service_date<'{x['year']+1}-01-01' GROUP BY payer",{'payer':{'unit':'id'},'claims':count},['payer'])),
      ('appointment-no-show',lambda x:(f'What percent of appointments in {x["year"]} were no-shows?',f"SELECT 100.0*SUM(final_status='no_show')/NULLIF(COUNT(*),0) AS no_show_percent FROM appointment WHERE scheduled_date>='{x['year']}-01-01' AND scheduled_date<'{x['year']+1}-01-01'",{'no_show_percent':percent},[])),
      ('appointment-kept',lambda x:(f'How many appointments were kept in {x["year"]}?',f"SELECT COUNT(*) AS appointments FROM appointment WHERE kept=1 AND scheduled_date>='{x['year']}-01-01' AND scheduled_date<'{x['year']+1}-01-01'",{'appointments':count},[])),
      ('appointment-status',lambda x:(f'Count appointments in {x["year"]} by final_status.',f"SELECT final_status,COUNT(*) AS appointments FROM appointment WHERE scheduled_date>='{x['year']}-01-01' AND scheduled_date<'{x['year']+1}-01-01' GROUP BY final_status",{'final_status':{'unit':'text'},'appointments':count},['final_status'])),
      ('terminology-strength-forms',lambda x:(f'How many medication strength and form entries are listed for {x["drug"]} in the received terminology snapshot?',f"SELECT COUNT(*) AS forms FROM drug_reference WHERE lower(display_name) LIKE '{x['drug']}%'",{'forms':count},[])),
      ('terminology-names',lambda x:(f'List the distinct drug names beginning with {x["drug"]} from the received terminology snapshot.',f"SELECT DISTINCT display_name FROM drug_reference WHERE lower(display_name) LIKE '{x['drug']}%'",{'display_name':{'unit':'text'}},['display_name']))]


def build(output,*,count=10000,reference=None):
    if type(count) is not int or count<1:raise ValueError('Choose a positive explicit curriculum size')
    output=C.checked_path(output);output.mkdir(parents=True,exist_ok=True);provenance=source(output/'source',reference)
    source(output/'source-variant',reference,variant=True)
    ctx=T.Context(output/'source');variant=T.Context(output/'source-variant');tasks=[];seen=set();families=templates();started=time.monotonic()
    prefixes=('','Please tell me: ','For our analytics team: ','Using only the connected records: ','I would like to know: ')
    suffixes=('',' Report the received data.',' Keep missing values explicit.',' Do not infer outside records.')
    # Produce distinct wording/parameter combinations, rather than inflate the
    # training count with identical questions. The requested size is explicit.
    for n in range(count):
        family,render=families[n%len(families)];j=n//len(families)
        params={'year':2010+j%20,'sex':'F' if j%2 else 'M','coverage':'medicare' if j%2 else 'commercial',
            'payer':f'P{j%4}','birth_year':1940+j%60,'amount':10+j*7,
            'drug':('carvedilol','metformin','atorvastatin','amoxicillin','albuterol','sertraline','apixaban','lisinopril')[j%8]}
        q,sql,columns,keys=render(params);q=prefixes[(j//2)%len(prefixes)]+q+suffixes[(j//10)%len(suffixes)]
        # A stable literal context label makes repeated finite lookup combinations
        # explicit duplicates to discard, never disguised extra learning evidence.
        if q in seen:continue
        seen.add(q)
        returns='patient_set' if list(columns)==['patient_id'] else 'table' if len(columns)>1 or keys else 'count' if next(iter(columns.values()))['unit']=='count' else 'value'
        analysis={'steps':[{'name':'answer_rows','purpose':q,'sql':sql}],'final':'answer_rows','returns':returns,
            'requirements':[{'quote':q,'applied_in':['answer_rows']}],'unresolved':[],'workflow':[]}
        tasks.append({'question':q,'target':{'analysis':analysis},'reference_sql':sql,'acceptance':{'returns':returns,'columns':columns,'keys':keys},
            'family':family,'split':partition(family),'source_kind':'authored_engineering','source_digest':ctx.identity})
    data=output/'tasks.jsonl';data.write_text(''.join(json.dumps(r)+'\n' for r in tasks))
    manifest={'schema':'approved-adapter-data/1','learner':'typed','source_identity':ctx.identity,
        'source_variants':[{'path':str(variant.path),'source_identity':variant.identity}],
        'training':{'iterations':20,'num_layers':4,'rank':8},'files':[{'path':str(data),'sha256':A.file_hash(data)}],
        'provenance':'New authored structured analytics tasks on explicitly authored source rows and optional pinned NLM terminology. Native engineering acceptance, not clinical treatment labels.',
        'public_reference':{'source':provenance['source'],'sha256':provenance['sha256'],'retrieved_at':provenance['retrieved_at']} if provenance else None}
    path=output/'manifest.json';path.write_text(json.dumps(manifest,indent=2)+'\n')
    result={'schema':'agere-curriculum-build/1','requested_combinations':count,'unique_questions':len(tasks),'duplicates_discarded':count-len(tasks),
        'families':len({r['family'] for r in tasks}),'splits':dict(Counter(r['split'] for r in tasks)),'source_identity':ctx.identity,
        'provider_calls':0,'clinical_qualification':False,'seconds':time.monotonic()-started}
    (output/'build.json').write_text(json.dumps(result,indent=2)+'\n');return path,result


def verify(manifest,output):
    spec,by,_=T.load(manifest);rows=[r for values in by.values() for r in values];ctx=T.Context(Path(manifest).parent/'source')
    variants=[T.Context(entry['path']) for entry in spec.get('source_variants',[])];results=[];started=time.monotonic()
    for row in rows:
        r=ctx.assess('typed',row,json.dumps(row['target']));other=[v.assess('typed',row,json.dumps(row['target'])) for v in variants]
        r={**r,'acceptable':r['acceptable'] and all(v['acceptable'] for v in other)}
        results.append({'task':digest(row['question']),'family':row['family'],**r})
    receipt={'schema':'agere-curriculum-native-verification/1','cases':len(rows),'acceptable':sum(r['acceptable'] for r in results),
        'failures':[r for r in results if not r['acceptable']],'families':dict(Counter(r['family'] for r in results)),
        'source_variants':len(variants),'seconds':time.monotonic()-started,'provider_calls':0,'clinical_qualification':False,
        'basis':'Declared task targets compile and reproduce their explicit received-source calculations; this is not a frontier/SLM interpretation grade'}
    Path(output).write_text(json.dumps(receipt,indent=2)+'\n');return receipt


def main():
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('--output',required=True,type=Path);p.add_argument('--count',type=int,default=10000);p.add_argument('--reference',type=Path);p.add_argument('--verify',action='store_true')
    p.add_argument('--healthcare',action='store_true',help='Broader independently checked authored healthcare analytics structures')
    p.add_argument('--fit-per-family',type=int,default=80);p.add_argument('--calibration-per-family',type=int,default=3);p.add_argument('--verification-per-family',type=int,default=8)
    a=p.parse_args()
    if a.healthcare:
        from tools.training import healthcare_curriculum as H
        result=H.build(a.output,fit_per_family=a.fit_per_family,calibration_per_family=a.calibration_per_family,verification_per_family=a.verification_per_family)
        if a.verify:result['native_verification']={learner:H.verify(a.output,learner) for learner in ('typed','bottled')}
        print(json.dumps(result,indent=2));return
    path,result=build(a.output,count=a.count,reference=a.reference)
    if a.verify:result['native_verification']=verify(path,a.output/'native-verification.json')
    print(json.dumps(result,indent=2))


if __name__=='__main__':main()
