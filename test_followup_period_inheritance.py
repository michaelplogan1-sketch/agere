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
        Base.setUp(self)
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
    def test_discourse_prefix_does_not_hide_the_monetary_measure(self):
        from clinical_core.agere.clinical.insight_families import CohortSpend
        for prefix in ('','and ','now '):
            q=prefix+'what share of total spend do they account for?'
            self.assertTrue(C.monetary_followup(self.catalog,q))
            self.assertTrue(CohortSpend.parse(q.rstrip('?')+' over the past 12 months',self.catalog)['params']['rolling_12_months'])

    def test_new_fill_measure_inherits_period_but_warehouse_scope_does_not(self):
        self.ask_turn('Count of paid prescription fills over the last 12 months?','fills')
        q='and what was the average plan-paid amount per fill?'
        out=self.ask_turn(q,'fills')
        values=[r[5]/100 for r in PHARMACY if r[4] and in_win(r[1])]
        self.assertAlmostEqual(out['answer']['value'],sum(values)/len(values))
        fresh=C.handle_compose(self.stack,{'question':q,'conversation_id':'fills','scope':'warehouse'})
        self.assertNotIn('inherited_period',fresh)


class DateAppliesToMeasure(FollowupPeriods):
    def test_condition_cooccurrence_does_not_inherit_a_financial_event_date(self):
        self.ask_turn('How many living patients accumulated $50,000 or more in paid claims over the past 12 months?','conditions')
        out=self.ask_turn('count those patients with diabetes','conditions')
        self.assertNotIn('inherited_period',out)
        self.assertNotIn('over the past 12 months',out['conversation']['resolved_question'])


class NamedCohortAncestry(Base):
    setUp=FollowupPeriods.setUp
    ask_turn=FollowupPeriods.ask_turn
    def test_named_population_survives_an_intervening_summary(self):
        first=self.ask_turn('Who are the high-cost claimants for 2029, meaning $50,000 or more paid?','named')
        root_id=first['action_context']['id']
        self.ask_turn('what share of total 2029 spend do they account for?','named')
        out=self.ask_turn('How many of the high-cost claimants have diabetes?','named')
        self.assertEqual(out['answer']['count'],1)  # P01: $71k; every other living patient is below $50k in 2029.
        self.assertEqual(out['reference_binding']['context_id'],root_id)
        self.assertEqual(out['conversation']['question'],'How many of the high-cost claimants have diabetes?')
        self.assertEqual(out['conversation']['method'],'named_cohort')
    def test_known_condition_population_does_not_rebind_to_a_previous_subset(self):
        first=self.ask_turn('How many diabetic patients have an A1c above 9?','known')
        self.assertIsNone(C.named_cohort_reference(self.stack,'How many of the diabetic patients have CKD?',first['action_context']))

    def test_partial_named_population_cannot_be_promoted_to_a_complete_reference(self):
        from clinical_core.agere.deliverables import answer_context as A
        root=self.ask_turn('Who are the high-cost claimants for 2029, meaning $50,000 or more paid?','partial')
        root['answer']['status']='partial'
        root['answer']['missing']=['Unsettled membership']
        saved=A.capture(self.path,'Who are the high-cost claimants for 2029, meaning $50,000 or more paid?',root)
        self.assertIsNone(C.named_cohort_reference(self.stack,'How many of the high-cost claimants have diabetes?',saved))
