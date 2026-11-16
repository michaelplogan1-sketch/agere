"""VoI decisions in the actual clarification/native-read loop; no providers."""
import copy
import json
import sqlite3
import unittest
from types import SimpleNamespace
from unittest.mock import patch
from clinical_core.agere.clinical import clarification as I
import test_intent_clarification as fixture
SPEC,QUESTION=fixture.SPEC,fixture.QUESTION


class ClarificationValue(unittest.TestCase):
    def setUp(self):
        self.fixture=fixture.IntentClarification('test_pause_restart_resume_reuses_native_read_and_reviews_the_new_answer')
        self.fixture.setUp();self.addCleanup(self.fixture.doCleanups)

    def tools(self,**kw):
        return SimpleNamespace(stack=self.fixture.stack,native_calls=kw.get('calls',[]),
            executed_results=kw.get('results',{}),resolved_intent_slots=kw.get('resolved',set()),
            continuation_identity=lambda:{'source':'authored-native-fixture','question':QUESTION})

    def rows(self):
        with I.database(self.fixture.path)as db:
            return [json.loads(row[0])for row in db.execute('SELECT payload FROM intent_clarification_decisions ORDER BY seq')]

    def run_with(self,spec):
        original=self.fixture.sender
        def send(body):
            response,cost,cached=original(body)
            for block in response.get('content',[]):
                if block.get('name')=='request_clarification':block['input']=copy.deepcopy(spec)
            return response,cost,cached
        with patch.object(self.fixture,'sender',side_effect=send):return self.fixture.run_actor(QUESTION)

    def test_identical_answers_do_not_interrupt_or_repeat_native_work(self):
        spec=copy.deepcopy(SPEC)
        spec['value_model']['outcomes']={o:{'population_size':2,'measure_value':12,'ranking':['A','B']}for o in ('roster','summary')}
        result=self.run_with(spec)
        self.assertEqual(result['answer']['status'],'answered');self.assertNotIn('clarification_checkpoint',result)
        self.assertEqual(len(self.fixture.queries),1)
        row=self.rows()[0];self.assertEqual(row['gross_value'],0);self.assertLess(row['net_value'],0);self.assertFalse(row['ask'])

    def test_small_population_or_measure_change_is_not_worth_interruption(self):
        for key in ('population_size','measure_value'):
            spec=copy.deepcopy(SPEC);spec['value_model']['outcomes']={'roster':{key:100},'summary':{key:101}}
            row=I.request(spec,tools=self.tools(results={'agere_result_1':{}}))
            self.assertIn('intent_unresolved',row)
            self.assertLess(row['clarification_decision']['net_value'],0)

    def test_rare_alternative_has_low_expected_value_despite_large_change(self):
        spec=copy.deepcopy(SPEC);spec['value_model']['prior']={'roster':.999,'summary':.001}
        result=self.run_with(spec)
        self.assertNotIn('clarification_checkpoint',result);self.assertEqual(result['answer']['status'],'partial')
        self.assertFalse(self.rows()[0]['ask']);self.fixture.propose.assert_not_called()

    def test_zero_net_value_does_not_ask(self):
        gross=I.score(SPEC)['gross_value']
        with patch.object(I,'INTERRUPTION_COST',gross):
            result=I.request(SPEC,tools=self.tools(results={'agere_result_1':{}}))
        self.assertEqual(result['clarification_decision']['net_value'],0);self.assertNotIn('clarification_required',result)

    def test_positive_ranking_change_asks_and_records_assumptions(self):
        result=self.run_with(SPEC)
        self.assertIn('clarification_checkpoint',result)
        row=self.rows()[0];self.assertTrue(row['ask']);self.assertGreater(row['net_value'],0)
        self.assertFalse(row['calibrated']);self.assertEqual(row['outcomes_are'],'assumed_counterfactuals_not_observed_facts')

    def test_early_intent_only_asks_before_dispatch(self):
        for kind in ('goal','population','period','definition'):
            spec={**copy.deepcopy(SPEC),'kind':kind};spec.pop('branch')
            early=I.request(spec,tools=self.tools())
            self.assertIn('clarification_required',early)
            late=I.request(spec,tools=self.tools(calls=[{'name':'query_sql'}]))
            self.assertNotIn('clarification_required',late)
            self.assertEqual(late['clarification_decision']['reason'],'early_intent_after_dispatch')

    def test_early_population_question_pauses_before_native_dispatch_and_resumes(self):
        spec={**copy.deepcopy(SPEC),'kind':'population'};spec.pop('branch')
        responses=[fixture.tool('intent','request_clarification',spec),
            fixture.tool('read','query_sql',{'sql':"SELECT patient_id FROM patient WHERE patient_id='A'"}),
            {'content':[{'type':'text','text':json.dumps({'status':'answered','answer_type':'table',
                'headline':'The retained roster is available.','table_from_tool':'agere_result_2','sources':[]})}]}]
        def send(body):
            self.fixture.bodies.append(copy.deepcopy(body));return responses.pop(0),0,False
        with patch.object(self.fixture,'sender',side_effect=send):
            paused=self.fixture.pause();self.assertEqual(len(self.fixture.queries),0)
            final=self.fixture.respond(paused,option_id='roster')
        self.assertEqual(final['answer']['table'],[{'patient_id':'A'}]);self.assertEqual(len(self.fixture.queries),1)

    def test_deferred_input_is_reassessed_when_its_branch_becomes_ready(self):
        responses=[fixture.tool('too_early','request_clarification',SPEC),
            fixture.tool('read','query_sql',{'sql':"SELECT patient_id FROM patient WHERE patient_id='A'"}),
            fixture.tool('ready','request_clarification',{**copy.deepcopy(SPEC),'branch':'agere_result_2'})]
        def send(body):return responses.pop(0),0,False
        with patch.object(self.fixture,'sender',side_effect=send):paused=self.fixture.pause()
        self.assertIn('clarification',paused);self.assertEqual(len(self.fixture.queries),1)
        self.assertEqual([r['ask']for r in self.rows()],[False,True])

    def test_input_and_value_wait_for_the_dependent_native_branch(self):
        for kind in ('input','value'):
            spec={**copy.deepcopy(SPEC),'kind':kind}
            unrelated=I.request(spec,tools=self.tools(results={'agere_result_other':{}}))
            self.assertNotIn('clarification_required',unrelated)
            self.assertEqual(unrelated['clarification_decision']['reason'],'dependent_branch_not_ready')
            ready=I.request(spec,tools=self.tools(results={'agere_result_1':{}}))
            self.assertIn('clarification_required',ready)

    def test_missing_or_invalid_impact_is_never_treated_as_positive(self):
        spec=copy.deepcopy(SPEC);spec.pop('value_model')
        missing=I.request(spec,tools=self.tools(results={'agere_result_1':{}}))
        self.assertIsNone(missing['clarification_decision']['net_value'])
        self.assertIn('clarification_required',missing)
        self.assertEqual(missing['clarification_decision']['reason'],'unscored_material_intent')
        for value in (float('nan'),True,-.2):
            invalid=copy.deepcopy(SPEC);invalid['value_model']['prior']['roster']=value
            with self.assertRaises(ValueError):I.request(invalid,tools=self.tools())
        self.assertEqual(len(self.rows()),4)
        self.assertTrue(all(not row['ask']for row in self.rows()[1:]))

    def test_existing_material_question_needs_no_invented_numeric_projections(self):
        legacy={k:v for k,v in SPEC.items() if k in ('slot','question','reason','options')}
        self.assertIn('clarification_required',I.request(legacy,tools=self.tools()))
        with I.interaction('autonomous'):
            self.assertIn('intent_unresolved',I.request(legacy,tools=self.tools()))

    def test_autonomous_and_resolved_choices_are_logged_without_interrupts(self):
        with I.interaction('autonomous'):
            result=I.request(SPEC,tools=self.tools(results={'agere_result_1':{}}))
        self.assertNotIn('clarification_required',result)
        with self.assertRaisesRegex(ValueError,'already clarified'):
            I.request(SPEC,tools=self.tools(resolved={SPEC['slot']}))
        self.assertEqual([r['reason']for r in self.rows()],['autonomous_mode','already_resolved'])

    def test_decision_log_is_append_only(self):
        I.request(SPEC,tools=self.tools())
        with I.database(self.fixture.path)as db:
            for sql in ('UPDATE intent_clarification_decisions SET payload=payload','DELETE FROM intent_clarification_decisions'):
                with self.assertRaises(sqlite3.IntegrityError):db.execute(sql)


if __name__=='__main__':unittest.main()
