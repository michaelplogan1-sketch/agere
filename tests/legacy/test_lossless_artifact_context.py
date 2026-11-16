"""Lossless prompt sharing preserves every clinical datum and native binding."""
import copy,json,unittest
from clinical_core.agere.deliverables.receipts import Bundle,share_json,unshare_json

class Sharing(unittest.TestCase):
 def test_round_trip_complete_rows_strings_dates_nulls_and_late_exceptions(self):
  text='A complete dated document. '*200
  rows=[{'patient_id':str(i),'value':None,'text':text,'date':'2030-05-27'} for i in range(57)]
  rows[-1]['exception']='Historical barrier resolved; current status differs.'
  value={'answer':rows,'audits':[copy.deepcopy(rows)]*5,'literal':{'$agere_shared':['answer']},'numbers':[0,False,0.0,-2,'10 mg','三','<tag>']}
  before=copy.deepcopy(value);encoded=share_json(value)
  self.assertEqual(unshare_json(encoded),value);self.assertEqual(value,before)
  self.assertLess(len(json.dumps(encoded)),len(json.dumps(value))//2)
  self.assertIn(rows[-1]['exception'],json.dumps(encoded))
 def test_list_order_and_different_values_are_not_merged(self):
  value=[['x'*500,'y'*500],['y'*500,'x'*500],['x'*500,'x'*499+'z']]
  self.assertEqual(unshare_json(share_json(value)),value)
 def test_tamper_or_cycle_cannot_claim_an_exact_round_trip(self):
  x=share_json(['x'*500]*2);x['data'][0]='changed'
  with self.assertRaises(ValueError):unshare_json(x)
  x=share_json(['x'*500]*2);x['references'][0]['from']=[1];x['data'][1]={'$agere_shared':[1]}
  with self.assertRaises(ValueError):unshare_json(x)
 def test_model_view_keeps_all_native_receipts_bindings_and_original_bundle(self):
  b=Bundle();rid=b.add('saved_answer',{'context_id':'owned'},{'answer':{'table':[{'patient_id':'A','text':'full source '*100}]},'duplicate':['full source '*100]*5})
  before=copy.deepcopy(b.to_json());regular=json.loads(b.digest_for_model(max_list=None,max_str=None,max_chars=None));shared=json.loads(b.digest_for_model(max_list=None,max_str=None,max_chars=None,shared=True))
  self.assertEqual(unshare_json(shared),regular);self.assertEqual(b.to_json(),before)
  self.assertEqual(b.get(rid+':answer.table.0.patient_id'),'A')

if __name__=='__main__':unittest.main()
