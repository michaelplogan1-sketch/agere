"""Authored negative feedback scope; no training, provider or clinical labels."""
import json,os,tempfile,unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch
from clinical_core.agere.clinical import component_learning as C,adapter_learning as A


class ScopedFeedback(unittest.TestCase):
    def setUp(self):
        temp=tempfile.TemporaryDirectory();self.addCleanup(temp.cleanup);self.path=Path(temp.name)
        self.stack=SimpleNamespace(path=self.path,allow_live=False);self.root=C.root_for(self.path)
        env=patch.dict(os.environ,{'AGERE_EVALUATION':'0','AGERE_COMPONENT_RELEASE_ROOT':str(self.root),'AGERE_COMPONENT_LEARNING_PLAN':''})
        env.start();self.addCleanup(env.stop)
        p=patch('socket.create_connection',side_effect=AssertionError('No provider'));p.start();self.addCleanup(p.stop)
        self.models={r:C.register(self.root,r,{'authored_fixture':r},{'selected':True},activate=True)for r in ['router','shape','slm_typed']}
        db=C.database(self.root)
        with db:
            for role in self.models:
                row={'question':'Authored same question','role':role,'family':'authored-family','source_kind':'user_review','eligible':True,
                    'accepted_labels':['table'if role=='shape'else'trajectory'],'source_digest':'authored-source',
                    'target':{},'reference_sql':'SELECT 1','acceptance':{},'population':None}
                db.execute('INSERT INTO component_observations(payload) VALUES (?)',(json.dumps(row),))
        db.close()

    def exported(self,role):
        manifest=(A.export(self.root,self.path/'exports'/role,'typed',{'authored-family':'fit'})if role=='slm_typed'else
            C.export(self.root,self.path/'exports'/role,role,{'authored-family':'fit'}))
        spec=json.loads(manifest.read_text());return Path(spec['files'][0]['path']).read_text().splitlines()

    def test_endpoint_scopes_quarantine_and_exports_then_explicit_rollback(self):
        out=C.handle_feedback(self.stack,{'question':'Authored same question','note':'Authored shape correction','roles':['shape'],'accepted':False})
        self.assertEqual(out['affected_roles'],['shape']);self.assertFalse(out['eligible'])
        self.assertIsNone(C.active(self.root,'shape'))
        self.assertIsNotNone(C.active(self.root,'router'));self.assertIsNotNone(C.active(self.root,'slm_typed'))
        self.assertEqual(len(self.exported('router')),1);self.assertEqual(len(self.exported('slm_typed')),1)
        self.assertEqual(self.exported('shape'),[])
        C.rollback(self.root,'shape',self.models['shape']['id'],'Explicit authored recovery')
        self.assertEqual(C.active(self.root,'shape')['id'],self.models['shape']['id'])
        # Model rollback does not silently restore a rejected training label.
        self.assertEqual(self.exported('shape'),[])

    def test_unscoped_corrections_keep_conservative_legacy_behavior(self):
        C.handle_feedback(self.stack,{'question':'Authored same question','note':'Unscoped authored correction'})
        for role in self.models:self.assertIsNone(C.active(self.root,role));self.assertEqual(self.exported(role),[])

    def test_invalid_scope_is_rejected_before_feedback_or_observation_mutation(self):
        db=C.database(self.root);before=db.execute('SELECT count(*)FROM component_events').fetchone()[0];db.close()
        for roles in [[],['unknown'],['shape','shape'],'shape',None]:
            with self.subTest(roles=roles),self.assertRaises(ValueError):
                C.handle_feedback(self.stack,{'question':'Authored same question','note':'Invalid scope','roles':roles})
        with self.assertRaises(ValueError):
            C.handle_feedback(self.stack,{'question':'Authored same question','note':'Positive role scope is invalid','accepted':True,'roles':['shape']})
        db=C.database(self.root);self.assertEqual(db.execute('SELECT count(*)FROM component_events').fetchone()[0],before);db.close()
        self.assertIsNotNone(C.active(self.root,'router'))

    def test_negative_correction_never_inherits_a_positive_machine_routing_label(self):
        result={'answer':{'status':'answered','verification':'checked'},'planner_receipt':{'interpretation':{
            'execution_accepted':True,'proposer':{'name':'backstop_agent'}}}}
        out=C.observe(self.stack,'Authored same question',result,{'learning':{'correction':{'note':'Negative scope','roles':['shape']}}})
        self.assertFalse(out['eligible'])
        db=C.database(self.root);row=json.loads(db.execute('SELECT payload FROM component_observations ORDER BY seq DESC LIMIT 1').fetchone()[0]);db.close()
        self.assertEqual(row['roles'],['shape']);self.assertIsNone(row['role']);self.assertEqual(row['accepted_labels'],[])
        self.assertFalse(row['clinical_qualification']);self.assertTrue(C.correction_affects({'source_kind':'user_correction'},'router'))


if __name__=='__main__':unittest.main()
