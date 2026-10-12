"""Predeclared engineering expectations; no clinician gold, fitting or promotion."""
import hashlib
import json
import urllib.error
import urllib.request
from pathlib import Path
from clinical_core.contracts import digest

OUT=Path('artifacts/agere-reader-comparison-v1')


def call(route, body):
    req=urllib.request.Request('http://localhost:8885/api/stack/reader-comparison/'+route,
                              json.dumps(body).encode(),{'Content-Type':'application/json'})
    with urllib.request.urlopen(req,timeout=600) as r:return json.load(r)


def main():
    plan=json.loads((OUT/'predeclaration.json').read_text());before=hashlib.sha256((OUT/'predeclaration.json').read_bytes()).hexdigest()
    status=call('availability',{});assert any(c['cohort_id']==plan['cohort_id'] and c['status']=='available' for c in status['cohorts'])
    (OUT/'availability.json').write_text(json.dumps(status,indent=2)+'\n')
    results=[]
    for case in plan['cases']:
        body={'cohort_id':plan['cohort_id'],'roles':plan['roles'],'target':case['target']}
        response=call('prepare',body);r=response['comparison']
        assert r['context_digest']==case['context_digest'] and digest(r['context'])==case['context_digest']
        assert r['source_hash']==case['source_hash'] and r['context']['target_quote']==case['quote']
        assert not any(r[k] for k in ('clinical_validated','automatic_acceptance','human_review_created','training_performed','model_promoted','work_created','new_paid_api_calls','external_model_calls'))
        assert all(o['same_patient']==o['same_source']==o['same_model_text']==0 for o in r['training_overlap'].values())
        replay=call('prepare',body);assert replay['cache_hit'] and replay['comparison']==r
        assert call('inspect',{'id':r['id']})==r
        scores={}
        for role,v in {**r['baselines'],**r['variants']}.items():
            scores[role]={'status':v.get('status','baseline'),'axes':{}}
            if 'labels' not in v:continue
            for axis,expected in case['expected_axes'].items():
                observed=v['labels'][axis]
                scores[role]['axes'][axis]={'expected':expected,'observed':observed,'matches_declared_expectation':observed in expected,'abstained':observed=='unknown'}
        saved={'case_id':case['id'],'comparison_id':r['id'],'workflow':case['workflow'],'scores':scores,
               'complete_roles':r['stage_assessment']['completed_roles'],'failed_roles':r['stage_assessment']['failed_roles'],'exact_cache_replay':True}
        (OUT/(case['id']+'.json')).write_text(json.dumps(r,indent=2)+'\n');results.append(saved)
        (OUT/'case-results.json').write_text(json.dumps(results,indent=2)+'\n')
        print(json.dumps({'case':case['id'],'id':r['id'],'completed':saved['complete_roles'],'failed':saved['failed_roles']}),flush=True)
    for body in ({},{'id':'bad'}):
        try:call('inspect',body)
        except urllib.error.HTTPError as e:assert e.code==400
        else:raise AssertionError('Invalid comparison inspection accepted')
    metrics={}
    for role in ['abstain_all','training_majority']+plan['roles']:
        axes=[a for row in results for a in row['scores'][role]['axes'].values()]
        metrics[role]={'declared_axes':len(axes),'matches':sum(a['matches_declared_expectation'] for a in axes),
                       'abstentions':sum(a['abstained'] for a in axes),'failed_cases':sum(row['scores'][role]['status']=='failed' for row in results)}
    assert before==hashlib.sha256((OUT/'predeclaration.json').read_bytes()).hexdigest()
    report={'status':'passed','cases':results,'engineering_expectation_counts':metrics,'predeclaration_sha256':before,
            'meaning':'Agreement with 28 selected assistant-authored axis expectations; NOT clinical accuracy, an independent representative benchmark or evidence of superiority.',
            'clinical_validation':False,'no_tuning_or_training':True,'new_paid_api_calls':0,'external_actions':0}
    (OUT/'http-verification.json').write_text(json.dumps(report,indent=2)+'\n');print(json.dumps(metrics),flush=True)


if __name__=='__main__':main()
