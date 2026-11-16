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


class SmokingStatusV22(unittest.TestCase):
    def test_never_and_former_are_not_current(self):
        self.assertEqual(facts('Social: Never smoker.', ft='smoking_status'), [('smoking_status', 'never', False, 'current')])
        self.assertEqual(facts('SOCIAL\nFormer smoker.\n', ft='smoking_status'), [('smoking_status', 'former', False, 'current')])
        self.assertEqual(facts('Social: Current smoker. Transportation barriers.', ft='smoking_status'),
                         [('smoking_status', 'current', False, 'current')])

    def test_transposed_typo_is_read_as_the_word(self):
        out = extract('Social: Nveer smoker.', 'notes', 'text')
        self.assertEqual([(f['fact_type'], f['value']) for f in out], [('smoking_status', 'never')])
        self.assertEqual(out[0]['evidence_text'], 'Nveer smoker')  # spans index the original text
        self.assertEqual(facts('Follwo up in 1 month or sooner PRN.', ft='follow_up')[0][1], 30)

    def test_denies_tobacco(self):
        self.assertIn(('smoking_status', 'never', False, 'current'), facts('HEADSS reviewed. Denies tobacco, vaping, alcohol.'))
        self.assertEqual(facts('Reports occasional vaping; counseled.', ft='smoking_status'), [])


class AllergyScope(unittest.TestCase):
    def test_reactions_in_allergy_lists_are_not_symptoms(self):
        self.assertEqual(facts('Allergies: lisinopril (cough), statins (myalgias)', ft='symptom'), [])
        self.assertEqual(facts('ALLERGIES\ncodeine (nausea), penicillin (rash)\nSOCIAL\nNever smoker.\n', ft='symptom'), [])
        self.assertEqual(facts('PMH: HTN. Allergies: codeine (nausea). Social: Never smoker.', ft='symptom'), [])

    def test_symptoms_outside_the_allergy_list_still_count(self):
        text = 'Allergies: lisinopril (cough)\nROS: Constitutional: no weight loss. CV: no chest pain. Resp: +cough.'
        out = facts(text, ft='symptom')
        self.assertIn(('symptom', 'cough', False, 'current'), out)
        self.assertIn(('symptom', 'chest_pain', True, 'current'), out)


class ImagingNegation(unittest.TestCase):
    def test_negative_for_fracture(self):
        out = facts('X-ray negative for fracture; splinted.', 'hie_documents', ft='imaging_finding')
        self.assertEqual(out, [('imaging_finding', 'fracture', True, 'current')])
        self.assertEqual(facts('Wrist radiograph negative for acute fracture.', 'imaging_reports', ft='imaging_finding'),
                         [('imaging_finding', 'fracture', True, 'current')])
        self.assertEqual(facts('Salter-Harris injury not seen.', 'imaging_reports', ft='imaging_finding'),
                         [('imaging_finding', 'fracture', True, 'current')])

    def test_encounter_reason_is_not_a_study_finding(self):
        self.assertEqual(facts('81 y/o presented to the ED with hip fracture. Admitted.', 'hie_documents', ft='imaging_finding'), [])
        self.assertEqual(facts('Principal diagnosis: Closed fracture of femoral neck', 'hie_documents', ft='imaging_finding'), [])
        self.assertEqual(facts('Hospital course: CXR with right lower lobe infiltrate.', 'hie_documents', ft='imaging_finding'),
                         [('imaging_finding', 'consolidation', False, 'current')])


class Abbreviations(unittest.TestCase):
    def test_ros_abbreviations(self):
        out = facts('ROS: Constitutional: no weight loss. CV: no CP. Resp: no SOB. GI: no N/V/D. GU: no dysuria.')
        for fact in [('dyspnea', 'unspecified', True, 'current'), ('symptom', 'chest_pain', True, 'current'),
                     ('symptom', 'nausea_vomiting', True, 'current'), ('symptom', 'diarrhea', True, 'current')]:
            self.assertIn(fact, out)
        self.assertNotIn(True, [neg for ft, _, neg, _ in out if ft == 'edema'])

    def test_positive_abbreviations(self):
        self.assertIn(('dyspnea', 'exertional', False, 'current'), facts('Reports DOE after one block.'))
        self.assertIn(('dyspnea', 'unspecified', False, 'current'), facts('Resp: +SOB.'))
        self.assertEqual(facts('Ext: 2+ LE edema.', ft='edema'), [('edema', 'moderate', False, 'current')])
        self.assertEqual(facts('Ext: no LE edema.', ft='edema'), [('edema', 'none', True, 'current')])
        self.assertEqual(facts('CV: +edema.', ft='edema'), [('edema', 'present', False, 'current')])

    def test_bare_mentions_need_a_list_cue(self):
        self.assertEqual(facts('No SOB or chest pain.', ft='dyspnea'), [('dyspnea', 'unspecified', True, 'current')])
        self.assertEqual(facts('No shortness of breath or chest pain.', ft='dyspnea'), [('dyspnea', 'unspecified', True, 'current')])
        self.assertIn(('dyspnea', 'unspecified', True, 'current'), facts('Denies orthopnea, PND or new edema.'))
        self.assertIn(('edema', 'none', True, 'current'), facts('Denies orthopnea, PND or new edema.'))
        self.assertEqual(facts('Hospital course: Presented with dyspnea, orthopnea and weight gain.', ft='dyspnea'),
                         [('dyspnea', 'unspecified', False, 'current')])
        self.assertEqual(facts('A single study does not identify the cause of breathlessness.', ft='dyspnea'), [])
        self.assertEqual(facts('Return if fever > 3 days, dyspnea or worsening.'), [])

    def test_symptom_phrasings(self):
        out = facts('Twisted ankle 4 days ago, able to bear weight w/ pain. Subjective fevers. No vomiting, no melena.')
        for fact in [('symptom', 'pain', False, 'current'), ('symptom', 'fever', False, 'current'),
                     ('symptom', 'nausea_vomiting', True, 'current'), ('symptom', 'bleeding', True, 'current')]:
            self.assertIn(fact, out)
        self.assertIn(('symptom', 'syncope', False, 'current'), facts('84 y/o presented to the ED with a syncopal episode.', 'hie_documents'))
        self.assertEqual(facts('Basis: Chronic knee pain worse with stairs.', ft='symptom'), [('symptom', 'pain', False, 'current')])
        self.assertEqual(facts('History of chronic back pain.', ft='symptom'), [])
        hist = facts('Seen in the emergency department on 2027-10-23 for abdominal pain; feeling better since discharge.', ft='symptom')
        self.assertEqual(hist, [('symptom', 'pain', False, 'historical')])


class SocialAndAdherenceV22(unittest.TestCase):
    def test_social_need_phrasings(self):
        self.assertEqual(facts('Social: Never smoker. Transportation barriers.', ft='social_need'),
                         [('social_need', 'transport', False, 'current')])
        self.assertEqual(facts('No transportation barriers.', ft='social_need'), [('social_need', 'transport', True, 'current')])
        self.assertEqual(facts('Has difficulty getting rides to appointments.', ft='social_need'),
                         [('social_need', 'transport', False, 'current')])
        out = facts('Reports trouble affording groceries at times.', ft='social_need')
        self.assertEqual(sorted(out), [('social_need', 'financial', False, 'current'), ('social_need', 'food', False, 'current')])
        self.assertEqual(facts('Food insecurity screen negative.', ft='social_need'), [('social_need', 'food', True, 'current')])
        self.assertEqual(facts('Ask whether transportation barriers prevented the visit.', ft='social_need'), [])

    def test_nonadherence_phrasings(self):
        self.assertEqual(facts('Admits missing doses over the past few weeks.', ft='medication_nonadherence'),
                         [('medication_nonadherence', 'unspecified', False, 'current')])
        self.assertEqual(facts('No missed doses reported. Reports good adherence.', ft='medication_nonadherence'), [])
        self.assertEqual(facts('Ran out of some medications and did not refill on time.', ft='medication_nonadherence'),
                         [('medication_nonadherence', 'supply_access', False, 'current')])
        out = facts('Reports cost has made it hard to keep up w/ refills.')
        self.assertIn(('medication_nonadherence', 'cost', False, 'current'), out)
        self.assertIn(('social_need', 'financial', False, 'current'), out)


class HieDocuments(unittest.TestCase):
    TEXT = ('Admission date: 2028-01-27  Discharge date: 2028-02-01\nPrincipal diagnosis: Acute on chronic heart failure\n'
            'Hospital course: Presented with dyspnea, orthopnea and weight gain. Diuresed with IV furosemide.\n'
            'Discharge medications: metoprolol succinate 50 mg ER tablet daily; furosemide 40 mg tablet daily\n'
            'Disposition: home health\nFollow-up: PCP within 7 days; cardiology in 2 weeks.')

    def test_hie_is_a_text_source(self):
        self.assertEqual(note_facts.TEXT_SOURCES['hie_documents'], ['text'])

    def test_follow_up_section_targets(self):
        out = [(f['value'], f['qualifier']) for f in extract(self.TEXT, 'hie_documents', 'text') if f['fact_type'] == 'follow_up']
        self.assertEqual(sorted(out), [(7, 'pcp'), (14, 'cardiology')])
        self.assertIn(('follow_up', 3, False, 'current'), facts('Treated and released with instructions to follow up with PCP in 2-3 days.',
                                                               'hie_documents'))
        self.assertIn(('follow_up', 14, False, 'current'), facts('Plan: Start lisinopril 10 mg daily; BMP in 2 weeks; recheck in 4 weeks.'))

    def test_sections_and_facts(self):
        out = facts(self.TEXT, 'hie_documents')
        self.assertIn(('dyspnea', 'unspecified', False, 'current'), out)
        self.assertEqual([x for x in out if x[0] == 'symptom'], [])


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


class CopyForward(unittest.TestCase):
    NOTE = ('HPI: Reports increased dyspnea on exertion.\nROS: Resp: no SOB.\nSocial: Never smoker.\nExam: Gen: NAD; CV: RRR; '
            'Ext: {ext}\nAssessment and Plan: 1. HTN: recheck in 4 weeks.')

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        d = Path(self.tmp.name)
        db = sqlite3.connect(d / 'semantic.sqlite3')
        db.execute('CREATE TABLE record(record_id TEXT PRIMARY KEY, patient_id TEXT, stream TEXT, event_date TEXT, recorded_date TEXT, '
                   'available_date TEXT, organization_id TEXT, payload TEXT)')
        exts = ['2+ pitting edema bilaterally', '2+ pitting edema bilaterally', 'no edema', '2+ pitting edema bilaterally']
        rows = [(f'n{i}', 'p1', 'notes', f'2028-0{i + 1}-01', {'text': self.NOTE.format(ext=e)}) for i, e in enumerate(exts)]
        rows.append(('n9', 'p2', 'notes', '2028-01-01', {'text': self.NOTE.format(ext='no edema')}))
        rows.append(('h1', 'p1', 'hie_documents', '2028-02-15', {'text': 'Follow-up: PCP within 7 days.'}))
        db.executemany('INSERT INTO record VALUES (?,?,?,?,?,?,?,?)',
                       [(r, p, s, d_, d_, d_, None, json.dumps(pl)) for r, p, s, d_, pl in reversed(rows)])
        db.commit(); db.close()
        self.adm = d / 'admission.json'
        self.adm.write_text(json.dumps({'admitted': ['edema', 'dyspnea', 'smoking_status', 'follow_up'], 'precision_bar': 0.9}))
        self.dir = d

    def tearDown(self):
        self.tmp.cleanup()

    def test_copied_lines_count_once_changed_lines_count_again(self):
        res = note_facts.build(self.dir, evaluation=self.adm)
        db = sqlite3.connect(self.dir / 'semantic.sqlite3')
        got = db.execute("SELECT record_id, value FROM note_fact WHERE patient_id = 'p1' AND fact_type = 'edema' ORDER BY event_date").fetchall()
        # n1 copies n0's exam line; n2 changes it; n3 changes it back (new evidence, not a copy)
        self.assertEqual(got, [('n0', 'moderate'), ('n2', 'none'), ('n3', 'moderate')])
        smoking = db.execute("SELECT record_id FROM note_fact WHERE patient_id = 'p1' AND fact_type = 'smoking_status'").fetchall()
        self.assertEqual(smoking, [('n0',)])
        ros = db.execute("SELECT count(*) FROM note_fact WHERE patient_id = 'p1' AND fact_type = 'dyspnea' AND negated = 1").fetchone()[0]
        self.assertEqual(ros, 1)
        # HPI and plan sentences are not copy-forward sections: one per note
        self.assertEqual(db.execute("SELECT count(*) FROM note_fact WHERE patient_id = 'p1' AND fact_type = 'dyspnea' "
                                    "AND negated = 0").fetchone()[0], 4)
        self.assertEqual(db.execute("SELECT count(*) FROM note_fact WHERE patient_id = 'p1' AND fact_type = 'follow_up' "
                                    "AND stream = 'notes'").fetchone()[0], 4)
        # HIE documents are a text source; other patients keep their own first copy
        self.assertEqual(db.execute("SELECT value FROM note_fact WHERE stream = 'hie_documents'").fetchall(), [(7,)])
        self.assertEqual(db.execute("SELECT count(*) FROM note_fact WHERE patient_id = 'p2' AND fact_type = 'edema'").fetchone()[0], 1)
        meta = json.loads(db.execute("SELECT value FROM meta WHERE key = 'note_fact_version'").fetchone()[0])
        self.assertEqual(meta['copy_forward_dropped'], res['copy_forward_dropped'])
        self.assertEqual(res['copy_forward_dropped'], 1 + 3 + 3)  # n1 exam edema; ROS + social repeated in n1..n3
        db.close()


if __name__ == '__main__':
    unittest.main()
