"""Terminology mapping: deterministic exact matches, retrieval, System One adjudication (mocked), saved mappings, the
grammar consuming mapped labels, the cascade tier and the terminology_map foundation engine."""
import json
import re
import sqlite3
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace

from clinical_core.agere.clinical import terminology as T, terminology_engine as TE
from clinical_core.agere.clinical.question_input import QuestionNotUnderstood

ACTIVE = Path(json.loads(Path('config/agere/active_warehouse.json').read_text())['build']) / 'stack'


def decider(label, p, calls, narrow=None):
    """A fake System One. The term-mapping choice answers `label` at `p`; the granularity choice (task terminology_narrow) answers 'all'
    (the term means the whole concept) unless `narrow` is given as (label, p); its prompts go to fake.narrow, not `calls`."""
    def fake(question, options, evidence, *, as_of, threshold, task_key, ledger, allow_live):
        if task_key == 'terminology_narrow':
            nl, np_ = narrow or ('all', 0.95)
            fake.narrow.append({'question': question, 'options': dict(options), 'threshold': threshold, 'allow_live': allow_live})
            return {'label': nl, 'probabilities': {nl: np_}, 'calibrated_probabilities': {nl: np_}, 'decided_by': 'jev' if np_ >= threshold else None,
                    'cost_usd': 0.0002, 'request_hash': 'n1', 'steps': [{'provider': 'jev', 'status': 'answered', 'cache_hit': False, 'cost_usd': 0.0002}]}
        calls.append({'question': question, 'options': dict(options), 'task_key': task_key, 'allow_live': allow_live})
        return {'label': label, 'probabilities': {label: p}, 'calibrated_probabilities': {label: p}, 'decided_by': 'jev' if p >= threshold else None,
                'cost_usd': 0.0002, 'request_hash': 'h1', 'steps': [{'provider': 'jev', 'status': 'answered', 'cache_hit': False, 'cost_usd': 0.0002}]}
    fake.narrow = []
    return fake


class Deterministic(unittest.TestCase):
    def test_exact_matches(self):
        cases = {'CHF': ('condition', 'heart_failure'), 'T2DM': ('condition', 'type_2_diabetes'),
                 'high blood pressure': ('condition', 'hypertension'), 'Chronic congestive heart failure': ('condition', 'heart_failure'),
                 'Entresto': ('medication', 'arni'), 'lisinopril': ('medication', 'ace_inhibitor'),
                 'sacubitril/valsartan': ('medication', 'arni'), 'glycated hgb': ('lab', 'hba1c'), 'HbA1c': ('lab', 'hba1c')}
        for label, (kind, concept) in cases.items():
            got = T.deterministic(label)
            self.assertEqual((got['kind'], got['concept']), (kind, concept), label)

    def test_never_swallows_extra_words(self):
        self.assertIsNone(T.deterministic('heart failure readmissions'))
        self.assertIsNone(T.deterministic('family history of heart failure'))
        self.assertIsNone(T.deterministic('patients'))

    def test_codes(self):
        self.assertIn('I50', T.codes('condition', 'heart_failure')['codes'])
        self.assertIn('4548-4', T.codes('lab', 'hba1c')['codes'])

    def test_retrieval_ranks_the_right_concept(self):
        top = T.retrieve('congestive cardiac failure')
        self.assertEqual(top[0]['concept'], 'heart_failure')
        self.assertLessEqual(len(top), T.MAX_OPTIONS)


class Adjudication(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.TemporaryDirectory(); self.store = Path(self.dir.name) / T.STORE_NAME

    def tearDown(self): self.dir.cleanup()

    def test_system_one_accepts_and_saves_then_free(self):
        calls = []
        r = T.map_label('congestive cardiac failure', store=self.store, question='How many patients have congestive cardiac failure?',
                        as_of='2030-05-27', decider=decider('condition__heart_failure', 0.93, calls))
        self.assertEqual((r['method'], r['kind'], r['concept']), ('system_one', 'condition', 'heart_failure'))
        self.assertIn('none', calls[0]['options']); self.assertEqual(calls[0]['task_key'], 'terminology_map')
        self.assertEqual(r['calls'], 2); self.assertAlmostEqual(r['cost_usd'], 0.0004)  # the mapping choice + the granularity choice
        again = T.map_label('Congestive  cardiac failure', store=self.store, decider=decider('none', 0.99, calls))
        self.assertEqual((again['method'], again['concept'], len(calls)), ('saved_system_one', 'heart_failure', 1))
        self.assertEqual(T.grammar_terms('Who has congestive cardiac failure?', self.store)['condition'],
                         {'congestive cardiac failure': 'heart_failure'})

    def test_abstains_below_threshold_or_none(self):
        for label, p in (('condition__heart_failure', 0.6), ('none', 0.97)):
            r = T.map_label('congestive cardiac failure', store=self.store, decider=decider(label, p, []))
            self.assertEqual((r['method'], r['concept']), ('abstained', None))
        self.assertFalse(self.store.exists() and T.saved(self.store))

    def test_no_candidates_no_call(self):
        calls = []
        r = T.map_label('zzqx', store=self.store, decider=decider('none', 0.9, calls))
        self.assertEqual((r['method'], calls), ('no_candidates', []))


class Tier(unittest.TestCase):
    def test_maps_unconsumed_term_and_recompiles_original(self):
        with tempfile.TemporaryDirectory() as d:
            catalog = SimpleNamespace(target=str(Path(d) / 'semantic.sqlite3'))
            q = 'How many patients have congestive cardiac failure?'

            def interpret(cat, question, population=None):
                terms = T.grammar_terms(question, T.store_path(Path(cat.target).parent))
                if 'congestive cardiac failure' not in terms['condition']:
                    raise QuestionNotUnderstood('needs clarification: Unconsumed request: have congestive cardiac failure. No language model was called')
                return {'kind': 'request', 'question': question}, {'interpreter': 'compositional_grammar', 'attempts': []}
            stack = SimpleNamespace(allow_live=True, retriever=SimpleNamespace(meta={'snapshot': {'as_of': '2030-05-27'}}))
            contract, interp = T.tier(stack, catalog, q, decider=decider('condition__heart_failure', 0.95, []), interpret=interpret)
            self.assertEqual(contract['question'], q)
            self.assertEqual(interp['interpreter'], 'terminology_mapping')
            self.assertEqual(interp['terminology'][0]['concept'], 'heart_failure')
            self.assertEqual(interp['terminology_usage']['new_paid_api_calls'], 2)  # mapping + granularity
            with self.assertRaises(QuestionNotUnderstood):
                T.tier(stack, catalog, 'How many patients have zzqx blorp?', decider=decider('none', 0.9, []),
                       interpret=lambda c, q, population=None: (_ for _ in ()).throw(QuestionNotUnderstood('Unconsumed request: have zzqx blorp')))

    def test_cascade_registers_the_tier(self):
        from clinical_core.agere.clinical import proposers
        self.assertIn('terminology_mapping', proposers.TIERS)
        self.assertNotIn('terminology_mapping', proposers.PAID)  # saved/deterministic mappings work offline
        self.assertTrue(hasattr(proposers.Cascade, 'tier_terminology_mapping'))


class Engine(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.TemporaryDirectory()
        db = sqlite3.connect(Path(self.dir.name) / 'semantic.sqlite3')
        db.execute('CREATE TABLE measurement(patient_id, name, loinc, concept)')
        db.executemany('INSERT INTO measurement VALUES (?,?,?,?)', [('P1', 'HbA1c', '4548-4', 'hba1c'), ('P2', 'glycated hgb', None, None),
                                                                   ('P3', 'Hematocrit', '4544-3', None), ('P3', 'HbA1c', '4548-4', 'hba1c')])
        db.commit(); db.close()

    def tearDown(self): self.dir.cleanup()

    def test_code_and_status_questions(self):
        g = TE.local_question('Which lab names in our data map to LOINC 4548-4?')
        rep = TE.execute(self.dir.name, None, g['nodes'][0]['parameters'], '2030-05-27')
        self.assertEqual({r['label']: r['status'] for r in rep['rows']}, {'HbA1c': 'mapped', 'glycated hgb': 'recoverable'})
        g = TE.local_question('Which source lab labels are unmapped?')
        rep = TE.execute(self.dir.name, None, g['nodes'][0]['parameters'], '2030-05-27')
        self.assertEqual({r['label']: r['status'] for r in rep['rows']}, {'glycated hgb': 'recoverable', 'Hematocrit': 'unmapped'})
        a = TE.answer(rep, g, 'q'); self.assertEqual(a['count'], 2)
        self.assertIsNone(TE.local_question('How many patients have diabetes?'))

    def test_registered_on_the_menu(self):
        from clinical_core.agere.clinical import analysis_graph, capability_registry, graph_planner
        self.assertIn('terminology_map', analysis_graph.OPERATIONS)
        self.assertIn('terminology_map', capability_registry.PRODUCT)
        self.assertIn('terminology_map', graph_planner.CONTRACTS)


def fixture_stack(path):
    """A small received-warehouse stack: patients with opioid (F11), alcohol (F10) and cannabis (F12) diagnoses from EHR condition
    records and payer-claim diagnoses. No terminology-map store yet."""
    path = Path(path)
    db = sqlite3.connect(path / 'semantic.sqlite3')
    db.execute('CREATE TABLE patient(patient_id TEXT)')
    db.executemany('INSERT INTO patient VALUES (?)', [(f'P{i}',) for i in range(1, 7)])
    db.execute('CREATE TABLE condition(record_id TEXT, patient_id TEXT, concept TEXT, label TEXT, icd10 TEXT, normalized_label TEXT, '
               'is_diagnosis INTEGER, event_date TEXT, diagnosis_basis TEXT)')
    sud = 'substance_use_disorder'
    db.executemany('INSERT INTO condition VALUES (?,?,?,?,?,?,?,?,?)', [
        ('r1', 'P1', sud, 'Opioid use disorder, moderate', 'F11.20', 'Opioid use disorder, moderate (opioid dependence)', 1, '2029-01-05', None),
        ('r2', 'P2', sud, 'Alcoholism', 'F10.20', 'Alcohol dependence', 1, '2029-02-05', None),
        ('r3', 'P5', sud, 'Alcoholism', 'F10.20', 'Alcohol dependence', 1, '2029-03-05', None),
        ('r4', 'P6', sud, 'AUD, severe', 'F10.20', 'Alcohol use disorder, severe (alcohol dependence)', 1, '2029-03-06', None)])
    db.execute('CREATE TABLE payer_claim_diagnosis(payer_claim_id TEXT, patient_id TEXT, service_from TEXT, rank INTEGER, icd10 TEXT, '
               'icd10_received TEXT, counts_in_metrics INTEGER)')
    db.executemany('INSERT INTO payer_claim_diagnosis VALUES (?,?,?,?,?,?,?)', [
        ('c1', 'P3', '2029-04-01', 1, 'F11.10', 'F11.10', 1), ('c2', 'P4', '2029-04-02', 1, 'F12.20', 'F12.20', 1)])
    db.commit(); db.close()
    return path


def fixture_count(path, query):
    db = sqlite3.connect(Path(path) / 'semantic.sqlite3')
    try: return db.execute(query).fetchone()[0]
    finally: db.close()


def tier_stack(live=True):
    return SimpleNamespace(allow_live=live, retriever=SimpleNamespace(meta={'snapshot': {'as_of': '2030-05-27'}}))


class Granularity(unittest.TestCase):
    """A subtype term mapped to a broader concept is narrowed to the categories it names (opioid use disorder -> F11), or kept broad
    and disclosed when System One is not confident."""
    Q = 'How many patients have opioid use disorder?'
    MAPPED = 'condition__substance_use_disorder'

    def setUp(self):
        self.dir = tempfile.TemporaryDirectory()
        self.stack = fixture_stack(self.dir.name)
        from clinical_core.agere.clinical import request_plan
        self.catalog = request_plan.Catalog.load(self.stack)
        self.store = T.store_path(self.stack)

    def tearDown(self): self.dir.cleanup()

    def run_tier(self, narrow, question=None):
        fake = decider(self.MAPPED, 0.95, [], narrow)
        contract, interp = T.tier(tier_stack(), self.catalog, question or self.Q, decider=fake)
        return contract, interp, fake

    def test_category_titles_come_from_the_received_descriptions(self):
        titles = T.category_titles(self.stack)
        self.assertEqual((titles['F11'], titles['F10']), ('Opioid use disorder', 'Alcohol dependence'))
        self.assertNotIn('F13', titles)  # nothing in the data describes it
        self.assertEqual(T.category_titles(self.store), titles)  # the mapping store path finds the same warehouse

    def test_opioid_use_disorder_narrows_to_f11_and_the_sql_filters_it(self):
        contract, interp, fake = self.run_tier(('F11', 0.93))
        entry = interp['terminology'][0]
        self.assertEqual((entry['concept'], entry['granularity'], entry['codes']['codes']), ('substance_use_disorder', 'narrowed', ['F11']))
        self.assertEqual(entry['narrowing']['categories'], ['F11'])
        self.assertAlmostEqual(entry['narrowing']['probability'], 0.93)
        opts = fake.narrow[0]['options']  # each category labelled with its data-derived title, plus all / several
        self.assertEqual((opts['F11'], opts['F10'], opts['F13']), ('F11 — Opioid use disorder', 'F10 — Alcohol dependence', 'F13'))
        self.assertIn('all', opts); self.assertIn('several', opts)
        self.assertEqual(fake.narrow[0]['threshold'], T.NARROW_P)
        query = contract['query']  # the narrowed codes reach the executed plan
        self.assertIn("icd10 LIKE 'F11%'", query)
        self.assertNotIn("'F10%'", query); self.assertNotIn("'F12%'", query)
        self.assertEqual(fixture_count(self.stack, query), 2)  # P1 (EHR) + P3 (claim); not the alcohol / cannabis patients
        self.assertEqual(interp['terminology_usage']['new_paid_api_calls'], 2)

    def test_substance_use_disorder_stays_the_whole_concept(self):
        fake = decider(self.MAPPED, 0.95, [], ('F11', 0.99))
        r = T.map_label('substance use disorder', store=self.store, decider=fake)  # the concept's own name: nothing to narrow
        self.assertEqual((r['concept'], r['granularity'], r['calls']), ('substance_use_disorder', 'concept', 1))  # the mapping call only
        self.assertEqual(fake.narrow, [])
        self.assertEqual(r['codes']['codes'], ['F10', 'F11', 'F12', 'F13', 'F14', 'F15', 'F16', 'F18', 'F19'])
        from clinical_core.agere.clinical import shared_planner
        contract, interp = shared_planner._interpret(self.catalog, 'How many patients have substance use disorder?')
        self.assertIn("icd10 LIKE 'F12%'", contract['query'])
        self.assertEqual(fixture_count(self.stack, contract['query']), 6)

    def test_all_of_them_keeps_the_concept_and_is_saved(self):
        contract, interp, fake = self.run_tier(('all', 0.95))
        self.assertEqual(interp['terminology'][0]['granularity'], 'whole')
        self.assertIn("concept IN ('substance_use_disorder')", contract['query'])
        self.assertEqual(T.saved(self.store, 'opioid use disorder')['narrowing']['granularity'], 'whole')

    def test_unconfident_pick_keeps_the_concept_and_marks_it_unverified(self):
        contract, interp, fake = self.run_tier(('F11', 0.6))
        entry = interp['terminology'][0]
        self.assertEqual((entry['concept'], entry['granularity']), ('substance_use_disorder', 'unverified'))
        self.assertIn("concept IN ('substance_use_disorder')", contract['query'])  # healed, not blocked: the broad plan still answers
        self.assertIsNone(T.saved(self.store, 'opioid use disorder')['narrowing'])  # not saved: asked again (a cache hit) next time
        note = T.granularity_note(entry)
        self.assertIn('opioid use disorder', note); self.assertIn('could not be confirmed', note)
        self.assertIsNone(T.granularity_note({**entry, 'granularity': 'narrowed'}))
        free = T.grammar_terms(self.Q, self.store)['used'][0]
        self.assertEqual((free['granularity'], free['narrowing']['reason']), ('unverified', 'granularity not assessed'))

    def test_saved_narrowing_is_reused_without_a_call(self):
        self.run_tier(('F11', 0.93))
        boom = decider('none', 0.99, [], ('all', 0.99))
        again = T.map_label('opioid use disorder', 'condition', store=self.store, decider=boom)
        self.assertEqual((again['method'], again['granularity'], again['codes']['codes'], again['calls']), ('saved_system_one', 'narrowed', ['F11'], 0))
        self.assertEqual(boom.narrow, [])
        self.assertEqual(T.grammar_terms(self.Q, self.store)['condition']['opioid use disorder'], 'substance_use_disorder@F11')
        from clinical_core.agere.clinical import shared_planner
        self.assertIsNone(T.tier(tier_stack(), self.catalog, self.Q, decider=boom))  # nothing left for the tier to do
        contract, interp = shared_planner._interpret(self.catalog, self.Q)  # the free grammar's own plan filters F11
        self.assertIn("icd10 LIKE 'F11%'", contract['query'])
        self.assertEqual(interp['terminology'][0]['granularity'], 'narrowed')
        self.assertEqual(boom.narrow, [])

    def test_legacy_saved_mapping_is_narrowed_by_the_tier(self):
        db = sqlite3.connect(self.store)  # saved before granularity existed: no narrowing column
        db.execute('CREATE TABLE mapping(label TEXT, kind TEXT, concept TEXT, classes TEXT, method TEXT, provider TEXT, probability REAL, '
                   'cost_usd REAL, request_hash TEXT, question TEXT, candidates TEXT, created TEXT, version TEXT, PRIMARY KEY(label, kind))')
        db.execute("INSERT INTO mapping VALUES ('opioid use disorder','condition','substance_use_disorder',NULL,'system_one','jev',0.94,0,'h',NULL,'[]','2026-10-22','terminology-map/1')")
        db.commit(); db.close()
        from clinical_core.agere.clinical import shared_planner
        contract, interp = shared_planner._interpret(self.catalog, self.Q)  # the free grammar reads it broad and says so
        self.assertEqual(interp['terminology'][0]['granularity'], 'unverified')
        self.assertIn("'F10%'", contract['query'])
        fake = decider('none', 0.99, [], ('F11', 0.9))
        contract, interp = T.tier(tier_stack(), self.catalog, self.Q, decider=fake)
        self.assertEqual(interp['terminology'][0]['granularity'], 'narrowed')
        self.assertIn("icd10 LIKE 'F11%'", contract['query']); self.assertNotIn("'F10%'", contract['query'])
        self.assertEqual(T.saved(self.store, 'opioid use disorder')['narrowing']['codes'], ['F11'])
        self.assertEqual(len(fake.narrow), 1)

    def test_several_categories_are_asked_one_by_one(self):
        seen = []

        def fake(question, options, evidence, *, as_of, threshold, task_key, ledger, allow_live):
            if 'Does the term name' in question: lab = 'yes' if re.search(r'category F1[12]\b', question) else 'no'
            elif task_key == 'terminology_narrow': lab = 'several'
            else: lab = self.MAPPED
            seen.append(lab)
            return {'label': lab, 'probabilities': {lab: 0.95}, 'calibrated_probabilities': {lab: 0.95}, 'decided_by': 'jev', 'cost_usd': 0.0002,
                    'request_hash': 'x', 'steps': [{'status': 'answered', 'cache_hit': False}]}
        r = T.map_label('opioid and cannabis use disorder', store=self.store, decider=fake)
        self.assertEqual((r['granularity'], r['codes']['codes']), ('narrowed', ['F11', 'F12']))
        self.assertEqual((seen.count('yes'), seen.count('no')), (2, 7))

    def test_without_a_live_provider_the_concept_stays_and_is_disclosed(self):
        r = T.map_label('opioid use disorder', store=self.store, decider=decider(self.MAPPED, 0.95, [], ('F11', 0.2)))
        self.assertEqual(r['granularity'], 'unverified')
        r = T.map_label('opioid use disorder', 'condition', store=self.store, adjudicate_with=False)  # the review engine never calls System One
        self.assertEqual((r['granularity'], r['narrowing']['reason']), ('unverified', 'granularity not assessed'))

    def test_scoped_names_are_validated_against_the_concepts_own_codes(self):
        from clinical_core.agere.clinical import concepts
        self.assertTrue(concepts.valid_scope('substance_use_disorder@F11'))
        self.assertTrue(concepts.valid_scope('chronic_kidney_disease@E11.2+N18'))
        self.assertFalse(concepts.valid_scope('substance_use_disorder@I50'))
        self.assertFalse(concepts.valid_scope("substance_use_disorder@F11'; DROP TABLE x"))
        self.assertEqual(concepts.split_scope('a@F11+F12'), ('a', ('F11', 'F12')))

    def test_a_stale_saved_plan_is_detected(self):
        self.run_tier(('F11', 0.6))
        before = T.grammar_terms(self.Q, self.store)['used']
        self.assertFalse(T.stale(self.store, before))
        T.save_narrowing(self.store, 'opioid use disorder', 'condition', {'granularity': 'narrowed', 'codes': ['F11'], 'categories': ['F11']})
        self.assertTrue(T.stale(self.store, before))


@unittest.skipUnless((ACTIVE / 'semantic.sqlite3').exists(), 'active warehouse not present')
class GrammarOnActiveWarehouse(unittest.TestCase):
    def test_grammar_compiles_messy_labels_to_queried_codes(self):
        from clinical_core.agere.clinical import request_plan, shared_planner
        cat = request_plan.Catalog.load(ACTIVE)
        for q, needle in (('How many patients have CHF?', "concept IN ('heart_failure')"), ('How many patients are on Entresto?', "drug_class IN ('arni')"),
                          ('How many patients had a glycated hemoglobin test?', "\"concept\" = 'hba1c'")):
            contract, interp = shared_planner._interpret(cat, q)
            self.assertIn(needle, contract['query'], q)
            self.assertTrue(interp['terminology'], q)


if __name__ == '__main__':
    unittest.main()
