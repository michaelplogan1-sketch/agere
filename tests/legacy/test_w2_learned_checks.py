import json
import sqlite3
import tempfile
import unittest
from pathlib import Path

from clinical_core.agere.clinical import answer_verifier as V, candidate_selector as S, label_choice as L, semantic_entropy as E


def learning_db(path, n=60):
    db = sqlite3.connect(Path(path) / 'learning.sqlite3')
    db.executescript("""CREATE TABLE verified(question TEXT, plan_kind TEXT, plan TEXT, signature TEXT, route TEXT, models TEXT,
        agreement TEXT, created REAL, context TEXT, provenance TEXT);
        CREATE TABLE quarantined(question TEXT, plan_kind TEXT, plan TEXT, signature TEXT, route TEXT, agreement TEXT, controls TEXT,
        created REAL, context TEXT, provenance TEXT);
        CREATE TABLE corrections(question TEXT, note TEXT, expected TEXT, created REAL);
        CREATE TABLE outcomes(question TEXT, route TEXT, status TEXT, reason TEXT, paid_calls INTEGER, usd REAL, created REAL);""")
    for i in range(n):
        db.execute('INSERT INTO verified VALUES (?,?,?,?,?,?,?,?,?,?)', (f'count patients with diabetes cohort {i}', 'sql',
                   f'SELECT count(*) FROM patient WHERE id={i}', 's', 'hosted_verified_sql', '', 'primary=local_model', 0, '', ''))
        db.execute('INSERT INTO quarantined VALUES (?,?,?,?,?,?,?,?,?,?)', (f'average cost per member for readmission group {i}', 'sql',
                   f'SELECT a.x FROM a JOIN b JOIN c WHERE a.y=1 AND b.z=2 AND c.w=3 -- {i}', 's', 'hosted_verified_sql', 'judge_own', 'QC-X', 0, '', ''))
    db.commit(); db.close()


class Learned(unittest.TestCase):
    def test_verifier_learns_from_verified_vs_quarantined_and_flags_risk(self):
        with tempfile.TemporaryDirectory() as d:
            learning_db(d)
            rows = V.labels([d])
            self.assertEqual(sum(r['wrong'] for r in rows), 60)
            m = V.train(rows)
            self.assertEqual(m['status'], 'fitted')
            bad = V.p_wrong('average cost per member for readmission group 99', 'hosted_verified_sql', 'judge_own', m)
            good = V.p_wrong('count patients with diabetes cohort 99', 'hosted_verified_sql', 'primary=local_model', m)
            self.assertGreater(bad['p_wrong'], good['p_wrong'])
            self.assertTrue(bad['risky']); self.assertFalse(good['risky'])

    def test_verifier_hook_annotates_and_lowers_confidence_but_never_blocks(self):
        with tempfile.TemporaryDirectory() as d:
            learning_db(d)
            m = V.train(V.labels([d])); V._CACHE.clear()
            mp = Path(d) / 'm.json'; V.save(m, mp)
            import os
            os.environ['AGERE_ANSWER_VERIFIER_MODEL'] = str(mp)
            try:
                verdict = {'annotations': [], 'accept': True, 'certificate': {'status': 'uncalibrated'}}
                V.annotate('average cost per member for readmission group 7', {'route': 'hosted_verified_sql', 'agreement': 'judge_own'}, {}, verdict)
                self.assertTrue(verdict['answer_verifier']['risky']); self.assertEqual(verdict['confidence'], 'low'); self.assertTrue(verdict['accept'])
            finally:
                del os.environ['AGERE_ANSWER_VERIFIER_MODEL']; V._CACHE.clear()

    def test_selector_orders_verified_style_candidates_first(self):
        with tempfile.TemporaryDirectory() as d:
            learning_db(d)
            m = S.train(S.labels([d]))
            self.assertEqual(m['status'], 'fitted')
            good = {'route': 'hosted_verified_sql', 'agreement': 'primary=local_model', 'plan': 'SELECT count(*) FROM patient WHERE id=5', 'reasons': []}
            bad = {'route': 'hosted_verified_sql', 'agreement': 'judge_own', 'plan': 'SELECT a.x FROM a JOIN b JOIN c WHERE a.y=1 AND b.z=2 AND c.w=3', 'reasons': ['x']}
            out, receipts = S.order([bad, good], m)
            self.assertIs(out[0], good); self.assertGreater(receipts[0]['p_beats_next'], 0.5)

    def test_selector_without_a_model_keeps_order(self):
        out, receipts = S.order([{'route': 'a'}, {'route': 'b'}], {'status': 'untrained'})
        self.assertEqual([c['route'] for c in out], ['a', 'b']); self.assertEqual(receipts, [])

    def test_semantic_entropy_agreement_vs_disagreement(self):
        same = E.measure(results=[{'value': 10}, {'value': 10}, {'value': 10.00001}])
        self.assertEqual(same['clusters'], 1); self.assertEqual(same['entropy_bits'], 0.0)
        split = E.measure(results=[{'value': 10}, {'value': 12}, {'value': 14}, {'value': 10}])
        self.assertEqual(split['clusters'], 3); self.assertGreater(split['normalized'], 0.5)
        self.assertIsNone(E.measure(results=[{'value': 1}]))

    def test_semantic_entropy_from_receipt_clusters_and_hook(self):
        receipt = {'hosted_sql': {'verification': {'clusters': [{'labels': ['a', 'b']}, {'labels': ['c']}, {'labels': ['d']}]}}}
        v = {'annotations': [], 'certificate': {'status': 'uncalibrated'}}
        m = E.check_answer(receipt, {}, v)
        self.assertEqual(m['samples'], 4); self.assertEqual(v['confidence'], 'low'); self.assertIn('Semantic entropy', v['annotations'][0])
        agree = {'annotations': []}
        E.check_answer({'hosted_sql': {'verification': {'clusters': [{'labels': ['a', 'b', 'c']}]}}}, {}, agree)
        self.assertEqual(agree['annotations'], [])

    def test_label_choice_discloses_cost_label_and_never_calls_cost_need(self):
        v = {'annotations': []}
        found = L.annotate({'nodes': [{'operation': 'outreach'}, {'operation': 'sql'}]}, {}, v)
        self.assertEqual(found[0]['label_kind'], 'cost'); self.assertTrue(found[0]['cost_proxy_for_need'])
        self.assertIn('not clinical need', v['annotations'][0])
        self.assertFalse(L.disclosure('uplift')['cost_proxy_for_need'])
        self.assertIsNone(L.annotate({'nodes': [{'operation': 'sql'}]}, {}, {'annotations': []}))

    def test_need_gap_finds_obermeyer_pattern(self):
        rows = []
        for i in range(400):
            score = i / 400
            rows.append({'score': score, 'need': 2 + 3 * score, 'group': 'A'})
            rows.append({'score': score, 'need': 2 + 3 * score + 1.5, 'group': 'B'})  # B sicker at the same score
        a = L.need_gap(rows)
        self.assertTrue(a['flagged']); self.assertEqual(a['gaps'][0]['group'], 'B')
        fair = L.need_gap([{**r, 'need': 2 + 3 * r['score']} for r in rows])
        self.assertFalse(fair['flagged'])


if __name__ == '__main__':
    unittest.main()
