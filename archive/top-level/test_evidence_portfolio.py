"""Independent set enumeration, lifecycle invalidation and exact question scope."""
import copy,itertools,random,sqlite3,unittest
from unittest.mock import patch
from clinical_core.agere.clinical import evidence_portfolio as ep,evidence_acquisition as ea,analysis_graph as graph,economical_planner as planner
import test_native_requirements as native
import test_analysis_graph as graphs


def proposal(ids,sets):
 proofs=[]
 for i,cases in enumerate(sets):
  proofs.append({'patient_id':'p','contract_id':str(i),'requirement_receipt_id':str(i),'resolution':{'current_value':None,'complete':True,'cases':[
   {'request_ids':list(c),'binding_values':{'x':bool(j%2)},'contract_value':bool(j%2)} for j,c in enumerate(cases)]}})
 return {'items':[{'id':v} for v in ids],'conditional_resolution':proofs,'references':list(range(len(sets))),'population_digest':'test'}


class BundleMath(unittest.TestCase):
 def test_complements_defeat_greedy_and_request_is_counted_once(self):
  p=proposal('abc',[[{'a'}],[{'b','c'}],[{'b','c'}],[{'b','c'}]])
  out=ep.compare(p,2)
  self.assertEqual(out['optimal']['request_ids'],['b','c']);self.assertEqual(out['optimal']['covered_checks'],3)
  self.assertEqual(out['greedy_baseline']['covered_checks'],1);self.assertEqual(out['baseline_gap'],2)
 def test_multiple_cases_count_one_check_and_fewer_requests_win_tie(self):
  out=ep.compare(proposal('ab',[[{'a'},{'b'},{'a','b'}]]),2)
  self.assertEqual(out['optimal']['covered_checks'],1);self.assertEqual(out['optimal']['request_ids'],['a'])
 def test_independent_bruteforce_reference_across_random_hypergraphs(self):
  rng=random.Random(219)
  for _ in range(80):
   ids=list('abcdef')[:rng.randint(1,6)]
   sets=[[set(rng.sample(ids,rng.randint(1,len(ids)))) for _ in range(rng.randint(1,4))] for _ in range(rng.randint(1,6))]
   slots=rng.randint(0,len(ids));out=ep.compare(proposal(ids,sets),slots)
   options=[set(c) for n in range(slots+1) for c in itertools.combinations(ids,n)]
   score=lambda c:sum(any(case<=c for case in cases) for cases in sets)
   best=min(options,key=lambda c:(-score(c),len(c),sorted(c)))
   self.assertEqual(out['optimal']['request_ids'],sorted(best));self.assertEqual(out['optimal']['covered_checks'],score(best))
   self.assertGreaterEqual(out['baseline_gap'],0)
 def test_zero_empty_and_already_resolved(self):
  self.assertEqual(ep.compare(proposal('a',[[{'a'}]]),0)['optimal']['covered_checks'],0)
  out=ep.compare(proposal([],[]),3);self.assertTrue(out['complete']);self.assertEqual(out['optimal']['slots_used'],0)
  p=proposal('a',[[{'a'}]]);p['conditional_resolution'][0]['resolution']['current_value']=False
  self.assertEqual(ep.compare(p,1)['unresolved_check_count'],0)
 def test_input_order_does_not_change_output(self):
  p=proposal('abc',[[{'a','b'}],[{'b'}]]);q=copy.deepcopy(p);q['items'].reverse();q['conditional_resolution'].reverse()
  self.assertEqual(ep.compare(p,2),ep.compare(q,2))
 def test_exceeded_bounds_never_sample(self):
  out=ep.compare(proposal([str(i) for i in range(17)],[]),3)
  self.assertFalse(out['complete']);self.assertIsNone(out['optimal']);self.assertEqual(out['request_count'],17)
  self.assertFalse(ep.compare(proposal('a',[[{'a'}]]*129),1)['complete'])
 def test_incomplete_and_unbound_proofs_never_claim_optimality(self):
  p=proposal('a',[[{'a'}]]);p['conditional_resolution'][0]['resolution']['complete']=False
  self.assertFalse(ep.compare(p,1)['complete'])
  for cases in [[{'b'}],[set()]]:
   self.assertFalse(ep.compare(proposal('a',[cases]),1)['complete'])
 def test_duplicate_and_invalid_inputs_rejected(self):
  for slots in [True,-1,17,1.1]:
   with self.assertRaises(ValueError):ep.compare(proposal([],[]),slots)
  with self.assertRaises(ValueError):ep.compare(proposal('aa',[]),1)
  for p in [{'contracts':['made_up'],'review_slots':1},{'contracts':['native_medication_nonuse']*2,'review_slots':1},{'contracts':[],'review_slots':1}]:
   with self.assertRaises(ValueError):ep.validate(p)


class PortfolioPersistence(unittest.TestCase):
 setUp=native.NativePersistenceTests.setUp
 tearDown=native.NativePersistenceTests.tearDown
 save=native.NativePersistenceTests.save
 def test_replay_and_stale_source_rejection(self):
  p=ea.prepare(self.root,['native_medication_nonuse'],['P-2'])
  r=ep.save(self.root,p['id'],1);self.assertEqual(r['optimal']['covered_checks'],1)
  self.assertTrue(ep.save(self.root,p['id'],1)['cache_hit']);self.assertEqual(ep.inspect(self.root,r['id'])['proposal_id'],p['id'])
  self.save('P-2',[native.use('b',patient_id='P-2')])
  with self.assertRaisesRegex(ValueError,'source or review changed'):ep.inspect(self.root,r['id'])
  with sqlite3.connect(self.root/'evidence-portfolios.sqlite3') as db:
   self.assertEqual(db.execute('SELECT COUNT(*) FROM receipts').fetchone()[0],1)
   with self.assertRaises(sqlite3.IntegrityError):db.execute('DELETE FROM receipts')
 def test_unknown_logic_identity_is_not_reused(self):
  r=ep.execute(self.root,['P-2'],{'contracts':['native_medication_nonuse'],'review_slots':1})
  with patch.object(ep,'identity',return_value='changed'):
   with self.assertRaisesRegex(ValueError,'logic changed'):ep.inspect(self.root,r['id'])
 def test_empty_scope_not_replaced_by_roster(self):
  r=ep.execute(self.root,[],{'contracts':['native_medication_nonuse'],'review_slots':1})
  self.assertEqual(r['patient_contracts_evaluated'],0);self.assertEqual(r['request_count'],0)


class PortfolioQuestions(unittest.TestCase):
 setUp=graphs.GraphExecution.setUp
 tearDown=graphs.GraphExecution.tearDown
 def test_exact_question_graph_and_replay(self):
  q=ep.question('p0',['native_medication_nonuse'],1);g,route=planner.local_plan(q,graphs.ASOF)
  self.assertEqual(route,'exact_evidence_portfolio');r=graph.execute(self.stack,g)
  d=r['results'][-1]['result'];self.assertEqual(d['patient_contracts_evaluated'],1);self.assertEqual(d['request_count'],1)
  self.assertEqual(d['optimal']['checks'][0]['patient_id'],'p0');self.assertTrue(graph.execute(self.stack,g)['cache_hit'])
 def test_qualifiers_and_unknowns_not_dropped(self):
  q=ep.question('p0',['native_medication_nonuse'],1)
  for v in [q+' only Medicare patients',q+' before 2020',q.replace('Compare','Do not compare')]:self.assertIsNone(ep.local_question(v))
  with self.assertRaises(ValueError):ep.local_question(ep.question('p0',['made_up'],1))
 def test_non_membership_input_rejected(self):
  g=ep.local_question(ep.question('p0',['native_medication_nonuse'],1));g['nodes'].append({**g['nodes'][1],'id':'again','inputs':['bundles']})
  with self.assertRaisesRegex(ValueError,'membership'):graph.validate(g,graphs.ASOF)

if __name__=='__main__':unittest.main()
