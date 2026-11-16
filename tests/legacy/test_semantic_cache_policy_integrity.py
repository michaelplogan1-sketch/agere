"""Authored cache policy/feedback workflow checks. No corpus, encoder or provider."""
import copy
import json
import math
import os
from pathlib import Path
import sqlite3
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

import numpy as np
from clinical_core.agere.clinical import semantic_cache as SC, vcache as VC, gate_policy as GP, cascade


class Encoder:
    identity = 'authored-encoder'
    def __init__(self, similarities=(.9,)):
        self.similarities = similarities
    def encode(self, questions):
        return np.array([[1., 0.]] + [[s, math.sqrt(max(0, 1-s*s))] for s in self.similarities[:len(questions)-1]])


class CachePolicy(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.docfile = self.root/'thresholds.json'
        self.enc = Encoder()
        self.env = patch.dict(os.environ, {'AGERE_VCACHE':'on', 'AGERE_SEMANTIC_CACHE':'on',
            'AGERE_VCACHE_DELTA':'.02', 'AGERE_VCACHE_THRESHOLDS':str(self.docfile)})
        self.env.start(); self.addCleanup(self.env.stop)
        for target, value in [('benchmark_questions', frozenset()), ('signature', ('count', (), ('patient',))), ('slots', {})]:
            p = patch.object(SC, target, return_value=value); p.start(); self.addCleanup(p.stop)
        p = patch('clinical_core.agere.clinical.learning.context', return_value='authored-context')
        p.start(); self.addCleanup(p.stop)
        p = patch.object(SC, 'similarity_gate', return_value=self.gate())
        p.start(); self.addCleanup(p.stop)
        db=sqlite3.connect(self.root/'learning.sqlite3')
        db.execute('CREATE TABLE verified(question,plan_kind,plan,agreement,context,created)')
        db.execute('INSERT INTO verified VALUES (?,?,?,?,?,?)', ('base one','sql',json.dumps({'query':'select 1'}),'authored','authored-context',1))
        db.commit(); db.close()
        self.write_doc()

    @staticmethod
    def gate():
        # Literal boundary fixture; no fitted certificate or measured accuracy.
        return {'threshold':.95, 'calibrated':True, 'alpha':.02, 'delta':.1, 'guarantee':'authored global fixture'}

    def doc(self):
        return {'version':VC.VERSION, 'policy':VC.policy(self.enc.identity), 'delta':.02,
                'encoder_identity':self.enc.identity, 'data_identity':'a'*64,
                'qualification':{'method':VC.METHOD, 'qualified':False},
                'entries':{'base one':{'threshold':.8,'n':3,'errors':0}}}

    def write_doc(self, doc=None):
        self.docfile.write_text(json.dumps(self.doc() if doc is None else doc))
        VC._LOADED.clear()

    def lookup(self, score=.9, **kwargs):
        return SC.lookup(self.root, 'new phrasing', Encoder((score,)), **kwargs)

    def assess(self, info, **kwargs):
        def certificate(name, *_):
            return self.gate() if name=='semantic_cache.similarity' else {'threshold':1.,'calibrated':True}
        with patch.object(GP, 'certificate', side_effect=certificate), patch.object(cascade, 'certify_route', return_value={'certified':True, 'tier':'semantic_cache'}):
            return GP.assess({'route':'semantic_cache','semantic_cache':info}, encoder=self.enc, **kwargs)

    def test_same_policy_adaptive_reuse_keeps_ordinary_review(self):
        hit, info=self.lookup()
        self.assertEqual(hit['plan']['query'],'select 1')
        self.assertEqual(info['threshold'],.8)
        self.assertTrue(info['global_gate']['calibrated'])
        self.assertFalse(info['threshold_calibrated'])
        self.assertIsNone(info['effective_rule']['guarantee'])
        self.assertEqual(self.assess(info)['level'],'unverified')
        self.assertIsNone(cascade.route_score('semantic_cache', {'semantic_cache':info}))

    def test_legacy_cut_cannot_inherit_tighter_policy_or_global_flag(self):
        self.write_doc({'version':'vcache/1','delta':.02,'encoder_identity':self.enc.identity,
                        'entries':{'base one':{'threshold':.8,'n':3,'errors':0}}})
        with patch.dict(os.environ, {'AGERE_VCACHE_DELTA':'.0001'}):
            hit,info=self.lookup()
        self.assertIsNone(hit)
        self.assertEqual(info['threshold'],.95)
        self.assertFalse(info['threshold_calibrated'])

    def test_changed_error_target_needs_matching_evidence(self):
        for bound in ('.0001','.05'):
            with self.subTest(bound=bound), patch.dict(os.environ,{'AGERE_VCACHE_DELTA':bound}):
                hit,info=self.lookup()
                self.assertIsNone(hit)
                self.assertEqual(info['threshold'],.95)
                self.assertIn('stale',info['vcache']['source'])

    def test_policy_loss_denominator_method_version_and_data_are_bound(self):
        for field in ('version','loss','denominator','method','scorer'):
            doc=self.doc();doc['policy'][field]='changed'
            self.write_doc(doc)
            self.assertIsNone(self.lookup()[0],field)
        for field in ('data_identity','qualification','encoder_identity','version'):
            doc=self.doc();doc.pop(field)
            self.write_doc(doc)
            self.assertIsNone(self.lookup()[0],field)

    def test_changed_encoder_falls_back_to_global(self):
        doc=self.doc();doc['encoder_identity']='other';self.write_doc(doc)
        self.assertIsNone(self.lookup()[0])

    def test_missing_entry_uses_unchanged_qualified_global(self):
        doc=self.doc();doc['entries']={};self.write_doc(doc)
        hit,info=self.lookup(.96)
        self.assertIsNotNone(hit)
        self.assertTrue(info['threshold_calibrated'])
        self.assertEqual(self.assess(info)['level'],'verified')

    def test_explicit_threshold_bypasses_adaptation(self):
        hit,info=self.lookup(threshold=.95)
        self.assertIsNone(hit)
        self.assertNotIn('vcache',info)
        self.assertFalse(info['threshold_calibrated'])
        self.assertEqual(info['effective_rule']['source'],'explicit')

    def test_vcache_off_retains_global(self):
        with patch.dict(os.environ,{'AGERE_VCACHE':'off'}):
            self.assertIsNone(self.lookup()[0])
            hit,info=self.lookup(.96)
            self.assertIsNotNone(hit)
            self.assertTrue(info['threshold_calibrated'])
            self.assertEqual(self.assess(info)['level'],'verified')

    def test_no_qualifying_cut_remains_unresolved_at_one(self):
        doc=self.doc();doc['entries']['base one'].update(threshold=None,n=3,errors=3);self.write_doc(doc)
        for score in (.995,1.):
            hit,info=self.lookup(score)
            self.assertIsNone(hit)
            self.assertIsNone(info['threshold'])
            self.assertFalse(info['threshold_calibrated'])

    def test_legacy_null_cut_cannot_become_qualified_global(self):
        doc=self.doc();doc['version']='vcache/1';doc.pop('policy')
        doc['entries']['base one'].update(threshold=None,n=3,errors=3);self.write_doc(doc)
        self.assertIsNone(self.lookup(1.)[0])

    def test_unresolved_entry_heals_through_another_candidate(self):
        db=sqlite3.connect(self.root/'learning.sqlite3')
        db.execute('INSERT INTO verified VALUES (?,?,?,?,?,?)',('base two','sql','{"query":"select 2"}','authored','authored-context',2));db.commit();db.close()
        doc=self.doc();doc['entries']['base one']['threshold']=None;self.write_doc(doc)
        hit,info=SC.lookup(self.root,'new phrasing',Encoder((1.,.96)))
        self.assertEqual(hit['plan']['query'],'select 2')
        self.assertFalse(info['threshold_calibrated']) # filtering changes selection
        self.assertEqual(self.assess(info)['level'],'unverified')

    def test_online_null_cut_is_not_replaced_with_point99(self):
        doc=self.doc();doc['global']={'t':1.,'gamma':1.};self.write_doc(doc)
        VC.observe(self.root,'base one',.9,False,'authored',binding=VC.policy(self.enc.identity))
        with patch.object(VC,'fit_curve',return_value=(1.,1.,np.zeros((2,2)))):
            # Three matching observations invoke the curve, whose cut is genuinely None.
            VC.observe(self.root,'base one',.91,False,'authored',binding=VC.policy(self.enc.identity))
            VC.observe(self.root,'base one',.92,False,'authored',binding=VC.policy(self.enc.identity))
            self.assertIsNone(self.lookup(1.)[0])

    def test_invalid_delta_is_explicit_unqualified_global(self):
        for bound in ('nan','inf','0','-1','2','garbage'):
            with self.subTest(bound=bound), patch.dict(os.environ,{'AGERE_VCACHE_DELTA':bound}):
                hit,info=self.lookup(.96)
                self.assertIsNotNone(hit)
                self.assertFalse(info['threshold_calibrated'])
                self.assertIn('invalid',info['vcache']['source'])

    def test_no_unilateral_floor_on_requested_error_target(self):
        with patch.dict(os.environ,{'AGERE_VCACHE_DELTA':'0.00000001'}):
            self.assertEqual(VC.delta(),1e-8)

    def test_no_entry_evidence_keeps_supplied_global_cut_below_adaptive_floor(self):
        self.write_doc({})
        cut,info=VC.entry_threshold(self.root,'base one',.6,self.enc.identity)
        self.assertEqual(cut,.6)
        self.assertEqual(info['effective'],'global')

    def test_global_certificate_must_cover_current_target(self):
        doc=self.doc();doc['entries']={};self.write_doc(doc)
        with patch.dict(os.environ,{'AGERE_VCACHE_DELTA':'.0001'}):
            hit,info=self.lookup(.96)
            self.assertIsNotNone(hit)
            self.assertFalse(info['threshold_calibrated'])
            self.assertEqual(self.assess(info)['level'],'unverified')

    def test_saved_global_receipt_rechecked_against_current_policy(self):
        doc=self.doc();doc['entries']={};self.write_doc(doc)
        _,info=self.lookup(.96)
        with patch.dict(os.environ,{'AGERE_VCACHE_DELTA':'.0001'}):
            self.assertEqual(self.assess(info)['level'],'unverified')
            self.assertIsNone(cascade.route_score('semantic_cache',{'semantic_cache':info}))

    def test_legacy_adaptive_receipt_cannot_be_certified_by_other_tier(self):
        info={'similarity':.9,'threshold':.8,'threshold_calibrated':True,'vcache':{'source':'entry (fitted)'}}
        self.assertEqual(self.assess(info)['level'],'unverified')
        self.assertIsNone(cascade.route_score('semantic_cache',{'semantic_cache':info}))

    def test_online_feedback_retains_policy_and_exact_literal(self):
        binding=VC.policy(self.enc.identity)
        VC.log_reuse(self.root,'follow up','base one',.9,{'query':"select 'a  b'"},binding)
        VC.on_verified(self.root,'follow up',{'query':"select 'a b'"})
        rows=VC.online_observations(self.root,'base one',binding)
        self.assertEqual(len(rows),1);self.assertFalse(rows[0]['correct'])
        db=sqlite3.connect(self.root/'vcache.sqlite3')
        self.assertEqual(json.loads(db.execute('SELECT binding FROM observation').fetchone()[0]),binding);db.close()

    def test_online_positive_cannot_cross_scorer_or_target(self):
        old=VC.policy('older-encoder')
        VC.observe(self.root,'base one',.9,True,'authored',binding=old)
        VC.observe(self.root,'base one',.9,True,'authored')
        self.assertEqual(VC.online_observations(self.root,'base one',VC.policy(self.enc.identity)),[])
        VC.observe(self.root,'base one',.93,False,'user_correction')
        self.assertFalse(VC.online_observations(self.root,'base one',VC.policy(self.enc.identity))[0]['correct'])

    def test_legacy_database_migrates_without_losing_feedback(self):
        db=sqlite3.connect(self.root/'vcache.sqlite3');db.executescript(VC.DDL)
        db.execute('INSERT INTO observation VALUES (?,?,?,?,?,?)',('base one',.92,0,'user_correction','q',1.));db.commit();db.close()
        self.assertEqual(len(VC.online_observations(self.root,'base one',VC.policy(self.enc.identity))),1)
        VC.log_reuse(self.root,'q','base one',.96,{'query':'select 1'},VC.policy(self.enc.identity))
        self.assertEqual(VC.on_correction(self.root,'q'),1)
        self.assertEqual(len(VC.online_observations(self.root,'base one')),2)

    def test_table_cache_keys_include_path(self):
        self.assertEqual(VC.table()['data_identity'],'a'*64)
        other=self.root/'other.json';doc=self.doc();doc['data_identity']='b'*64;other.write_text(json.dumps(doc))
        stamp=self.docfile.stat().st_mtime_ns;os.utime(other,ns=(stamp,stamp))
        with patch.dict(os.environ,{'AGERE_VCACHE_THRESHOLDS':str(other)}):
            self.assertEqual(VC.table()['data_identity'],'b'*64)

    def test_sql_lint_failure_heals_to_next_existing_rung(self):
        from clinical_core.agere.clinical.economical_planner import Ladder
        ladder=object.__new__(Ladder)
        ladder._profile={'authored':True};ladder.stack=SimpleNamespace(path=self.root,retriever=SimpleNamespace(encoder=self.enc))
        ladder.question='new phrasing';ladder.decline={}
        ladder._sql_graph=lambda *_: (_ for _ in ()).throw(ValueError('authored unsafe SQL'))
        self.assertIsNone(ladder.semantic_cache())
        self.assertIn('authored unsafe SQL',ladder.decline['semantic_cache_rejected'])

    def test_entry_rule_of_three_uses_entry_sample_size(self):
        cases=[{'key':'many','score':.9,'correct':True} for _ in range(200)]
        cases += [{'key':'few','score':.8,'correct':True} for _ in range(3)]
        doc=VC.fit(cases,encoder_identity=self.enc.identity,global_threshold=.95)
        self.assertIsNotNone(doc['entries']['many']['threshold'])
        self.assertIsNone(doc['entries']['few']['threshold'])
        self.assertFalse(doc['qualification']['qualified'])
        self.assertEqual(doc['qualification']['method'],VC.METHOD)
        self.assertEqual(len(doc['data_identity']),64)

    def test_fit_retains_unresolved_homogeneous_entry_instead_of_erasing_it(self):
        for n,correct in [(3,False),(5,False),(3,True),(5,True)]:
            with self.subTest(n=n,correct=correct):
                doc=VC.fit([{'key':'base one','score':.99,'correct':correct} for _ in range(n)],
                           encoder_identity=self.enc.identity,global_threshold=.95)
                self.assertIsNone(doc['entries']['base one']['threshold'])
                self.assertEqual(doc['entries']['base one']['errors'],0 if correct else n)
                self.write_doc(doc)
                self.assertIsNone(self.lookup(1.)[0])


if __name__=='__main__':
    unittest.main()
