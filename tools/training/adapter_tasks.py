"""Task acceptance for local adapters, using explicit new source-bound tasks.

Alternative SQL/program spellings can pass. Numeric comparisons use each task's
declared display units/precision/tolerance; no legacy answer-key scorer is used.
These engineering tasks do not adjudicate treatment suitability or clinical truth.
"""
import json
import math
import re
import sqlite3
from decimal import Decimal,ROUND_HALF_UP
from pathlib import Path
from clinical_core.contracts import digest
from clinical_core.agere.clinical import component_learning as C


def load(manifest):
    path=C.checked_path(manifest);spec=json.loads(path.read_text())
    from clinical_core.agere.clinical.adapter_learning import role
    if spec.get('schema')!='approved-adapter-data/1':raise ValueError('Explicit approved-adapter-data/1 required')
    role(spec['learner'])
    if not isinstance(spec.get('provenance'),str) or not spec['provenance'].strip():raise ValueError('Task provenance required')
    rows=[];families={};wordings={};patients={}
    for entry in spec['files']:
        p=C.checked_path(path.parent/entry['path']);raw=p.read_bytes()
        import hashlib
        if hashlib.sha256(raw).hexdigest()!=entry['sha256']:raise ValueError('Approved task content changed')
        for line in raw.decode().splitlines():
            if not line.strip():continue
            row=json.loads(line)
            if set(row)-{'id','question','target','reference_sql','acceptance','family','split','source_kind','population','patient_id','source_digest'}:raise ValueError('Unknown task fields; answer keys are not approved inputs')
            if row.get('source_kind') not in ('user_review','authored_engineering','primary_reference'):raise ValueError('Adapter targets need reviewed or explicit authored supervision')
            if not isinstance(row.get('question'),str) or not row['question'].strip() or not isinstance(row.get('family'),str) or not row['family'].strip():raise ValueError('Task question and source family required')
            if row.get('split') not in ('fit','calibration','verification'):raise ValueError('Three separate task splits required')
            q=' '.join(row['question'].split()).casefold().rstrip('?.!')
            for mapping,key in ((families,row['family']),(wordings,q),(patients,row.get('patient_id'))):
                if key is None:continue
                if key in mapping and mapping[key]!=row['split']:raise ValueError('Family, patient or wording crosses task split boundaries')
                mapping[key]=row['split']
            if 'target' not in row or not isinstance(row.get('reference_sql'),str):raise ValueError('Explicit target and source calculation required')
            validate_acceptance(row['acceptance'])
            if row.get('population') is not None and (not isinstance(row['population'],list) or any(not isinstance(x,str) for x in row['population'])):raise ValueError('Explicit population must be a list of received patient IDs')
            rows.append(row)
    by={s:[r for r in rows if r['split']==s] for s in ('fit','calibration','verification')}
    if any(not v for v in by.values()):raise ValueError('Need separate nonempty fit/calibration/verification task families')
    return spec,by,digest({'manifest':spec,'rows':rows})


def validate_acceptance(value):
    if not isinstance(value,dict) or set(value)-{'returns','columns','keys','ordered','required_tables','required_patterns'}:raise ValueError('Explicit task acceptance contract required')
    if value.get('returns') not in ('count','value','table','patient_set') or not isinstance(value.get('columns'),dict) or not value['columns']:raise ValueError('Task returns and column units required')
    if any(k not in value['columns'] for k in value.get('keys',[])):raise ValueError('Row keys must name declared result columns')
    for rule in value['columns'].values():
        if not isinstance(rule,dict) or set(rule)-{'unit','decimals','absolute_tolerance','relative_tolerance','scale'}:raise ValueError('Unknown numeric acceptance rule')
        if rule.get('unit') not in ('count','number','percent','dollars','text','id','yes_no'):raise ValueError('Use clinically readable display units')
        for k in ('absolute_tolerance','relative_tolerance'):
            if k in rule and (type(rule[k]) not in (int,float) or not math.isfinite(rule[k]) or rule[k]<0):raise ValueError('Nonnegative finite tolerance required')
        if 'decimals' in rule and (type(rule['decimals']) is not int or rule['decimals']<0):raise ValueError('Explicit nonnegative display precision required')
        if rule['unit'] in ('number','percent','dollars') and not any(k in rule for k in ('decimals','absolute_tolerance','relative_tolerance')):raise ValueError('Numeric acceptance requires explicit display precision or tolerance')
        if 'scale' in rule and (type(rule['scale']) not in (int,float) or not math.isfinite(rule['scale']) or rule['scale']<=0):raise ValueError('Positive unit scale required')
    return value


def same_value(actual,expected,rule):
    if actual is None or expected is None:return actual is expected
    unit=rule['unit']
    if unit=='yes_no':
        yn=lambda v:{'yes':True,'true':True,'1':True,'no':False,'false':False,'0':False}.get(str(v).strip().casefold())
        a,b=yn(actual),yn(expected);return a is not None and b is not None and a==b
    if unit in ('text','id'):return str(actual).strip()==str(expected).strip()
    try:
        a,b=Decimal(str(actual)),Decimal(str(expected))
        if not a.is_finite() or not b.is_finite():return False
        scale=Decimal(str(rule.get('scale',1)));a*=scale;b*=scale
        if unit=='count':return a==a.to_integral_value() and b==b.to_integral_value() and a==b
        tolerance=max(Decimal(str(rule.get('absolute_tolerance',0))),abs(b)*Decimal(str(rule.get('relative_tolerance',0))))
        if abs(a-b)<=tolerance:return True
        if 'decimals' in rule:
            quant=Decimal(1).scaleb(-rule['decimals']);return a.quantize(quant,rounding=ROUND_HALF_UP)==b.quantize(quant,rounding=ROUND_HALF_UP)
        return False
    except (ValueError,TypeError,ArithmeticError):return False


def compare(actual,expected,acceptance):
    if actual.get('truncated') or expected.get('truncated'):return False,'Incomplete native results'
    columns=acceptance['columns']
    if acceptance['returns'] in ('count','value') and len(columns)==len(actual.get('columns',[]))==1:
        # A physician's numeric answer does not become wrong because its SQL
        # alias is "count" instead of "patients". Units/values still must pass.
        name=next(iter(columns));old=actual['columns'][0]
        actual={**actual,'columns':[name],'rows':[{name:r[old]} for r in actual['rows']]}
    missing=set(columns)-set(actual.get('columns',[]));extra=set(actual.get('columns',[]))-set(columns)
    if len(missing)==len(extra)==1 and acceptance.get('keys') and set(acceptance['keys'])<=set(actual.get('columns',[])):
        new,old=next(iter(missing)),next(iter(extra))
        if new not in acceptance['keys'] and columns[new]['unit'] in ('count','number','percent','dollars','yes_no') and not old.endswith('_id'):
            actual={**actual,'columns':[new if c==old else c for c in actual['columns']],
                'rows':[{new if k==old else k:v for k,v in row.items()} for row in actual['rows']]}
    if set(actual.get('columns',[]))!=set(columns) or set(expected.get('columns',[]))!=set(columns):return False,'Result columns differ from task contract'
    a,b=actual['rows'],expected['rows']
    if len(a)!=len(b):return False,'Result population/row count differs'
    keys=acceptance.get('keys') or []
    if keys:
        key=lambda row:json.dumps([row.get(k) for k in keys],sort_keys=True,default=str)
        if len({key(r) for r in a})!=len(a) or len({key(r) for r in b})!=len(b):return False,'Duplicate result keys'
        a,b=sorted(a,key=key),sorted(b,key=key)
    elif not acceptance.get('ordered',False):
        key=lambda row:json.dumps(row,sort_keys=True,default=str)
        a,b=sorted(a,key=key),sorted(b,key=key)
    for x,y in zip(a,b):
        if not all(same_value(x.get(k),y.get(k),rule) for k,rule in columns.items()):return False,'Outside task acceptability or different membership'
    return True,'Within declared task acceptability'


class Context:
    def __init__(self,stack):
        from clinical_core.agere.clinical.request_plan import Catalog
        self.path=C.checked_path(stack);self.catalog=Catalog.load(self.path)
        self.as_of=str(self.catalog.as_of) if hasattr(self.catalog,'as_of') else None
        import sqlite3
        with sqlite3.connect((self.path/'semantic.sqlite3').as_uri()+'?mode=ro',uri=True) as db:
            self.as_of=str(db.execute("SELECT value FROM meta WHERE key='as_of'").fetchone()[0])[:10]
            self.patients={r[0] for r in db.execute('SELECT patient_id FROM patient')} if 'patient' in self.catalog.tables else set()
        from clinical_core.agere.clinical.adapter_learning import file_hash
        self.identity=digest({'catalog':self.catalog.identity,'source':file_hash(self.path/'semantic.sqlite3'),'as_of':self.as_of})

    def messages(self,learner,row,*,target=False):
        from clinical_core.agere.clinical import local_request_worker as W,local_normalizer as N,bottled as B,source_profile
        q=row['question']
        if learner=='typed':messages=W.short_messages(self.catalog,q)[0]
        elif learner=='normalizer':messages=N.prompt(q)
        elif learner=='narrow':messages=B.narrow_messages(q)
        else:messages=B.messages(q,source_profile.load(self.path),self.as_of)
        if target:messages=[*messages,{'role':'assistant','content':row['target'] if isinstance(row['target'],str) else json.dumps(row['target'],sort_keys=True,separators=(',',':'))}]
        return messages

    def query(self,sql,population=None,*,native=False):
        from clinical_core.agere.clinical import semantic_analyst as S,backstop_agent as B
        if population is not None and not set(population)<=self.patients:raise ValueError('Adapter task references unreceived patients')
        with_context=S.SemanticWarehouse(self.path/'semantic.sqlite3')
        try:
            with_context.define(self.as_of);B.define_cohort(with_context,population)
            if population is not None:
                from clinical_core.agere.clinical import general_analysis
                if not native:
                    q='Read selected received records'
                    spec={'steps':[{'name':'received_rows','purpose':q,'sql':sql}],'returns':'table',
                        'requirements':[{'quote':q,'applied_in':['received_rows']}],'unresolved':[]}
                    sql=general_analysis.prepare(self.catalog,q,spec,population=population)['query']
                cte='population(patient_id) AS (SELECT patient_id FROM cohort)'
                sql=('WITH '+cte+', '+sql[4:].lstrip()) if re.match(r'(?is)^with\b',sql) else 'WITH '+cte+' '+sql
            return with_context.query(sql,as_of=self.as_of,max_rows=None,max_chars=None)
        finally:with_context.close()

    def compile(self,learner,row,text):
        from clinical_core.agere.clinical import local_request_worker as W,request_plan as R,request_language as L,local_normalizer as N,bottled as B
        population=row.get('population');q=row['question']
        if learner=='typed':contract=W.compile_value(self.catalog,q,W.decode(text),population)
        elif learner=='normalizer':
            canonical=re.sub(r'<think>.*?</think>','',text,flags=re.S).strip().splitlines()[0]
            if canonical==N.UNSUPPORTED or N.guard(q,canonical):raise ValueError('Normalizer changed a literal requirement')
            value=L.parse(self.catalog,canonical)
            if value is None:raise ValueError('Normalizer output is outside the supported native grammar')
            contract=R.compile_request(self.catalog,canonical,value,population=population)
        elif learner=='narrow':
            slots=B.parse_slots(text)
            if slots is None or slots.get('family') is None:raise ValueError('Narrow output declined')
            parsed=B.spec_from_slots(slots,self.as_of)
            from clinical_core.agere.clinical import cohort_families
            spec={k:parsed[k] for k in ('family','parameters','returns')}
            contract={'query':cohort_families.render(spec,self.as_of,self.catalog.tables),'returns':spec['returns'],'native_parameters':spec}
        else:
            match=re.search(r'```sql\s*(.*?)```',text,re.S|re.I)
            sql=match.group(1) if match else re.sub(r'<think>.*?</think>','',text,flags=re.S).strip()
            contract={'query':sql,'returns':row['acceptance']['returns']}
        if not isinstance(contract.get('query'),str) or contract.get('returns')!=row['acceptance']['returns']:raise ValueError('Task must compile to the requested read-only result shape')
        from clinical_core.agere.clinical import literal_check
        executable=literal_check.executable_text({'query':contract['query'],'parameters':contract.get('native_parameters')})
        gaps=literal_check.missing(q,executable)
        if gaps:raise ValueError('Missing literal requirements: '+str(gaps))
        for table in row['acceptance'].get('required_tables',[]):
            if not re.search(r'\b'+re.escape(table)+r'\b',executable,re.I):raise ValueError('Missing required source table: '+table)
        for pattern in row['acceptance'].get('required_patterns',[]):
            if not re.search(pattern,executable,re.I):raise ValueError('Missing reviewed query requirement')
        return contract

    def assess(self,learner,row,text):
        try:
            contract=self.compile(learner,row,text)
            expected=self.query(row['reference_sql'],row.get('population'))
            actual=self.query(contract['query'],row.get('population'),native=learner in ('typed','normalizer'))
            ok,reason=compare(actual,expected,row['acceptance'])
            return {'guarded':True,'acceptable':ok,'reason':reason,'query':contract['query'],'rows':len(actual['rows'])}
        except (ValueError,KeyError,TypeError,IndexError,sqlite3.Error) as exc:
            return {'guarded':False,'acceptable':False,'reason':type(exc).__name__+': '+str(exc)}

    def deterministic(self,row):
        from clinical_core.agere.clinical import request_language as L
        try:value=L.parse(self.catalog,row['question'])
        except (ValueError,KeyError,TypeError):return False
        return self.assess('typed',row,json.dumps(value))['acceptable'] if value else False


def reviewed_tasks(stack,question,result,review):
    """Derive code targets from an explicitly accepted saved answer, never from its headline."""
    answer=result.get('answer') or {};interpretation=(result.get('planner_receipt') or {}).get('interpretation') or {}
    if answer.get('status')!='answered' or answer.get('verification') not in ('checked','system_one_reviewed') or not interpretation.get('execution_accepted'):
        return [],'The execution needs native verification before it can supervise an adapter'
    if answer.get('workflow') or result.get('workflow'):return [],'Compound deliverables remain whole-workflow review examples, not scalar adapter targets'
    nodes=(result.get('graph') or {}).get('nodes') or []
    if len(nodes)!=1 or nodes[0].get('operation')!='sql':return [],'A portable native SQL execution is required for this adapter target'
    sql=(nodes[0].get('parameters') or {}).get('query')
    if not isinstance(sql,str) or re.search(r'\bagere_result_\d+\b',sql):return [],'Intermediate model-result views are not source training truth'
    ctx=Context(stack.path);population=review.get('population',result.get('input_patient_ids'))
    actual=ctx.query(sql,population,native=True);returns=(nodes[0].get('parameters') or {}).get('returns')
    acceptance=review.get('acceptance')
    if acceptance is None:
        if returns=='count' and len(actual['columns'])==1:acceptance={'returns':'count','columns':{actual['columns'][0]:{'unit':'count'}}}
        elif returns=='patient_set' and actual['columns']==['patient_id']:acceptance={'returns':'patient_set','columns':{'patient_id':{'unit':'id'}},'keys':['patient_id']}
        else:return [],'A reviewed unit/precision contract is needed for values or multi-column results'
    validate_acceptance(acceptance)
    masked=re.sub(r"'[^']*'",'?',re.sub(r'\b\d+(?:\.\d+)?\b','?',sql))
    family=review.get('family') or 'native-sql/'+digest(' '.join(masked.lower().split()))
    analysis={'steps':[{'name':'answer_rows','purpose':question,'sql':sql}],'final':'answer_rows','returns':returns,
        'requirements':[{'quote':question,'applied_in':['answer_rows']}],'unresolved':[]}
    targets={'typed':{'analysis':analysis},'bottled':sql}
    normalized=interpretation.get('normalized_question')
    if isinstance(normalized,str):targets['normalizer']=normalized
    learners=review.get('learners') or ['typed','bottled']
    if not isinstance(learners,list) or not learners or any(learner not in ('typed','bottled','normalizer') for learner in learners):raise ValueError('Choose supported accepted-answer adapter learners')
    rows=[]
    for learner in learners:
        if learner not in targets:continue
        target=targets[learner];text=target if isinstance(target,str) else json.dumps(target)
        row={'question':question,'target':target,'reference_sql':sql,'acceptance':acceptance,'family':family,'source_digest':ctx.identity}
        if population is not None:row['population']=population
        if not ctx.assess(learner,row,text)['acceptable']:continue
        rows.append({'role':'slm_'+learner,**row})
    return rows,'Explicitly accepted native code targets; clinical answer text is not used as an answer key'
