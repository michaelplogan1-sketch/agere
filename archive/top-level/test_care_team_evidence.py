"""Dated relationship development fixtures, not clinical attribution validation."""
import json
import sqlite3
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace

from clinical_core.agere.clinical import dose, clinical_inputs as C, general_analysis as G, request_plan as R
from clinical_core.agere.deliverables import answer_context as A, workflow as W
from tools.learning_loop.temporal_development import fixture
import test_general_analysis_workflow as existing_workflow


class CareTeamEvidence(unittest.TestCase):
    def setUp(self):
        self.db=dose.register(sqlite3.connect(':memory:'));self.db.row_factory=sqlite3.Row
        self.addCleanup(self.db.close)
        self.db.execute('CREATE TABLE record(record_id,patient_id,stream,payload)')

    def add(self,rid='r1',patient='A',**values):
        raw=dict(patient_id=patient,clinician_id='C1',role='primary_care',organization_id='O1',
                 status='assigned',event_date='2030-01-01',available_date='2030-01-02',effective_start='2030-01-01')
        raw.update(values)
        self.db.execute('INSERT INTO record VALUES(?,?,?,?)',(rid,patient,'care_team_relationships',json.dumps(raw)))

    def rows(self):
        return [dict(r) for r in self.db.execute('SELECT * FROM '+C.care_team_sql('2030-05-27'))]

    def test_assignment_is_dated_documentation_not_verified_current_responsibility(self):
        self.add();r,=self.rows()
        self.assertEqual((r['assignment_status'],r['assigned_at_snapshot']),('documented_assignment',1))
        self.assertEqual(r['current_responsibility_verified'],0)
        self.assertEqual(json.loads(r['source_record_ids']),['r1'])

    def test_referrals_and_missing_status_do_not_become_assignments(self):
        for status,expected,flag in [('referred','referral_only',0),('referral_received','referral_only',0),
                                     (None,'unknown_status',None),('completed','unknown_status',None)]:
            with self.subTest(status=status):
                self.db.execute('DELETE FROM record');self.add(status=status)
                r,=self.rows();self.assertEqual((r['assignment_status'],r['assigned_at_snapshot']),(expected,flag))

    def test_later_ending_supersedes_prior_assignment(self):
        self.add();self.add('r2',event_date='2030-05-01',available_date='2030-05-02',status='ended')
        r,=self.rows();self.assertEqual(r['assignment_status'],'ended_documentation')
        self.assertEqual(json.loads(r['source_record_ids']),['r2'])

    def test_effective_boundaries_are_inclusive_and_future_start_is_not_current(self):
        for start,end,want in [('2030-05-27','2030-05-27','documented_assignment'),
                               ('2030-01-01','2030-05-26','ended_documentation'),
                               ('2030-05-28',None,'not_started')]:
            with self.subTest(start=start,end=end):
                self.db.execute('DELETE FROM record');self.add(effective_start=start,effective_end=end)
                self.assertEqual(self.rows()[0]['assignment_status'],want)

    def test_tied_conflicts_and_undated_accounts_are_not_chosen_by_id(self):
        self.add();self.add('r2',status='ended')
        r,=self.rows();self.assertEqual(r['assignment_status'],'conflicting');self.assertIsNone(r['assigned_at_snapshot'])
        self.assertEqual(set(json.loads(r['source_record_ids'])),{'r1','r2'})
        self.db.execute('DELETE FROM record');self.add();self.add('r2',event_date=None)
        r,=self.rows();self.assertEqual(r['assignment_status'],'unknown_date')

    def test_missing_start_and_invalid_or_conflicting_intervals_remain_unknown(self):
        for fields,status in [({'effective_start':None},'unknown_start'),
                              ({'effective_start':'2030-01'},'invalid_interval'),
                              ({'effective_end':'2029-01-01'},'invalid_interval'),
                              ({'relationship_start':'2030-02-01'},'invalid_interval'),
                              ({'effective_end':'2031-01-01','relationship_end':'2030-05-27'},'invalid_interval')]:
            with self.subTest(fields=fields):
                self.db.execute('DELETE FROM record');self.add(**fields)
                r,=self.rows();self.assertEqual(r['assignment_status'],status);self.assertIsNone(r['assigned_at_snapshot'])

    def test_future_source_and_unreceived_record_cannot_replace_received_relationship(self):
        self.add();self.add('future',event_date='2030-06-01',available_date='2030-05-01',status='ended')
        self.add('late',event_date='2030-05-01',available_date='2030-06-01',status='ended')
        r,=self.rows();self.assertEqual(r['assigned_at_snapshot'],1)
        self.assertEqual(json.loads(r['source_record_ids']),['r1'])

    def test_unknown_availability_and_identity_are_not_assignments(self):
        self.add(available_date=None);self.assertEqual(self.rows()[0]['assignment_status'],'unknown_date')
        for clinician in (None,'',12,{'id':'C1'},['C1']):
            with self.subTest(clinician=clinician):
                self.db.execute('DELETE FROM record');self.add(clinician_id=clinician)
                r,=self.rows();self.assertEqual(r['assignment_status'],'unknown_identity')

    def test_patients_clinicians_roles_and_multiple_assignments_remain_separate(self):
        self.add();self.add('r2',clinician_id='C2');self.add('r3',role='cardiology')
        self.add('r4',patient='B',status='referred')
        rows=self.rows();self.assertEqual(len(rows),4)
        self.assertEqual(sum(r['assigned_at_snapshot']==1 for r in rows),3)


def spec(role):
    question=(f'List patients and their documented {role} relationships; '
              'simulate evidence review for patients with referral-only relationships')
    return question,dict(steps=[dict(name='relationships',purpose='Assignments and referrals remain distinct',sql=
        "SELECT p.patient_id, group_concat(CASE WHEN c.assigned_at_snapshot=1 THEN c.clinician_id END) AS assigned_clinicians, "
        "group_concat(CASE WHEN c.assignment_status='referral_only' THEN c.clinician_id END) AS referred_clinicians, "
        "sum(CASE WHEN c.assignment_status='referral_only' THEN 1 ELSE 0 END)>0 AS referral_review "
        "FROM patient p LEFT JOIN {{care_team}} c ON c.patient_id=p.patient_id AND c.role='"+role+"' GROUP BY p.patient_id ORDER BY p.patient_id")],
        final='relationships',returns='table',patient_selection=dict(column='patient_id',quote='List patients'),
        requirements=[dict(quote=f'List patients and their documented {role} relationships',applied_in=['relationships'],how='Literal relationship status with dates'),
                      dict(quote='patients with referral-only relationships',applied_in=['relationships'],how='Explicit referrals select evidence review',outputs=['referral_review'])],
        unresolved=[],workflow=[dict(kind='simulation',title='Synthetic relationship review',program='custom',
            quote='simulate evidence review for patients with referral-only relationships',unresolved=[],
            selection=dict(output='referral_review',quote='patients with referral-only relationships'))])


class CareTeamWorkflow(unittest.TestCase):
    execute=existing_workflow.GeneralAnalysisWorkflow.execute

    def setUp(self):
        t=tempfile.TemporaryDirectory();self.addCleanup(t.cleanup);self.path=fixture(Path(t.name)/'fixture')
        with sqlite3.connect(self.path/'semantic.sqlite3') as db:
            for role in ['primary_care','cardiology','physical_therapist']:
                for pid,status in [('A','assigned'),('B','referred'),('C',None)]:
                    rid=pid+'-'+role
                    db.execute('INSERT INTO record VALUES(?,?,?,?,?)',(rid,pid,'care_team_relationships','2030-01-01',json.dumps(dict(
                        patient_id=pid,clinician_id=role+'-clinician',role=role,status=status,event_date='2030-01-01',
                        available_date='2030-01-02',effective_start='2030-01-01'))))
        self.catalog=R.Catalog.load(self.path);self.stack=SimpleNamespace(path=self.path,allow_live=False,shared_request_planning=True)

    def test_three_roles_share_exact_assignment_and_review_semantics(self):
        for role in ['primary_care','cardiology','physical_therapist']:
            with self.subTest(role=role):
                q,s=spec(role);result,ctx=self.execute(s,q)
                rows={r['patient_id']:r for r in result['answer']['table']}
                self.assertEqual(rows['A']['assigned_clinicians'],role+'-clinician')
                self.assertIsNone(rows['B']['assigned_clinicians']);self.assertIsNone(rows['C']['assigned_clinicians'])
                self.assertEqual(rows['B']['referred_clinicians'],role+'-clinician')
                W.materialize(self.path,ctx['id'])
                self.assertEqual({i['patient_id'] for i in A.inspect(self.path,ctx['id'])['items']},{'B'})
                self.assertEqual(A.load(self.path,ctx['id'],current=True)['answer']['table'],result['answer']['table'])

    def test_saved_population_and_empty_never_broaden(self):
        q,s=spec('cardiology')
        for population in (['A','C'],[]):
            result,ctx=self.execute(s,q,population=population)
            self.assertEqual(result['answer']['patient_ids'],population)
            W.materialize(self.path,ctx['id']);self.assertEqual(A.inspect(self.path,ctx['id'])['items'],[])

    def test_relationship_update_invalidates_saved_scope_and_macro_arguments_fail(self):
        q,s=spec('cardiology');_,ctx=self.execute(s,q)
        with sqlite3.connect(self.path/'semantic.sqlite3') as db:
            db.execute("UPDATE record SET payload=json_set(payload,'$.status','ended') WHERE record_id='A-cardiology'")
        with self.assertRaises(ValueError):A.load(self.path,ctx['id'],current=True)
        s['steps'][0]['sql']=s['steps'][0]['sql'].replace('{{care_team}}','{{care_team:2030}}')
        with self.assertRaisesRegex(R.PlanError,'no macro argument'):G.prepare(self.catalog,q,s)


if __name__=='__main__':unittest.main()
