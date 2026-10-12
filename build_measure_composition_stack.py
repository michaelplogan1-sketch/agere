"""Full original-cohort composition against pinned received-source sets."""
import argparse,copy,hashlib,json,zlib
from collections import Counter
from pathlib import Path
from clinical_core.contracts import digest
from clinical_core.agere.active import stack_path
from clinical_core.agere.clinical import measure_composition as M,program_documents as D,program_work as W
from clinical_core.agere.clinical.stack_runtime import Stack
from test_measure_composition import component_package


def main():
 parser=argparse.ArgumentParser();parser.add_argument('--output',type=Path,required=True);args=parser.parse_args();out=args.output;out.mkdir(exist_ok=False)
 def write(n,x):(out/n).write_text(json.dumps(x,indent=2,allow_nan=False)+'\n')
 prior=Path('artifacts/agere-protocol-requirements-v1');states=prior/'independent-source-states.json';manifest=json.loads((prior/'implementation-receipt.json').read_text());assert hashlib.sha256(states.read_bytes()).hexdigest()==manifest['evidence_sha256'][str(states)]
 expected=json.loads(states.read_text());ids=sorted(expected);stack=Stack(stack_path(),allow_live=False)
 assert stack.retriever.meta['snapshot']==json.loads((prior/'verification.json').read_text())['active_source_snapshot']
 try:
  keys={}
  for role in ('denominator','numerator'):
   spec=component_package(role,known=role=='denominator');write('synthetic-'+role+'.json',spec);keys[role]=D.install(stack.path,spec)['id']
  question=M.question(keys['denominator'],keys['numerator']);unbound=stack.compose(question);write('unbound-question.json',unbound)
  pending=unbound['results'][-1]['result'];assert pending['counts']['unresolved_measure_patients']==len(ids);assert pending['counts']['known_denominator']==0;assert pending['rate'] is None
  graph=copy.deepcopy(unbound['graph']);p=graph['nodes'][-1]['parameters'];p['title']='Synthetic descriptive affordability reporting — no payer measure'
  p['components']['exclusion']={'not_applied':'Explicit descriptive engineering statistic; no payer exclusion specification applied.'};p['components']['attribution']={'not_applied':'Explicit descriptive engineering statistic over the observed population, not attributed payer performance.'}
  answer=stack.run_plan(graph);write('descriptive-graph.json',graph);write('descriptive-question.json',answer);r=answer['results'][-1]['result'];population=M.load(stack.path,r['id']);db=M.storage(stack.path);doc=D.storage(stack.path);checks=Counter();samples={}
  try:
   assert [x['patient_id'] for x in population['patient_references']]==ids
   for ref in population['patient_references']:
    row=M.read(db,'patients',ref['id']);assert digest(row)==ref['hash'];pid=row['patient_id'];wanted={'supported':'numerator_met','contradicted':'numerator_not_met','unresolved':'denominator_unresolved'}[expected[pid]];assert row['status']==wanted;checks['patient_component_states_verified']+=1
    pair={'supported':[[1,1]],'contradicted':[[1,0]],'unresolved':[[1,0],[1,1]]}[expected[pid]];assert row['sensitivity']['possible_denominator_numerator_pairs']==pair;checks['shared_source_possibilities_verified']+=1
    for c in row['references'].values():
     source=json.loads(zlib.decompress(doc.execute('SELECT payload FROM receipts WHERE id=? AND patient=?',(c['receipt_id'],pid)).fetchone()[0]));assert digest(source)==c['receipt_hash'];checks['exact_component_source_receipts_verified']+=1
    samples.setdefault(expected[pid],pid)
  finally:db.close();doc.close()
  counts=Counter(expected.values());assert r['counts']['known_denominator']==counts['supported']+counts['contradicted'];assert r['counts']['known_numerator']==counts['supported'];assert r['sensitivity_bounds']['minimum']==counts['supported']/len(ids);assert r['sensitivity_bounds']['maximum']==(counts['supported']+counts['unresolved'])/len(ids)
  for state,pid in samples.items():write('patient-'+state+'.json',M.inspect(stack.path,r['id'],pid))
  replay=M.execute(stack.path,ids,p,selection=population['selection']);assert replay['processing_counts']=={'patient_cache_hits':len(ids)};assert replay['cache_hit'];write('patient-replay.json',replay)
  graph['nodes'].append({'id':'work','operation':'worklist','inputs':['population','measure'],'parameters':{'program':'protocol:affordability_action_review@2'},'purpose':'Attach quoted measure components to separate clinical-review assessments'})
  work=stack.run_plan(graph);write('work-question.json',work);w=work['results'][-1]['result'];db=W.database(stack.path)
  try:
   proposal=W.load(db,'work_proposals',w['proposal_id']);rows=[W.load(db,'work_assessments',key) for key in proposal['assessment_ids']];assert sorted(x['patient_id'] for x in rows)==ids
   for row in rows:
    c=row['decision_context']['measure'];assert c['patient_id']==row['patient_id'] and c['population_id']==r['id'];checks['work_contexts_verified']+=1
   write('sample-work-assessment.json',rows[0])
  finally:db.close()
  from clinical_core.agere.clinical.analytic_registry import code_identity
  assert json.loads((stack.path/'operators.json').read_text())['code_identity']==code_identity()
  verification={'passed':True,'synthetic':True,'patients':len(ids),'source_snapshot':stack.retriever.meta['snapshot'],'component_packages':keys,'descriptive_measure_id':r['id'],'unbound_measure_id':pending['id'],'descriptive_counts':r['counts'],'unbound_counts':pending['counts'],'logical_bounds':r['sensitivity_bounds'],'checks':dict(checks),'work_counts':w['counts'],'samples':samples,'new_paid_api_calls':0,'new_weights':0,'human_reviews_created':0,'work_items_created':0,'native_analytic_identity_unchanged':True,'clinical_accuracy_measured':False,'full_stack_acceptance_complete':False};write('verification.json',verification);print(json.dumps(verification,indent=2),flush=True)
 finally:stack.close()

if __name__=='__main__':main()
