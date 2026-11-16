"""Joint experiment math, exact existing request bindings, immutable history."""
import copy,itertools,random,sqlite3,unittest
from unittest.mock import patch
from clinical_core.agere.clinical import shared_information as S,evidence_acquisition as A,evidence_portfolio as P
import test_native_requirements as native


def assumptions():
 return {'prior':{'left':.5,'right':.5},'utilities':{'choose left':{'left':1,'right':0},'choose right':{'left':0,'right':1}},
  'requests':{r:{'available':True,'cost':.1,'basis':'Explicit engineering assumption'} for r in ('a','b')},
  'outcomes':{'both left':{'a':'left','b':'left'},'both right':{'a':'right','b':'right'}},
  'likelihoods':{'left':{'both left':.8,'both right':.2},'right':{'both left':.2,'both right':.8}},
  'utility_scale':'hypothetical usefulness units','assumption_source':'Engineering demonstration, not patient estimates',
  'objective':'One review decision across selected documentary questions','joint_basis':'Perfectly correlated responses explicitly assumed; omitted discordant outcomes have probability zero'}


def bundle(result,ids):return next(b for b in result['bundles'] if b['request_ids']==ids)


class JointMath(unittest.TestCase):
 def test_correlated_bundle_does_not_double_information(self):
  c=S.calculate(assumptions());self.assertEqual(c['baseline_utility'],.5)
  for ids in [['a'],['b'],['a','b']]:self.assertAlmostEqual(bundle(c,ids)['gross_value'],.3)
  self.assertAlmostEqual(bundle(c,['a','b'])['net_value'],.1);self.assertEqual(c['preferred_request_ids_under_assumptions'],['a'])
  self.assertEqual(c['net_gain_over_best_single_or_none'],0)
 def test_joint_complementarity_without_independence_guess(self):
  a=assumptions();a['outcomes']={str(i):dict(zip('ab',bits)) for i,bits in enumerate(itertools.product('01',repeat=2))}
  a['likelihoods']={'left':{'0':.5,'1':0,'2':0,'3':.5},'right':{'0':0,'1':.5,'2':.5,'3':0}}
  c=S.calculate(a);self.assertEqual(bundle(c,['a'])['gross_value'],0);self.assertEqual(bundle(c,['b'])['gross_value'],0)
  self.assertEqual(bundle(c,['a','b'])['gross_value'],.5);self.assertAlmostEqual(c['net_gain_over_best_single_or_none'],.3)
 def test_unavailable_visible_but_not_selected(self):
  a=assumptions();a['requests']['a']['available']=False;c=S.calculate(a)
  self.assertFalse(bundle(c,['a','b'])['available']);self.assertEqual(c['preferred_request_ids_under_assumptions'],['b'])
 def test_no_information_and_ties_prefer_no_acquisition(self):
  a=assumptions();a['utilities']={'same':{'left':1,'right':1}}
  self.assertEqual(S.calculate(a)['preferred_request_ids_under_assumptions'],[])
  for r in a['requests'].values():r['cost']=0
  self.assertEqual(S.calculate(a)['preferred_request_ids_under_assumptions'],[])
 def test_zero_prior_and_zero_probability_outcomes(self):
  a=assumptions();a['prior']={'left':1,'right':0};a['likelihoods']['left']={'both left':1,'both right':0}
  c=S.calculate(a);self.assertEqual(len(bundle(c,['a','b'])['branches']),1);self.assertEqual(bundle(c,['a'])['gross_value'],0)
 def test_cost_once_per_exact_id(self):
  a=assumptions();c=S.calculate(a);self.assertEqual(bundle(c,[])['assumed_cost'],0);self.assertEqual(bundle(c,['a','b'])['assumed_cost'],.2)
 def test_invalid_joint_vectors_distributions_authority_and_bounds(self):
  mutations=[lambda a:a['outcomes']['both left'].pop('a'),lambda a:a['outcomes'].update(extra=a['outcomes']['both left']),
   lambda a:a['likelihoods']['left'].update({'both left':.9}),lambda a:a['likelihoods']['left'].update(extra=0),
   lambda a:a['requests']['a'].update(cost=True),lambda a:a['requests']['a'].update(cost=float('inf')),
   lambda a:a.update(clinical_execution_authorized=True),lambda a:a.update(objective=''),lambda a:a.update(joint_basis=''),
   lambda a:a['requests'].update(c=a['requests']['a'],d=a['requests']['a'])]
  for fn in mutations:
   a=assumptions();fn(a)
   with self.subTest(fn=fn),self.assertRaises(ValueError):S.calculate(a)
 def test_independent_direct_preposterior_sum_for_random_joint_models(self):
  rng=random.Random(77)
  for _ in range(30):
   a=assumptions();p=rng.random();a['prior']={'left':p,'right':1-p}
   a['outcomes']={str(i):dict(zip('ab',bits)) for i,bits in enumerate(itertools.product('01',repeat=2))}
   a['likelihoods']={}
   for s in a['prior']:
    vals=[rng.random() for _ in range(4)];a['likelihoods'][s]={str(i):v/sum(vals) for i,v in enumerate(vals)}
   c=S.calculate(a)
   for b in c['bundles']:
    groups={}
    for o,vector in a['outcomes'].items():groups.setdefault(tuple(vector[r] for r in b['request_ids']),[]).append(o)
    after=sum(max(sum(a['prior'][s]*sum(a['likelihoods'][s][o] for o in outcomes)*u[s] for s in a['prior']) for u in a['utilities'].values()) for outcomes in groups.values())
    self.assertAlmostEqual(b['gross_value'],after-max(p,1-p))
   self.assertGreaterEqual(bundle(c,['a','b'])['gross_value']+1e-12,bundle(c,['a'])['gross_value'])
 def test_input_dictionary_order_does_not_change_preferred(self):
  a=assumptions();a['requests']=dict(reversed(list(a['requests'].items())))
  self.assertEqual(S.calculate(a),S.calculate(assumptions()))


class StoredJoint(unittest.TestCase):
 setUp=native.NativePersistenceTests.setUp
 tearDown=native.NativePersistenceTests.tearDown
 save=native.NativePersistenceTests.save
 def request(self):
  proposal=A.prepare(self.root,['native_medication_nonuse'],['P-2']);p=P.save(self.root,proposal['id'],1);source=A.load(self.root,proposal['id'])
  rid=source['items'][0]['id'];a=assumptions();a['requests']={rid:a['requests']['a']};a['outcomes']={o:{rid:v['a']} for o,v in a['outcomes'].items()}
  return {'portfolio_id':p['id'],'question_ids':[source['references'][0]['receipt_id']],'rationale':'Review hypothetical information acquisition','assumptions':a}
 def test_exact_scope_replay_and_immutable_storage(self):
  req=self.request();p=S.preview(self.root,req);self.assertEqual(len(p['questions']),1)
  r=S.prepare(self.root,{'scenario':req,'preview_id':p['id']});self.assertEqual(r,p);self.assertEqual(S.prepare(self.root,{'scenario':req,'preview_id':p['id']}),p)
  self.assertTrue(S.listing(self.root,{'portfolio_id':req['portfolio_id']})['scenarios'][0]['current'])
  with sqlite3.connect(self.root/'shared-information.sqlite3') as db:
   self.assertEqual(db.execute('select count(*) from scenarios').fetchone()[0],1)
   with self.assertRaises(sqlite3.IntegrityError):db.execute('delete from scenarios')
 def test_stale_source_is_history_and_cannot_be_saved(self):
  req=self.request();p=S.preview(self.root,req);S.prepare(self.root,{'scenario':req,'preview_id':p['id']});self.save('P-2',[native.use('new',patient_id='P-2')])
  self.assertFalse(S.listing(self.root,{'portfolio_id':req['portfolio_id']})['scenarios'][0]['current'])
  with self.assertRaises(ValueError):S.prepare(self.root,{'scenario':req,'preview_id':p['id']})
 def test_changed_assumptions_or_preview_id_rejected(self):
  req=self.request();p=S.preview(self.root,req);req['assumptions']['objective']='Changed objective'
  with self.assertRaises(ValueError):S.prepare(self.root,{'scenario':req,'preview_id':p['id']})
 def test_outside_or_duplicate_questions_rejected(self):
  for change in ['outside','duplicate']:
   req=self.request();req['question_ids']=['f'*64] if change=='outside' else req['question_ids']*2
   with self.assertRaises(ValueError):S.preview(self.root,req)
 def test_unknown_request_and_unlinked_question_rejected(self):
  req=self.request();rid=next(iter(req['assumptions']['requests']));a=req['assumptions'];a['requests']['fake']=a['requests'].pop(rid)
  for v in a['outcomes'].values():v['fake']=v.pop(rid)
  with self.assertRaisesRegex(ValueError,'outside'):S.preview(self.root,req)
 def test_shared_question_links_do_not_multiply_cost(self):
  req=self.request();p,a=S.source(self.root,req['portfolio_id']);a=copy.deepcopy(a);other=copy.deepcopy(a['references'][0]);other['receipt_id']='b'*64;a['references'].append(other);a['items'][0]['requirements'].append({**a['items'][0]['requirements'][0],'receipt_id':other['receipt_id']});req['question_ids'].append(other['receipt_id'])
  original=S.Q.inspect(self.root,req['question_ids'][0])
  def inspect(path,key):return {**original,'id':key}
  with patch.object(S,'source',return_value=(p,a)),patch.object(S.Q,'inspect',side_effect=inspect):
   out=S.snapshot(self.root,req);self.assertEqual(len(out['questions']),2);self.assertEqual(out['calculation']['bundles'][1]['assumed_cost'],.1)
 def test_implementation_change_marks_history(self):
  req=self.request();p=S.preview(self.root,req);S.prepare(self.root,{'scenario':req,'preview_id':p['id']})
  with patch.object(S,'identity',return_value='changed'):self.assertFalse(S.listing(self.root,{'portfolio_id':req['portfolio_id']})['scenarios'][0]['current'])

if __name__=='__main__':unittest.main()
