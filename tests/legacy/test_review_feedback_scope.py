"""A machine repair request cannot become a source fact on the next review."""
import copy,json,unittest
from clinical_core.agere.clinical import semantic_analyst as S

class ReviewFeedbackScope(unittest.TestCase):
 def test_retained_and_fresh_rejected_answers_preserve_only_real_source_limits(self):
  source_limit='The received cash transaction has no currency field.'
  diagnostic='Internal source-definition repair: rebuild the financial basis from native result_12.'
  answer={'status':'answered','answer_type':'table','table_from_tool':'agere_result_1',
   'facts':{'source':'Recorded allocation amounts.'},'limitations':[source_limit],'missing':[]}
  assessment={'status':'repair','repair_kind':'source_definition','feedback':diagnostic,
   'retry_disposition':'retain_partial_until_new_evidence','review_stop_reason':'unresolved_source_definition'}
  for retained in (True,False):
   before=copy.deepcopy(answer);sent=[]
   def send(body):
    sent.append(body);return {'content':[{'type':'text','text':json.dumps(answer)}]},0.,True
   result=S.tool_loop('Compare recorded amounts.','system',[],lambda u:self.fail('No source rerun'),
    send=send,model='scripted',max_tools=None,review=lambda a,c:copy.deepcopy(assessment),
    initial_answer=answer if retained else None)
   with self.subTest(retained=retained):
    self.assertEqual(result['answer']['limitations'],[source_limit])
    self.assertEqual(result['answer']['facts'],answer['facts']);self.assertEqual(answer,before)
    self.assertEqual(result['answer']['status'],'partial');self.assertEqual(result['answer']['verification'],'unverified')
    self.assertEqual(result['answer_reviews'][-1]['feedback'],diagnostic)
    self.assertEqual(result['review_stopped'],'unresolved_source_definition')
    self.assertEqual(len(sent),0 if retained else 1)

if __name__=='__main__':unittest.main()
