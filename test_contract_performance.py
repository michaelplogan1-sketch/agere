"""Source-free contract/cohort conservation and changed-term work boundaries."""
import copy
import json
from pathlib import Path
import sqlite3
import tempfile
import unittest
from unittest.mock import patch

from clinical_core.agere.clinical import contract_performance as C, plan_fence, decision_context


class ContractPerformance(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.path = Path(self.temp.name)
        self.contract = json.loads((C.DIRECTORY / 'demo-vbc-2029-c3.json').read_text())
        self.file = self.path / 'demo-vbc-2029-c3.json'
        self.file.write_text(json.dumps(self.contract))
        self.addCleanup(patch.stopall)
        patch.object(C, 'DIRECTORY', self.path).start()
        self.db = sqlite3.connect(':memory:')
        self.addCleanup(self.db.close)
        self.db.executescript('CREATE TABLE member_month(patient_id,month,payer_id,attribution_practice_id,record_id);'
                             'CREATE TABLE payer_claim(payer_claim_id,patient_id,payer_id,service_from,allowed_cents,counts_in_metrics,last_available_date,record_id);')
        self.db.executemany('INSERT INTO member_month VALUES(?,?,?,?,?)', [
            ('A','2029-01','WH-PAYER-C3','Practice1','E1'),
            ('A','2029-02','WH-PAYER-C3','Practice2','E2'),
            ('B','2029-01','WH-PAYER-C3','Practice1','E3'),
            ('FOREIGN','2029-01','OTHER','Practice1','E4')])
        self.db.executemany('INSERT INTO payer_claim VALUES(?,?,?,?,?,?,?,?)', [
            ('A1','A','WH-PAYER-C3','2029-01-10',10000,1,'2030-01-01','R1'),
            ('A2','A','WH-PAYER-C3','2029-02-10',20000,1,'2030-01-01','R2'),
            ('B1','B','WH-PAYER-C3','2029-01-10',5000,1,'2030-01-01','R3'),
            ('OLD','A','WH-PAYER-C3','2029-01-10',90000,0,'2030-01-01','R4'),
            ('NO_MM','B','WH-PAYER-C3','2029-02-10',8000,1,'2030-01-01','R5'),
            ('OTHER','A','OTHER','2029-01-10',7000,1,'2030-01-01','R6'),
            ('FUTURE','A','WH-PAYER-C3','2029-01-10',6000,1,'2031-01-01','R7'),
            ('ZERO','B','WH-PAYER-C3','2029-01-10',0,1,'2030-01-01','R8')])
        patch.object(C.vbc, 'eligibility', return_value={p:{'months':['2030-05'], 'age':50,'sex':'F'} for p in ['A','B','FOREIGN']}).start()
        def quality(db, definition, start, end, as_of, **kw):
            self.assertEqual(set(kw['people']), set(kw['keep']))
            self.assertNotIn('2030-05', [m for row in kw['people'].values() for m in row['months']])
            return {'declined':False,'overall':{'denominator':1,'numerator':1,'rate':1.0},
                    'patient_rows':[{'patient_id':'A','met':True,'excluded':False}], 'warnings':[]}
        self.quality = patch.object(C.measures, 'evaluate_db', side_effect=quality).start()

    def run_report(self, people=None):
        return C.run(self.db, people, {'contract_id':self.contract['id']}, '2030-05-27')

    def test_monthly_attribution_financial_conservation_and_same_buyer_basis(self):
        report = self.run_report()
        s = report['summary']
        self.assertEqual((s['members'],s['recorded_member_months'],s['included_claims']), (2,3,4))
        self.assertEqual(s['observed_allowed_cents'],35000)
        self.assertEqual(s['benchmark_cents'],300000)
        self.assertEqual(s['illustrative_provider_share_cents'],132500)
        self.assertEqual(s['illustrative_provider_share_cents']+s['illustrative_payer_retained_positive_variance_cents'],s['observed_variance_cents'])
        self.assertFalse(s['settlement_established'])
        by = {r['practice_id']:r for r in report['practice_drivers']}
        self.assertEqual(by['Practice1']['observed_allowed_cents'],15000)
        self.assertEqual(by['Practice2']['observed_allowed_cents'],20000)
        self.assertEqual(len(report['monthly']),12)
        self.assertEqual(report['excluded_claim_counts'],{'superseded_or_nonmetric_claim':1,'no_same_payer_attributed_member_month':1,'not_observed_by_snapshot':1})

    def test_explicit_subset_and_empty_scope_do_not_expand_to_all_members(self):
        report = self.run_report(['B'])
        self.assertEqual(report['patient_ids'],['B'])
        self.assertEqual(report['summary']['observed_allowed_cents'],5000)
        self.assertEqual(self.run_report([])['patient_ids'],[])

    def test_missing_money_and_missing_quality_never_become_zero_or_full_credit(self):
        self.db.execute("UPDATE payer_claim SET allowed_cents=NULL WHERE payer_claim_id='A1'")
        report = self.run_report()
        self.assertIsNone(report['summary']['observed_allowed_cents'])
        self.assertEqual(report['summary']['known_allowed_subtotal_cents'],25000)
        self.assertIsNone(report['summary']['illustrative_provider_share_cents'])
        self.assertEqual(C.answer(report,{'coverage':'supported_observation'},'Contract')['status'],'partial')
        self.db.execute("UPDATE payer_claim SET allowed_cents=10000 WHERE payer_claim_id='A1'")
        self.quality.side_effect = None
        self.quality.return_value = {'declined':True,'missing_inputs':['Absent required source'],'overall':{},'warnings':[]}
        self.assertIsNone(self.run_report()['summary']['illustrative_provider_share_cents'])

    def test_changed_terms_invalidate_only_the_dependent_plan_and_restore(self):
        p = C.validate({'contract_id':self.contract['id']})
        graph = {'nodes':[{'operation':C.OP,'parameters':p}]}
        self.assertFalse(plan_fence.premise_issues(self.path,'Contract',graph,schema={}))
        changed = copy.deepcopy(self.contract)
        changed['version'] += 1
        changed['benchmark_pmpm_cents'] += 10000
        self.file.write_text(json.dumps(changed))
        self.assertTrue(any('changed' in issue for issue in plan_fence.premise_issues(self.path,'Contract',graph,schema={})))
        self.assertFalse(plan_fence.premise_issues(self.path,'Other',{'nodes':[]},schema={}))
        with self.assertRaisesRegex(ValueError,'changed'):C.validate(p)
        self.file.write_text(json.dumps(self.contract))
        self.assertFalse(plan_fence.premise_issues(self.path,'Contract',graph,schema={}))

    def test_exact_request_and_input_guard(self):
        question='Show contract performance for demo-vbc-2029-c3'
        self.assertIsNotNone(C.route(question,{},'2030-05-27'))
        self.assertIsNone(C.route(question+' and send emails',{},'2030-05-27'))
        self.assertIsNone(C.route('Show contract performance for unknown',{},'2030-05-27'))
        with self.assertRaises(ValueError):C.validate({'contract_id':'../outside'})
        self.assertTrue(any('saved version' in issue for issue in C.premise_issues({})))

    def test_registered_retrospective_scope_does_not_change_living_care_default(self):
        q='Show contract performance for demo-vbc-2029-c3'
        self.assertFalse(decision_context.population(q)['living_only'])
        self.assertTrue(decision_context.population(q+' for living patients')['living_only'])
        self.assertTrue(decision_context.population('Show contract performance for unknown')['living_only'])
        self.assertTrue(decision_context.population('Show patients with hypertension')['living_only'])

    def test_closed_command_uses_existing_authored_entry_without_general_planner_waiver(self):
        from clinical_core.agere.clinical.economical_planner import local_plan
        q='Show contract performance for demo-vbc-2029-c3'
        graph, recipe = local_plan(q,'2030-05-27')
        self.assertEqual(recipe,'exact_registered_input_contract_performance')
        self.assertEqual(graph['nodes'][0]['operation'],C.OP)
        self.assertEqual(graph['nodes'][0]['parameters'],C.validate({'contract_id':self.contract['id']}))
        self.assertIsNone(C.registered_input_command(q+' for people aged over 60','2030-05-27'))


if __name__ == '__main__':
    unittest.main()
