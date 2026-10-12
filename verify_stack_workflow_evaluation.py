"""Read-only retention and live scorecard checks for the shared development run."""
import argparse,hashlib,json,sqlite3,urllib.request
from collections import Counter
from pathlib import Path
from clinical_core.agere.active import stack_path
from clinical_core.agere.economics import usage_cost
from clinical_core.agere.workflow_scorecard import build
from clinical_core.agere.clinical.meaning_source_dispatch import pinned

OUT=Path('artifacts/agere-workflow-evaluation-v1')
PRIOR=Path('artifacts/agere-source-plan-answers-v1')

def sha(p):return hashlib.sha256(Path(p).read_bytes()).hexdigest()
def read(p):return json.loads(Path(p).read_text())
def write(p,value):Path(p).write_text(json.dumps(value,indent=2,allow_nan=False)+'\n')
def database(p):return sqlite3.connect(Path(p).resolve().as_uri()+'?mode=ro',uri=True)

def retention(*, prior=PRIOR, out=OUT, expected_history_delta=None, new_task_description=None):
    prior=Path(prior);out=Path(out)
    if expected_history_delta is None:expected_history_delta={'work_items':0,'work_events':0,'capability_tasks':6,'capability_events':12}
    stack=stack_path();protected=read(prior/'protected-for-continuation.json')
    for p,h in protected['protected_files'].items():assert sha(p)==h,p
    assert len(pinned(stack))==33
    docs=[r for r in read('artifacts/agere-multipass-synthesis-v1/research-recovery-index.json')['files'] if r['path'].startswith('/Users/mikel/Documents/')]
    for row in docs:assert sha(row['path'])==row['sha256'],row['path']
    old_history=read(prior/'history-baseline.json');current={}
    with database(stack/'program-work.sqlite3') as db:
        for table,old in old_history.items():
            rows=[list(r) for r in db.execute('SELECT * FROM '+table+' ORDER BY 1,2')]
            assert not (Counter(map(tuple,old))-Counter(map(tuple,rows))),table
            current[table]=rows
    semantic=read(prior/'semantic-baseline.json')
    with database(stack/'semantic-readings.sqlite3') as db:
        for table,old in semantic.items():assert [list(r) for r in db.execute('SELECT * FROM '+table+' ORDER BY 1,2')]==old,table
    old_ledger=read(prior/'ledger-row-baseline.json');old_ids={r[0] for r in old_ledger}
    with database('artifacts/agere-full/provider-budget.sqlite3') as db:
        for old in old_ledger:assert list(db.execute('SELECT * FROM calls WHERE id=?',(old[0],)).fetchone())==old
        assert db.execute('SELECT cap FROM authorization WHERE id=1').fetchone()[0] is None
        ledger=[list(r) for r in db.execute('SELECT * FROM calls ORDER BY id')]
        db.row_factory=sqlite3.Row;new=[dict(r) for r in db.execute('SELECT * FROM calls') if r['id'] not in old_ids]
    costs=[];unpriced=[];calls=[]
    for call in new:
        assert call['status'] in ('validated','failed'),call['id']
        raw=json.loads(call['response']) if call['response'] else None
        receipt=None
        if raw is None:
            for folder in ('provider-receipts','availability-reconciliation/provider'):
                candidate=stack/folder/(call['id']+'.json')
                if candidate.exists():raw=read(candidate).get('response');receipt=str(candidate);break
        estimate=usage_cost(call['model'],raw.get('usage')) if raw else None
        if estimate:costs.append(estimate['estimated_usd'])
        else:unpriced.append(call['id'])
        calls.append({'id':call['id'],'status':call['status'],'error':call['error'],
            'seconds':call['seconds'],'cost_estimate':estimate,'failed_response_receipt':receipt})
    plan=read('research/agere/workflow-evaluation-v1/plan.json');meta=read(stack/'run.json')
    assert plan['snapshot']==meta['snapshot']
    with database(stack/'stack.sqlite3') as db:
        patients=db.execute('SELECT COUNT(*) FROM patients').fetchone()[0]
        records=db.execute('SELECT COUNT(*) FROM records').fetchone()[0]
        assert patients==1614 and records==1238686
    counts={t:len(v) for t,v in current.items()};deltas={t:len(current[t])-len(v) for t,v in old_history.items()}
    assert deltas==expected_history_delta,deltas
    summary={'status':'passed','prior_protected_files_unchanged':len(protected['protected_files']),
        'prior_ledger_rows_unchanged':len(old_ledger),'ledger_counts':dict(Counter(r[4] for r in ledger)),
        'new_provider_calls':len(new),'new_provider_statuses':dict(Counter(c['status'] for c in new)),
        'cost':{'estimated_usd':round(sum(costs),8) if not unpriced else None,'known_estimated_usd':round(sum(costs),8),
            'unpriced_calls':unpriced,'includes_failed_responses':True,'invoice_verified':False,'spending_limit':'uncapped'},
        'history_counts':counts,'history_delta':deltas,'semantic_readings_unchanged':len(semantic['readings']),
        'human_reviews_created':0,'documents_unchanged':len(docs),'patients':patients,'records':records,
        'pinned_source_routes':33,
        'new_tasks':new_task_description or 'Four diagnostic and two saved-answer-linked synthetic handoff tasks; no patient or clinician contact or care executed.',
        'clinical_validated':False,'source_migration':False}
    write(out/'preservation.json',summary);write(out/'usage-accounting.json',{'summary':summary['cost'],'calls':calls})
    write(out/'protected-for-continuation.json',protected);write(out/'history-baseline.json',current)
    write(out/'semantic-baseline.json',semantic);write(out/'ledger-row-baseline.json',ledger)
    return summary

def http_check(base,report):
    for route in ('/api/stack/workflow-evaluation','/stack-evaluation','/stack'):
        with urllib.request.urlopen(base+route,timeout=60) as response:
            raw=response.read();assert response.status==200
            if route.startswith('/api'):assert json.loads(raw)==report
            elif route=='/stack-evaluation':assert b'Where the full workflow breaks' in raw
            else:assert b'href="/stack-evaluation"' in raw
    return {'status':'passed','routes':['/api/stack/workflow-evaluation','/stack-evaluation','/stack'],
        'report_sha256':sha(OUT/'scorecard.json'),'new_provider_calls':0}

def main():
    p=argparse.ArgumentParser();p.add_argument('--base');a=p.parse_args()
    summary=retention()
    report=build('research/agere/workflow-evaluation-v1/plan.json',OUT/'baseline',OUT/'planner-v3')
    report['usage_accounting']={k:summary[k] for k in ('new_provider_calls','new_provider_statuses','cost')}
    report['known_gaps']=['Candidate retrieval misses named received categories in three cases.',
        'Five planning cases still fail after the first shared contract correction.',
        'Two source reconciliations reject ambiguous references; one exceeds the request bound; one question context omits units.',
        'Two saved-answer handoffs retain the original question, but service tags are manually selected; planning and source-coverage failures remain. Other work branches are isolated diagnostics.',
        'No representative clinical adjudication, model promotion or full-product acceptance.']
    write(OUT/'scorecard.json',report)
    if a.base:write(OUT/'http-verification.json',http_check(a.base,report))
    print(json.dumps({'preservation':summary,'stages':report['stage_counts'],'planner_comparison':report['planner_comparison']},indent=2))

if __name__=='__main__':main()
