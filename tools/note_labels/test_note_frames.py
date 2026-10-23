"""Tests for note frames (fake provider, no network): schema, options, combine rules, unknown handling, retrieval bounds,
calibration application, interface shapes.

    .venv/bin/python -m unittest tools.note_labels.test_note_frames
"""
from __future__ import annotations

import json
import os
import sqlite3
import tempfile
import unittest
from pathlib import Path

from clinical_core.agere.clinical import note_evidence as ne
from clinical_core.agere.clinical import note_frames as nf
from clinical_core.agere.clinical import note_questions as nq


def pred(**kw):
    base = {'op': 'note', 'frame': 'medication_event', 'concept': {'term': 'metformin'}, 'statuses': ['documented']}
    base.update(kw)
    return base


class SchemaTests(unittest.TestCase):
    def test_twelve_frames_and_spec_names(self):
        self.assertEqual(set(nf.FRAMES), {'condition', 'symptom', 'medication_event', 'procedure_test', 'finding_value',
                                          'adverse_event', 'care_process', 'social_need', 'function_cognition', 'preference_refusal',
                                          'plan_followup', 'note_contradiction'})
        self.assertEqual(list(nf.STATUSES), ['documented', 'denied', 'possible', 'planned_not_done', 'historical', 'other_person',
                                             'not_mentioned'])
        self.assertEqual(set(nf.COMBINE), {'any', 'latest', 'first', 'trend', 'conflict', 'all'})

    def test_every_frame_declares_closed_details(self):
        for name, f in nf.FRAMES.items():
            self.assertTrue(f['details'], name)
            self.assertIn(f['default_combine'], nf.COMBINE)
            for field, d in f['details'].items():
                self.assertIn(d['kind'], ('choice', 'multi', 'number', 'count', 'date', 'time', 'quote'), (name, field))
                if d['kind'] in ('choice', 'multi'):
                    self.assertGreaterEqual(len(d['options']), 2, (name, field))
                    self.assertEqual(len(set(d['options'])), len(d['options']), (name, field))
                self.assertTrue(d['ask'], (name, field))

    def test_spec_example_medication_event_options(self):
        d = nf.FRAMES['medication_event']['details']
        self.assertEqual(d['event']['options'][:5], ['started', 'stopped', 'changed_dose', 'held', 'switched'])
        for r in ('side_effect', 'cost', 'ineffective', 'patient_choice', 'contraindication', 'course_completed',
                  'clinician_decision', 'other_with_quote'):
            self.assertIn(r, d['reason']['options'])

    def test_every_status_has_a_definition(self):
        self.assertTrue(all(len(v) > 40 for v in nf.STATUSES.values()))

    def test_validate_normalizes_spec_example(self):
        p = nf.validate_predicate({
            'op': 'note', 'frame': 'medication_event', 'concept': {'term': 'metformin', 'codes': ['RxNorm:6809'], 'quote': 'metformin'},
            'statuses': ['documented'], 'detail': {'event': 'stopped', 'reason': ['side_effect', 'cost']},
            'window': {'start': '2029-05-28', 'end': '2030-05-27', 'quote': 'in the last year'}, 'combine': 'any',
            'sources': ['clinical_note', 'scanned_document_text', 'hie_document'], 'quote': 'x'})
        self.assertEqual(p['detail']['reason'], ['side_effect', 'cost'])
        self.assertEqual(p['sources'], ['clinical_note', 'scanned_document_text', 'hie_document'])

    def test_validate_rejections(self):
        bad = [pred(frame='nope'), pred(statuses=[]), pred(statuses=['maybe']), pred(combine='sum'), pred(detail={'event': 'exploded'}),
               pred(detail={'colour': 'red'}), pred(window={'start': '2030-01-01', 'end': '2029-01-01'}), pred(op='x'),
               pred(sources=['fax']), pred(concept={'term': ''}), pred(combine='trend'), pred(frame='note_contradiction'),
               pred(extra=1), pred(frame='finding_value', detail={'event_time': '25:00'}), pred(min_p=0)]
        for b in bad:
            with self.assertRaises(ValueError, msg=str(b)):
                nf.validate_predicate(b)

    def test_validate_defaults_and_trend(self):
        p = nf.validate_predicate(pred(frame='finding_value', concept={'term': 'A1c'}, combine='trend', detail={'trend': 'decreased'}))
        self.assertEqual(p['combine'], 'trend')
        self.assertEqual(nf.validate_predicate(pred())['combine'], 'latest')
        self.assertEqual(nf.validate_predicate(pred(frame='condition'))['combine'], 'any')

    def test_detail_matches(self):
        got = {'event': 'stopped', 'reason': ['cost'], 'amount': '500', 'event_time': '14:30'}
        self.assertTrue(nf.detail_matches('medication_event', {'event': 'stopped', 'reason': ['side_effect', 'cost']}, got))
        self.assertFalse(nf.detail_matches('medication_event', {'event': 'started'}, got))
        self.assertTrue(nf.detail_matches('medication_event', {'amount': {'min': 100, 'max': 600}}, got))
        self.assertTrue(nf.detail_matches('medication_event', {'event_time': {'max': '15:00'}}, got))
        self.assertFalse(nf.detail_matches('medication_event', {'initiated_by': 'patient'}, got))  # unread never matches

    def test_catalog_is_plain_data(self):
        json.dumps(nf.frame_catalog())


class FakeDB:
    """A tiny semantic layer: patients p1 (rich) and p2 (empty)."""
    def __init__(self, directory):
        self.path = Path(directory) / 'semantic.sqlite3'
        c = sqlite3.connect(self.path)
        c.executescript("""create table meta(key text primary key, value text);
        create table record(record_id text primary key, patient_id text, stream text, event_date text, recorded_date text,
                            available_date text, organization_id text, payload text);
        create table scanned_document_text(record_id text primary key, patient_id text, text_source text, text text);
        create table scanned_document(record_id text primary key, patient_id text, document_type text, event_date text);""")
        notes = [
            ('N1', 'p1', 'notes', '2029-06-01', 'Stop metformin 500 mg due to side effects. Plan to recheck in 3 months.'),
            ('N2', 'p1', 'notes', '2029-09-01', 'Patient denies taking metformin. A1c 7.9%.'),
            ('N3', 'p1', 'notes', '2029-12-01', 'Mother takes metformin. Back pain today. A1c 7.1%.'),
            ('N4', 'p1', 'hie_documents', '2029-12-02', 'Discharge medications: metformin 1000 mg daily.'),
            ('N5', 'p1', 'billing_documents', '2029-12-02', 'Metformin invoice.'),
            ('N6', 'p1', 'notes', '2031-01-01', 'Future note about metformin.'),
        ] + [(f'M{i}', 'p1', 'telephone_messages', f'2029-10-{i + 1:02d}', f'Call {i}. metformin refill number {i} requested.')
             for i in range(20)]
        for rid, pid, st, day, text in notes:
            c.execute('insert into record values(?,?,?,?,?,?,?,?)', (rid, pid, st, day, day, day, 'o', json.dumps({'text': text, 'note_type': 'office_followup'})))
        c.execute("insert into scanned_document_text values('S1','p1','pdf_text_layer','Metformin held, creatinine 2.1.')")
        c.execute("insert into scanned_document values('S1','p1','Consult Note','2029-11-01')")
        c.execute("insert into meta values('as_of','2030-05-27')")
        c.commit()
        c.close()


def fake_decide(question, options, evidence, *, as_of, order=None, threshold=0.8, task_key=None, ledger=None, allow_live=True):
    """Reads the passage with keyword rules; probabilities are sharp. Records every call."""
    fake_decide.calls.append((question, tuple(options), order))
    text = evidence[0]['text'].lower() if evidence else ''
    if 'yes' in options and 'no' in options and 'unknown' in options and 'documented' not in options:
        want = question.split('"')[-2] if question.count('"') >= 2 else ''
        label = 'yes' if want.replace(' ', '') in text.replace(' ', '').replace('_', '') or ('side effect' in want and 'side effect' in text) else 'no'
    elif 'documented' in options:
        if 'mother' in text:
            label = 'other_person'
        elif 'denies' in text:
            label = 'denied'
        elif 'stop' in text or 'held' in text or 'discharge medications' in text or 'refill' in text:
            label = 'documented'
        elif 'metformin' in text:
            label = 'possible'
        else:
            label = 'not_mentioned'
    elif 'stopped' in options:
        label = 'stopped' if 'stop' in text or 'held' in text else 'continued'
    else:
        label = 'unknown'
    conf = fake_decide.conf.get(label, 0.97)
    probs = {o: (1 - conf) / (len(options) - 1) for o in options}
    probs[label] = conf
    return {'label': label, 'probabilities': probs, 'calibrated_probabilities': probs, 'provider': (order or ('jev',))[0],
            'cost_usd': 0.0001, 'cache_hit': False, 'status': 'answered'}


fake_decide.calls = []
fake_decide.conf = {}


class EvidenceTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.db = FakeDB(self.tmp.name)

    def tearDown(self):
        self.tmp.cleanup()

    def test_retrieval_bounds_and_window(self):
        r = ne.retrieve(self.db, 'p1', {'term': 'metformin'}, as_of='2030-05-27', max_passages=5, per_record=1)
        self.assertEqual(len(r), 5)
        self.assertEqual(len({c['record_id'] for c in r}), 5)
        self.assertNotIn('N5', {c['record_id'] for c in r})   # billing is never read
        self.assertNotIn('N6', {c['record_id'] for c in r})   # after the cutoff
        win = ne.retrieve(self.db, 'p1', {'term': 'metformin'}, as_of='2030-05-27', window={'start': '2029-09-01', 'end': '2029-09-30'})
        self.assertEqual([c['record_id'] for c in win], ['N2'])

    def test_spans_are_exact_and_deterministic(self):
        a = ne.retrieve(self.db, 'p1', {'term': 'metformin'}, as_of='2030-05-27')
        b = ne.retrieve(self.db, 'p1', {'term': 'metformin'}, as_of='2030-05-27')
        self.assertEqual([c['passage_id'] for c in a], [c['passage_id'] for c in b])
        conn = ne.connect(self.db)
        for c in a:
            if c['source'] == 'clinical_note':
                full = json.loads(conn.execute('select payload from record where record_id=?', (c['record_id'],)).fetchone()[0])['text']
                self.assertEqual(full[c['start']:c['end']].strip(), c['text'])

    def test_sources_filter_and_scanned(self):
        r = ne.retrieve(self.db, 'p1', {'term': 'metformin'}, as_of='2030-05-27', sources=['scanned_document_text'])
        self.assertEqual([c['record_id'] for c in r], ['S1'])
        self.assertEqual(r[0]['note_type'], 'Consult Note')

    def test_empty_patient(self):
        self.assertEqual(ne.retrieve(self.db, 'p2', {'term': 'metformin'}, as_of='2030-05-27'), [])


class CombineTests(unittest.TestCase):
    def reading(self, status, day, p=0.95, detail=None, undecided=False, rid=None, undecided_detail=()):
        return {'passage_id': rid or f'r{day}', 'record_id': rid or f'r{day}', 'date': day, 'note_type': 't', 'source': 'clinical_note',
                'quote': 'q', 'start': 0, 'end': 1, 'status': None if undecided else status, 'p': p, 'provider': 'jev',
                'best_guess': status, 'detail': detail or {}, 'detail_undecided': list(undecided_detail), 'undecided': undecided,
                'cost_usd': 0}

    def test_any(self):
        p = nf.validate_predicate(pred(combine='any'))
        r = nq.combine(p, [self.reading('denied', '2029-01-01'), self.reading('documented', '2029-02-01')])
        self.assertEqual((r['status'], r['holds']), ('documented', True))
        r = nq.combine(p, [self.reading('denied', '2029-01-01'), self.reading('other_person', '2029-02-01')])
        self.assertEqual((r['status'], r['holds']), ('denied', False))
        self.assertEqual(nq.combine(p, [])['status'], 'not_mentioned')

    def test_unknown_is_never_no(self):
        p = nf.validate_predicate(pred(combine='any'))
        r = nq.combine(p, [self.reading('denied', '2029-01-01'), self.reading('documented', '2029-02-01', undecided=True)])
        self.assertEqual((r['status'], r['holds'], r['undecided']), ('unknown', None, 1))
        r = nq.combine(p, [self.reading('documented', '2029-01-01'), self.reading('documented', '2029-02-01', undecided=True)])
        self.assertTrue(r['holds'])  # a decided hit stands regardless of undecided passages

    def test_latest_first(self):
        rs = [self.reading('documented', '2029-01-01'), self.reading('denied', '2029-06-01')]
        self.assertEqual(nq.combine(nf.validate_predicate(pred(combine='latest')), rs)['status'], 'denied')
        self.assertEqual(nq.combine(nf.validate_predicate(pred(combine='first')), rs)['status'], 'documented')
        late = rs + [self.reading('documented', '2029-09-01', undecided=True)]
        self.assertEqual(nq.combine(nf.validate_predicate(pred(combine='latest')), late)['status'], 'unknown')
        self.assertEqual(nq.combine(nf.validate_predicate(pred(combine='first')), late)['status'], 'documented')

    def test_all(self):
        p = nf.validate_predicate(pred(combine='all', statuses=['denied']))
        self.assertTrue(nq.combine(p, [self.reading('denied', '2029-01-01'), self.reading('denied', '2029-02-01')])['holds'])
        self.assertFalse(nq.combine(p, [self.reading('denied', '2029-01-01'), self.reading('documented', '2029-02-01')])['holds'])
        self.assertIsNone(nq.combine(p, [self.reading('denied', '2029-01-01'), self.reading('x', '2029-02-01', undecided=True)])['holds'])

    def test_conflict(self):
        p = nf.validate_predicate(pred(frame='note_contradiction', detail={'base_frame': 'medication_event'}, combine='conflict'))
        r = nq.combine(p, [self.reading('documented', '2029-01-01'), self.reading('denied', '2029-02-01')])
        self.assertEqual((r['status'], r['holds']), ('documented', True))
        r = nq.combine(p, [self.reading('documented', '2029-01-01'), self.reading('documented', '2029-02-01')])
        self.assertFalse(r['detail']['conflict'])
        r = nq.combine(p, [self.reading('documented', '2029-01-01', detail={'value': '7.9'}),
                           self.reading('documented', '2029-02-01', detail={'value': '7.1'})])
        self.assertEqual(r['detail']['by'], 'value')

    def test_trend(self):
        p = nf.validate_predicate(pred(frame='finding_value', concept={'term': 'A1c'}, combine='trend', detail={'trend': 'decreased'}))
        r = nq.combine(p, [self.reading('documented', '2029-01-01', detail={'value': '9.1'}),
                           self.reading('documented', '2029-06-01', detail={'value': '7.4'})])
        self.assertEqual((r['detail']['trend'], r['holds'], r['detail']['delta']), ('decreased', True, -1.7))
        r = nq.combine(p, [self.reading('documented', '2029-01-01', detail={'value': '7.0'})])
        self.assertNotEqual(r['status'], 'documented')

    def test_detail_constrained_match(self):
        p = nf.validate_predicate(pred(detail={'event': 'stopped'}, combine='any'))
        yes = self.reading('documented', '2029-01-01', detail={'event': 'stopped'})
        no = self.reading('documented', '2029-02-01', detail={'event': 'started'})
        self.assertTrue(nq.combine(p, [no, yes])['holds'])
        self.assertFalse(nq.combine(p, [no])['holds'])
        pend = self.reading('documented', '2029-02-01', undecided_detail=['event'])
        self.assertIsNone(nq.combine(p, [pend])['holds'])


class ExecutorTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.db = FakeDB(self.tmp.name)
        fake_decide.calls.clear()
        fake_decide.conf = {}
        self.env = os.environ.get('AGERE_NOTE_FRAMES_CALIBRATION')
        os.environ['AGERE_NOTE_FRAMES_CALIBRATION'] = str(Path(self.tmp.name) / 'cal.json')

    def tearDown(self):
        if self.env is None:
            os.environ.pop('AGERE_NOTE_FRAMES_CALIBRATION', None)
        else:
            os.environ['AGERE_NOTE_FRAMES_CALIBRATION'] = self.env
        self.tmp.cleanup()

    def run_eval(self, **kw):
        p = nf.validate_predicate(pred(**kw))
        return nq.evaluate(self.db, p, ['p1', 'p2'], as_of='2030-05-27', workers=1, decide=fake_decide, max_passages=6)

    def test_shape_and_documented(self):
        out = self.run_eval(statuses=['denied'], combine='any', window={'start': '2029-09-01', 'end': '2029-09-30'})
        for pid in ('p1', 'p2'):
            self.assertLessEqual({'status', 'detail', 'evidence', 'p', 'holds', 'undecided'}, set(out[pid]))
        self.assertEqual(out['p1']['status'], 'denied')
        self.assertTrue(out['p1']['holds'])
        ev = out['p1']['evidence'][0]
        self.assertEqual((ev['record_id'], ev['date'], ev['note_type']), ('N2', '2029-09-01', 'office_followup'))
        self.assertIn('denies taking metformin', ev['quote'])
        self.assertEqual(out['p2']['status'], 'not_mentioned')

    def test_detail_reads_only_for_qualifying_passages(self):
        self.run_eval(statuses=['documented'], detail={'event': 'stopped'}, combine='any', window={'start': '2029-09-01', 'end': '2029-09-30'})
        self.assertEqual(len(fake_decide.calls), 1)  # N2 is `denied`: the detail question is never asked

    def test_detail_match_through_executor(self):
        out = self.run_eval(statuses=['documented'], detail={'event': 'stopped'}, combine='any', window={'start': '2029-06-01', 'end': '2029-06-30'})
        self.assertTrue(out['p1']['holds'])
        self.assertEqual(out['p1']['detail']['event'], 'stopped')

    def test_providers_failing_gives_unknown_not_no(self):
        def boom(*a, **k):
            raise RuntimeError('429')
        p = nf.validate_predicate(pred(statuses=['documented']))
        out = nq.evaluate(self.db, p, ['p1'], as_of='2030-05-27', workers=1, decide=boom, max_passages=3)
        self.assertEqual((out['p1']['status'], out['p1']['holds']), ('unknown', None))

    def test_low_probability_escalates_then_undecided(self):
        fake_decide.conf = {'documented': 0.5}
        out = self.run_eval(statuses=['documented'], combine='any', window={'start': '2029-06-01', 'end': '2029-06-30'})
        self.assertEqual(out['p1']['status'], 'unknown')
        self.assertEqual([c[2] for c in fake_decide.calls], [('jev',), ('perplexity',)])

    def test_calibration_shift_and_threshold_applied(self):
        fake_decide.conf = {'documented': 0.7}
        cal = {'schema': 'note-frames-calibration/1', 'tasks': {nf.task_key('medication_event'): {'jev': {
            'threshold': 0.6, 'shift': {'documented': 1.0}}}}}
        Path(os.environ['AGERE_NOTE_FRAMES_CALIBRATION']).write_text(json.dumps(cal))
        out = self.run_eval(statuses=['documented'], combine='any', window={'start': '2029-06-01', 'end': '2029-06-30'})
        self.assertTrue(out['p1']['holds'])
        self.assertGreater(out['p1']['p'], 0.7)  # the shift raised the documented probability
        self.assertEqual(len(fake_decide.calls), 1)  # no escalation: accepted at the fitted threshold

    def test_answer_patient_shape(self):
        p = {'op': 'note', 'frame': 'medication_event', 'concept': {'term': 'metformin'}, 'statuses': ['documented', 'denied']}
        orig = nq.system_one.decide
        nq.system_one.decide = fake_decide
        try:
            a = nf.answer_patient(self.db, p, 'p1', as_of='2030-05-27', workers=1)
        finally:
            nq.system_one.decide = orig
        self.assertIn(a['status'], nf.RESULT_STATUSES)
        self.assertLessEqual({'status', 'detail', 'evidence', 'p'}, set(a))


if __name__ == '__main__':
    unittest.main()
