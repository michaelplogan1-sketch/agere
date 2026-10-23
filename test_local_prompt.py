"""The local planner prompt fits the small model's budget: only this question's tables, vocabulary and rule parts; a repair adds
only the missing fragment. Fake models only (no MLX, no weights)."""
import json
import os
import sqlite3
import tempfile
import unittest
from pathlib import Path
from unittest import mock

os.environ.setdefault('AGERE_LOCAL_TYPED', 'off')
os.environ.setdefault('AGERE_LOCAL_NORMALIZER', 'off')

from clinical_core.agere.clinical import local_prompt as LP, local_request_worker as W, request_plan as R, shared_planner as SP  # noqa: E402

DIABETES = 'How many patients have type 2 diabetes?'
CKD = ('How many patients have chronic kidney disease stage 3 or worse? Definition: Persons with a documented N18.3-N18.6 code '
       'on a paid claim, a problem-list entry or an encounter diagnosis')
BUDGET = 8000
MODEL_DIR = next((p / 'models' / 'qwen3-4b' for p in Path(__file__).resolve().parents if (p / 'models' / 'qwen3-4b' / 'tokenizer.json').exists()), None)


def warehouse(path):
    with sqlite3.connect(Path(path) / 'semantic.sqlite3') as db:
        db.executescript('''
            CREATE TABLE meta(key,value);
            INSERT INTO meta VALUES('version','fixture'),('source_digest','local-prompt-1'),('as_of','2030-05-27');
            CREATE TABLE patient(patient_id,birth_date,sex_reported,coverage_type,payer_id);
            CREATE TABLE condition(record_id,patient_id,concept,label,icd10,is_diagnosis,event_date);
            INSERT INTO condition VALUES('r1','A','diabetes','Type 2 diabetes','E11.9',1,'2029-01-01');
            CREATE TABLE problem_list_entry(entry_id,patient_id,label,icd10,status);
            CREATE TABLE claim(claim_id,patient_id,payer,service_date,allowed_cents,ever_denied,icd10);
            CREATE TABLE adt_visit(visit_id,patient_id,admit_date,discharge_date,unit);
            CREATE TABLE appointment(patient_id,scheduled_date,kept,final_status);
            CREATE TABLE prescription(rx_id,patient_id,drug,fill_date,days_supply);
            CREATE TABLE incident_diagnosis(patient_id,icd10,first_date);
        ''')
    return R.Catalog.load(Path(path))


class Recorder:
    """Scripted local model: records every (system, user); replies in order, then unparseable text."""
    hosted = False
    identity = 'fake-local'

    def __init__(self, replies=()):
        self.replies, self.seen = list(replies), []

    def __call__(self, system, user, **kw):
        self.seen.append((system, user))
        return (json.dumps(self.replies.pop(0)) if self.replies else 'not json'), {'seconds': 0, 'model': 'fake'}


class Shaped(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(); self.addCleanup(self.tmp.cleanup)
        self.catalog = warehouse(self.tmp.name)
        self.count = LP.counter(MODEL_DIR or '/nonexistent')  # the model's tokenizer file when present, else the conservative estimate

    def run_model(self, question, replies=(), **kw):
        model = Recorder(replies)
        with self.assertRaises(SP.QuestionNotUnderstood):
            SP.interpret(self.catalog, question, model=model, force_model=True, use_memory=False, prompt_budget=BUDGET, **kw)
        return model

    def tokens(self, prompt):
        return self.count(prompt[0]) + self.count(prompt[1]) + LP.TEMPLATE_TOKENS

    def tables(self, prompt):
        return set(json.loads(prompt[1])['catalog']['selected_schemas'])

    def test_diabetes_fits_and_keeps_concept_vocabulary(self):
        full = Recorder()
        with self.assertRaises(SP.QuestionNotUnderstood):
            SP.interpret(self.catalog, DIABETES, model=full, force_model=True, use_memory=False)  # hosted shape: no budget
        self.assertGreater(self.tokens(full.seen[0]), 3 * BUDGET)  # the old prompt: whole catalog
        first = self.run_model(DIABETES).seen[0]
        self.assertLessEqual(self.tokens(first), BUDGET)
        catalog = json.loads(first[1])['catalog']
        concepts = catalog['named_concepts']
        self.assertIn('type_2_diabetes', concepts['condition']); self.assertIn('diabetes', concepts['condition_codes'])
        self.assertIn('E11', concepts['condition_codes']['type_2_diabetes']['icd10'])
        self.assertNotIn('definition_digest', json.dumps(concepts))  # concept digests are hosted-only bulk (the compiler never reads them)
        self.assertLess(first[1].count('definition_digest'), 8)  # only the few measures the question matches carry theirs
        self.assertIn('condition', self.tables(first))
        self.assertFalse(self.tables(first) & {'adt_visit', 'appointment'}, self.tables(first))  # unrelated tables stay out
        self.assertIn('Preserve EVERY predicate', first[0])  # the core grammar text is the same text
        self.assertNotIn('GENERAL ANALYSIS', first[0])

    def test_definition_heavy_question_fits_with_code_vocabulary(self):
        first = self.run_model(CKD).seen[0]
        self.assertLessEqual(self.tokens(first), BUDGET)
        catalog = json.loads(first[1])['catalog']
        self.assertIn('N18', catalog['named_concepts']['condition_codes']['chronic_kidney_disease']['icd10'])
        self.assertIn(CKD, first[1].replace('\\u2011', '-'))  # the whole definition travels as the question
        self.assertTrue({'claim', 'problem_list_entry'} <= self.tables(first), self.tables(first))  # tables the definition names
        self.assertNotIn('appointment', self.tables(first))

    def test_repair_sends_only_the_missing_fragment(self):
        model = self.run_model(DIABETES, [{'need_tables': ['appointment']}, {'need_rules': ['analysis'], 'need_concepts': ['copd']}])
        first, tables, rules = model.seen[0], model.seen[1], model.seen[2]
        self.assertEqual(self.tables(tables) - self.tables(first), {'appointment'})  # one table added, nothing resent wholesale
        self.assertLessEqual(self.tokens(tables), BUDGET)
        self.assertNotIn('GENERAL ANALYSIS', tables[0]); self.assertIn('GENERAL ANALYSIS', rules[0])
        self.assertIn('copd', json.loads(rules[1])['catalog']['named_concepts']['condition_codes'])
        self.assertIn('appointment', self.tables(rules))  # an asked-for table stays
        self.assertLessEqual(self.tokens(rules), BUDGET + 200)
        for prompt in model.seen: self.assertLess(prompt[1].count('definition_digest'), 8)  # never the full catalog (67 measures)

    def test_unknown_request_is_a_validation_error_not_a_crash(self):
        model = self.run_model(DIABETES, [{'need_rules': ['nonsense']}, {'need_concepts': ['not_a_concept']}])
        self.assertIn('Unknown rule part', model.seen[1][1])
        self.assertIn('Unknown concept', model.seen[2][1])

    def test_reply_using_an_omitted_grammar_part_adds_that_part(self):
        model = self.run_model(DIABETES, [{'analysis': {'steps': []}}])
        self.assertNotIn('GENERAL ANALYSIS', model.seen[0][0])
        self.assertIn('GENERAL ANALYSIS', model.seen[1][0])
        self.assertLessEqual(self.tokens(model.seen[1]), BUDGET + 200)

    def test_workflow_rules_travel_when_the_question_asks_for_a_dashboard(self):
        first = self.run_model(DIABETES + ' Build a dashboard and monitor it.').seen[0]
        self.assertIn('Downstream product intentions', first[0])
        self.assertLessEqual(self.tokens(first), BUDGET)

    def test_prompt_that_already_fits_is_untouched(self):
        shaper = LP.Shaper('q', 8000)
        system, user = 'Interpret.', json.dumps({'catalog': {'selected_schemas': {'patient': {'columns': ['patient_id']}}}, 'question': 'q'})
        got = shaper(system, user)
        self.assertEqual(got[:2], (system, user)); self.assertFalse(got[2]['trimmed'])

    def test_rules_split_is_lossless_and_hosted_prompt_unchanged(self):
        self.assertEqual(''.join(t for _, t in SP.SYSTEM_PARTS), SP.SYSTEM)
        self.assertTrue(set(LP.ALWAYS + LP.OPTIONAL) <= {n for n, _ in SP.SYSTEM_PARTS})

    def test_budget_env_and_conservative_counter(self):
        with mock.patch.dict(os.environ, {'AGERE_LOCAL_TYPED_PROMPT_TOKENS': '5000'}): self.assertEqual(LP.budget(), 5000)
        with mock.patch.dict(os.environ, {'AGERE_LOCAL_TYPED_PROMPT_TOKENS': 'junk'}): self.assertEqual(LP.budget(), LP.DEFAULT_TOKENS)
        self.assertEqual(LP.counter('/nonexistent')('x' * 30), 10)

    def test_worker_long_prompt_passes_the_budget(self):
        with mock.patch.dict(os.environ, {'AGERE_LOCAL_TYPED_PROMPT_TOKENS': '6500'}), \
                mock.patch('clinical_core.agere.clinical.nl2sql.LocalModel.get', return_value=mock.Mock()), \
                mock.patch.object(SP, 'interpret', return_value=({}, {})) as interpret:
            W.long_prompt(self.catalog, DIABETES, None)
        self.assertEqual(interpret.call_args.kwargs['prompt_budget'], 6500)


if __name__ == '__main__':
    unittest.main()
