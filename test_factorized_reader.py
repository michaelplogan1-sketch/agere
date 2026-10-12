import hashlib
import json
from pathlib import Path
import sqlite3
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch

from clinical_core.agere.clinical.evidence_reader import AXES, VERSION, EvidenceReader, candidates, interpretation, validate_input
from clinical_core.agere.clinical.factorized_training import check, leakage_filter, recipes
from clinical_core.agere.clinical.reader_warehouse import search


def record(text, **extra):
    return {'patient_id': 'p1', 'record_id': 'r1', 'synthetic': True, 'source_table': 'notes',
            'event_date': '2026-01-01', 'available_date': '2026-01-02', 'identity_status': 'matched', 'text': text, **extra}


def example(text='Today metoprolol was ordered.'):
    a = text.index('metoprolol')
    return {'concept': 'medication', 'text': text, 'target_quote': 'metoprolol',
            'target_start': a, 'target_end': a+len('metoprolol'), 'prefix_omitted': False, 'suffix_omitted': False}


class FactorizedReaderTest(unittest.TestCase):
    def test_axes_allow_historical_affirmed_other_person(self):
        axes = {a: {'label': v} for a, v in dict(assertion='affirmed', subject='other_person', time='historical', relation='clinical_observation').items()}
        notes = interpretation(axes)
        self.assertIn('Does not establish a fact about this patient', notes)
        self.assertIn('Does not establish current clinical state', notes)

    def test_missing_records_never_establish_missing_care(self):
        axes = {a: {'label': v} for a, v in dict(assertion='affirmed', subject='patient', time='current', relation='source_coverage').items()}
        self.assertIn('Missing documentation does not establish missing care', interpretation(axes))

    def test_all_requested_classes_have_authored_training_recipes(self):
        for axis, choices in AXES.items():
            self.assertEqual(set(choices), {r['labels'][axis] for r in recipes()})

    def test_exact_target_and_copied_context_survive(self):
        text = 'Copied prior history:\nmetoprolol listed last year, current taking not verified.'
        rows = list(candidates('p1', [record(text)], '2026-01-03'))
        self.assertEqual(len(rows), 1)
        x = rows[0]['input']
        self.assertIn('Copied prior history', x['text'])
        self.assertEqual(x['text'][x['target_start']:x['target_end']], 'metoprolol')
        self.assertEqual(text[rows[0]['window_start']:rows[0]['window_end']], x['text'])

    def test_documents_never_use_hidden_clean_text(self):
        r = record('', source_table='documents', original_text='metoprolol taken now', clean_text='heart failure', extracted_text='Unreadable.')
        self.assertEqual(list(candidates('p1', [r], '2026-01-03')), [])

    def test_identity_and_late_arrivals_fail_closed(self):
        for r in [record('metoprolol', patient_id='other'), record('metoprolol', available_date='2027-01-01'),
                  record('metoprolol', synthetic=False), record('metoprolol', identity_status='unknown')]:
            with self.assertRaises(ValueError):
                list(candidates('p1', [r], '2026-01-03'))

    def test_target_offset_validation_rejects_ambiguous_negative_index(self):
        x = example(); x['target_start'] = -10
        with self.assertRaises(ValueError):
            validate_input(x)

    def test_teacher_blindness(self):
        r = {'example_id': 'hidden-id', 'input': example(), 'synthetic': True,
             'authored_labels': 'DO_NOT_SEND_LABEL', 'split': 'DO_NOT_SEND_SPLIT', 'family_id': 'DO_NOT_SEND_FAMILY'}
        with patch('clinical_core.agere.clinical.factorized_training.send', return_value=([], {})) as send:
            check([r], 'jev', None, '.')
        raw = json.dumps(send.call_args.args[0])
        self.assertNotIn('DO_NOT_SEND', raw)
        self.assertEqual(len(send.call_args.args[0]['questions']), 4)

    def test_invalid_teacher_axis_is_quarantined_not_normalized_into_label(self):
        r = {'example_id': 'test', 'input': example(), 'synthetic': True}
        def fake_send(body, *args, parse, **kwargs):
            answers = {}
            for key, spec in body['questions'].items():
                keys = list(spec['criteria']); ps = dict.fromkeys(keys, 0.)
                ps[keys[0]] = 1.
                answers[key] = {'choice': keys[0], 'probabilities': ps}
            answers['c0_time'] = {'choice': 'current', 'probabilities': {
                'current': .34, 'historical': .31, 'unspecified': .35, 'planned': 0., 'unknown': 0.}}
            return parse({'answers': answers}), {}
        with patch('clinical_core.agere.clinical.factorized_training.send', side_effect=fake_send):
            result, _ = check([r], 'jev', None, '.')
        self.assertFalse(result[0]['time']['valid'])
        self.assertIsNone(result[0]['time']['label'])
        self.assertTrue(result[0]['assertion']['valid'])

    def test_cross_split_duplicates_quarantine_training_not_test(self):
        rows = [{'example_id': str(i), 'input': example(), 'split': split} for i, split in enumerate(('train', 'calibration', 'test'))]
        excluded, _ = leakage_filter(rows)
        self.assertEqual(excluded, {'0', '1'})

    def test_weight_integrity_cache_and_abstention(self):
        import joblib
        from sklearn.pipeline import Pipeline
        from sklearn.feature_extraction.text import TfidfVectorizer
        from sklearn.dummy import DummyClassifier
        with TemporaryDirectory() as directory:
            path = Path(directory); hashes = {}
            for axis, labels in AXES.items():
                label = next(iter(labels))
                model = Pipeline([('vectorizer', TfidfVectorizer()), ('classifier', DummyClassifier(strategy='constant', constant=label))])
                model.fit(['synthetic chart'], [label])
                file = path/f'{axis}-lexical.joblib'; joblib.dump(model, file)
                hashes[file.name] = hashlib.sha256(file.read_bytes()).hexdigest()
            (path/'model-card.json').write_text(json.dumps({'schema': VERSION, 'decision_authority': False, 'weight_hashes': hashes}))
            reader = EvidenceReader(path, path/'cache.sqlite3')
            try:
                predictions = reader.predict([example(), example()])
                self.assertEqual(predictions[0], predictions[1])
                self.assertEqual(reader.stats['new_unique_predictions'], 1)
                self.assertEqual(reader.stats['batch_reuses'], 1)
                self.assertEqual(reader.predict([example()])[0]['decision'], 'abstain')
                self.assertFalse(predictions[0]['care_gap_established'])
                self.assertEqual(reader.stats['persistent_cache_hits'], 1)
            finally:
                reader.close()
            (path/'assertion-lexical.joblib').write_bytes(b'changed')
            with self.assertRaisesRegex(ValueError, 'weights changed'):
                EvidenceReader(path, path/'other.sqlite3')

    def test_annotation_filter_is_parameterized_and_bounded(self):
        with TemporaryDirectory() as directory:
            path = Path(directory)/'test.sqlite3'
            db = sqlite3.connect(path)
            db.execute('CREATE TABLE annotations (id TEXT,patient TEXT,relation TEXT,assertion TEXT,subject TEXT,time TEXT,payload TEXT)')
            db.execute('INSERT INTO annotations VALUES (?,?,?,?,?,?,?)', ('1', 'p1', 'source_coverage', 'affirmed', 'patient', 'current', '{}'))
            db.commit(); db.close()
            self.assertEqual(search(path, relation='source_coverage')['total_bindings'], 1)
            self.assertEqual(search(path, patient="' OR 1=1 --")['total_bindings'], 0)
            with self.assertRaises(ValueError):
                search(path, relation='UNTRUSTED SQL')
            with self.assertRaises(ValueError):
                search(path, limit=101)
            with self.assertRaisesRegex(ValueError, 'historical run'):
                search(path, priority='context_conflict')
            with self.assertRaises(ValueError):
                search(path, priority="' OR 1=1 --")


if __name__ == '__main__':
    unittest.main()
