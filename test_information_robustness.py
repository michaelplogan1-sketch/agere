import copy,itertools,random,sqlite3,unittest
from unittest.mock import patch
from clinical_core.agere.clinical import information_robustness as R,shared_information as S
from test_shared_information import assumptions
import test_shared_information as shared_tests


def rows():
 a=assumptions();b=copy.deepcopy(a)
 for row in b['likelihoods'].values():row.update({'both left':.5,'both right':.5})
 return [{'id':str(i),'request':{'assumptions':v,'question_ids':['q']},'dependencies':{'source':'same'}} for i,v in enumerate((a,b))]


class RobustMath(unittest.TestCase):
 def test_common_rule_maximin_and_regret_differ(self):
  r=R.calculate(rows());self.assertEqual(r['maximin_policy']['request_ids'],[])
  self.assertAlmostEqual(r['maximin_policy']['worst_expected_net_utility'],.5)
  self.assertEqual(r['minimax_regret_policy']['request_ids'],['a']);self.assertAlmostEqual(r['minimax_regret_policy']['worst_regret'],.1)
  self.assertAlmostEqual(r['no_acquisition_minimax_regret']['worst_regret'],.2);self.assertEqual(r['policies_enumerated'],14)
 def test_opposite_models_cannot_choose_model_specific_terminal_actions(self):
  rs=rows();b=rs[1]['request']['assumptions'];b['likelihoods']={'left':{'both left':.2,'both right':.8},'right':{'both left':.8,'both right':.2}}
  r=R.calculate(rs);self.assertEqual(r['maximin_policy']['request_ids'],[]);self.assertEqual(r['minimax_regret_policy']['request_ids'],[])
  self.assertAlmostEqual(r['maximin_policy']['worst_expected_net_utility'],.5)
 def test_unavailable_in_one_model_remains_visible(self):
  rs=rows();rs[1]['request']['assumptions']['requests']['a']['available']=False;r=R.calculate(rs)
  for b in r['bundle_frontier']:
   if 'a' in b['request_ids']:self.assertIsNone(b['maximin']);self.assertFalse(b['available_in_every_model'])
  self.assertEqual(r['minimax_regret_policy']['request_ids'],['b'])
 def test_all_requests_unavailable_leaves_no_acquisition(self):
  rs=rows()
  for v in rs[0]['request']['assumptions']['requests'].values():v['available']=False
  r=R.calculate(rs);self.assertEqual(r['universally_available_policies'],2);self.assertEqual(r['minimax_regret_policy']['request_ids'],[])
 def test_scope_unit_objective_utility_or_observation_mismatch_rejected(self):
  for key in ['dependencies','question_ids','utility_scale','objective','utilities','outcomes']:
   rs=rows()
   if key=='dependencies':rs[1][key]={}
   elif key=='question_ids':rs[1]['request'][key]=['different']
   else:rs[1]['request']['assumptions'][key]='different'
   with self.subTest(key=key),self.assertRaises(ValueError):R.calculate(rs)
 def test_duplicates_and_policy_bounds_rejected(self):
  rs=rows()
  with self.assertRaises(ValueError):R.calculate([rs[0],rs[0]])
  with self.assertRaises(ValueError):R.calculate(rs[:1])
  for r in rs:r['request']['assumptions']['utilities'].update({'third':{'left':0,'right':0},'fourth':{'left':0,'right':0}})
  with self.assertRaisesRegex(ValueError,'three review choices'):R.calculate(rs)
 def test_no_acquisition_is_same_choice_across_models_with_different_priors(self):
  rs=rows();rs[0]['request']['assumptions']['prior']={'left':.9,'right':.1};rs[1]['request']['assumptions']['prior']={'left':.1,'right':.9}
  r=R.calculate(rs);self.assertAlmostEqual(r['no_acquisition_maximin']['worst_expected_net_utility'],.1)
 def test_independent_direct_enumeration_random_models(self):
  rng=random.Random(141)
  for _ in range(25):
   rs=rows()
   for row in rs:
    a=row['request']['assumptions'];p=rng.random();a['prior']={'left':p,'right':1-p}
    for s in a['prior']:
     p=rng.random();a['likelihoods'][s]={'both left':p,'both right':1-p}
   r=R.calculate(rs);ref=[];individual=[-float('inf')]*2
   for ids in [[],['a'],['b'],['a','b']]:
    outcomes=[()] if not ids else [('left',),('right',)]
    for rule in itertools.product(['choose left','choose right'],repeat=len(outcomes)):
     values=[]
     for row in rs:
      a=row['request']['assumptions'];total=0
      for s,prior in a['prior'].items():
       for o,p in a['likelihoods'][s].items():
        pos=0 if not ids or o=='both left' else 1;total+=prior*p*a['utilities'][rule[pos]][s]
      values.append(total-sum(a['requests'][i]['cost'] for i in ids))
     individual=[max(x,y) for x,y in zip(individual,values)];ref.append((ids,values))
   self.assertAlmostEqual(r['maximin_policy']['worst_expected_net_utility'],max(min(v) for ids,v in ref))
   self.assertAlmostEqual(r['minimax_regret_policy']['worst_regret'],min(max(a-b for a,b in zip(individual,v)) for ids,v in ref))


class RobustPersistence(unittest.TestCase):
 setUp=shared_tests.StoredJoint.setUp
 tearDown=shared_tests.StoredJoint.tearDown
 save=shared_tests.StoredJoint.save
 request=shared_tests.StoredJoint.request
 def pair(self):
  req=self.request();p=S.preview(self.root,req);a=S.prepare(self.root,{'scenario':req,'preview_id':p['id']})
  req=copy.deepcopy(req)
  for dist in req['assumptions']['likelihoods'].values():dist.update({'both left':.5,'both right':.5})
  req['assumptions']['assumption_source']='Explicit alternative engineering model'
  p=S.preview(self.root,req);b=S.prepare(self.root,{'scenario':req,'preview_id':p['id']})
  return {'portfolio_id':req['portfolio_id'],'scenario_ids':[a['id'],b['id']],'rationale':'Compare explicit uncertainty set'}
 def test_save_replay_and_immutable_comparison(self):
  req=self.pair();p=R.preview(self.root,req);self.assertEqual(R.prepare(self.root,{'comparison':req,'preview_id':p['id']}),p)
  self.assertEqual(R.prepare(self.root,{'comparison':req,'preview_id':p['id']}),p)
  self.assertTrue(R.listing(self.root,{'portfolio_id':req['portfolio_id']})['comparisons'][0]['current'])
  with sqlite3.connect(self.root/'information-robustness.sqlite3') as db:
   with self.assertRaises(sqlite3.IntegrityError):db.execute('delete from comparisons')
 def test_changed_model_or_preview_rejected(self):
  req=self.pair();p=R.preview(self.root,req);req['rationale']='Different'
  with self.assertRaises(ValueError):R.prepare(self.root,{'comparison':req,'preview_id':p['id']})
  req['scenario_ids'][0]='0'*64
  with self.assertRaises(ValueError):R.preview(self.root,req)
 def test_source_change_preserves_stale_history(self):
  import test_native_requirements as native
  req=self.pair();p=R.preview(self.root,req);R.prepare(self.root,{'comparison':req,'preview_id':p['id']});self.save('P-2',[native.use('changed',patient_id='P-2')])
  self.assertFalse(R.listing(self.root,{'portfolio_id':req['portfolio_id']})['comparisons'][0]['current'])
 def test_implementation_change_preserves_history(self):
  req=self.pair();p=R.preview(self.root,req);R.prepare(self.root,{'comparison':req,'preview_id':p['id']})
  with patch.object(R,'identity',return_value='changed'):self.assertFalse(R.listing(self.root,{'portfolio_id':req['portfolio_id']})['comparisons'][0]['current'])

if __name__=='__main__':unittest.main()
