"""Evidence-type head for concept cohorts: every matched record is tagged coded_diagnosis | problem_list | claim_diagnosis |
medication_indication | note_mention | lab_inferred (concepts.evidence_type, deterministic from stream + fields); a concept
atom may restrict itself with evidence: [types]; the default keeps exactly the old bindings; the compiler maps source
wording to the parameter; the answer's computed_as carries the evidence mix. Fixture stack only, no model or paid calls."""
import json, sqlite3, tempfile, unittest
from pathlib import Path

from clinical_core.agere.clinical import analysis_graph, cohorts, concepts, local_compiler, source_profile
from clinical_core.agere.clinical.clinical_events import build

ASOF = '2026-09-30'
V23_PROFILE = Path('/Users/mikel/clinical-insights-local/artifacts/agere-healthcare-v23-sample-0.35/stack/source-profile.json')
W = {'basis': 'all_observed', 'start': '', 'end': ''}


def rec(i, patient, stream, **kw):
    return dict(record_id=i, patient_id=patient, source_table=stream, event_date='2026-03-01', available_date='2026-03-02',
                organization_id='a', **kw)


RECORDS = [
    rec('c1', 'p_code', 'conditions', icd10='E11.9', description='Type 2 diabetes mellitus', diagnosis_basis='clinical_encounter'),
    rec('c2', 'p_prob', 'conditions', description='Diabetes', problem_type='problem_list', diagnosis_basis='ehr_problem_list'),
    rec('c3', 'p_lab', 'conditions', icd10='E11.9', description='Type 2 diabetes mellitus', diagnosis_basis='laboratory_evidence'),
    rec('c4', 'p_other', 'conditions', icd10='I10', description='Essential hypertension', diagnosis_basis='clinical_encounter'),
    rec('m1', 'p_med', 'medication_lists', name='insulin glargine', indication='type1 diabetes', list_status='active'),
    rec('m2', 'p_other', 'medication_lists', name='lisinopril', indication='hypertension', list_status='active'),
    rec('n1', 'p_note', 'notes', note_type='progress', text='Assessment: diabetes, well controlled on metformin. Follow up in 3 months.'),
    rec('n2', 'p_neg', 'notes', note_type='progress', text='Denies diabetes. Family history of diabetes in mother. Screening for prediabetes ordered.'),
    rec('n3', 'p_other', 'notes', note_type='progress', text='Hypertension stable.'),
    rec('x1', 'p_claim', 'medical_claims', claim_id='C1', principal_diagnosis='E11.65', diagnosis_codes=['E11.65', 'I10']),
    rec('x2', 'p_other', 'medical_claims', claim_id='C2', principal_diagnosis='I10', diagnosis_codes=['I10']),
]
PATIENTS = ['p_code', 'p_prob', 'p_lab', 'p_med', 'p_note', 'p_neg', 'p_claim', 'p_other']


def stack(path):
    (path / 'run.json').write_text(json.dumps({'synthetic': True, 'patients': len(PATIENTS), 'source_digest': 'evidence-fixture',
                                                'source_run': 'r', 'snapshot': {'as_of': ASOF}}))
    db = sqlite3.connect(path / 'stack.sqlite3')
    db.executescript('CREATE TABLE patients(id TEXT PRIMARY KEY,payload TEXT); CREATE TABLE records(id TEXT PRIMARY KEY,patient TEXT,'
                     'stream TEXT,payload TEXT); CREATE TABLE analyses(patient TEXT,operator TEXT,payload TEXT,PRIMARY KEY(patient,operator));'
                     ' CREATE TABLE result_cache(id TEXT PRIMARY KEY,payload TEXT);')
    with db:
        for pid in PATIENTS:
            db.execute('INSERT INTO patients VALUES (?,?)', (pid, json.dumps({'patient_id': pid})))
        for r in RECORDS:
            db.execute('INSERT INTO records VALUES (?,?,?,?)', (r['record_id'], r['patient_id'], r['source_table'], json.dumps(r)))
    db.close()
    build(path)


def diabetes(evidence=None, **extra):
    atom = {'kind': 'concept', 'concept': 'diabetes', 'window': W, **extra}
    if evidence is not None: atom['evidence'] = evidence
    return {'all': [atom], 'any': [], 'exclude': []}


class EvidenceTypeRules(unittest.TestCase):
    def test_type_from_stream_and_fields(self):
        t = {r['record_id']: concepts.evidence_type(r) for r in RECORDS}
        self.assertEqual(t['c1'], 'coded_diagnosis')
        self.assertEqual(t['c2'], 'problem_list')
        self.assertEqual(t['c3'], 'lab_inferred')
        self.assertEqual(t['m1'], 'medication_indication')
        self.assertEqual(t['n1'], 'note_mention')
        self.assertEqual(t['x1'], 'claim_diagnosis')
        self.assertEqual(concepts.evidence_type({'source_table': 'problem_lists'}), 'problem_list')
        self.assertIsNone(concepts.evidence_type({'source_table': 'encounters'}))

    def test_note_mentions_negation_family_hypothetical_history(self):
        d = concepts.CONDITIONS['diabetes']
        state = lambda text: concepts.mention_state(text, d['text'], d.get('exclude'))
        self.assertEqual(state('Assessment: diabetes, well controlled.'), 'affirmed')
        self.assertEqual(state('Denies diabetes.'), 'negated')
        self.assertEqual(state('No evidence of diabetes on review.'), 'negated')
        self.assertEqual(state('Mother has diabetes.'), 'family')
        self.assertIsNone(state('Family history of diabetes in mother.'))  # the concept's own exclude ('family history')
        self.assertEqual(state('Screening for diabetes next visit.'), None)  # the concept's own exclude ('screening')
        self.assertEqual(state('Concern for diabetes; check A1c.'), 'hypothetical')
        self.assertEqual(state('History of diabetes, diet controlled.'), 'historical')
        self.assertEqual(state('Prediabetes, recheck in a year.'), None)  # excluded phrase: not a diabetes mention
        self.assertEqual(state('Denies chest pain. Diabetes stable.'), 'affirmed')  # the cue is in another sentence
        self.assertIsNone(state(None))

    def test_validation(self):
        cohorts.validate(diabetes(['note_mention', 'claim_diagnosis']), ASOF)
        for bad in ([], ['notes'], ['note_mention', 'note_mention'], 'note_mention'):
            with self.assertRaises(ValueError): cohorts.validate(diabetes(bad), ASOF)
        with self.assertRaises(ValueError):  # evidence types apply to condition concepts only
            cohorts.validate({'all': [{'kind': 'concept', 'concept': 'emergency_visit', 'window': W, 'evidence': ['note_mention']}],
                              'any': [], 'exclude': []}, ASOF)


class EvidenceCohorts(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory()
        cls.path = Path(cls.tmp.name)
        stack(cls.path)
        cls.profile = source_profile.load(cls.path)

    @classmethod
    def tearDownClass(cls):
        cls.tmp.cleanup()

    def members(self, evidence=None):
        return sorted(cohorts.select(self.path, diabetes(evidence))['patient_ids'])

    def test_default_is_unchanged(self):
        # No evidence parameter: exactly the old diagnosis-stream bindings (claims, indications and notes are not bound).
        bound = concepts.resolve('diabetes', self.profile)
        self.assertEqual({b['stream'] for b in bound['bindings']}, {'conditions'})
        self.assertNotIn('evidence', bound)
        self.assertEqual(self.members(), ['p_code', 'p_lab', 'p_prob'])

    def test_each_type_selects_its_source(self):
        self.assertEqual(self.members(['coded_diagnosis']), ['p_code'])
        self.assertEqual(self.members(['problem_list']), ['p_prob'])
        self.assertEqual(self.members(['lab_inferred']), ['p_lab'])
        self.assertEqual(self.members(['claim_diagnosis']), ['p_claim'])
        self.assertEqual(self.members(['medication_indication']), ['p_med'])
        self.assertEqual(self.members(['note_mention']), ['p_note'])  # the negated / family-history note does not count
        self.assertEqual(self.members(list(concepts.EVIDENCE_TYPES)),
                         ['p_claim', 'p_code', 'p_lab', 'p_med', 'p_note', 'p_prob'])

    def test_type_1_from_indication_text_is_not_type_2(self):
        self.assertEqual(cohorts.select(self.path, {'all': [{'kind': 'concept', 'concept': 'type_2_diabetes', 'window': W,
                                                             'evidence': list(concepts.EVIDENCE_TYPES)}], 'any': [], 'exclude': []})['patient_ids'],
                         ['p_claim', 'p_code', 'p_lab'])

    def test_evidence_mix_and_trace(self):
        r = cohorts.select(self.path, diabetes(list(concepts.EVIDENCE_TYPES)))
        mix = r['evidence_mix'][0]
        self.assertEqual(mix['patients_by_type'], {'coded_diagnosis': 1, 'problem_list': 1, 'claim_diagnosis': 1,
                                                   'medication_indication': 1, 'note_mention': 1, 'lab_inferred': 1})
        default = cohorts.select(self.path, diabetes())['evidence_mix'][0]
        self.assertEqual(default['evidence'], 'default')
        self.assertEqual(default['default_evidence'], ['coded_diagnosis', 'problem_list', 'lab_inferred'])
        trace = r['examples']['matched'][0]['predicates'][0]
        self.assertTrue(all('evidence_type' in s for s in trace['support']))
        self.assertIn('evidence', r['interpretation'][0])

    def test_unavailable_evidence_is_a_grounding_error(self):
        from clinical_core.agere.clinical import grounding
        profile = json.loads(json.dumps(self.profile))
        profile['streams'].pop('notes')
        with self.assertRaises(grounding.GroundingError) as ctx:
            grounding.prepare(diabetes(['note_mention']), profile)
        self.assertEqual(ctx.exception.issues[0]['issue'], 'evidence_not_observed')

    def test_answer_computed_as_states_the_mix(self):
        plan = {'interpretation': 'q', 'coverage': 'supported_observation', 'unmet_requirements': [],
                'nodes': [{'id': 'population', 'operation': 'cohort', 'inputs': [], 'parameters': {'specification': diabetes(['note_mention'])}}]}
        selected = cohorts.select(self.path, diabetes(['note_mention']))
        universe = set(PATIENTS)
        members = {'population': analysis_graph.Membership(universe, set(selected['patient_ids']), set())}
        results = [{'node': plan['nodes'][0], 'result': {'interpretation': selected['interpretation'], 'grounding': [],
                                                         'evidence_mix': selected['evidence_mix']}}]
        answer = analysis_graph.build_answer(plan, results, members, 'How many patients have diabetes mentioned in notes?')
        self.assertEqual(answer['count'], 1)
        self.assertEqual(answer['computed_as']['evidence']['concepts'][0]['patients_by_type'], {'note_mention': 1})
        self.assertIn('note_mention', answer['computed_as']['evidence']['statement'])

    @unittest.skipUnless(V23_PROFILE.exists(), 'v2.3 sample source profile not present')
    def test_compiler_maps_wording(self):
        profile = json.loads(V23_PROFILE.read_text())  # read-only: the compiler needs a full warehouse profile

        def evidence(q):
            g, info = local_compiler.compile(q, profile, '2030-05-27')
            self.assertIsNotNone(g, info.get('reason'))
            atoms = [a for n in g['nodes'] if n['operation'] == 'cohort' for part in n['parameters']['specification'].values()
                     for a in part if a['kind'] == 'concept']
            return atoms[0].get('evidence')
        self.assertIsNone(evidence('How many patients have diabetes?'))
        self.assertIsNone(evidence('How many patients were diagnosed with diabetes?'))  # diagnosis records = the default
        self.assertIsNone(evidence('How many patients have diabetes on their problem list?'))  # = the condition list (bench usage)
        self.assertEqual(evidence('How many patients have diabetes mentioned in notes?'), ['note_mention'])
        self.assertEqual(evidence('How many patients have diabetes only on the problem list?'), ['problem_list'])
        self.assertEqual(evidence('How many patients have diabetes as a medication indication?'), ['medication_indication'])
        self.assertEqual(evidence('How many patients have diabetes from any source?'), list(concepts.EVIDENCE_TYPES))


if __name__ == '__main__':
    unittest.main()
