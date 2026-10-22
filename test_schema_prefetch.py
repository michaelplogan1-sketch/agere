"""Likely schemas up front and diff-only repairs in the shared planner (fixture warehouse, scripted model; no network)."""
import copy
import json
import sqlite3
import tempfile
import unittest
from pathlib import Path

from clinical_core.agere.clinical import request_plan as R, request_language as L, shared_planner as S, schema_prefetch as P


def warehouse(path):
    with sqlite3.connect(Path(path) / 'semantic.sqlite3') as db:
        db.executescript('''
            CREATE TABLE meta(key,value);
            INSERT INTO meta VALUES('version','fixture'),('source_digest','prefetch-1'),('as_of','2030-05-27');
            CREATE TABLE patient(patient_id,birth_date,sex_reported,coverage_type,payer_id);
            INSERT INTO patient VALUES('A','1960-05-27','F','medicare','P1'),('B','1965-05-27','M','commercial','P1'),('C','1950-05-27','F','medicare','P2');
            CREATE TABLE claim(claim_id,patient_id,payer,service_date,allowed_cents,ever_denied);
            INSERT INTO claim VALUES('Q1','A','P1','2029-01-01',10000,1),('Q2','A','P1','2029-01-02',20000,0),
              ('Q3','B','P1','2029-01-03',5000,1),('Q4','C','P2','2029-01-01',0,1);
            CREATE TABLE appointment(patient_id,scheduled_date,kept,final_status);
            INSERT INTO appointment VALUES('A','2029-03-01',1,'completed'),('B','2029-03-01',0,'no_show');
        ''')
    return R.Catalog.load(Path(path))


class Scripted:
    """Planner/judge replies in order; records every user message it was sent."""
    hosted = False
    identity = 'scripted'

    def __init__(self, replies):
        self.replies, self.users = list(replies), []

    def __call__(self, system, user, **kw):
        self.users.append((system, user))
        return json.dumps(self.replies.pop(0)), {'seconds': 0, 'model': 'scripted'}


class Prefetch(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(); self.addCleanup(self.tmp.cleanup)
        self.catalog = warehouse(self.tmp.name)

    def test_likely_picks_tables_by_search_score(self):
        got = P.likely(self.catalog, 'How many denied claims were there in 2029 by payer?')
        self.assertEqual(got['tables'][0], 'claim')
        self.assertNotIn('error', got)

    def test_fragment_and_patch(self):
        prev = {'source': {'table': 'claim'}, 'select': [{'kind': 'count'}], 'where': None, 'group_by': [], 'limit': None}
        frag = P.fragment(prev, ['The question asks to group the result by payer; group_by is empty'])
        self.assertEqual(set(frag), {'source', 'group_by'})
        self.assertIsNone(P.fragment(prev, []))
        merged = P.apply_patch(('request', prev), {'patch': {'group_by': ['payer']}})
        self.assertEqual(merged['group_by'], ['payer']); self.assertEqual(merged['select'], prev['select'])
        self.assertEqual(P.apply_patch(('analysis', {'a': 1}), {'patch': {'b': 2}}), {'analysis': {'a': 1, 'b': 2}})
        self.assertEqual(P.apply_patch(None, {'patch': {}}), {'patch': {}})  # nothing kept: the reply is left to the validator

    def test_planner_sends_schema_first_and_repairs_by_patch(self):
        q = 'Count claims where ever_denied = 1 in 2029 by payer'
        right = L.parse(self.catalog, q)
        first_try = copy.deepcopy(right)  # compiles; the scripted judge still flags its grouping
        model = Scripted([first_try, {'equivalent': False, 'issues': ['Check the grouping: group_by must be the claim payer']},
                          {'patch': {'group_by': right['group_by']}},
                          {'equivalent': True, 'issues': []}])
        contract, interp = S.interpret(self.catalog, q, model=model, force_model=True, use_memory=False)
        first = json.loads(model.users[0][1])
        self.assertIn('claim', first['catalog']['selected_schemas'])  # no need_tables turn was needed
        repair = json.loads(model.users[2][1])['validator_feedback']
        self.assertIn('failing_fragment', repair); self.assertNotIn('previous_proposal', repair)
        self.assertIn('group_by', repair['failing_fragment']); self.assertNotIn('where', repair['failing_fragment'])
        self.assertLess(len(json.dumps(repair['failing_fragment'])), len(json.dumps(first_try)))
        self.assertIn('GROUP BY', contract['query'].upper())
        self.assertEqual(len(interp['attempts']), 4)


if __name__ == '__main__':
    unittest.main()
