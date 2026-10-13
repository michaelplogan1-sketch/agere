"""Deterministic note_fact extractor (negation, temporality, EF parsing) and loader idempotence."""
import json
from pathlib import Path
import sqlite3
import tempfile
import unittest

from clinical_core.agere.clinical import note_facts
from clinical_core.agere.clinical.note_facts import extract


def facts(text, stream='notes', ft=None):
    return [(f['fact_type'], f['value'], f['negated'], f['temporality']) for f in extract(text, stream, 'text')
            if ft is None or f['fact_type'] == ft]


class EjectionFraction(unittest.TestCase):
    def test_echo_value_and_modality(self):
        out = extract('Report: Left ventricular ejection fraction 35% by biplane measurement.', 'notes', 'text')
        ef = [f for f in out if f['fact_type'] == 'ejection_fraction']
        self.assertEqual([(f['value'], f['unit'], f['qualifier']) for f in ef], [(35, '%', 'echocardiogram')])
        self.assertIn('35%', ef[0]['evidence_text'])

    def test_range_midpoint_and_abbreviations(self):
        self.assertEqual(facts('LVEF 30-40% on prior study.', ft='ejection_fraction')[0][1], 35)
        self.assertEqual(facts('HFrEF EF 25% · BP 110/70', 'chart_summaries', ft='ejection_fraction')[0][1], 25)
        self.assertEqual(facts('Estimated LVEF 55%. No large pericardial effusion.', 'inpatient_cardiac_reports',
                               ft='ejection_fraction')[0][1], 55)

    def test_unquantifiable_and_implausible(self):
        self.assertEqual(facts('Ejection fraction cannot be reliably quantified.', ft='ejection_fraction')[0][1], 'not_quantifiable')
        self.assertEqual(facts('EF 2% artefact', ft='ejection_fraction'), [])

    def test_evidence_spans_are_exact(self):
        text = 'HISTORY\nHeart failure.\n\nREPORT\nLeft ventricular ejection fraction 42% by biplane measurement.'
        for f in extract(text, 'notes', 'text'):
            self.assertEqual(text[f['span_start']:f['span_end']], f['evidence_text'])


class Negation(unittest.TestCase):
    def test_symptom_negation(self):
        out = facts('Reports breathless on stairs; no resting breathlessness. Weight 80.1 kg; edema none.')
        self.assertIn(('dyspnea', 'exertional', False, 'current'), out)
        self.assertIn(('dyspnea', 'resting', True, 'current'), out)
        self.assertIn(('edema', 'none', True, 'current'), out)

    def test_negation_scope_covers_list(self):
        out = facts('No chest pain, resting shortness of breath or inability to keep fluids down reported.', ft='symptom')
        self.assertIn(('symptom', 'chest_pain', True, 'current'), out)
        self.assertTrue(all(neg for _, _, neg, _ in out))

    def test_imaging_negation(self):
        out = facts('FINDINGS: No acute fracture or dislocation identified. Mild soft-tissue swelling.', 'imaging_reports',
                    ft='imaging_finding')
        self.assertIn(('imaging_finding', 'fracture', True, 'current'), out)
        self.assertIn(('imaging_finding', 'soft_tissue_swelling', False, 'current'), out)
        pos = facts('FINDINGS: Nondisplaced extra-articular distal radius fracture.', 'imaging_reports', ft='imaging_finding')
        self.assertEqual(pos, [('imaging_finding', 'fracture', False, 'current')])

    def test_structured_social_flags(self):
        out = facts('Helper: no_regular_helper. Transport difficulty reported: True; food access concern: False.', ft='social_need')
        self.assertIn(('social_need', 'caregiver_support', False, 'current'), out)
        self.assertIn(('social_need', 'transport', False, 'current'), out)
        self.assertIn(('social_need', 'food', True, 'current'), out)


class Hypothetical(unittest.TestCase):
    def test_return_precautions_are_not_facts(self):
        text = 'Seek urgent assessment for new resting breathing difficulty, chest pain or rapidly worsening symptoms.'
        self.assertEqual(facts(text), [])

    def test_instructions_to_ask_are_not_needs(self):
        text = 'Contact the pharmacy about delayed supply and ask whether cost or transport has prevented collection.'
        self.assertEqual(facts(text, ft='social_need'), [])


class Temporality(unittest.TestCase):
    def test_historical_explanation(self):
        text = ('Patient says Furosemide 40 MG Oral Tablet still has not been restarted. Earlier explanation on file: Patient '
                'reports Furosemide 40 MG Oral Tablet is not being used: could not afford the refill.')
        out = [f for f in extract(text, 'notes', 'text') if f['fact_type'] == 'medication_nonadherence']
        by = {(f['value'], f['temporality']) for f in out}
        self.assertIn(('cost', 'historical'), by)
        self.assertIn(('unspecified', 'current'), by)
        self.assertTrue(all(f['qualifier'] == 'furosemide' for f in out))

    def test_days_used(self):
        out = [f for f in extract('carvedilol 25 mg tablet: reports use on 3 of 14 days; 0 tablet reported left.', 'notes', 'text')]
        self.assertIn(('medication_days_used', 0.21, 'carvedilol'), [(f['fact_type'], f['value'], f['qualifier']) for f in out])
        self.assertIn('medication_nonadherence', [f['fact_type'] for f in out])
        full = [f['fact_type'] for f in extract('lisinopril 10 mg tablet: reports use on 7 of the last 7 days.', 'notes', 'text')]
        self.assertNotIn('medication_nonadherence', full)

    def test_follow_up_interval(self):
        out = [f for f in extract('Follow up: Orthopedic reassessment within one week.', 'notes', 'text') if f['fact_type'] == 'follow_up']
        self.assertEqual([(f['value'], f['unit'], f['qualifier']) for f in out], [(7, 'days', 'orthopedic')])


class Loader(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        d = Path(self.tmp.name)
        db = sqlite3.connect(d / 'semantic.sqlite3')
        db.execute('CREATE TABLE record(record_id TEXT PRIMARY KEY, patient_id TEXT, stream TEXT, event_date TEXT, recorded_date TEXT, '
                   'available_date TEXT, organization_id TEXT, payload TEXT)')
        db.execute('CREATE TABLE meta(key TEXT PRIMARY KEY, value TEXT)')
        rows = [('r1', 'p1', 'notes', '2028-01-02', {'text': 'Left ventricular ejection fraction 30% by biplane measurement.'}),
                ('r2', 'p2', 'notes', '2028-02-03', {'text': 'Reports breathless on stairs; no resting breathlessness.'}),
                ('r3', 'p2', 'labs', '2028-02-03', {'text': 'EF 20% should not be read from a non-text stream'})]
        db.executemany('INSERT INTO record VALUES (?,?,?,?,?,?,?,?)',
                       [(r, p, s, d_, d_, d_, None, json.dumps(pl)) for r, p, s, d_, pl in rows])
        db.commit(); db.close()
        self.adm = d / 'admission.json'
        self.adm.write_text(json.dumps({'admitted': ['ejection_fraction', 'dyspnea'], 'precision_bar': 0.9}))
        self.dir = d

    def tearDown(self):
        self.tmp.cleanup()

    def test_build_is_idempotent(self):
        first = note_facts.build(self.dir, evaluation=self.adm)
        second = note_facts.build(self.dir, evaluation=self.adm)
        self.assertEqual(first['rows'], second['rows'])
        db = sqlite3.connect(self.dir / 'semantic.sqlite3')
        got = db.execute('SELECT patient_id, fact_type, value, negated, event_date FROM note_fact ORDER BY 1, 2, 3').fetchall()
        self.assertEqual(got, [('p1', 'ejection_fraction', 30, 0, '2028-01-02'), ('p2', 'dyspnea', 'exertional', 0, '2028-02-03'),
                               ('p2', 'dyspnea', 'resting', 1, '2028-02-03')])
        self.assertEqual(db.execute("SELECT count(*) FROM note_fact WHERE value < 40 AND fact_type = 'ejection_fraction'").fetchone()[0], 1)
        self.assertEqual(db.execute("SELECT count(*) FROM sqlite_master WHERE name = 'note_fact'").fetchone()[0], 1)
        self.assertEqual(json.loads(db.execute("SELECT value FROM meta WHERE key = 'note_fact_version'").fetchone()[0])['rows'], 3)
        db.close()

    def test_admission_filters_fact_types(self):
        self.adm.write_text(json.dumps({'admitted': ['dyspnea'], 'precision_bar': 0.9}))
        self.assertEqual(note_facts.build(self.dir, evaluation=self.adm)['rows'], 2)

    def test_separate_target_leaves_source_untouched(self):
        target = self.dir / 'note_facts.sqlite3'
        note_facts.build(self.dir, target=target, evaluation=self.adm)
        src = sqlite3.connect(self.dir / 'semantic.sqlite3')
        self.assertEqual(src.execute("SELECT count(*) FROM sqlite_master WHERE name = 'note_fact'").fetchone()[0], 0)
        src.close()
        self.assertEqual(sqlite3.connect(target).execute('SELECT count(*) FROM note_fact').fetchone()[0], 3)


if __name__ == '__main__':
    unittest.main()
