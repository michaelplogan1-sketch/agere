"""Wave-1 System One evidence components (providers mocked, temp stores, no models loaded).

evidence_packets, therapy_barrier_answers, external_evidence, system_one_deciders, system_one_student (+ decide() student
first), normalization_check, hcc_suspecting (+ analysis_graph registration), evidence_proposers.system_one_packet,
atomic_questions WHY wiring, semantic_student BioClinical provider.
"""
import json
import os
import sqlite3
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

os.environ.setdefault('AGERE_LOCAL_TYPED', 'off')
os.environ.setdefault('AGERE_LOCAL_NORMALIZER', 'off')

from clinical_core.agere.clinical import evidence_packets as EP
from clinical_core.agere.clinical import therapy_barrier_answers as TBA
from clinical_core.agere.clinical import external_evidence as EX
from clinical_core.agere.clinical import system_one_deciders as SD
from clinical_core.agere.clinical import system_one_student as ST
from clinical_core.agere.clinical import system_one as S1
from clinical_core.agere.clinical import normalization_check as NC
from clinical_core.agere.clinical import hcc_suspecting as HS

AS_OF = '2030-05-27'


def row(pid, rid, text, day='2030-01-01', table='notes', i='1'):
    return {'id': i, 'text': text, 'source': {'patient_id': pid, 'record_id': rid, 'field': 'text', 'start': 100, 'end': 100 + len(text),
                                               'event_date': day, 'source_table': table}}


def fake_decide(label_for, calls=None):
    def decide(question, options, evidence, *, as_of, order=None, threshold=0.8, task_key=None, ledger=None, allow_live=True):
        if calls is not None: calls.append({'q': question, 'ev': list(evidence), 'order': order, 'task_key': task_key})
        lab = label_for(question, evidence)
        probs = {k: (0.9 if k == lab else 0.1 / max(1, len(options) - 1)) for k in options}
        return {'label': lab, 'probabilities': probs, 'calibrated_probabilities': probs, 'evidence_ids': [evidence[0]['id']] if evidence else [],
                'decided_by': 'jev', 'cost_usd': 0.0002, 'steps': [{'provider': 'jev', 'status': 'answered', 'label': lab, 'cost_usd': 0.0002,
                                                                   'cache_hit': False, 'probabilities': probs}]}
    return decide


class Temp(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(); self.dir = Path(self.tmp.name)
        self.env = mock.patch.dict(os.environ, {'AGERE_SYSTEM_ONE_DECIDERS': str(self.dir/'deciders.json'),
                                                'AGERE_SYSTEM_ONE_STUDENTS': str(self.dir/'students'),
                                                'AGERE_SYSTEM_ONE_DISTILL': str(self.dir/'distill.sqlite3'),
                                                'AGERE_SYSTEM_ONE_CALIBRATION': str(self.dir/'cal.json'),
                                                'AGERE_LEDGER_PATH': str(self.dir/'ledger.sqlite3'),
                                                'AGERE_EXTERNAL_EVIDENCE_CACHE': str(self.dir/'external.json')})
        self.env.start()

    def tearDown(self):
        self.env.stop(); self.tmp.cleanup()


class Packets(unittest.TestCase):
    def test_anchored_bounded_offsets_dedupe_and_record_cap(self):
        rows = [row('P-1', 'r1', 'Seen today. Dapagliflozin stopped for yeast infection. BP fine. Empagliflozin discussed.', '2030-01-01', i='a'),
                row('P-1', 'r2', 'Dapagliflozin stopped for yeast infection.', '2030-03-01', i='b'),
                row('P-1', 'r3', 'Knee pain. Ice advised.', '2029-01-01', i='c')]
        out, stats = EP.select(rows, 'sglt2 inhibitor stopped', subject='sglt2_inhibitor', categories=['notes'], limit=3)
        texts = [r['text'] for r in out]
        self.assertEqual(texts.count('Dapagliflozin stopped for yeast infection.'), 1)  # copy-forward folded into the later copy
        self.assertEqual(next(r for r in out if r['text'].startswith('Dapagliflozin'))['source']['record_id'], 'r2')
        self.assertLessEqual(len(out), 3)
        first = next(r for r in out if r['source']['record_id'] == 'r1')
        a = rows[0]['text'].find(first['text'])
        self.assertEqual(first['source']['start'], 100 + a)
        self.assertTrue(stats['anchored_sentences'] >= 2)
        self.assertEqual([r['source'].get('event_date') for r in out], sorted(r['source'].get('event_date') for r in out))

    def test_unanchored_context_fills_budget_never_empty(self):
        out, stats = EP.select([row('P-1', 'r3', 'Knee pain. Ice advised.')], 'sglt2 stopped', subject='sglt2_inhibitor')
        self.assertEqual(len(out), 1); self.assertFalse(out[0]['anchored']); self.assertTrue(stats['fallback_unanchored'])

    def test_packet_heals_when_selection_fails(self):
        st = SimpleNamespace(path=Path('/nonexistent'), retriever=SimpleNamespace(search=lambda q, **k: {'evidence': [row('P-1', 'r1', 'Spironolactone held for K 5.9.')], 'method': 'fake'}))
        p = EP.packet(st, 'mra held', 'P-1', subject='mra')
        self.assertEqual(p['selection']['status'], 'failed'); self.assertEqual(len(p['evidence']), 1)


def semantic_db(path):
    db = sqlite3.connect(path/'semantic.sqlite3')
    db.executescript("""CREATE TABLE therapy_barrier(patient_id TEXT, drug_class TEXT, gdmt_pillar TEXT, drug_key TEXT, barrier_type TEXT, detail TEXT,
      event_date TEXT, evidence_text TEXT, record_id TEXT, source_type TEXT, method TEXT, decided_by TEXT, confidence REAL, negated INTEGER,
      hypothetical INTEGER, experiencer TEXT);
      CREATE TABLE therapy_barrier_candidate(case_id TEXT, patient_id TEXT, record_id TEXT, event_date TEXT, drug_class TEXT, drug_key TEXT,
      sentence TEXT, decided_by TEXT, tier INTEGER, rule TEXT, label TEXT);""")
    db.execute("INSERT INTO therapy_barrier VALUES ('P-1','sglt2_inhibitor','sglt2i','dapagliflozin','contraindication_lab','eGFR 18','2029-05-01',"
               "'eGFR 18; dapagliflozin stopped','r9','clinical_note','structured','rules',1,0,0,'patient')")
    db.execute("INSERT INTO therapy_barrier VALUES ('P-1','sglt2_inhibitor','sglt2i','dapagliflozin','intolerance_side_effect',NULL,'2029-06-01',"
               "'denies side effects','r8','clinical_note','rule','rules',1,1,0,'patient')")  # negated: not a fact
    for cid, s in (('c1', 'Jardiance not filled: copay too high.'), ('c2', 'Jardiance not filled: copay too high.'), ('c3', 'Monitor for yeast infection on Jardiance.')):
        db.execute("INSERT INTO therapy_barrier_candidate VALUES (?,?,?,?,?,?,?,?,?,?,?)", (cid, 'P-1', 'r7', '2030-02-01', 'sglt2_inhibitor', 'jardiance', s, 'pending_typed', 3, None, None))
    db.commit(); db.close()


class TherapyBarriers(Temp):
    def test_facts_pending_and_live_cascade(self):
        semantic_db(self.dir)
        st = SimpleNamespace(path=self.dir, allow_live=True)
        self.assertEqual([r['barrier_type'] for r in TBA.facts(st, ['P-1'], 'sglt2_inhibitor')['P-1']], ['contraindication_lab'])
        rows, total = TBA.pending(st, ['P-1'], 'sglt2i')
        self.assertEqual(total, 2)  # the copy-forward repeat is one decision
        calls = []
        ev, rec = TBA.evidence(st, ['P-1'], 'sglt2_inhibitor', as_of=AS_OF,
                               decide=fake_decide(lambda q, e: 'cost_access' if 'copay' in e[0]['text'] else 'no_barrier', calls))
        self.assertEqual(rec['read'], 2); self.assertEqual(rec['new_paid_api_calls'], 2)
        self.assertTrue(all(c['task_key'] == TBA.TASK_KEY for c in calls))
        texts = [e['text'] for e in ev['P-1']]
        self.assertTrue(any('contraindication lab' in t for t in texts)); self.assertTrue(any('cost access' in t for t in texts))
        self.assertFalse(any('yeast' in t for t in texts))


class External(Temp):
    RAW = {'output': [{'type': 'search_results', 'results': [{'id': 1, 'title': 'KDIGO 2024', 'url': 'https://kdigo.org/x'}]},
                      {'type': 'message', 'content': [{'type': 'output_text', 'text': '{"answer": "Do not start below eGFR 20 [1].", "criteria": ["eGFR < 20"], "source_ids": [1]}'}]}]}

    def test_scrub_and_need(self):
        self.assertNotIn('WHP-51EE88231854', EX.scrub('Is WHP-51EE88231854 contraindicated?'))
        self.assertNotIn('P-HF03', EX.scrub('For this patient only, does the submitted evidence establish: P-HF03 is eligible?'))
        self.assertTrue(EX.needs_external('Is there a documented contraindication to mra?'))
        self.assertFalse(EX.needs_external('Did the patient decline sglt2?'))
        self.assertTrue(EX.needs_external('Did the patient decline sglt2?', []))

    def test_parse_cited_offline_and_cached(self):
        r = EX.parse(self.RAW)
        self.assertEqual((r['status'], r['sources'][0]['url']), ('cited', 'https://kdigo.org/x'))
        self.assertEqual(EX.search('contraindications', allow_live=False)['status'], 'not_searched_offline')
        with mock.patch.dict(os.environ, {'PERPLEXITY_API_KEY': 'test'}), \
             mock.patch('clinical_core.agere.clinical.context_student.send', return_value=(r, {'cost_estimate': {'estimated_usd': 0.005}, 'request_hash': 'h'})) as send:
            got = EX.search('What are the contraindications to an SGLT2 inhibitor?', receipts=self.dir/'rec')
            self.assertEqual((got['status'], got['cost_usd']), ('cited', 0.005))
            again = EX.search('What are the contraindications to an SGLT2 inhibitor?')
            self.assertTrue(again['cached']); self.assertEqual(send.call_count, 1)
        item = EX.evidence_item(got, AS_OF)
        self.assertTrue(item['text'].startswith('[external reference, not patient data]')); self.assertEqual(item['note_id'], 'https://kdigo.org/x')


class Deciders(Temp):
    def test_order_for_reads_bakeoff_and_filters(self):
        self.assertEqual(SD.order_for('x'), ('jev', 'perplexity'))
        (self.dir/'deciders.json').write_text(json.dumps({'tasks': {'t': {'order': ['perplexity', 'jev']}}}))
        self.assertEqual(SD.order_for('t'), ('perplexity', 'jev'))
        self.assertEqual(SD.order_for('t', ['jev']), ('jev',))

    def test_bakeoff_picks_primary_writes_config_and_calibration(self):
        rows = SD.labeled('atomic_why_factor')

        def choice(q, opts, ev, *, as_of, provider, ledger=None, allow_live=True, task_key=None):
            lab = next(l for qq, o, e, l in rows if qq == q and e[0]['text'] == ev[0]['text'])
            good = provider == 'perplexity'
            top = lab if good else 'unknown'
            probs = {k: (0.85 if k == top else 0.15 / (len(opts) - 1)) for k in opts}
            return {'label': top, 'probabilities': probs, 'cost_usd': 0.0001, 'cache_hit': False}
        with mock.patch.object(S1, 'choice', choice):
            doc = SD.bakeoff(['atomic_why_factor'], candidates=('jev', 'perplexity'))
        e = doc['tasks']['atomic_why_factor']
        self.assertEqual((e['primary'], e['order']), ('perplexity', ['perplexity', 'jev']))
        self.assertEqual(SD.order_for('atomic_why_factor'), ('perplexity', 'jev'))
        cal = json.loads((self.dir/'cal.json').read_text())['tasks']['atomic_why_factor']
        self.assertEqual(cal['source']['provider'], 'perplexity')


class Student(Temp):
    @mock.patch.dict(os.environ, {'AGERE_SYSTEM_ONE_STUDENT': 'on'})
    def test_fit_predict_and_decide_uses_student_first(self):
        o = {'yes': 'documented', 'no': 'denied', 'unknown': 'not addressed'}
        things = ['cough', 'fever', 'rash', 'nausea', 'dizziness', 'fatigue', 'headache', 'edema', 'wheezing', 'palpitations']
        rows = [(f'Does the note document {t}?', o, [{'id': 'n', 'text': text.format(t=t)}], lab) for t in things
                for text, lab in (('Patient reports {t} for three days.', 'yes'), ('Patient denies any {t}.', 'no'), ('Here for a flu shot.', 'unknown'))]
        m = ST.fit('chart_assertion', gold=[ST.example(*r) for r in rows])
        self.assertTrue(m['decides']); self.assertIsNotNone(m['cross_fitted']['threshold'])
        q, o, ev, lab = 'Does the note document chills?', o, [{'id': 'n', 'text': 'Patient denies any chills.'}], 'no'
        got = ST.predict('chart_assertion', q, o, ev)
        self.assertEqual(got['label'], lab)
        with mock.patch.object(S1, 'choice', side_effect=AssertionError('paid provider called')):
            d = S1.decide(q, o, [{**e, 'date': AS_OF} for e in ev], as_of=AS_OF, task_key='chart_assertion')
        self.assertEqual((d['decided_by'], d['cost_usd']), ('local_student', 0.0))
        with mock.patch.dict(os.environ, {'AGERE_SYSTEM_ONE_STUDENT': 'off'}):
            self.assertIsNone(ST.predict('chart_assertion', q, o, ev))

    def test_paid_decision_is_recorded_as_teacher_label(self):
        probs = {'yes': 0.95, 'no': 0.03, 'unknown': 0.02}
        with mock.patch.object(S1, 'choice', return_value={'label': 'yes', 'probabilities': probs, 'calibrated_probabilities': probs,
                                                           'cost_usd': 0.0002, 'cache_hit': False, 'request_hash': 'h', 'evidence_ids': []}):
            d = S1.decide('q?', {'yes': 'y', 'no': 'n'}, [], as_of=AS_OF, task_key='novel_task')
        self.assertEqual(d['decided_by'], 'jev')
        self.assertEqual(len(ST.teacher_examples('novel_task')), 1)

    def test_healthcare_tokens(self):
        t = ST.healthcare_tokens('Jardiance held, K 5.9, eGFR 22; denies chest pain; mother had CHF')
        for tok in ('LAB_potassium_high', 'LAB_egfr_low', 'NEGCUE', 'FAMCUE'): self.assertIn(tok, t)


class Normalization(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(); d = Path(self.tmp.name)
        (d/'medications.json').write_text(json.dumps({'entries': {
            'Zocor 20 mg': {'drug_classes': ['statin'], 'jev_choice': 'dictionary_and_rules', 'jev_p': 0.97, 'source_count': 40},
            'Nystatin oral': {'drug_classes': ['statin'], 'jev_choice': 'none', 'jev_p': 0.93, 'source_count': 5},
            'Rosu new': {'drug_classes': ['statin'], 'source_count': 2}}}))
        (d/'labs.json').write_text(json.dumps({'entries': {}}))
        (d/'encounters.json').write_text(json.dumps({'entries': {'office': {'agere_concept': None, 'setting': 'primary_care', 'jev_choice': 'rules',
                                                                            'jev_p': 0.98, 'source_count': 7340}}}))
        self.p = mock.patch.object(NC, 'DIR', d); self.p.start(); NC._CACHE.clear()

    def tearDown(self):
        self.p.stop(); self.tmp.cleanup(); NC._CACHE.clear()

    def test_withheld_table_and_annotation(self):
        self.assertEqual(NC.withheld('medications'), {'Nystatin oral': 'rejected'})
        db = sqlite3.connect(':memory:')
        self.assertEqual(NC.build_table(db)['withheld'], 1)
        self.assertEqual(db.execute("SELECT status FROM normalization_check WHERE source_string='Rosu new'").fetchone()[0], 'unchecked')
        g = {'nodes': [{'operation': 'medication_state', 'parameters': {'subjects': ['statin']}}]}
        calls = []
        a = NC.annotate(g, allow_live=True, decide=fake_decide(lambda q, e: 'dictionary_and_rules', calls))
        self.assertEqual((a['source_strings'], a['agreed']), (3, 1))
        self.assertEqual(a['contested'][0]['source_string'], 'Nystatin oral')
        self.assertEqual(a['live'][0]['status'], 'agreed'); self.assertEqual(calls[0]['task_key'], NC.TASK_KEY)
        verdict = {'annotations': [], 'cost': {'new_paid_api_calls': 0, 'usd': 0.0}}
        answer = {}
        NC.annotate_verdict(SimpleNamespace(allow_live=False), g, answer, verdict)
        self.assertIn('Nystatin oral', verdict['annotations'][0]); self.assertIn('normalization_check', answer)
        self.assertIsNone(NC.annotate({'nodes': [{'operation': 'sql', 'parameters': {'query': 'SELECT 1'}}]}))

    def test_rule_preferred_encounter_is_reported_not_withheld(self):
        self.assertEqual(NC.withheld('encounters'), {})  # the dictionary's care setting still applies at build
        a = NC.annotate({'nodes': [{'operation': 'sql', 'parameters': {'query': "SELECT count(*) FROM visit WHERE type_concept='primary_care_visit'"}}]})
        c = a['contested'][0]
        self.assertEqual((c['source_string'], c['rule_mapping'], c['status']), ('office', ['primary_care_visit'], 'rules_preferred'))


class Hcc(Temp):
    PAT = {'patient_id': 'P-1', 'age': 74, 'sex': 'F', 'hccs': {'V28': [{'hcc': 38}]},
           'open_recapture_gaps': [{'hcc': 280, 'label': 'COPD', 'codes': ['J449'], 'raf_delta': 0.3,
                                    'last_supporting': {'date': '2028-04-01', 'kind': 'visit', 'ref': 'e1', 'icd10': 'J449', 'reason': 'face-to-face visit'}}],
           'suspects': [{'category': 'ckd_egfr', 'hcc': 328, 'hcc_label': 'CKD 3B', 'label': 'CKD', 'illustrative_icd10': 'N1832', 'evidence_rows': 2,
                         'raf_delta_if_documented': 0.1, 'evidence': [{'record_id': 'm1', 'date': '2029-03-01', 'value': 38, 'source': 'measurement', 'name': 'egfr'}]},
                        {'category': 'diabetes_a1c', 'hcc': 38, 'hcc_label': 'Diabetes', 'illustrative_icd10': 'E119', 'evidence_rows': 1, 'evidence': []}]}

    def stack(self):
        hits = [row('P-1', 'n1', 'Assessment: COPD stable on tiotropium. Denies heart failure. Atrial fibrillation on apixaban.', '2029-02-01')]
        return SimpleNamespace(path=self.dir, allow_live=True, retriever=SimpleNamespace(meta={'snapshot': {'as_of': AS_OF}},
                               search=lambda q, **k: {'evidence': hits}))

    def test_question_and_validate(self):
        self.assertTrue(HS.hcc_question('Which HCCs should we suspect for P-1?'))
        self.assertTrue(HS.hcc_question('List undocumented conditions missing an ICD-10 code'))
        self.assertFalse(HS.hcc_question('How many patients have diabetes?'))
        self.assertEqual(HS.validate({})['channels'], list(HS.CHANNELS))
        with self.assertRaises(ValueError): HS.validate({'top': 0})

    def test_retrieve_rank_confirm(self):
        from clinical_core.agere.clinical import atomic_questions as aq
        aq._MEMO.clear()
        calls = []
        with mock.patch.object(HS, 'structured', return_value={'patients': [self.PAT], 'version': 'risk-adjustment/1'}):
            r = HS.run(self.stack(), ['P-1'], {'top': 2}, AS_OF, decide=fake_decide(lambda q, e: 'yes' if 'Chronic Obstructive' in q or 'COPD' in q else 'unknown', calls))
        hccs = [x['hcc'] for x in r['rows']]
        self.assertNotIn(38, hccs)  # already coded
        self.assertEqual(hccs[0], 280)  # recapture + note corroboration ranks first
        top = r['rows'][0]
        self.assertEqual(set(top['channels']), {'recapture', 'notes'}); self.assertEqual(top['confirmation'], 'yes')
        self.assertIn(238, hccs)  # atrial fibrillation from the closed note table
        self.assertFalse(any('Heart Failure' in (x['label'] or '') for x in r['rows']))  # 'Denies heart failure' skipped
        self.assertEqual(len(calls), 2); self.assertTrue(all(c['task_key'] == HS.TASK_KEY for c in calls))
        a = HS.answer(r, {'coverage': 'supported_observation'}, 'Which HCCs should we suspect for P-1?')
        self.assertEqual((a['answer_type'], a['patient_ids']), ('table', ['P-1'])); self.assertTrue(a['sources'])

    def test_graph_registered(self):
        from clinical_core.agere.clinical.analysis_graph import validate
        from clinical_core.agere.clinical.graph_contracts import parameter_schema, TYPED
        from clinical_core.agere.clinical.graph_planner import CONTRACTS
        from clinical_core.agere.clinical.capability_registry import PRODUCT, answers
        from clinical_core.agere.clinical import proposers
        validate(HS.graph_of('Which HCCs should we suspect for P-1?', ['P-1'], {'top': 3}), AS_OF)
        self.assertIn('hcc_suspects', TYPED); self.assertIn('hcc_suspects', CONTRACTS); self.assertIn('hcc_suspects', PRODUCT)
        self.assertIn('count', answers('hcc_suspects')); self.assertIn('channels', parameter_schema('hcc_suspects', __import__('test_graph_contracts').catalog())['properties'])
        self.assertIn('hcc_suspecting', proposers.ORDER)


class Wiring(Temp):
    def test_patient_token_reads_letter_suffix_ids(self):
        from clinical_core.agere.clinical.evidence_proposers import PATIENT_TOKEN
        self.assertIn('P-HF03', [m.group() for m in PATIENT_TOKEN.finditer('Why is P-HF03 not on X?')])

    def test_system_one_packet_cites_external_and_uses_bakeoff_order(self):
        from clinical_core.agere.clinical.evidence_proposers import system_one_packet
        st = SimpleNamespace(retriever=SimpleNamespace(meta={'snapshot': {'as_of': AS_OF}}))
        calls = []
        ext = {'query': 'q', 'answer': 'Avoid below eGFR 20.', 'sources': [{'title': 'KDIGO', 'url': 'https://kdigo.org'}]}
        out = system_one_packet(st, 'Is dapagliflozin contraindicated for P-1?', [row('P-1', 'r1', 'eGFR 14 on dialysis.')], ext,
                                decide=fake_decide(lambda q, e: 'yes', calls))
        self.assertEqual((out['label'], out['new_paid_api_calls'], out['external_cited']), ('yes', 1, True))
        self.assertTrue(any(e['id'].startswith('external:') for e in calls[0]['ev'])); self.assertEqual(calls[0]['task_key'], 'atomic_yes_no')

    def test_atomic_why_carries_barriers_and_external(self):
        from clinical_core.agere.clinical import atomic_questions as aq
        import test_atomic_questions as T
        aq._MEMO.clear()
        st = T.Stack(['P-1007'])
        p = aq.plan(st, 'Why is P-1007 not on an SGLT2 inhibitor?')
        self.assertIn('therapy_barriers', p['scope']['sources']); self.assertIn('external', p['scope']['sources'])
        seen = []
        bar = {'id': 'barrier:P-1007:r9:cost_access', 'text': 'Therapy barrier: cost access', 'date': '2030-01-01', 'note_id': 'r9'}
        ext = {'query': 'q', 'answer': 'Contraindicated below eGFR 20.', 'sources': [{'title': 'KDIGO', 'url': 'https://kdigo.org'}], 'cost_usd': 0.004}
        with mock.patch.object(aq, 'medication_rows', return_value={}), \
             mock.patch.object(TBA, 'evidence', return_value=({'P-1007': [bar]}, {'new_paid_api_calls': 1, 'estimated_usd': 0.0002, 'read': 1})), \
             mock.patch.object(EX, 'search', return_value=ext):
            out = aq.execute(st, p, decide=fake_decide(lambda q, e: seen.append((q, [x['id'] for x in e])) or 'unknown'))
        cost = [ids for q, ids in seen if 'cost' in q][0]
        self.assertIn('barrier:P-1007:r9:cost_access', cost)
        contra = [ids for q, ids in seen if 'contraindication' in q][0]
        self.assertTrue(any(i.startswith('external:') for i in contra))
        rep = out['report']
        self.assertEqual(rep['new_paid_api_calls'], len(seen) + 1 + 2)  # decisions + live barrier read + two external searches
        self.assertEqual(len(rep['external_evidence']), 2)  # contraindication and not-indicated reference questions
        self.assertTrue(rep['evidence_packets'])

    def test_bioclinical_student_is_a_local_reader(self):
        from clinical_core.agere.clinical import semantic_student, evidence_proposers as E
        self.assertEqual(semantic_student.PROVIDERS['student_bioclinical'], 'bioclinical')
        self.assertLess(E.LOCAL_READERS.index('student_minilm'), E.LOCAL_READERS.index('student_bioclinical'))
        self.assertEqual(E.READER_COMPONENTS['student_bioclinical'], 'bioclinical_modernbert_student')


if __name__ == '__main__':
    unittest.main()
