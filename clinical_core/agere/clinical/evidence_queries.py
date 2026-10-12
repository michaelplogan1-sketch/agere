"""Bounded evidence queries across structured observations and model annotations.

SQL execution is deterministic. Plans describe recorded evidence, never medical
eligibility, causal effects or absence of care. This is not a free-form NL agent.
"""
from pathlib import Path
from collections import Counter
from datetime import date
import hashlib,json,sqlite3,time,re
from ...contracts import digest
from .relation_learning import write

VERSION='evidence-query/1'
FIELDS={'concept','assertion','subject','time','relation'}

CATALOG=[
 {'id':'hf_text_without_structured','title':'Heart failure mentioned in notes without a matching structured diagnosis',
  'why':'Review source mapping and documentation differences; a text annotation does not establish a diagnosis.',
  'plan':{'match':'all','clauses':[{'source':'text','concept':'diagnosis heart failure','assertion':'affirmed','subject':'patient'}, {'source':'structured','concept':'diagnosis:I50','concept_match':'prefix','observation':'not_observed'}]}},
 {'id':'ckd_text_without_structured','title':'Kidney disease mentioned in notes without a matching structured diagnosis',
  'why':'Investigate narrative-versus-code coverage, including outside records. This is not a coding recommendation.',
  'plan':{'match':'all','clauses':[{'source':'text','concept':'diagnosis ckd','assertion':'affirmed','subject':'patient'}, {'source':'structured','concept':'diagnosis:N18','concept_match':'prefix','observation':'not_observed'}]}},
 {'id':'nephrology_request_without_completion','title':'Nephrology records requested without an observed completed referral record',
  'why':'Locate the requested records or verify attendance. Lack of a completed referral record does not prove no visit occurred.',
  'plan':{'match':'all','clauses':[{'source':'text','concept':'care nephrology','relation':'record_request'}, {'source':'structured','concept':'referral:nephro','concept_match':'prefix','value_equals':'completed','observation':'not_observed'}]}},
 {'id':'historical_ef_without_current_annotation','title':'Historical EF annotations without a current EF annotation',
  'why':'Find dated observation reports and reconcile phenotype; source-relative current is not proof of current physiology.',
  'plan':{'match':'all','clauses':[{'source':'text','concept':'ef','time':'historical','assertion':'affirmed','subject':'patient'}, {'source':'text','concept':'ef','time':'current','assertion':'affirmed','subject':'patient','observation':'not_observed'}]}},
 {'id':'mixed_dyspnea_accounts','title':'Both affirmed and denied dyspnea in the available notes',
  'why':'Inspect dates and context. The patient may have changed over time; these are mixed accounts, not necessarily a contradiction.',
  'plan':{'match':'all','clauses':[{'source':'text','concept':'symptom dyspnea','assertion':'affirmed','subject':'patient'}, {'source':'text','concept':'symptom dyspnea','assertion':'negated','subject':'patient'}]}},
 {'id':'financial_text_without_positive_screen','title':'Narrative financial hardship without an observed positive structured screen',
  'why':'Reconcile the patient account and screening data before evaluating assistance options.',
  'plan':{'match':'all','clauses':[{'source':'text','concept':'financial hardship','assertion':'affirmed','subject':'patient'}, {'source':'structured','concept':'financial_hardship','value_equals':True,'observation':'not_observed'}]}},
]


def validate_plan(plan):
    if not isinstance(plan,dict) or set(plan)!={'match','clauses'} or plan['match'] not in ('all','any'):raise ValueError('Plan requires match=all/any and clauses')
    if not isinstance(plan['clauses'],list) or not 1<=len(plan['clauses'])<=8:raise ValueError('One to eight evidence clauses required')
    normalized=[]
    for c in plan['clauses']:
        if not isinstance(c,dict) or not set(c)<={'source','observation','concept_match','value_equals','event_date_from','event_date_through',*FIELDS}:raise ValueError('Unsupported evidence predicate')
        if c.get('source') not in ('text','structured'):raise ValueError('Explicit source required')
        if not isinstance(c.get('concept'),str) or not 1<=len(c['concept'])<=120:raise ValueError('Explicit bounded concept required')
        if c.get('observation','present') not in ('present','not_observed'):raise ValueError('Observation is present or not_observed, never absent care')
        if c.get('concept_match','equals') not in ('equals','prefix'):raise ValueError('Unsupported concept match')
        for key in FIELDS&set(c):
            if not isinstance(c[key],str) or not 1<=len(c[key])<=120:raise ValueError('Bounded evidence filter required')
        if 'value_equals' in c:
            if c['source']!='structured' or type(c['value_equals']) not in (str,bool):raise ValueError('Structured scalar value comparison only')
            if isinstance(c['value_equals'],str) and len(c['value_equals'])>120:raise ValueError('Bounded scalar required')
        for key in ('event_date_from','event_date_through'):
            if key in c:
                if not isinstance(c[key],str) or not re.fullmatch(r'\d{4}-\d{2}-\d{2}',c[key]):raise ValueError('ISO source event date required')
                date.fromisoformat(c[key])
        if c.get('event_date_from','0001-01-01')>c.get('event_date_through','9999-12-31'):raise ValueError('Source event date range is reversed')
        normalized.append({'observation':'present','concept_match':'equals',**c})
    return {'match':plan['match'],'clauses':normalized}


def predicate(c,alias='e'):
    clauses=[f'{alias}.kind=?'];params=[c['source']]
    for field in sorted(FIELDS&set(c)):
        if field=='concept' and c['concept_match']=='prefix':clauses.append(f'substr({alias}.concept,1,?)=?');params.extend([len(c[field]),c[field]])
        else:clauses.append(f'{alias}.{field}=?');params.append(c[field])
    if 'value_equals' in c:clauses.append(f'{alias}.value_json=?');params.append(json.dumps(c['value_equals'],sort_keys=True))
    for field,operator in [('event_date_from','>='),('event_date_through','<=')]:
        if field in c:
            event=f"CASE WHEN {alias}.kind='text' THEN json_extract({alias}.payload,'$.source.event_date') ELSE json_extract({alias}.payload,'$.fact.evidence[0].event_date') END"
            clauses.append(f'({event}) {operator} ?');params.append(c[field])
    return ' AND '.join(clauses),params


def build_index(clinical_run,annotation_run,output):
    clinical_run,annotation_run,output=map(Path,(clinical_run,annotation_run,output))
    if output.exists():raise ValueError('Fresh evidence index directory required')
    c=json.loads((clinical_run/'run.json').read_text());a=json.loads((annotation_run/'run.json').read_text())
    if c.get('synthetic') is not True or a.get('synthetic') is not True or a['source_run']!=c['cache_key_digest']:raise ValueError('Synthetic snapshot mismatch')
    output.mkdir(parents=True);db=sqlite3.connect(output/'evidence.sqlite3');started=time.perf_counter();counts=Counter()
    db.executescript('CREATE TABLE patients(id TEXT PRIMARY KEY); CREATE TABLE evidence(id TEXT PRIMARY KEY,patient TEXT,kind TEXT,concept TEXT,assertion TEXT,subject TEXT,time TEXT,relation TEXT,value_json TEXT,payload TEXT); CREATE TABLE query_cache(id TEXT PRIMARY KEY,payload TEXT);')
    def insert(pid,kind,identifier,concept,assertion,subject,temporal,relation,value,payload):
        db.execute('INSERT INTO evidence VALUES (?,?,?,?,?,?,?,?,?,?)',(identifier,pid,kind,concept,assertion,subject,temporal,relation,json.dumps(value,sort_keys=True),json.dumps(payload)));counts[kind]+=1
    source=sqlite3.connect(annotation_run.resolve().joinpath('annotations.sqlite3').as_uri()+'?mode=ro',uri=True)
    try:
        with db:
            with (clinical_run/'patients.jsonl').open() as stream:
                for line in stream:
                    row=json.loads(line);pid=row['patient_id'];db.execute('INSERT INTO patients VALUES (?)',(pid,));counts['patients']+=1
                    for f in row['state']['facts']:
                        if f['method']!='structured_source':continue
                        insert(pid,'structured','fact:'+f['fact_id'],f['concept'],f['assertion'],f['subject'],f['temporality'],f.get('relationship',''),f['value'],{'fact':f,'authority':'Recorded structured observation; not established patient state.'})
            for identifier,payload in source.execute('SELECT id,payload FROM annotations'):
                r=json.loads(payload);p=r['annotation'];axes={k:v['label'] for k,v in p['axes'].items()}
                if p['model_identity']!=a['model_identity']:raise ValueError('Annotation identity mismatch')
                if not db.execute('SELECT 1 FROM patients WHERE id=?',(r['patient_id'],)).fetchone():raise ValueError('Unknown annotation patient')
                insert(r['patient_id'],'text','annotation:'+identifier,r['input']['concept'],axes['assertion'],axes['subject'],axes['time'],axes['relation'],None,{
                    'source':r['source'],'input':r['input'],'axes':axes,'model_identity':p['model_identity'],'review_routing':p.get('review_routing'),'authority':'Unvalidated machine annotation; no clinical decision authority.'})
            db.executescript('CREATE INDEX patient_evidence ON evidence(patient,kind,concept); CREATE INDEX concept_evidence ON evidence(kind,concept,patient);')
        integrity=db.execute('PRAGMA integrity_check').fetchone()[0]
        if integrity!='ok':raise ValueError('Evidence index integrity failure')
        meta={'schema':VERSION,'synthetic':True,'source_run':c['cache_key_digest'],'model_identity':a['model_identity'],'snapshot':c['snapshot'],'counts':dict(counts),'elapsed_seconds':time.perf_counter()-started,'new_paid_api_calls':0,'clinical_decisions_changed':0,
            'limitations':['Bounded evidence predicates, not general natural-language reasoning','Machine text annotations remain unvalidated','Not observed is absence from this index only, never proof of missing care','Sources share a synthetic generator; no clinical accuracy claim']}
        write(output/'run.json',meta);return meta
    finally:source.close();db.close()


def execute(path,plan,*,limit=20,offset=0):
    path=Path(path);plan=validate_plan(plan)
    if type(limit) is not int or not 1<=limit<=100 or type(offset) is not int or not 0<=offset<=1000000:raise ValueError('Bounded pagination required')
    meta=json.loads((path/'run.json').read_text())
    if meta.get('schema')!=VERSION or meta.get('synthetic') is not True:raise ValueError('Synthetic evidence index required')
    cutoff=meta.get('snapshot',{}).get('as_of')
    for c in plan['clauses']:
        if any(k in c for k in ('event_date_from','event_date_through')):
            if not cutoff:raise ValueError('Snapshot cutoff required for date-bounded queries')
            if c.get('event_date_from',cutoff)>cutoff or c.get('event_date_through',cutoff)>cutoff:raise ValueError('Requested dates extend beyond the observed snapshot cutoff '+cutoff)
    version=hashlib.sha256(Path(__file__).read_bytes()).hexdigest();key=digest([meta['source_run'],meta['model_identity'],version,plan,limit,offset])
    db=sqlite3.connect(path/'evidence.sqlite3');started=time.perf_counter()
    try:
        cached=db.execute('SELECT payload FROM query_cache WHERE id=?',(key,)).fetchone()
        if cached:return {**json.loads(cached[0]),'cache_hit':True,'elapsed_seconds':time.perf_counter()-started}
        parts=[];params=[]
        for c in plan['clauses']:
            where,values=predicate(c);negate='NOT ' if c['observation']=='not_observed' else ''
            parts.append(f'{negate}EXISTS (SELECT 1 FROM evidence e WHERE e.patient=p.id AND {where})');params.extend(values)
        expr=(' AND ' if plan['match']=='all' else ' OR ').join(parts)
        total=db.execute('SELECT count(*) FROM patients p WHERE '+expr,params).fetchone()[0]
        ids=[p[0] for p in db.execute('SELECT id FROM patients p WHERE '+expr+' ORDER BY id LIMIT ? OFFSET ?',[*params,limit,offset])]
        patients=[]
        for pid in ids:
            checks=[]
            for c in plan['clauses']:
                where,values=predicate(c);query=' FROM evidence e WHERE e.patient=? AND '+where
                count=db.execute('SELECT count(*)'+query,[pid,*values]).fetchone()[0]
                support=[{'evidence_id':r[0],'recorded_evidence':json.loads(r[1])} for r in db.execute('SELECT id,payload'+query+' ORDER BY id LIMIT 3',[pid,*values])]
                checks.append({'clause':c,'matches_predicate':count==0 if c['observation']=='not_observed' else count>0,'matching_evidence_occurrences':count,'examples':support,'meaning':'No qualifying indexed evidence within these predicates and source dates; care status remains unknown.' if count==0 else 'Recorded evidence exists; inspect context and dates.'})
            patients.append({'patient_id':pid,'evidence_checks':checks,'care_gap_established':False,'execution_authorized':False})
        result={'schema':VERSION,'synthetic':True,'plan':plan,'patients_matching_evidence_plan':total,'patients_in_snapshot':meta['counts']['patients'],'patients':patients,'limit':limit,'offset':offset,'source_run':meta['source_run'],'model_identity':meta['model_identity'],'cache_hit':False,'elapsed_seconds':time.perf_counter()-started,'new_paid_api_calls':0,'clinical_decisions_changed':0,'question_fully_answered':'Only the explicit recorded-evidence predicates','scope':'Counts are evidence-query matches, not confirmed disease prevalence, care gaps or program eligibility.'}
        with db:db.execute('INSERT OR REPLACE INTO query_cache VALUES (?,?)',(key,json.dumps(result)))
        return result
    finally:db.close()
