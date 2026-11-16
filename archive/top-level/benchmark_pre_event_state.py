"""Cold derived-cache graph run in a disposable APFS clone; live stores untouched.

Uses the actual free-form generated plan. It isolates deterministic execution;
planning, server startup, OS caches and model loading are explicitly separate.
"""
import json, shutil, sqlite3, subprocess, tempfile, time
from pathlib import Path
from types import SimpleNamespace
from clinical_core.agere.clinical import analysis_graph

ROOT=Path(__file__).resolve().parent
SOURCE=ROOT/'artifacts/agere-healthcare-v123/stack'
OUT=ROOT/'artifacts/agere-pre-event-state-v1'
saved=json.loads((OUT/'candidate-earlier_population.json').read_text())['response']
target=OUT/'cold-derived-cache-benchmark.json'
if target.exists(): raise ValueError('Existing benchmark retained')
with tempfile.TemporaryDirectory(prefix='agere-temporal-cold-') as directory:
 path=Path(directory)
 # Copy-on-write clone is private; never purge the live cache or provider ledger.
 subprocess.run(['/bin/cp','-c',str(SOURCE/'stack.sqlite3'),str(path/'stack.sqlite3')],check=True)
 for p in [SOURCE/'run.json',*SOURCE.glob('*-link.json')]: shutil.copy2(p,path/p.name)
 for name in ('semantic-readings.sqlite3','program-documents.sqlite3'):
  src=sqlite3.connect((SOURCE/name).resolve().as_uri()+'?mode=ro',uri=True);dst=sqlite3.connect(path/name)
  src.backup(dst);src.close();dst.close()
 db=sqlite3.connect(path/'stack.sqlite3')
 with db: db.execute('DELETE FROM result_cache')
 db.close()
 assert not any((path/n).exists() for n in ('outcomes.sqlite3','assertion-receipts.sqlite3','interpretation-state.sqlite3'))
 meta=json.loads((path/'run.json').read_text())
 stack=SimpleNamespace(path=path,retriever=SimpleNamespace(meta=meta),allow_live=False)
 measured=[]
 for name in ('cold','warm'):
  start=time.perf_counter();r=analysis_graph.execute(stack,saved['graph'],question=saved['question']);elapsed=time.perf_counter()-start
  assert r['new_paid_api_calls']==0
  for old,new in zip(saved['results'],r['results']):
   assert old['node']==new['node']
   for field in ('status_counts','temporal_exclusion_counts','evidence_coverage','membership_digest','counts'):
    assert old['result'].get(field)==new['result'].get(field),(old['node']['id'],field)
  measured.append({'stage':name,'seconds':elapsed,'cache_hit':r['cache_hit'],'new_paid_api_calls':r['new_paid_api_calls'],
   'results':[{k:v for k,v in n['result'].items() if k in ('status_counts','processing_counts','interpretation_processing_counts','temporal_exclusion_counts','counts')} for n in r['results']]})
  print(json.dumps({'stage':name,'seconds':elapsed,'cache_hit':r['cache_hit']}),flush=True)
 with target.open('x') as f:json.dump({'status':'passed','same_actual_generated_plan_and_answer':True,
  'cache_scope':'Empty graph/cohort, outcome, assertion, interpretation and temporal projection caches in a disposable copy. Existing source artifacts and reviews retained.',
  'excluded_from_timing':['clone preparation','free-form planning','server startup','model loading','OS disk-cache control'],
  'not_a_fully_cold_end_to_end_product_benchmark':True,'measurements':measured},f,indent=2)
