"""Open-concept note questions, source cues, dated-note binding and multi-part rendering (fake provider, fake warehouse; no network).

    .venv/bin/python -m unittest tools.note_labels.test_note_open
"""
from __future__ import annotations

import json
import re
import sqlite3
import tempfile
import unittest
from pathlib import Path

from clinical_core.agere.clinical import chart_notes as CN
from clinical_core.agere.clinical import chart_reader as CR
from clinical_core.agere.clinical import note_evidence as ne
from clinical_core.agere.clinical import note_frames as nf
from clinical_core.agere.clinical import note_open as no
from clinical_core.agere.clinical import note_questions as nq

AS_OF = '2030-05-27'
FILL = ' '.join(f'Filler line {i} of a long templated note.' for i in range(30))


class DB:
    def __init__(self, directory):
        self.path = Path(directory) / 'semantic.sqlite3'
        c = sqlite3.connect(self.path)
        c.executescript("""create table meta(key text primary key, value text);
        create table record(record_id text primary key, patient_id text, stream text, event_date text, recorded_date text,
                            available_date text, organization_id text, payload text);
        create table scanned_document_text(record_id text primary key, patient_id text, text_source text, text text);
        create table scanned_document(record_id text primary key, patient_id text, document_type text, document_title text, event_date text);""")
        notes = [
            ('A1', 'p1', 'notes', '2027-06-03', 'office_followup', 'CC\nannual physical\n' + FILL + '\nNone\nALLERGIES\nlisinopril (cough)\nSOCIAL\nNever smoker.'),
            ('A2', 'p1', 'notes', '2027-07-01', 'office_followup', 'CC\nfollow up\n' + FILL + '\nNone\nALLERGIES\nlisinopril (cough)\nSOCIAL\nNever smoker.'),
            ('A3', 'p1', 'notes', '2027-08-01', 'office_followup', 'Allergies: sulfa (rash) and penicillin (hives).'),
            ('A4', 'p1', 'notes', '2027-09-01', 'office_followup', 'Allergies: NKDA.'),
            ('B1', 'p2', 'notes', '2027-12-25', 'office_followup',
             'HPI: Reports trouble affording groceries at times.\nPlan: SDOH: referred to community food resources; follow up in 1 month.'),
            ('B2', 'p2', 'notes', '2028-01-30', 'office_followup', 'Wellness screen: transportation assistance: False. Food insecurity: denied.'),
            ('B3', 'p2', 'hie_documents', '2028-02-03', 'Discharge summary', 'Discharged home. Follow up with PCP in one week. Reports a fall at home.'),
            ('B4', 'p2', 'notes', '2028-02-03', 'ed_note', 'ED visit: fall at home, no fracture. Follow up with PCP.'),
            ('C1', 'p3', 'notes', '2028-03-01', 'office_followup', 'Patient reports cough and fatigue. Denies chest pain.'),
        ]
        for rid, pid, st, day, nt, text in notes:
            c.execute('insert into record values(?,?,?,?,?,?,?,?)', (rid, pid, st, day, day, day, 'o', json.dumps({'text': text, 'note_type': nt})))
        c.execute("insert into scanned_document_text values('S1','p2','front_office_ocr','EXAM: XRAY\nIMPRESSION: Possible early right lower lobe infiltrate.')")
        c.execute("insert into scanned_document values('S1','p2','Outside Records','Radiology Report','2027-09-03')")
        c.execute("insert into scanned_document_text values('S2','p2','pdf_text_layer','Cardiology consult. Reports cough. Plan: echo.')")
        c.execute("insert into scanned_document values('S2','p2','Consult Note','','2027-10-01')")
        c.execute("insert into meta values('as_of',?)", (AS_OF,))
        c.commit()
        c.close()


def fake_decide(question, options, evidence, *, as_of, order=None, threshold=0.8, task_key=None, ledger=None, allow_live=True):
    """Keyword reader. Status questions quote the asked term; the reader says `documented` when the term (or the class words) sits in a
    sentence without a negation, `denied` when negated, else `not_mentioned`. Yes/no and choice questions answer `no` / the first option."""
    fake_decide.calls.append(question)
    text = (evidence[0]['text'] if evidence else '').lower()
    if 'documented' in options:
        term = question.split('"')[-2].lower() if question.count('"') >= 2 else ''
        term = re.sub(r'^an adverse reaction, allergy or intolerance to ', '', term).split(' (')[0]
        key = term.split()[0] if term else ''
        label = 'not_mentioned'
        for sent in re.split(r'[.\n;]', text):
            if key and key in sent or (term in ('food insecurity', 'financial strain') and re.search(r'afford|grocer', sent)):
                label = 'denied' if re.search(r'\b(?:denied|false|nkda|denies|no)\b', sent) else 'documented'
                break
        if term.startswith('action taken'):
            label = 'documented' if re.search(r'referred|follow up|plan', text) else 'not_mentioned'
        if term.startswith('drug intolerance') and re.search(r'nkda', text):
            label = 'denied'
    elif 'yes' in options:
        label = 'no'
    else:
        label = [o for o in options if o != 'unknown'][0]
    conf = fake_decide.conf.get(label, 0.97)
    probs = {o: (1 - conf) / (len(options) - 1) for o in options}
    probs[label] = conf
    return {'label': label, 'probabilities': probs, 'calibrated_probabilities': probs, 'provider': (order or ('jev',))[0],
            'cost_usd': 0.0001, 'cache_hit': False, 'status': 'answered'}


fake_decide.calls = []
fake_decide.conf = {}


def open_pred(frame, cls, term, **extra):
    return nf.validate_predicate({'op': 'note', 'frame': frame, 'concept': {'term': term, 'open': True, 'class': cls},
                                  'statuses': ['documented', 'historical'], **extra})


class OpenBase(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.db = DB(self.tmp.name)
        fake_decide.calls.clear()
        fake_decide.conf = {}

    def tearDown(self):
        self.tmp.cleanup()

    def run_open(self, pred, pid, **kw):
        return nq.evaluate(self.db, pred, [pid], as_of=AS_OF, workers=1, decide=fake_decide, **kw)[pid]


class OpenRetrieval(OpenBase):
    def test_the_class_word_is_not_the_concept(self):
        """'drug intolerance' never appears in the notes; the frame's cue lexicon (ALLERGIES, rash ...) finds the passages."""
        pred = open_pred('adverse_event', 'adverse', 'drug intolerance')
        ps = ne.retrieve(self.db, 'p1', pred['concept'], as_of=AS_OF, patterns=no.patterns('adverse_event', 'adverse'))
        self.assertEqual({p['record_id'] for p in ps}, {'A1', 'A2', 'A3', 'A4'})

    def test_findings_are_named_from_the_library_and_quoted(self):
        r = self.run_open(open_pred('adverse_event', 'adverse', 'drug intolerance'), 'p1')
        names = {g['concept']: g for g in r['findings']}
        self.assertEqual(set(names), {'lisinopril', 'sulfa', 'penicillin'})
        lis = names['lisinopril']
        self.assertEqual((lis['first_date'], lis['last_date'], lis['n_records']), ('2027-06-03', '2027-07-01', 2))
        self.assertEqual(lis['evidence'][0]['quote'], 'lisinopril (cough)')
        self.assertTrue(all(e['record_id'] and e['quote'] for g in r['findings'] for e in g['evidence']))
        self.assertTrue(r['holds'])

    def test_repeated_template_is_read_once_but_lists_every_record(self):
        self.run_open(open_pred('adverse_event', 'adverse', 'drug intolerance'), 'p1')
        asked = [q for q in fake_decide.calls if 'lisinopril' in q and 'what does this' in q.lower()]
        self.assertEqual(len(asked), 1)  # A1 and A2 differ only in digits / dates: one status question

    def test_no_invention_denied_and_not_mentioned_are_not_findings(self):
        r = self.run_open(open_pred('adverse_event', 'adverse', 'drug intolerance', window={'start': '2027-09-01', 'end': '2027-09-30'}), 'p1')
        self.assertEqual((r['status'], r['holds'], r['findings']), ('not_mentioned', False, []))
        r = self.run_open(open_pred('social_need', 'social', 'social or financial barriers'), 'p3')
        self.assertEqual(r['findings'], [])  # nothing to retrieve at all
        r = self.run_open(open_pred('social_need', 'social', 'social or financial barriers', window={'start': '2028-01-30', 'end': '2028-01-30'}), 'p2')
        self.assertEqual(r['findings'], [])  # "transportation assistance: False", "food insecurity: denied"

    def test_undecided_findings_are_listed_as_undecided_not_counted(self):
        fake_decide.conf = {'documented': 0.4}
        r = self.run_open(open_pred('adverse_event', 'adverse', 'drug intolerance'), 'p1')
        self.assertEqual(r['findings'], [])
        self.assertIsNone(r['holds'])
        self.assertEqual(r['status'], 'unknown')
        self.assertTrue(r['undecided_findings'] and all(u['status'] == 'undecided' for u in r['undecided_findings']))

    def test_social_domains_name_the_finding(self):
        r = self.run_open(open_pred('social_need', 'social', 'social or financial barriers', window={'start': '2027-12-01', 'end': '2027-12-31'}), 'p2')
        self.assertEqual({g['concept'] for g in r['findings']}, {'food insecurity', 'financial strain'})
        self.assertTrue(all('afford' in g['evidence'][0]['quote'] for g in r['findings']))

    def test_symptom_vocabulary_and_negation(self):
        r = self.run_open(open_pred('symptom', 'symptom', 'symptoms'), 'p3')
        self.assertEqual({g['concept'] for g in r['findings']}, {'cough', 'fatigue'})  # "Denies chest pain" is not a finding

    def test_every_social_domain_option_has_cues(self):
        domains = set(nf.FRAMES['social_need']['details']['domain']['options'])
        self.assertEqual(set(no.DOMAIN_CUES) | set(no.NOT_BARRIERS), domains)

    def test_validation(self):
        with self.assertRaises(ValueError):
            nf.validate_predicate({'op': 'note', 'frame': 'symptom', 'concept': {'term': 'x', 'open': True}, 'statuses': ['documented']})
        with self.assertRaises(ValueError):
            nf.validate_predicate({'op': 'note', 'frame': 'symptom', 'concept': {'term': 'x', 'class': 'symptom'}, 'statuses': ['documented']})
        with self.assertRaises(ValueError):
            nf.validate_predicate({'op': 'note', 'frame': 'symptom', 'concept': {'term': 'x'}, 'statuses': ['documented'], 'note_types': ['poem']})


class ClosedPromptsUnchanged(unittest.TestCase):
    def test_closed_predicate_questions_are_the_calibrated_text(self):
        """Calibration was fit on these exact questions; open concepts must not change them."""
        pred = nf.validate_predicate({'op': 'note', 'frame': 'medication_event', 'concept': {'term': 'metformin'}, 'statuses': ['documented']})
        passage = {'note_type': 'office_followup', 'date': '2029-06-01', 'text': 't', 'passage_id': 'x', 'record_id': 'r'}
        f = nf.FRAMES['medication_event']
        self.assertEqual(nq.status_question(pred, passage),
                         f"About the PATIENT only: what does this office_followup passage dated 2029-06-01 say regarding the "
                         f"medicine \"metformin\"? {f['stem']} \"metformin\"? Choose the one best description of what the passage states.")
        self.assertEqual(nq.detail_question(pred, passage, 'event'),
                         "About the PATIENT only, regarding \"metformin\" in this office_followup passage dated 2029-06-01: " + f['details']['event']['ask'])


class SourceCues(OpenBase):
    def test_cue_table(self):
        c = no.source_cues('What was the impression on the outside chest x-ray report?')
        self.assertEqual((c['sources'], c['note_types']), (['scanned_document_text', 'hie_document', 'other_text'], ['radiology_report']))
        self.assertEqual(no.source_cues('What does the scanned referral say?')['sources'], ['scanned_document_text'])
        self.assertEqual(no.source_cues('Anything in the HIE documents about falls?')['sources'], ['hie_document'])
        self.assertEqual(no.source_cues('What did the discharge summary recommend?')['note_types'], ['discharge_summary'])
        self.assertEqual(no.source_cues('What did the ED note say?')['note_types'], ['ed_note'])
        self.assertEqual(no.source_cues('At the annual wellness visit, was a memory concern raised?')['note_types'], ['annual_wellness_visit'])
        self.assertEqual(no.source_cues('Is the patient on aspirin?'), {'sources': None, 'note_types': []})

    def test_note_types_restrict_retrieval(self):
        c = {'term': 'cough'}
        allp = ne.retrieve(self.db, 'p2', c, as_of=AS_OF, patterns=[r'cough', r'fall', r'infiltrate'])
        self.assertTrue({'S1', 'S2', 'B3', 'B4'} <= {p['record_id'] for p in allp})
        rad = ne.retrieve(self.db, 'p2', c, as_of=AS_OF, patterns=[r'cough', r'fall', r'infiltrate'], note_types=['radiology_report'])
        self.assertEqual({p['record_id'] for p in rad}, {'S1'})  # the document TITLE ("Radiology Report") counts as note type
        dis = ne.retrieve(self.db, 'p2', c, as_of=AS_OF, patterns=[r'fall'], note_types=['discharge_summary'])
        self.assertEqual({p['record_id'] for p in dis}, {'B3'})
        ed = ne.retrieve(self.db, 'p2', c, as_of=AS_OF, patterns=[r'fall'], note_types=['ed_note'])
        self.assertEqual({p['record_id'] for p in ed}, {'B4'})
        out = ne.retrieve(self.db, 'p2', c, as_of=AS_OF, patterns=[r'infiltrate'], sources=['hie_document'])
        self.assertEqual(out, [])

    def test_dated_note_reference_binds_that_note(self):
        w = lambda q: CN.window_for(*CN.asking_and_context(q), AS_OF)  # noqa: E731
        self.assertEqual((w('What did the 2029-08-05 note say about the fall?') or {}).get('start'), '2029-08-05')
        self.assertEqual((w('What did the 2029-08-05 ED note say?') or {}).get('end'), '2029-08-05')
        self.assertEqual((w('What did the ED note from August 5, 2029 say?') or {}).get('start'), '2029-08-05')
        self.assertEqual((w('What did the note dated 08/05/2029 say?') or {}).get('start'), '2029-08-05')
        self.assertIsNone(w('Was aspirin stopped on 2029-08-05?'))  # a date that dates an event does not bind a note

    def test_build_carries_sources_types_and_typed_chart_exposes_them(self):
        preds = CN.build('What did the 2028-02-03 discharge summary recommend for the patient?', AS_OF)
        self.assertEqual(preds[0]['note_types'], ['discharge_summary'])
        self.assertEqual((preds[0]['window']['start'], preds[0]['window']['end']), ('2028-02-03', '2028-02-03'))
        out = CR.typed_chart({'intent': 'status', 'domain': 'notes'}, preds)
        self.assertEqual(out['note_types'], ['discharge_summary'])
        self.assertEqual(out['note_dates'], ['2028-02-03'])
        preds = CN.build('What does the outside report for the patient say about the chest x-ray impression?', AS_OF)
        self.assertEqual(CR.typed_chart({'intent': 'status', 'domain': 'notes'}, preds)['note_sources'],
                         ['scanned_document_text', 'hie_document', 'other_text'])
        for p in preds:
            nf.validate_predicate({k: v for k, v in p.items() if not k.startswith('_')})


class OpenBuild(unittest.TestCase):
    def test_open_class_phrases(self):
        cases = {'What drug intolerance do the notes record for the patient?': ('adverse_event', 'adverse'),
                 'Anything in the patient notes about social or financial barriers?': ('social_need', 'social'),
                 'What symptoms has the patient reported?': ('symptom', 'symptom')}
        for q, (frame, cls) in cases.items():
            p = CN.build(q, AS_OF)[0]
            self.assertEqual((p['frame'], p['concept'].get('class'), p['concept'].get('open')), (frame, cls, True), q)
            self.assertEqual(p['statuses'], ['documented', 'historical'])

    def test_a_named_agent_is_not_open(self):
        for q in ('Is the patient allergic to penicillin according to the notes?', 'What side effects did metformin cause according to the notes?',
                  'What intolerance to lisinopril do the notes record?'):
            for p in CN.build(q, AS_OF):
                self.assertFalse(p['concept'].get('open'), q)

    def test_symptom_questions_route_to_notes(self):
        from clinical_core.agere.clinical import chart_intents as CI
        q = 'What symptoms has SYN-11111111-AAA reported?'
        self.assertTrue(CN.is_note_question(q, CI.parse(q, AS_OF)))

    def test_multi_part_split(self):
        q = 'Anything in the notes about social or financial barriers? What was done about it?'
        ask, ctx, resp = CN.split_parts(q)
        self.assertEqual((ask, resp), ('Anything in the notes about social or financial barriers?', True))
        p = CN.build(q, AS_OF)
        self.assertEqual(len(p), 1)
        self.assertTrue(p[0]['_response'])
        self.assertEqual(p[0]['frame'], 'social_need')
        self.assertFalse(CN.split_parts('Is a fall mentioned? Is a DNR documented?')[2])


class FakeOpenNotes:
    """note_frames stand-in: the primary open predicate returns canned findings; response predicates return a canned referral."""
    def __init__(self, findings=None, undecided=None, response=None):
        self.calls, self.findings, self.undecided, self.response = [], findings or [], undecided or [], response

    def validate_predicate(self, p):
        return nf.validate_predicate({k: v for k, v in p.items() if not k.startswith('_')})

    def answer_patient(self, stack, predicate, patient_id, *, as_of, ledger=None, allow_live=True):
        self.calls.append(predicate)
        if predicate['concept'].get('class') == 'response':
            if predicate['frame'] == 'care_process' and self.response:
                ev = {'record_id': 'N-1', 'date': '2027-12-25', 'note_type': 'office_followup', 'quote': self.response, 'status': 'documented',
                      'detail': {'kind': 'referral'}, 'p': 0.95}
                return {'status': 'documented', 'holds': True, 'detail': {}, 'p': 0.95, 'evidence': [{**ev, 'supports': True}],
                        'findings': [{'concept': self.response, 'status': 'documented', 'detail': {'kind': 'referral'}, 'evidence': [ev], 'p': 0.95}],
                        'undecided_findings': []}
            return {'status': 'not_mentioned', 'holds': False, 'detail': {}, 'p': None, 'evidence': [], 'findings': [], 'undecided_findings': []}
        evs = [e for g in self.findings for e in g['evidence']]
        return {'status': 'documented' if self.findings else 'not_mentioned', 'holds': bool(self.findings), 'detail': {}, 'p': 0.95,
                'evidence': [{**e, 'supports': True} for e in evs], 'findings': self.findings, 'undecided_findings': self.undecided}


def finding(name, quote, rid='N-1', date='2027-12-25', status='documented', detail=None):
    ev = {'record_id': rid, 'date': date, 'note_type': 'office_followup', 'quote': quote, 'status': status, 'detail': detail or {}, 'p': 0.95}
    return {'concept': name, 'concept_source': 'library', 'status': status, 'detail': detail or {}, 'p': 0.95, 'evidence': [ev],
            'first_date': date, 'last_date': date, 'n_records': 1}


class Rendering(unittest.TestCase):
    def render(self, preds, notes, **kw):
        return CN.render(None, preds, 'P-1', AS_OF, nf=notes, **kw)

    def test_multi_part_answers_the_finding_and_the_response(self):
        preds = CN.build('Anything in the notes about social or financial barriers? What was done about it?', AS_OF)
        notes = FakeOpenNotes([finding('food insecurity', 'Reports trouble affording groceries at times.')], response='SDOH: referred to community food resources;')
        out = self.render(preds, notes)
        h = out['headline']
        self.assertIn('food insecurity', h)
        self.assertIn('Reports trouble affording groceries at times.', h)
        self.assertIn('documented response', h)
        self.assertIn('referred to community food resources', h)
        self.assertNotRegex(h, r'^\d+ passages?\.?$')
        frames = [c['frame'] for c in notes.calls]
        self.assertEqual(frames, ['social_need', 'care_process', 'plan_followup'])
        resp = notes.calls[1]
        self.assertEqual(resp['window']['start'], '2027-12-25')
        self.assertEqual(resp['near_records'], ['N-1'])

    def test_no_finding_means_no_response_is_read(self):
        preds = CN.build('Anything in the notes about social or financial barriers? What was done about it?', AS_OF)
        notes = FakeOpenNotes([])
        out = self.render(preds, notes)
        self.assertIn('nothing documented', out['headline'])
        self.assertIn('no finding was established', out['headline'])
        self.assertEqual([c['frame'] for c in notes.calls], ['social_need'])

    def test_undecided_findings_are_labeled(self):
        preds = CN.build('What symptoms has the patient reported?', AS_OF)
        und = [{'concept': 'dizziness', 'concept_source': 'library', 'status': 'undecided', 'best_guess': 'documented',
                'evidence': [{'record_id': 'N-9', 'date': '2028-01-01', 'note_type': 'office_followup', 'quote': 'felt dizzy once', 'status': None}]}]
        out = self.render(preds, FakeOpenNotes([finding('cough', 'Reports cough.')], undecided=und))
        self.assertIn('undecided (not counted as findings): dizziness', out['headline'])
        self.assertEqual([r['status'] for r in out['table']], ['documented', 'undecided'])

    def test_yes_no_lead_is_kept(self):
        preds = CN.build('Is a fall mentioned anywhere in the notes?', AS_OF)
        out = self.render(preds, FakeOpenNotes(), yes_no=True)
        self.assertTrue(out['headline'].startswith(('Not documented', 'No', 'Undecided', 'Yes')))

    def test_focus_quote_cuts_to_the_concept_not_the_letterhead(self):
        pred = {'concept': {'term': 'chest x-ray'}, 'return_detail': ['result_text']}
        text = 'SYNTHETIC IMAGING CENTER ' + 'Phone (555) 0106. ' * 30 + 'EXAM: XRAY\nIMPRESSION: Possible early infiltrate.'
        self.assertIn('IMPRESSION: Possible early infiltrate.', CN.focus_quote(text, pred))


if __name__ == '__main__':
    unittest.main()
