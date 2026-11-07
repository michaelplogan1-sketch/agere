"""Source-bound machine repair and full documentary composition, authored only."""
import copy,json,unittest
from unittest.mock import patch
from clinical_core.agere.clinical import assertion_source_review as R,system_one,analysis_tools as A
from clinical_core.contracts import digest
import test_assertion_source_review as fixtures


class Interpretation(unittest.TestCase):
    def setUp(self):
        self.base=fixtures.AssertionReview();self.base.setUp();self.addCleanup(self.base.doCleanups)
        self.audit,self.tools,self.view=self.base.audit,self.base.tools,self.base.view
        self.tools.ledger=self.tools.stack.path/'authored-interpretation-ledger.sqlite3'

    def call(self):return self.base.call()

    def source_judge(self,subject,questions,evidence,**kwargs):
        state=json.loads(evidence[0]['text'])
        if any('patient_affirmed'in q['criteria']for q in questions.values()):
            out={}
            for key,q in questions.items():
                axis=q['instructions']['task'].split('Return ',1)[1].split('.',1)[0]
                label={'statement':'patient_affirmed','time':'current_at_source','relation':'clinical_observation'}[axis]
                out[key]={'label':label,'probabilities':{label:1.},'probability':1.,'threshold':.8,
                          'decided_by':'authored-judge','provider':'authored-judge'}
            return {'judgments':out,'steps':[{'provider':'authored-judge','status':'answered','cost_usd':.001,'request_hash':'authored-axes'}]}
        result=fixtures.judgment(questions)
        for ident,item in state['proposed_assertions'].items():
            if item['proposal']['subject']in ('family','unknown')or item['proposal']['temporality']in ('historical','unknown'):
                result['judgments'][ident]={**result['judgments'][ident],'label':'not_supported','probabilities':{'not_supported':1.}}
        return result

    def test_machine_corrections_recheck_changed_tuples_and_compose_all_original_assertions(self):
        original=copy.deepcopy(self.audit.result)
        with patch.object(system_one,'judge_fields',side_effect=self.source_judge)as judge:
            first=self.call()
            self.assertEqual(first['assertion_source_review']['status_counts'],{'supported':14,'not_supported':3})
            out=self.tools({'name':'review_documents','input':{'assertion_result_view':first['result_view']}})
        self.assertEqual(judge.call_count,3)  # first review, reader axes, changed-tuple review
        self.assertEqual(out['assertion_source_interpretation']['status_counts'],{'original_tuple_supported':14,'changed_tuple_source_supported':3})
        self.assertEqual(self.audit.result,original)
        self.assertEqual([r['audit_account_id']for r in first['rows']],[r['audit_account_id']for r in out['rows']])
        report=original['engine_result']['results'][-1]['result']
        originals=A.documentary_rows(self.tools.stack.path,'assertion_review',report)
        self.assertEqual(out['assertion_source_interpretation']['original_assertions_composed'],
                         sum(len(p['documentary_receipt']['assertions'])for p in originals))
        groups=out['assertion_source_interpretation']['documentary_groups']
        self.assertEqual({g['patient_id']for g in groups},
                         {p['patient_id']for p in originals if p['documentary_receipt']['assertions']})
        coverage=out['assertion_source_interpretation']['patient_state_coverage']
        self.assertEqual({p['patient_id']for p in coverage},set(self.audit.fixture.ids))
        self.assertEqual(out['assertion_source_interpretation']['input_patients'],12)
        self.assertEqual(next(p for p in coverage if p['patient_id']=='p02')['status'],'no_mapped_assertions')
        self.assertTrue(any(g['documentary_group']['status']=='same_date_conflict'for g in groups))
        for row in out['rows']:
            before=next(r for r in first['rows']if r['audit_account_id']==row['audit_account_id'])
            self.assertEqual(row['proposed_assertion'],before['proposed_assertion'])
            new=row['source_interpretation'];self.assertFalse(new['clinical_qualification'])
            self.assertEqual(new['authority'],'machine_source_interpretation')
            if new['status']=='changed_tuple_source_supported':
                self.assertTrue(new['changed_fields']);self.assertEqual(new['assertion']['subject'],'patient')
                self.assertEqual(new['assertion']['temporality'],'current')
                self.assertEqual(new['assertion']['value'],'affirmed')
                self.assertEqual(new['interpreted_conditional_effects'],[])
        self.assertFalse(out['assertion_source_interpretation']['human_review_created'])
        sql=f"SELECT audit_account_id,patient_id,json_extract(source_interpretation,'$.status') AS status,json_extract(source_interpretation,'$.assertion.subject') AS interpreted_subject FROM {out['result_view']}"
        table=self.tools({'name':'query_sql','input':{'sql':sql}})
        self.assertEqual(len(table['rows']),len(first['rows']))
        self.assertTrue(all(r['interpreted_subject']=='patient'for r in table['rows']))

    def test_uncertain_reader_axes_never_create_a_correction_or_buy_unchanged_tuple_votes(self):
        with patch.object(system_one,'judge_fields',side_effect=self.source_judge):first=self.call()
        def uncertain(s,q,e,**kw):return fixtures.judgment(q,'unknown',1.,None)
        with patch.object(system_one,'judge_fields',side_effect=uncertain)as judge:
            out=self.tools({'name':'review_documents','input':{'assertion_result_view':first['result_view']}})
        self.assertEqual(judge.call_count,2)  # distinct legacy and independent-qualifier tasks
        self.assertEqual(out['assertion_source_interpretation']['status_counts'],{'original_tuple_supported':14,'source_axes_unresolved':3})
        self.assertTrue(all(r['source_interpretation']['assertion']is None for r in out['rows']if r['source_support']=='not_supported'))

    def test_categorical_reader_cannot_change_quantities_units_domains_or_dates(self):
        item={'proposal':{'concept':'authored','relation':'received_observation','value':12.5,'unit':'mg','value_domain':'dose',
               'polarity':'affirmed','subject':'family','temporality':'historical','clinical_date':'2028-01-01','reference_date':'2027-01-01'}}
        readings={axis:{'label':label,'probabilities':{label:1.},'threshold':.8,'decided_by':'authored'}for axis,label in
                  [('statement','patient_affirmed'),('time','current_at_source'),('relation','clinical_observation')]}
        self.assertIsNone(R.categorical_candidate(item,readings))
        item['proposal'].update(value='affirmed',unit=None,value_domain=None)
        result=R.categorical_candidate(item,readings)
        self.assertEqual(result['clinical_date'],'2028-01-01');self.assertEqual(result['reference_date'],'2027-01-01')

    def test_conditional_effect_projection_does_not_assert_clinical_state(self):
        for row in self.audit.result['rows']:
            if row['source_binding_established']:
                self.assertEqual(A.conditional_qualifier_effects(row,row['account_group']),row['analytical_effects'])
                self.assertFalse(row['current_clinical_state_established'])

    def test_partial_reader_correction_is_fully_checked_and_continuation_reuses_prior_work(self):
        def partial_reader(subject,questions,evidence,**kw):
            result=self.source_judge(subject,questions,evidence,**kw)
            state=json.loads(evidence[0]['text'])
            for key,q in questions.items():
                if 'current_at_source'not in q['criteria']:continue
                ident=q['instructions']['target_path'].split('.')[-1]
                if state['proposed_assertions'][ident]['proposal']['subject']=='family':
                    result['judgments'][key]={'label':'current_at_source','probabilities':{'current_at_source':.7,'unspecified':.3},
                        'probability':.7,'threshold':.8,'decided_by':None}
            return result
        original_candidate=R.categorical_candidate
        def old_all_axes_gate(item,readings):
            if item['proposal']['subject']=='family':return None
            return original_candidate(item,readings)
        with patch.object(system_one,'judge_fields',side_effect=partial_reader),patch.object(R,'categorical_candidate',side_effect=old_all_axes_gate):
            first=self.call();partial=self.tools({'name':'review_documents','input':{'assertion_result_view':first['result_view']}})
        self.assertEqual(partial['assertion_source_interpretation']['status_counts'],
                         {'original_tuple_supported':14,'changed_tuple_source_supported':2,'source_axes_unresolved':1})
        with patch.object(system_one,'judge_fields',side_effect=partial_reader)as judge:
            final=self.tools({'name':'review_documents','input':{'assertion_result_view':partial['result_view']}})
            self.assertEqual(judge.call_count,1)
            self.assertEqual(len(judge.call_args.args[1]),1)  # only the new subject-corrected tuple
        self.assertEqual(final['assertion_source_interpretation']['status_counts'],
                         {'original_tuple_supported':14,'changed_tuple_source_supported':3})
        self.assertEqual(final['assertion_source_interpretation']['new_provider_check_count'],1)
        counter=dict(self.tools.subtool_accounting)
        with patch.object(system_one,'judge_fields')as judge:
            again=self.tools({'name':'review_documents','input':{'assertion_result_view':final['result_view']}});judge.assert_not_called()
        self.assertEqual(self.tools.subtool_accounting,counter)
        self.assertEqual(again['assertion_source_interpretation']['new_provider_check_count'],0)

    def test_independent_subject_followup_replaces_combined_uncertainty_before_tuple_check(self):
        def independent(subject,questions,evidence,**kw):
            if any('family'in q['criteria']and 'patient'in q['criteria']for q in questions.values()):
                out={}
                for key,q in questions.items():
                    label='patient'if 'patient'in q['criteria']else'affirmed'
                    out[key]={'label':label,'probabilities':{label:1.},'probability':1.,'threshold':.8,'decided_by':'authored'}
                return {'judgments':out,'steps':[{'provider':'authored','status':'answered','cost_usd':.001,'request_hash':'authored-precise'}]}
            result=self.source_judge(subject,questions,evidence,**kw);state=json.loads(evidence[0]['text'])
            for key,q in questions.items():
                if 'patient_affirmed'not in q['criteria']:continue
                ident=q['instructions']['target_path'].split('.')[-1]
                if state['proposed_assertions'][ident]['proposal']['subject']=='family':
                    result['judgments'][key]={'label':'patient_affirmed','probabilities':{'patient_affirmed':.78,'unknown':.22},
                        'probability':.78,'threshold':.8,'decided_by':None}
            return result
        with patch.object(system_one,'judge_fields',side_effect=independent)as judge:
            first=self.call();out=self.tools({'name':'review_documents','input':{'assertion_result_view':first['result_view']}})
        self.assertEqual(judge.call_count,4)
        self.assertEqual(out['assertion_source_interpretation']['status_counts'],
                         {'original_tuple_supported':14,'changed_tuple_source_supported':3})
        target=next(r['source_interpretation']for r in out['rows']if r['proposed_assertion']['subject']=='family')
        self.assertIsNone(target['reader_axes']['statement']['decided_by'])
        self.assertEqual(target['qualifier_axes']['subject']['label'],'patient')
        self.assertEqual(target['assertion']['subject'],'patient')
        self.assertEqual(target['machine_support']['judgment']['label'],'supported')
        # Even a re-registered edited result cannot turn a different tuple into
        # the actual source-supported interpretation.
        target['assertion']={**target['assertion'],'subject':'family'}
        self.tools.semantic.register_native_result(out['result_view'],out)
        with patch.object(system_one,'judge_fields')as judge,self.assertRaises(ValueError):
            self.tools({'name':'review_documents','input':{'assertion_result_view':out['result_view']}})
        judge.assert_not_called()


if __name__=='__main__':unittest.main()
