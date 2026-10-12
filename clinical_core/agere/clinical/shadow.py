"""Apply a trained assertion specialist to source-bound text without changing facts.

Prediction caching excludes patient IDs: only identical concept, target and context
can reuse a prediction. Patient-specific provenance remains on every output binding.
"""
from pathlib import Path
from collections import Counter
import hashlib,json,sqlite3,time
from ...contracts import digest
from .context_student import ContextStudent,feature

CONCEPTS={'ef':'ejection fraction measurement','financial_hardship':'difficulty paying medical bills',
          'diagnosis:ckd':'chronic kidney disease','diagnosis:heart_failure':'heart failure',
          'symptom:dyspnea':'shortness of breath','symptom:edema':'leg edema',
          'care:nephrology':'nephrology mention','medication:bb':'beta blocker medication mention'}

def rule_label(fact):
    if fact['subject']=='other_person':return 'other_person'
    if fact['subject'] not in ('patient','patient_context'):return 'unknown'
    assertion=fact['assertion']
    if assertion in ('negated','retracted'):return assertion
    if assertion in ('uncertain','hypothetical'):return 'unknown'
    if fact['temporality'] in ('historical','resolved'):return 'historical'
    return 'affirmed' if assertion=='affirmed' else 'unknown'

def example_from_fact(fact):
    if fact['method']!='context_rules':return None
    e=fact['evidence'][0]
    if not e.get('context') or not e.get('quote') or e['quote'] not in e['context']:return None
    return {'concept':CONCEPTS.get(fact['concept'],fact['concept'].replace(':',' ').replace('_',' ')),
            'target_quote':e['quote'],'text':e['context']}

def run_shadow(run_path,model_path):
    run_path=Path(run_path);model_path=Path(model_path);model=ContextStudent(model_path)
    identity=digest({'model_card':model.identity,'weights':hashlib.sha256((model_path/'student.joblib').read_bytes()).hexdigest(),
                     'adapter':hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
                     'predictor_code':hashlib.sha256(Path(__file__).with_name('context_student.py').read_bytes()).hexdigest(),
                     'contract':'context-shadow/1'})
    cache=sqlite3.connect(run_path/'context-shadow-cache.sqlite3')
    cache.execute('CREATE TABLE IF NOT EXISTS predictions (key TEXT PRIMARY KEY,payload TEXT)')
    counts=Counter();matrix=Counter();examples=[];t=time.perf_counter();batch={};bindings=[]
    def flush(stream):
        if not bindings:return
        keys=list(batch);predictions=dict(zip(keys,model.predict([batch[k] for k in keys])))
        counts['new_unique_predictions']+=len(keys)
        with cache:
            cache.executemany('INSERT INTO predictions VALUES (?,?)',[(k,json.dumps(v)) for k,v in predictions.items()])
        for binding,key,old in bindings:
            prediction=old if old is not None else predictions[key]
            disagree=binding['rule_label']!=prediction['label'];counts['rule_student_disagreements']+=int(disagree)
            matrix[(binding['rule_label'],prediction['label'])]+=1
            row={**binding,'prediction':prediction,'disagrees_with_rule':disagree,'decision_authority':False}
            stream.write(json.dumps(row)+'\n')
            if disagree and len(examples)<20:examples.append(row)
        batch.clear();bindings.clear()
    try:
        with (run_path/'patients.jsonl').open() as source,(run_path/'context-shadow.jsonl').open('w') as stream:
            for line in source:
                patient=json.loads(line);counts['patients']+=1
                for fact in patient['state']['facts']:
                    row=example_from_fact(fact)
                    if row is None:continue
                    key=digest({'identity':identity,'input':feature(row)});counts['text_fact_bindings']+=1
                    cached=cache.execute('SELECT payload FROM predictions WHERE key=?',(key,)).fetchone()
                    if cached:counts['persistent_prediction_cache_hits']+=1
                    elif key in batch:counts['within_batch_reuses']+=1
                    else:batch[key]=row
                    binding={'patient_id':patient['patient_id'],'fact_id':fact['fact_id'],'rule_label':rule_label(fact),
                             'concept':fact['concept'],'source':fact['evidence'][0],'input':row,'prediction_key':key}
                    bindings.append((binding,key,json.loads(cached[0]) if cached else None))
                if len(bindings)>=2000:flush(stream)
            flush(stream)
        result={'schema':'context-shadow/1','synthetic':True,'model_identity':identity,'counts':dict(counts),
                'elapsed_seconds':time.perf_counter()-t,'new_paid_api_calls':0,'decisions_changed':0,
                'label_pairs':[{'rule':a,'student':b,'count':n} for (a,b),n in sorted(matrix.items())],
                'disagreement_examples':examples,
                'interpretation':'Rule/student disagreement is a review signal, not an accuracy score. Rule extraction constrains which mentions reach the student; unseen mentions may be missed.',
                'source_run':json.loads((run_path/'run.json').read_text())['cache_key_digest']}
        (run_path/'context-shadow-summary.json').write_text(json.dumps(result,indent=2));return result
    finally:cache.close()
