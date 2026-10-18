"""Therapy-barrier reader (clinical_core/agere/clinical/therapy_barriers.py): candidate finder, rule assertion, structured
evidence, source types (notes, FHIR DocumentReference, scanned/OCR text), note_therapy_change reasons, the typed cascade
(off by default; fake senders only) and plan routing to pharmd_barrier_review. Small synthetic sqlite fixtures."""
import json
import sqlite3
import tempfile
import unittest
from contextlib import contextmanager
from pathlib import Path

from clinical_core.agere.clinical import therapy_barriers as tb
from clinical_core.agere.plans import act, router

import test_plans

NOTES = [  # record_id, patient_id, date, text, lineage table
    ('N-ALLERGY', 'P1', '2030-01-05', 'Allergies: lisinopril (cough).\nPlan: continue carvedilol 25 mg BID.', 'ehr_epic_hno_info'),
    ('N-NEG', 'P2', '2030-01-06', 'No cough on lisinopril. Tolerating carvedilol well without dizziness.', 'ehr_epic_hno_info'),
    ('N-HYPO', 'P3', '2030-01-07', 'Start carvedilol 3.125 mg BID. Call if dizziness or lightheadedness after starting carvedilol.', 'ehr_epic_hno_info'),
    ('N-FAMILY', 'P4', '2030-01-08', 'Family history: mother had angioedema with lisinopril.', 'ehr_epic_hno_info'),
    ('N-LAB', 'P5', '2030-01-09', 'Assessment: HFrEF. Spironolactone held for K 5.8 last month; recheck BMP.', 'ehr_epic_hno_info'),
    ('N-COST', 'P6', '2030-01-10', 'Patient reports Jardiance 10 MG Oral Tablet is not being used: could not afford the refill.', 'ehr_epic_hno_info'),
    ('N-DECLINE', 'P7', '2030-01-11', 'Discussed starting sacubitril/valsartan; patient declines at this time.', 'ehr_epic_hno_info'),
    ('N-FHIR', 'P8', '2030-01-12', 'Stopped atorvastatin due to muscle aches.', 'fhir_documentreference'),
    ('N-DEFER', 'P9', '2030-01-13', 'Decision and plan: Did not increase carvedilol 12.5 MG Oral Tablet today because BP 86/50 with dizziness.',
     'ehr_epic_hno_info'),
]

OCR_TEXT = ('OUTSIDE CARDIOLOGY NOTE (faxed)\nPatient is intolerant of spirono lactone (gynecomastia) and it was stop-\nped in March. '
            'Cont inue carvedilol.')

SCHEMA = '''
CREATE TABLE meta(key TEXT, value TEXT);
CREATE TABLE patient(patient_id TEXT PRIMARY KEY);
CREATE TABLE record(record_id TEXT PRIMARY KEY, patient_id TEXT, stream TEXT, event_date TEXT, recorded_date TEXT,
  available_date TEXT, organization_id TEXT, payload TEXT);
CREATE TABLE scanned_document(record_id TEXT PRIMARY KEY, patient_id TEXT, event_date TEXT);
CREATE TABLE scanned_document_text(record_id TEXT PRIMARY KEY, patient_id TEXT, text_source TEXT, text TEXT);
CREATE TABLE allergy_entry(patient_id TEXT, substance TEXT, substance_received TEXT, reaction TEXT, reaction_missing INTEGER,
  free_text INTEGER, no_known_allergies INTEGER, status TEXT, entries INTEGER, first_recorded TEXT, last_confirmed TEXT,
  days_since_confirmed INTEGER, record_id TEXT);
CREATE TABLE measurement(record_id TEXT, patient_id TEXT, concept TEXT, value REAL, value_canonical REAL, event_date TEXT);
CREATE TABLE note_therapy_change(record_id TEXT, patient_id TEXT, event_date TEXT, encounter_id TEXT, stream TEXT, action TEXT,
  target TEXT, target_kind TEXT, drug_key TEXT, drug_class TEXT, reason TEXT, dictated INTEGER, sound_alike_corrected INTEGER,
  evidence_text TEXT, order_record_id TEXT, fills_after_180d INTEGER, list_active_after INTEGER, extractor TEXT);
CREATE TABLE condition(record_id TEXT, patient_id TEXT, icd10 TEXT, event_date TEXT);
'''


def build_fixture(path):
    db = sqlite3.connect(path)
    db.executescript(SCHEMA)
    db.executemany('INSERT INTO patient VALUES (?)', [(f'P{i}',) for i in range(1, 13)])
    for rid, pid, day, text, table in NOTES:
        payload = {'patient_id': pid, 'text': text, '_lineage': [{'table': table, 'file': f'{table}.ndjson', 'row': 1}]}
        db.execute("INSERT INTO record VALUES (?,?,'notes',?,?,?,NULL,?)", (rid, pid, day, day, day, json.dumps(payload)))
    db.execute("INSERT INTO scanned_document VALUES ('D-OCR', 'P10', '2030-02-01')")
    db.execute("INSERT INTO scanned_document_text VALUES ('D-OCR', 'P10', 'front_office_ocr', ?)", (OCR_TEXT,))
    db.execute("INSERT INTO allergy_entry(patient_id, substance, reaction, no_known_allergies, status, first_recorded, record_id) "
               "VALUES ('P11', 'statins', 'myalgias', 0, 'active', '2029-05-01', 'AL-1')")
    # P12: spironolactone stopped (note-only) with potassium 6.1 four days earlier -> structured lab contraindication
    db.execute("INSERT INTO note_therapy_change(record_id, patient_id, event_date, action, target, drug_key, drug_class, evidence_text) "
               "VALUES ('N-STOP', 'P12', '2030-03-10', 'stop', 'spironolactone', 'spironolactone', 'mra', 'Stop spironolactone 25 mg.')")
    db.executemany('INSERT INTO measurement VALUES (?,?,?,?,?,?)', [
        ('LAB-K1', 'P12', 'potassium', 6.1, 6.1, '2030-03-06'), ('LAB-K2', 'P12', 'potassium', 4.4, 4.4, '2029-01-01')])
    # the deferral row the therapy-rules extractor writes with no reason
    db.execute("INSERT INTO note_therapy_change(record_id, patient_id, event_date, action, target, drug_key, drug_class, evidence_text) "
               "VALUES ('N-DEFER', 'P9', '2030-01-13', 'dose_increase', 'carvedilol', 'carvedilol', 'beta_blocker', "
               "'Decision and plan: Did not increase carvedilol 12.5 MG Oral Tablet today because BP 86/50 with dizziness.')")
    db.commit()
    db.close()


@contextmanager
def _no_network():
    def send(provider, body):
        raise AssertionError('no provider call expected')
    yield send


class BarrierReader(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory()
        cls.path = Path(cls.tmp.name) / 'semantic.sqlite3'
        build_fixture(cls.path)
        cls.summary = tb.build(cls.path, send_factory=_no_network)  # llm off: the factory must never be used
        db = sqlite3.connect(cls.path)
        db.row_factory = sqlite3.Row
        cls.rows = [dict(r) for r in db.execute('SELECT * FROM therapy_barrier')]
        cls.cands = [dict(r) for r in db.execute('SELECT * FROM therapy_barrier_candidate')]
        cls.changes = {(r['record_id'], r['action']): dict(r) for r in db.execute('SELECT * FROM note_therapy_change')}
        db.close()

    @classmethod
    def tearDownClass(cls):
        cls.tmp.cleanup()

    def by_patient(self, pid):
        return [r for r in self.rows if r['patient_id'] == pid]

    def test_affirmed_allergy_list_reaction(self):
        (r,) = self.by_patient('P1')
        self.assertEqual((r['drug_class'], r['gdmt_pillar'], r['barrier_type'], r['detail'], r['method']),
                         ('ace_inhibitor', 'rasi', 'intolerance_side_effect', 'cough', 'rules'))
        self.assertEqual((r['record_id'], r['event_date'], r['source_type'], r['assertion']), ('N-ALLERGY', '2030-01-05', 'clinical_note', 'affirmed'))

    def test_negated_is_not_a_barrier(self):
        self.assertEqual(self.by_patient('P2'), [])
        negs = [c for c in self.cands if c['patient_id'] == 'P2']
        self.assertTrue(negs and all(c['label'] == 'no_barrier' and c['negated'] == 1 for c in negs))

    def test_hypothetical_is_not_a_barrier(self):
        self.assertEqual(self.by_patient('P3'), [])
        self.assertTrue(any(c['hypothetical'] == 1 for c in self.cands if c['patient_id'] == 'P3'))

    def test_family_history_experiencer(self):
        self.assertEqual(self.by_patient('P4'), [])
        self.assertEqual({c['experiencer'] for c in self.cands if c['patient_id'] == 'P4'}, {'family'})

    def test_lab_contraindication_with_value(self):
        (r,) = self.by_patient('P5')
        self.assertEqual((r['drug_class'], r['gdmt_pillar'], r['barrier_type'], r['detail']),
                         ('mra', 'mra', 'contraindication_lab', 'hyperkalemia K 5.8'))

    def test_cost_from_brand_name(self):
        (r,) = self.by_patient('P6')
        self.assertEqual((r['drug_class'], r['drug_key'], r['barrier_type']), ('sglt2_inhibitor', 'empagliflozin', 'cost_access'))

    def test_declined(self):
        rows = self.by_patient('P7')
        self.assertEqual({(r['drug_class'], r['barrier_type']) for r in rows}, {('arni', 'patient_declined')})

    def test_fhir_document_reference_source(self):
        (r,) = self.by_patient('P8')
        self.assertEqual((r['source_type'], r['drug_class'], r['barrier_type']), ('fhir_document_reference', 'statin', 'intolerance_side_effect'))

    def test_scanned_ocr_text_tolerates_wraps_and_split_words(self):
        (r,) = self.by_patient('P10')
        self.assertEqual((r['source_type'], r['text_source'], r['drug_class'], r['barrier_type']),
                         ('scanned_document_text', 'front_office_ocr', 'mra', 'intolerance_side_effect'))
        self.assertIsNotNone(r['ocr_quality'])
        self.assertIn('spironolactone', r['evidence_text'])

    def test_structured_allergy_entry_and_lab_near_stop(self):
        (a,) = self.by_patient('P11')
        self.assertEqual((a['method'], a['source_type'], a['drug_class'], a['barrier_type']),
                         ('structured', 'allergy_entry', 'statin', 'intolerance_side_effect'))
        (k,) = self.by_patient('P12')
        self.assertEqual((k['method'], k['barrier_type'], k['detail'], k['record_id']), ('structured', 'contraindication_lab', 'hyperkalemia K 6.1', 'LAB-K1'))

    def test_therapy_change_reason_extended(self):
        row = self.changes[('N-DEFER', 'dose_increase')]
        self.assertEqual(row['reason_source'], tb.EXTRACTOR)
        self.assertTrue(row['reason'].startswith('deferred: BP 86/50 with dizziness'), row['reason'])

    def test_summary_counts_and_no_paid_calls(self):
        s = self.summary
        self.assertEqual(s['candidates'], len(self.cands))
        self.assertEqual(sum(s['decided'].values()), s['candidates'])
        self.assertIsNone(s['typed_receipt'])
        self.assertIn('jev_usd', s['tier3_estimate'])
        self.assertEqual(s['units_by_source'], {'clinical_note': 8, 'fhir_document_reference': 1, 'scanned_document_text': 1})

    def test_idempotent(self):
        again = tb.build(self.path)
        db = sqlite3.connect(self.path)
        try:
            self.assertEqual(db.execute('SELECT count(*) FROM therapy_barrier').fetchone()[0], len(self.rows))
            self.assertEqual(db.execute("SELECT count(*) FROM meta WHERE key = 'therapy_barrier_summary'").fetchone()[0], 1)
        finally:
            db.close()
        self.assertEqual(again['barrier_rows'], self.summary['barrier_rows'])


class Normalisation(unittest.TestCase):
    def test_fixed_width_wrap_is_joined(self):
        text = ('x' * 20 + ' Allergies: lisinopril (cough). More text follows here to fill the line out to width 60.\n')
        lines = [text[i:i + 60] for i in range(0, len(text), 60)]
        self.assertIn('lisinopril (cough)', tb.normalize('\n'.join(lines * 3)))

    def test_ocr_digit_confusion_only_for_ocr(self):
        self.assertIn('lisinopril', tb.normalize('Stop 1isinopril due to cough.', ocr=True))
        self.assertIn('HbA1c', tb.normalize('HbA1c 7.2', ocr=False))


class TypedCascade(unittest.TestCase):
    def cases(self):
        return [{'case_id': f'c{i}', 'drug_surface': 'lisinopril', 'drug_class': 'ace_inhibitor',
                 'sentence': f'lisinopril: asked about the concern documented {i}: dizziness'} for i in range(3)]

    def test_jev_then_perplexity_then_claude_only_when_both_uncertain(self):
        calls = []

        @contextmanager
        def factory():
            def send(provider, body):
                calls.append(provider)
                if provider in ('jev', 'perplexity'):
                    answers = {}
                    for k in body['questions']:
                        sure = (provider == 'jev' and k == 'c0') or (provider == 'perplexity' and k == 'c1')
                        label = 'intolerance_side_effect' if sure else 'unknown'
                        ps = {l: 0.0 for l in tb.TYPED_LABELS}
                        ps[label] = 0.9 if sure else 0.5
                        ps['no_barrier'] = 0.1 if sure else 0.5
                        answers[k] = {'type': 'choice', 'choice': label, 'probabilities': ps}
                    return {'model': body['model'], 'answers': answers}, 0.0001, False
                cases = json.loads(body['messages'][0]['content'])['cases']
                text = json.dumps({'labels': {k: 'no_barrier' for k in cases}})
                return {'model': body['model'], 'stop_reason': 'end_turn', 'content': [{'type': 'text', 'text': text}]}, 0.01, False
            yield send

        results, receipt = tb.typed_cascade(self.cases(), factory)
        self.assertEqual(results['c0'][:2], ('intolerance_side_effect', 'jev'))
        self.assertEqual(results['c1'][:2], ('intolerance_side_effect', 'perplexity'))
        self.assertEqual(results['c2'][:2], ('no_barrier', 'claude'))
        self.assertEqual(calls, ['jev', 'perplexity', 'claude'])
        self.assertEqual(receipt['calls'], {'jev': 1, 'perplexity': 1, 'claude': 1})

    def test_bodies_are_batched_and_bounded(self):
        cases = self.cases() * 20
        for i, c in enumerate(cases):
            c = dict(c)
        cases = [dict(c, case_id=f'k{i}') for i, c in enumerate(cases)]
        bodies = tb.typed_bodies(cases, 'jev')
        self.assertEqual(len(bodies), -(-len(cases) // tb.TYPED_BATCH))
        self.assertTrue(all(len(json.dumps(b)) < 20000 for b in bodies))
        self.assertEqual(bodies[0]['model'], 'jev-1.13.0')


class PlanRouting(unittest.TestCase):
    """Barriers change the action (plans/barriers.py through the catalog's barrier_policy). HF1 (missing mra + sglt2i): the
    hyperkalemia hold explains the MRA pillar (closed, monitoring action); the empagliflozin cost barrier names one agent,
    so the SGLT2i gap stays open with the barrier attached -> PharmD barrier review. HF4: lisinopril angioedema excludes
    ACE inhibitors and flags sacubitril/valsartan, an ARB is still possible, so RASi stays an open gap with the barrier ->
    PharmD barrier review (a barrier on a pillar the program would start)."""

    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory()
        cls.db = Path(cls.tmp.name) / 'semantic.sqlite3'
        test_plans.build_fixture(cls.db)
        db = sqlite3.connect(cls.db)
        db.execute('CREATE TABLE record(record_id TEXT PRIMARY KEY, patient_id TEXT, stream TEXT, event_date TEXT, recorded_date TEXT, '
                   'available_date TEXT, organization_id TEXT, payload TEXT)')
        notes = [('NB-1', 'HF1', '2030-02-01', 'Spironolactone held for K 5.9; CKD 3b.'),
                 ('NB-2', 'HF1', '2030-03-01', 'Patient reports empagliflozin 10 MG Oral Tablet is not being used: could not afford the refill.'),
                 ('NB-3', 'HF4', '2030-03-01', 'Allergies: lisinopril (angioedema).'),
                 ('NB-4', 'HF4', '2030-08-01', 'Spironolactone held for K 6.0.')]  # after as_of: never used
        db.executemany("INSERT INTO record VALUES (?,?,'notes',?,?,?,NULL,?)",
                       [(r, p, d, d, d, json.dumps({'text': t})) for r, p, d, t in notes])
        db.commit()
        db.close()
        tb.build(cls.db)
        cls.plan = router.route(cls.db, ['HF1', 'HF4', 'HF5'], 'pharmacy_gdmt', test_plans.AS_OF)

    @classmethod
    def tearDownClass(cls):
        cls.tmp.cleanup()

    def row(self, pid):
        return next(r for r in self.plan['patients'] if r['patient_id'] == pid)

    def test_lab_barrier_explains_pillar_and_cost_barrier_keeps_gap_open(self):
        r = self.row('HF1')
        g = r['gap']
        self.assertEqual(g['missing_pillars']['value'], ['sglt2i'])
        self.assertEqual(g['barrier_documented_pillars']['value'], ['mra'])
        self.assertEqual(sorted(g['barrier_pillars']['value']), ['mra', 'sglt2i'])
        self.assertFalse(g['all_missing_barrier_documented'])
        self.assertEqual(g['pillars']['mra']['status'], 'barrier_documented')
        self.assertEqual(g['pillars']['mra']['barriers']['value'][0]['detail'], 'hyperkalemia K 5.9')
        self.assertIn('therapy_barrier:NB-1', g['pillars']['mra']['barriers']['sources'])
        self.assertEqual(g['pillars']['sglt2i']['status'], 'gap')
        self.assertEqual(g['pillars']['sglt2i']['barrier_assessment']['excluded_agents'], ['empagliflozin'])
        self.assertIn('dapagliflozin', g['pillars']['sglt2i']['barrier_assessment']['usable_agents'])
        self.assertEqual((r['route'], r['program_id'], r['task']['script_template_id']),
                         ('enroll', 'pharmd_barrier_review', 'gdmt_barrier_pharmd_review_v1'))

    def test_ace_angioedema_excludes_acei_flags_arni_and_routes_to_review(self):
        r = self.row('HF4')
        g = r['gap']
        self.assertEqual(g['barrier_documented_pillars']['value'], [])
        self.assertIn('rasi', g['missing_pillars']['value'])
        self.assertIn('mra', g['missing_pillars']['value'])  # the post-as_of hold never counts
        a = g['pillars']['rasi']['barrier_assessment']
        self.assertEqual(a['status'], 'open_with_barrier')
        self.assertIn('lisinopril', a['excluded_agents'])
        self.assertIn('losartan', a['usable_agents'])
        self.assertNotIn('sacubitril valsartan', a['usable_agents'])
        self.assertEqual(a['flags'][0]['classes'], ['arni'])
        self.assertEqual(r['program_id'], 'pharmd_barrier_review')
        self.assertIn('open_with_barrier rasi(allergy_angioedema', router._gap_text(g))

    def test_summary_counts_by_barrier_type(self):
        s = self.plan['summary']
        self.assertEqual(s['barrier_documented_pillar_counts'], {'mra': 1})
        self.assertEqual(s['open_with_barrier_pillar_counts'], {'sglt2i': 1, 'rasi': 1})
        self.assertEqual(s['barriers_by_type'], {'contraindication_lab': 1, 'cost_access': 1, 'allergy_angioedema': 1})
        self.assertEqual(s['patients_with_barrier'], 2)
        self.assertEqual(s['by_program'].get('pharmd_barrier_review'), 2)

    def test_act_carries_barriers_into_every_action(self):
        out = act.act(self.plan, workspace='test')
        a = next(x for x in out['actions'] if x['patient_id'] == 'HF1')
        self.assertEqual(a['agent'], 'Agere PharmD barrier review agent')
        self.assertEqual({b['pillar'] for b in a['barriers']}, {'mra', 'sglt2i'})
        self.assertEqual(out['summary']['barrier_reviews'], 2)
        self.assertEqual(out['summary']['actions_with_barriers'], 2)

    def test_no_barrier_table_keeps_plain_gaps(self):
        tmp = tempfile.TemporaryDirectory()
        try:
            p = Path(tmp.name) / 'semantic.sqlite3'
            test_plans.build_fixture(p)
            plan = router.route(p, ['HF1'], 'pharmacy_gdmt', test_plans.AS_OF)
            self.assertEqual(plan['patients'][0]['gap']['missing_pillars']['value'], ['mra', 'sglt2i'])
            self.assertEqual(plan['patients'][0]['program_id'], 'pharmacy_gdmt_titration')
        finally:
            tmp.cleanup()


if __name__ == '__main__':
    unittest.main()
