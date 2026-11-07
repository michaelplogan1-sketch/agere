"""Retained-source continuation must keep subsequent explicit readings visible."""
import copy,json,unittest
from clinical_core.agere.clinical import semantic_analyst as S,backstop_agent as B
from clinical_core.contracts import digest

class ContinuationReadBoundary(unittest.TestCase):
 def test_append_only_executor_cannot_move_the_prior_reading_boundary(self):
  def reading(view,record):
   rows=[{'patient_id':'A','record_id':record,'quote':'A complete source observation.'}];columns=list(rows[0])
   return {'result_view':view,'rows':rows,'columns':columns,'truncated':False,
    'actor_reading':{'mode':'complete_rows','rows_digest':digest(rows),'columns_digest':digest(columns)}}
  old=reading('agere_result_1','old');fresh=reading('agere_result_2','new')
  initial=[{'name':'query_sql','input':{'sql':'SELECT old'},'result':old}];seen=[]
  def execute(use):
   initial.append({**copy.deepcopy(use),'result':fresh})
   return fresh
  def send(body):
   state=json.loads(body['messages'][0]['content']);seen.append(state)
   if len(seen)==1:
    self.assertIn('prior_reading_not_inherited',state['executed_results'][0])
    content=[{'type':'tool_use','id':'read-current','name':'query_sql','input':{'sql':'SELECT new','read_all_rows':True}}]
   else:
    entries={e['result_view']:e for e in state['executed_results']}
    self.assertIn('complete_reading',entries['agere_result_2'])
    self.assertNotIn('prior_reading_not_inherited',entries['agere_result_2'])
    self.assertIn('prior_reading_not_inherited',entries['agere_result_1'])
    self.assertIn('new',json.dumps(entries['agere_result_2']['complete_reading']))
    content=[{'type':'text','text':json.dumps({'status':'answered','answer_type':'count','count':1})}]
   return {'content':content,'usage':{}},0.,False
  out=S.tool_loop('Inspect the newly requested evidence.','sys',B.TOOLS,execute,send=send,model='scripted',
   max_tools=None,compact_execution_state=True,initial_calls=initial,initial_readings=False)
  self.assertEqual((out.get('answer')or{}).get('count'),1,out.get('error'))
  self.assertEqual(len(seen),2)
  self.assertEqual(len(initial),2)
  self.assertEqual(old['rows'][0]['record_id'],'old')

if __name__=='__main__':unittest.main()
