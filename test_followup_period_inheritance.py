"""Follow-up calculations preserve the explicitly stated time window and original input text."""
import unittest
from collections import defaultdict
from clinical_core.agere.clinical import conversation as C, shared_planner as S
from test_population_forms import Base, CLAIMS, PHARMACY, living, in_win, AS_OF
from unittest.mock import Mock

class TemporalResolution(unittest.TestCase):
    def test_literal_prior_period_is_carried_without_changing_original_input(self):
        prior={'resolved_question':'Count paid fills over the last 12 months?'}
        result=C.inherited_period('and what was the average paid per fill?',prior,AS_OF)
        self.assertTrue(result['standalone'].endswith('over the last 12 months'))
        self.assertEqual(result['from_question'],prior['resolved_question'])
        for q in ['and in 2028?', 'and in April?', 'and over the past three months?']:
            self.assertIsNone(C.inherited_period(q,prior,AS_OF))
        self.assertIsNone(C.inherited_period('what share do they account for?',{'resolved_question':'Compare paid in 2028 and in 2029'},AS_OF))

class FollowupPeriods(Base):
    def setUp(self):
        super().setUp()
        self.stack.compose=lambda q,**kw:S.compose(self.stack,q,model=Mock(side_effect=AssertionError('No model')),**kw)
    def ask_turn(self,q,conv):
        return C.handle_compose(self.stack,{'question':q,'conversation_id':conv})
    def test_cohort_share_keeps_prior_rolling_period_for_numerator_and_denominator(self):
        root='How many living patients accumulated $50,000 or more in paid claims over the past 12 months?'
        self.ask_turn(root,'cost')
        follow='what share of total spend do they account for?'
        out=self.ask_turn(follow,'cost')
        sums=defaultdict(int)
        for c in CLAIMS:
            if c[14] and in_win(c[7]) and living(c[1]):sums[c[1]]+=c[10]
        chosen={p for p,v in sums.items() if v>=5000000}
        total=sum(c[10] for c in CLAIMS if c[14] and in_win(c[7]))
        part=sum(c[10] for c in CLAIMS if c[14] and in_win(c[7]) and c[1] in chosen)
        self.assertAlmostEqual(out['answer']['value'],round(100*part/total,1))
        self.assertEqual(out['conversation']['question'],follow)
        self.assertEqual(out['conversation']['method'],'inherited_period')
        self.assertTrue(out['conversation']['resolved_question'].endswith('over the past 12 months'))
    def test_new_fill_measure_inherits_period_but_warehouse_scope_does_not(self):
        self.ask_turn('Count of paid prescription fills over the last 12 months?','fills')
        q='and what was the average plan-paid amount per fill?'
        out=self.ask_turn(q,'fills')
        values=[r[5]/100 for r in PHARMACY if r[4] and in_win(r[1])]
        self.assertAlmostEqual(out['answer']['value'],sum(values)/len(values))
        fresh=C.handle_compose(self.stack,{'question':q,'conversation_id':'fills','scope':'warehouse'})
        self.assertNotIn('inherited_period',fresh)
