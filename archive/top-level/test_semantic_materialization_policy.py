"""Authored atomic recovery and optional model-policy contracts, no model inference."""
import ast,json,os,sqlite3,tempfile,unittest,subprocess
from pathlib import Path
from unittest.mock import patch
from clinical_core.agere.clinical import semantic_layer as S,note_facts as N,record_store as R


class Materialization(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup);self.path=Path(self.temp.name)
        self.env=patch.dict(os.environ,{'AGERE_NOTE_NER':'off','AGERE_THERAPY_BARRIERS':'off','AGERE_SEMANTIC_DUCKDB':'off'});self.env.start();self.addCleanup(self.env.stop)
        self.meta={'source_digest':'authored-source','snapshot':{'as_of':'2026-09-30'},'patients':2}
        (self.path/'run.json').write_text(json.dumps(self.meta))
        self.target=self.path/'semantic.sqlite3';self.tmp=self.path/'semantic.sqlite3.tmp'
        with sqlite3.connect(self.target) as db:db.execute('create table marker(value)');db.execute("insert into marker values ('old-complete-target')")
        with sqlite3.connect(self.tmp) as db:
            db.executescript('CREATE TABLE meta(key TEXT PRIMARY KEY,value TEXT); CREATE TABLE patient(patient_id TEXT); CREATE TABLE record(record_id TEXT,patient_id TEXT,stream TEXT,event_date TEXT,recorded_date TEXT,payload TEXT);')
            db.executemany('insert into patient values (?)',[('p1',),('p2',)])
            db.executemany('insert into meta values (?,?)',[('version',S.VERSION),('source_digest','authored-source'),('as_of','2026-09-30')])

    def old_target(self):
        with sqlite3.connect(self.target) as db:return db.execute('select value from marker').fetchone()[0]

    def finish(self):
        with patch.object(R,'build',return_value={'status':'authored'}) as after:
            result=S.finish_materialization(self.path,self.tmp,verify_integrity=True)
        after.assert_called_once_with(self.path)
        return result

    def test_complete_core_resumes_native_notes_and_replaces_atomically(self):
        self.finish()
        self.assertFalse(self.tmp.exists())
        with sqlite3.connect(self.target) as db:
            meta=dict(db.execute('select key,value from meta'))
            self.assertEqual(db.execute('select count(*) from patient').fetchone()[0],2)
            self.assertEqual(db.execute('select count(*) from note_fact').fetchone()[0],0)
        self.assertFalse(json.loads(meta['postprocessing_policy'])['note_ner'])
        self.assertEqual(meta['postprocessing_identity'],S.policy_identity())

    def test_postprocessing_failure_preserves_original_and_retained_core(self):
        with patch.object(N,'build',side_effect=ValueError('authored postprocessing failure')):
            with self.assertRaisesRegex(ValueError,'authored postprocessing'):self.finish()
        self.assertEqual(self.old_target(),'old-complete-target')
        self.assertTrue(self.tmp.exists())

    def test_wrong_source_cutoff_version_and_roster_reject_before_enrichment(self):
        for key,value in [('source_digest','other-source'),('as_of','2026-10-01'),('version','old-schema')]:
            with self.subTest(key=key),sqlite3.connect(self.tmp) as db:
                old=db.execute('select value from meta where key=?',(key,)).fetchone()[0]
                db.execute('update meta set value=? where key=?',(value,key));db.commit()
                with patch.object(N,'build') as notes,self.assertRaisesRegex(ValueError,'source/schema/cutoff'):
                    S.finish_materialization(self.path,self.tmp)
                notes.assert_not_called();self.assertEqual(self.old_target(),'old-complete-target')
                db.execute('update meta set value=? where key=?',(old,key))
        self.meta['patients']=3;(self.path/'run.json').write_text(json.dumps(self.meta))
        with self.assertRaisesRegex(ValueError,'roster mismatch'):S.finish_materialization(self.path,self.tmp)

    def test_target_cannot_be_used_as_its_own_temporary_replacement(self):
        with self.assertRaisesRegex(ValueError,'separate temporary'):S.finish_materialization(self.path,self.target)
        self.assertEqual(self.old_target(),'old-complete-target')

    def test_matched_policy_reuses_materialization_without_build(self):
        self.finish()
        with patch.object(S,'build') as build,patch.object(S,'finish_materialization') as finish:
            self.assertEqual(S.ensure(self.path),self.target)
        build.assert_not_called();finish.assert_not_called()

    def test_changed_model_policy_refreshes_only_postprocessing(self):
        self.finish()
        with patch.dict(os.environ,{'AGERE_NOTE_NER':'on'}),patch.object(S,'build') as build,patch.object(S,'finish_materialization') as finish:
            self.assertEqual(S.ensure(self.path,allow_materialization=True),self.target)
        build.assert_not_called();finish.assert_called_once_with(self.path,self.tmp)
        with sqlite3.connect(self.tmp) as db:self.assertEqual(db.execute('select count(*) from patient').fetchone()[0],2)

    def test_existing_interrupted_file_is_not_silently_deleted(self):
        self.finish()
        self.tmp.write_bytes(b'authored retained interrupted build')
        with patch.dict(os.environ,{'AGERE_NOTE_NER':'on'}),self.assertRaisesRegex(ValueError,'Retained semantic build'):
            S.ensure(self.path,allow_materialization=True)
        self.assertEqual(self.tmp.read_bytes(),b'authored retained interrupted build')

    def test_disabling_bulk_ner_keeps_full_received_narrative(self):
        raw=json.dumps({'text':'Authored full narrative remains available to document review.'})
        with sqlite3.connect(self.tmp) as db:
            db.execute('insert into record values (?,?,?,?,?,?)',('r1','p1','notes','2026-09-01','2026-09-01',raw))
        self.finish()
        with sqlite3.connect(self.target) as db:
            self.assertEqual(db.execute('select payload from record where record_id=?',('r1',)).fetchone()[0],raw)

    def test_bulk_ner_requires_explicit_runner_opt_in(self):
        import run_automatic_workflow as W
        common=['--runtime',str(self.path),'--request',str(self.path/'request.json'),'--ledger',str(self.path/'unused-ledger.sqlite3'),'--output',str(self.path/'unused-output')]
        self.assertFalse(W.parser().parse_args(common).bulk_note_ner)
        self.assertTrue(W.parser().parse_args(common+['--bulk-note-ner']).bulk_note_ner)

    def test_preflight_reports_the_bulk_model_choice_without_execution(self):
        import run_automatic_workflow as W
        request=self.path/'request.json';request.write_text(json.dumps({'question':'Authored healthcare question'}))
        common=['--runtime',str(self.path),'--request',str(request),'--ledger',str(self.path/'unused-ledger.sqlite3'),'--output',str(self.path/'unused-output')]
        self.assertFalse(W.run(W.parser().parse_args(common))['bulk_note_ner'])
        self.assertTrue(W.run(W.parser().parse_args(common+['--bulk-note-ner']))['bulk_note_ner'])
        self.assertFalse((self.path/'unused-output').exists())

    def test_old_native_core_program_is_identical_before_shared_postprocessing(self):
        # Immutable parent of the materialization change; an expired temporary
        # checkout is neither a durable baseline nor a test prerequisite.
        before=subprocess.check_output(['git','show','20ec7c2a285a0965f3c1414c9fbeddd11ebe61ea^:clinical_core/agere/clinical/semantic_layer.py'],
            cwd=Path(__file__).resolve().parent,text=True,env={**os.environ,'GIT_OPTIONAL_LOCKS':'0'})
        after=Path(S.__file__).read_text()
        def parts(text):
            tree=ast.parse(text);build=next(n for n in tree.body if isinstance(n,ast.FunctionDef) and n.name=='build')
            body=build.body
            cut=next(i for i,n in enumerate(body) if isinstance(n,ast.ImportFrom) and any(v.name=='note_facts' for v in n.names)) if text==before else len(body)-1
            # The sole added retained-file protection precedes creation of the new core.
            body=[n for n in body[:cut] if not (isinstance(n,ast.If) and 'Retained semantic build' in ast.unparse(n)) and not (isinstance(n,ast.Expr) and ast.unparse(n).startswith('tmp.unlink('))]
            constants={target.id:ast.dump(n.value,include_attributes=False) for n in tree.body if isinstance(n,ast.Assign) for target in n.targets if isinstance(target,ast.Name) and target.id in ('VERSION','DDL','INDEXES','VERSIONED_STREAMS')}
            return [ast.dump(n,include_attributes=False) for n in body],constants
        self.assertEqual(parts(before),parts(after))


if __name__=='__main__':unittest.main()
