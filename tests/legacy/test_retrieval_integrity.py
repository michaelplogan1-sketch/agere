"""Actual retrieval/cache/scope code over authored data and deterministic rankers."""
from collections import Counter
import json
from pathlib import Path
import sqlite3
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch
import numpy as np

from clinical_core.agere.clinical import warehouse_retrieval as WR,med_graph_rag as MG,reranker_bakeoff as RB,system_one as SO
from clinical_core.providers import EvidenceTask


class Encoder:
    identity='deterministic-test-vector'
    def windows(self,text,**kw):return [{'text':text}]
    def encode(self,texts):return np.ones((len(texts),3))
    def close(self):pass


class Retrieval(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup);self.path=Path(self.tmp.name)
        db=sqlite3.connect(self.path/'stack.sqlite3');self.addCleanup(db.close)
        db.executescript('''CREATE TABLE patients(id TEXT PRIMARY KEY);CREATE TABLE records(id TEXT PRIMARY KEY,patient TEXT,payload TEXT);
          CREATE TABLE contents(id INTEGER PRIMARY KEY,text TEXT);CREATE TABLE passages(id INTEGER PRIMARY KEY,patient TEXT,content INTEGER,source TEXT);
          CREATE TABLE result_cache(id TEXT PRIMARY KEY,payload TEXT);CREATE VIRTUAL TABLE search USING fts5(text);''')
        db.executemany('INSERT INTO patients VALUES(?)',[('inside',),('outside',)])
        docs=[('R-Jan','inside','2030-01-10','metformin prescribed','medication_orders'),
              ('R-Jun','inside','2030-06-10','metformin stopped','medication_intent_changes'),
              ('R-outside','outside','2030-01-11','metformin prescribed','medication_orders')]
        for i,(rid,pid,day,text,stream) in enumerate(docs,1):
            source={'record_id':rid,'patient_id':pid,'source_table':stream,'field':'text','event_date':day,
                    'available_date':day,'start':0,'end':len(text),'quote':text}
            db.execute('INSERT INTO records VALUES(?,?,?)',(rid,pid,json.dumps({'event_date':day,'available_date':day})))
            db.execute('INSERT INTO contents VALUES(?,?)',(i,text));db.execute('INSERT INTO passages VALUES(?,?,?,?)',(i,pid,i,json.dumps(source)))
            db.execute('INSERT INTO search(rowid,text) VALUES(?,?)',(i,text))
        db.commit()
        gd=sqlite3.connect(self.path/MG.FILE)
        gd.executescript('CREATE TABLE meta(key,value);CREATE TABLE node(id,aliases);CREATE TABLE edge(a,b,weight);CREATE TABLE link(node,record,patient);')
        gd.executemany('INSERT INTO meta VALUES(?,?)',[('version',MG.VERSION),('report','{}'),('source_digest','authored')])
        gd.execute('INSERT INTO node VALUES(?,?)',('drug',json.dumps(['metformin'])))
        gd.executemany('INSERT INTO link VALUES(?,?,?)',[('drug',rid,pid) for rid,pid,*_ in docs]);gd.commit();gd.close()
        self.r=WR.Retriever.__new__(WR.Retriever);r=self.r;r.path=self.path;r.db=db
        r.meta={'source_digest':'authored','source_run':'authored','snapshot':{'as_of':'2030-12-31'}}
        r.encoder=Encoder();r.vectors=np.eye(3);r.dense_indices=None;r.reranker=None;r.annotation_db=None
        self.window={'basis':'source_event','start':'2030-01-01','end':'2030-01-31'}
        self.addCleanup(lambda:getattr(r,'_graph',None) and r._graph.close())

    def test_graph_and_completion_keep_the_patient_intersection_and_date_window(self):
        out=self.r.search('metformin',patient='inside',patient_ids=['inside'],analysis_window=self.window,limit=8,complete=False)
        self.assertEqual([c['source']['record_id'] for c in out['evidence']],['R-Jan'])
        self.assertTrue(out['scope_applied_before_ranking'])
        for members in ([],['inside']):
            for complete in (False,True):
                out=self.r.search('Is the patient taking metformin?',patient='outside',patient_ids=members,
                                  analysis_window=self.window,complete=complete)
                self.assertEqual(out['evidence'],[]);self.assertEqual(out['cohort_patient_count'],0)
        g=MG.GraphRetriever(self.path,stack_db=self.r.db);self.addCleanup(g.close)
        self.assertEqual(g.search('metformin',patient='outside',patient_ids=['inside'])['candidates'],[])
        self.assertEqual(g.search('metformin',patient='inside',patient_ids=[])['candidates'],[])

    def test_completion_subqueries_inherit_full_scope(self):
        from clinical_core.agere.clinical import evidence_completeness as EC
        original=self.r.search;requests=[]
        def search(q,**kw):
            requests.append(kw)
            return original(q,**kw)
        def complete(fetch,q,selected,limit):
            more=fetch('metformin prescription')
            self.assertEqual([c['source']['record_id'] for c in more],['R-Jan'])
            return selected,{'status':'fixture'}
        self.r.search=search
        with patch.object(EC,'complete',side_effect=complete):
            self.r.search('Is the patient taking metformin?',patient='inside',patient_ids=['inside'],analysis_window=self.window)
        self.assertEqual(requests[1]['patient_ids'],['inside'])
        self.assertEqual(requests[1]['analysis_window'],self.window)
        self.assertFalse(requests[1]['complete'])

    def test_model_identity_and_adopted_graph_flag_invalidate_cached_results(self):
        class Ranker:
            def __init__(self,identity,reverse=False):self.identity=identity;self.calls=0;self.reverse=reverse
            def rank(self,q,rows,top_k):
                self.calls+=1
                return sorted(rows,key=lambda r:int(r['id']),reverse=self.reverse)[:top_k]
            def close(self):pass
        r=self.r;r.reranker=Ranker('bge');r._installed_reranker=r.reranker
        model=Ranker('verified-Qwen-A');r._qwen=model
        qwen=self.path/'qwen';qwen.mkdir();(qwen/'agere-model.json').write_text('{}')
        choice={'type':'general','reranker':'qwen','graph_fallback':False,'basis':'authored substitute'}
        with patch.object(RB,'QWEN_PATH',qwen),patch.object(RB,'choice',side_effect=lambda q:dict(choice)):
            a=r.search('Routine evidence',limit=1,complete=False)
            cached=r.search('Routine evidence',limit=1,complete=False)
            self.assertTrue(cached['cache_hit']);self.assertEqual(model.calls,1)
            replacement=Ranker('verified-Qwen-B',True);r._qwen=replacement
            b=r.search('Routine evidence',limit=1,complete=False)
            self.assertFalse(b['cache_hit']);self.assertEqual(replacement.calls,1)
            self.assertNotEqual(a['evidence'][0]['id'],b['evidence'][0]['id'])
            choice['reranker']='bge'
            before=r.search('metformin',limit=1,complete=False)
            self.assertFalse(before['retrieval_policy']['graph_fallback'])
            choice['graph_fallback']=True
            after=r.search('metformin',limit=1,complete=False)
            self.assertFalse(after['cache_hit']);self.assertTrue(after['retrieval_policy']['graph_fallback'])


class UnknownDate(unittest.TestCase):
    def test_typed_evidence_preserves_unknown_source_date_separate_from_snapshot(self):
        normalized=SO._evidence([{'id':'undated','text':'Source text'},
                                 {'id':'dated','text':'Source text','event_date':'2029-01-01'}],'2030-12-31')
        self.assertEqual([e['date'] for e in normalized],['unknown','2029-01-01'])
        state=EvidenceTask('What is documented?','2030-12-31',{'yes':'yes','unknown':'unknown'},normalized).state()
        self.assertEqual(state['as_of'],'2030-12-31')
        self.assertEqual(state['evidence'][0]['date'],'unknown')

