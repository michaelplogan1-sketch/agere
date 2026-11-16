"""Incremental normalization: new-string detection, code-first preference, Opus-then-Jev protocol (agreement ->
dictionary, disagreement -> REVIEW.md), terminology-only payloads (asserted on request bodies), spend cap, and the
Entresto rule fix."""
import io, json, os, re, sqlite3, tempfile, unittest
from pathlib import Path

os.environ.setdefault('AGERE_ENV_FILE', '/nonexistent')
from tools.normalization import incremental as inc  # noqa: E402
from clinical_core.agere.clinical.drug_classes import classify  # noqa: E402

PATIENT_TOKENS = ('P-1001', 'P-2002', 'WHR-', 'SYN-', 'fc7e1b48', '2027-03-04', '987.5')


def make_warehouse(path):
    db = sqlite3.connect(path)
    for t in ('labs', 'vitals', 'conditions', 'medication_orders', 'medication_lists', 'encounters'):
        db.execute(f'CREATE TABLE {t}(record_id TEXT, patient_id TEXT, source_patient_id TEXT, event_date TEXT, facts TEXT)')
    rows = {
        'labs': [{'name': 'Hemoglobin A1c', 'unit': '%', 'value': 7.1, 'loinc': '4548-4'},
                 {'name': 'Cholesterol in HDL', 'unit': 'mg/dL', 'value': 987.5},
                 {'name': 'Known lab', 'unit': 'mg/dL', 'value': 3}, {'name': 'Known lab', 'unit': 'mmol/L', 'value': 3}],
        'vitals': [{'measurement': 'hr', 'unit': 'beats/min', 'value': 70}],
        'conditions': [{'description': 'Primary OA, R knee', 'icd10': 'M17.11'}],
        'medication_orders': [{'medication_name': 'prednisone', 'rxcui': '8640'}, {'medication_name': 'Mystery tablet'}],
        'medication_lists': [{'name': 'Known drug'}],
        'encounters': [{'encounter_id': 'WHE-1', 'type': 'diagnostic_testing'}, {'encounter_id': 'WHE-2', 'type': 'office'}],
    }
    for t, facts in rows.items():
        for i, f in enumerate(facts):
            db.execute(f'INSERT INTO {t} VALUES (?,?,?,?,?)', (f'WHR-{t}-{i}', 'P-1001' if i % 2 else 'P-2002', 'fc7e1b48', '2027-03-04', json.dumps(f)))
    db.commit(); db.close()


def make_dictionary(out):
    out.mkdir(parents=True)
    docs = {'labs': {'Known lab': {'variants_by_unit': {'mg/dL': {'agere_concept': None, 'confidence': 0.9}}}, 'hr': {'agere_concept': 'heart_rate', 'confidence': 0.9}},
            'medications': {'Known drug': {'drug_classes': [], 'confidence': 0.9},
                            'Ferrous sulfate': {'drug_classes': [], 'ingredients': ['ferrous sulfate'], 'confidence': 0.9, 'jev_choice': 'rules', 'source_count': 4}},
            'encounters': {'office': {'setting': 'primary_care', 'confidence': 0.9}},
            'conditions': {'Primary OA, R knee': {'icd10_category': 'M17', 'confidence': 0.9}}}
    for d, entries in docs.items():
        (out / f'{d}.json').write_text(json.dumps({'schema': 'agere-normalization/1', 'domain': d, 'entries': entries}))
    (out / 'REVIEW.md').write_text('# Normalization review list\n\n## labs\n- existing\n')


class FakeTransport:
    """Stands in for urllib: records every request body; answers Opus with mappings and Jev with typed choices."""
    def __init__(self, opus, jev_choice):
        self.opus, self.jev_choice, self.bodies, self.urls = opus, jev_choice, [], []

    def __call__(self, request, timeout=None):
        body = json.loads(request.data)
        self.bodies.append(body); self.urls.append(request.full_url)
        if 'anthropic' in request.full_url:
            raws = [x['raw'] for x in json.loads(re.search(r'INPUT\n(.*)$', body['messages'][0]['content'], re.S).group(1))]
            text = json.dumps({r: self.opus(r) for r in raws if self.opus(r) is not None})
            raw = {'model': body['model'], 'content': [{'type': 'text', 'text': text}], 'usage': {'input_tokens': 1500, 'output_tokens': 400}}
        else:
            answers = {}
            for key, q in body['questions'].items():
                choice = self.jev_choice(body['state'][key]['source_string'], list(q['criteria']))
                answers[key] = {'choice': choice, 'probabilities': {c: (0.9 if c == choice else 0.05) for c in q['criteria']}}
            raw = {'model': body['model'], 'answers': answers, 'usage': {'input_tokens': 300, 'output_tokens': 0}}
        return _Resp(json.dumps(raw).encode())


class _Resp(io.BytesIO):
    def __enter__(self): return self

    def __exit__(self, *a): self.close()


def opus_map(raw):
    if raw == 'Mystery tablet': return {'ingredients': ['mystery'], 'drug_classes': [], 'confidence': 0.9}
    if raw == 'Cholesterol in HDL': return {'loinc': '2085-9', 'component': 'Cholesterol in HDL', 'agere_concept': 'hdl', 'confidence': 0.95}
    if raw == 'diagnostic_testing': return {'setting': 'diagnostic', 'in_person': True, 'agere_concept': None, 'confidence': 0.9}
    if raw == 'Ferrous sulfate': return {'ingredients': ['ferrous sulfate'], 'drug_classes': ['iron_supplement'], 'confidence': 0.95}
    return None


def jev_pick(raw, options):
    if raw == 'Mystery tablet': return 'none'  # Jev disagrees with Opus
    return 'dictionary_and_rules' if 'dictionary_and_rules' in options else 'dictionary'


class Base(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(); t = Path(self.tmp.name)
        self.wh = t / 'warehouse.sqlite'; make_warehouse(self.wh)
        self.dict = t / 'normalization'; make_dictionary(self.dict)
        self._env = {k: os.environ.get(k) for k in ('ANTHROPIC_API_KEY', 'TYPESAFE_API_KEY')}
        os.environ['ANTHROPIC_API_KEY'] = 'test-anthropic'; os.environ['TYPESAFE_API_KEY'] = 'test-typesafe'

    def tearDown(self):
        for k, v in self._env.items():
            if v is None: os.environ.pop(k, None)
            else: os.environ[k] = v
        self.tmp.cleanup()

    def run_inc(self, transport, cap=5.0, remap=False, dry=False):
        t = Path(self.tmp.name)
        ledger = None if dry else inc.make_ledger(str(t / 'ledger.sqlite3'), cap)
        sent = []
        report = inc.run(self.wh, domains=['labs', 'medications', 'encounters', 'conditions'], name='test-release', out=self.dict,
                         dry_run=dry, ledger=ledger, receipts=t / 'receipts', transport=transport, cap=cap, remap=remap, sent=sent)
        return report, sent


class Detection(Base):
    def test_new_strings_and_code_first(self):
        report, _ = self.run_inc(None, dry=True)
        d = report['domains']
        self.assertEqual(d['labs']['to_map_list'], ['Cholesterol in HDL'])  # A1c carries LOINC -> code-resolved, not sent
        self.assertEqual([c['raw'] for c in d['labs']['code_resolved_sample']], ['Hemoglobin A1c'])
        self.assertEqual(d['labs']['code_resolved_sample'][0]['codes'], {'loinc': '4548-4'})
        self.assertEqual(d['labs']['new_unit_variants_of_known_labs'], [{'raw': 'Known lab', 'unit': 'mmol/L', 'n': 1}])
        self.assertEqual(d['medications']['to_map_list'], ['Mystery tablet'])
        self.assertEqual([c['raw'] for c in d['medications']['code_resolved_sample']], ['prednisone'])
        self.assertEqual(d['encounters']['to_map_list'], ['diagnostic_testing'])
        self.assertEqual(d['conditions']['new_strings'], 0)
        self.assertEqual(report['spend']['usd'], 0.0)

    def test_new_strings_unit(self):
        to_map, coded, _ = inc.new_strings('medications', [{'raw': 'A', 'n': 1, 'codes': {}}, {'raw': 'B', 'n': 2, 'codes': {}},
                                                           {'raw': 'C', 'n': 3, 'codes': {'rxcui': '1'}}, {'raw': 'C', 'n': 1, 'codes': {}}],
                                           {'A': {}})
        self.assertEqual([t['raw'] for t in to_map], ['B']); self.assertEqual(coded[0]['raw'], 'C'); self.assertEqual(coded[0]['n'], 4)


class Protocol(Base):
    def test_agreement_to_dictionary_disagreement_to_review(self):
        fake = FakeTransport(opus_map, jev_pick)
        report, _ = self.run_inc(fake)
        meds = json.loads((self.dict / 'medications.json').read_text())['entries']
        self.assertNotIn('Mystery tablet', meds)  # Jev chose none -> review, not dictionary
        labs = json.loads((self.dict / 'labs.json').read_text())['entries']
        self.assertEqual(labs['Cholesterol in HDL']['loinc'], '2085-9')
        self.assertEqual(labs['Cholesterol in HDL']['jev_choice'], 'dictionary')
        self.assertEqual(labs['Cholesterol in HDL']['source_release'], 'test-release')
        self.assertNotIn('Hemoglobin A1c', labs)  # code-resolved strings are never model-mapped
        enc = json.loads((self.dict / 'encounters.json').read_text())['entries']
        self.assertIn('diagnostic_testing', enc)
        review = (self.dict / 'REVIEW.md').read_text()
        self.assertIn('## Incremental mapping pending review (test-release)', review)
        self.assertIn('`Mystery tablet`', review); self.assertIn('Jev chose none', review)
        self.assertIn('## labs\n- existing', review)  # earlier sections preserved
        self.assertGreater(report['spend']['usd'], 0)
        # Re-running sends nothing new: accepted strings are dictionary keys now; the reviewed one replays from the ledger at $0
        n, usd = len(fake.bodies), report['spend']['usd']
        again, sent = self.run_inc(fake)
        self.assertEqual(len(fake.bodies), n)
        self.assertEqual([s['body']['model'] for s in sent], ['claude-opus-5-5', 'jev-1.13.0'])  # Mystery tablet only
        self.assertEqual(again['spend']['usd'], usd)
        self.assertEqual(again['spend']['calls'], {'opus': 0, 'jev': 0})

    def test_payloads_contain_only_terminology(self):
        fake = FakeTransport(opus_map, jev_pick)
        _, sent = self.run_inc(fake, remap=True)
        self.assertTrue(fake.bodies)
        allowed = {'Cholesterol in HDL', 'Mystery tablet', 'diagnostic_testing', 'Ferrous sulfate'}
        for body in fake.bodies:
            text = json.dumps(body)
            for token in PATIENT_TOKENS: self.assertNotIn(token, text)
            if body['model'].startswith('claude'):
                payload = json.loads(re.search(r'INPUT\n(.*)$', body['messages'][0]['content'], re.S).group(1))
                for item in payload:
                    self.assertLessEqual(set(item), {'raw', 'unit'}); self.assertIn(item['raw'], allowed)
            else:
                for key, state in body['state'].items():
                    if key.startswith('c'): self.assertLessEqual(set(state), {'source_string', 'unit'}); self.assertIn(state['source_string'], allowed)
        self.assertEqual(sum(1 for u in fake.urls if 'anthropic' in u), sum(1 for s in sent if s['provider'] == 'claude'))

    def test_remap_jev_omission(self):
        fake = FakeTransport(opus_map, jev_pick)
        report, _ = self.run_inc(fake, remap=True)
        self.assertEqual(report['remap_medications']['strings'], ['Ferrous sulfate'])
        e = json.loads((self.dict / 'medications.json').read_text())['entries']['Ferrous sulfate']
        self.assertEqual(e['drug_classes'], ['iron_supplement'])
        self.assertEqual(e['jev_choice'], 'dictionary_and_rules')  # Opus now agrees with the rules, Jev confirms
        self.assertEqual(e['previous_mapping']['drug_classes'], [])
        self.assertEqual(e['source_count'], 4)

    def test_cap_stops_before_spending(self):
        fake = FakeTransport(opus_map, jev_pick)
        report, _ = self.run_inc(fake, cap=0.05)
        self.assertIn('exceed', report['stopped'])
        self.assertEqual(fake.bodies, [])
        self.assertEqual(report['spend']['usd'], 0)


class EntrestoRule(unittest.TestCase):
    def test_entresto_is_arni_not_arb(self):
        self.assertEqual(classify('sacubitril 97 MG / valsartan 103 MG Oral Tablet [Entresto]'), ['arni'])
        self.assertEqual(classify('Sacubitril/valsartan'), ['arni'])
        self.assertEqual(classify('valsartan 160 mg tablet'), ['arb'])
        entries = json.loads((inc.OUT / 'medications.json').read_text())['entries']
        for raw, e in entries.items():
            if 'sacubitril' in raw.lower(): self.assertEqual(e['drug_classes'], ['arni'], raw)


if __name__ == '__main__':
    unittest.main()
