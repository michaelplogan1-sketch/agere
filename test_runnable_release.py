"""Package identity, isolation and dependency failures with independent fixtures."""
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
from clinical_core.agere import local_release as L, runnable_release as R
from test_local_release import RecoveryTests


class RunnableReleaseTests(unittest.TestCase):
    setUp = RecoveryTests.setUp

    def package(self):
        code = self.root / 'checkout'; code.mkdir()
        (code / 'engine').write_text('archived code')
        policy=code/'clinical_core/agere/local_release.py';policy.parent.mkdir(parents=True);policy.write_text('local route policy fixture')
        identity = {'engine': L.sha(code / 'engine'), 'clinical_core/agere/local_release.py': L.sha(policy)}
        self.runtime = self.root / 'python'; self.runtime.mkdir(); (self.runtime / 'interpreter').write_text('runtime')
        self.libs = self.root / 'libraries'; self.libs.mkdir(); (self.libs / 'library').write_text('library')
        self.ledger = self.root / 'ledger/provider-budget.sqlite3'; self.ledger.parent.mkdir(); self.ledger.write_text('shared')
        deps = [*self.deps, {'path':str(self.ledger), 'verification':'current_shared_ledger_never_restored'}]
        for obj, name, value in [(L,'ROOT',code),(L,'code_identity',lambda:identity),(L,'dependencies',lambda *a:deps)]:
            p=patch.object(obj,name,value);p.start();self.addCleanup(p.stop)
        L.checkpoint(self.config, self.cp)
        release = self.root / 'release'
        with patch.object(R.sys,'base_prefix',str(self.runtime)), patch.object(R.sysconfig,'get_paths',return_value={'purelib':str(self.libs)}):
            R.make(self.cp,release)
        return release

    def test_archived_runtime_independent_and_install_preserves_original(self):
        release=self.package(); (self.runtime/'interpreter').write_text('development runtime changed')
        R.verify(release)
        out=self.root/'installation';R.install(release,out,resume_work=True)
        self.assertEqual(L.read(out/'installation.json')['mode'],'owned_work_only')
        self.assertEqual(L.read(out/'configuration.json')['build'],str(out/'build'))
        self.assertEqual((out/'app/artifacts/agere-full').resolve(),self.ledger.parent)
        self.assertEqual(self.ledger.read_text(),'shared')
        self.assertEqual(L.read(out/'recovery.json')['build'],str(out/'build'))
        self.assertTrue(all(Path(L.read(out/'build/stack'/n)['path']).is_relative_to(out/'build') for n in L.INTERNAL_LINKS))
        R.verify(out,installed=True)

    def test_installation_identity_mode_and_mount_tampering_refused(self):
        release=self.package();out=self.root/'installation';R.install(release,out,resume_work=True)
        p=out/'installation.json';original=p.read_text()
        for key,value in [('root','/another'),('release_id','wrong'),('mode','all_mutations')]:
            row=json.loads(original);row[key]=value;p.write_text(json.dumps(row))
            with self.assertRaises(ValueError):R.verify(out,installed=True)
        p.write_text(original)
        mount=out/'app/artifacts/agere-full';mount.unlink();mount.symlink_to(self.dep,target_is_directory=True)
        with self.assertRaisesRegex(ValueError,'mount changed'):R.verify(out,installed=True)

    def test_rollback_new_directory_preserves_changed_copy_and_shared_ledger(self):
        import sqlite3
        release=self.package();first=self.root/'first';R.install(release,first,resume_work=True)
        with sqlite3.connect(first/'build/stack/work.sqlite3') as db:
            db.execute("update records set value='resumed'")
        self.ledger.write_text('new shared accounting must survive')
        rollback=self.root/'rollback';R.install(release,rollback,resume_work=True)
        for directory,value in [(first,'resumed'),(rollback,'preserved'),(self.build.parent/'active','preserved')]:
            path=directory/'build/stack/work.sqlite3' if directory!=self.build else directory/'stack/work.sqlite3'
            with sqlite3.connect(path) as db:self.assertEqual(db.execute('select value from records').fetchone()[0],value)
        self.assertEqual(self.ledger.read_text(),'new shared accounting must survive')

    def test_changed_archive_code_runtime_and_bootstrap_rejected(self):
        release=self.package()
        for name in ['app/engine','runtime/python/interpreter','runtime/site-packages/library','bootstrap.py']:
            p=release/name;old=p.read_bytes();p.write_bytes(b'changed')
            with self.assertRaises(ValueError):R.verify(release)
            p.write_bytes(old)

    def test_unknown_runtime_file_and_external_symlink_rejected(self):
        release=self.package();p=release/'runtime/python/unexpected.py';p.write_text('extra')
        with self.assertRaisesRegex(ValueError,'bytes changed'):R.verify(release)
        p.unlink();p.symlink_to(self.ledger)
        with self.assertRaisesRegex(ValueError,'External runtime symlink'):R.verify(release)

    def test_existing_install_never_overwritten(self):
        release=self.package();out=self.root/'installation';R.install(release,out)
        with self.assertRaisesRegex(ValueError,'already exists'):R.install(release,out,resume_work=True)
        self.assertEqual(L.read(out/'installation.json')['mode'],'inspection_only')

    def test_different_platform_refused(self):
        release=self.package()
        with patch.object(R.platform,'machine',return_value='other'):
            with self.assertRaisesRegex(ValueError,'architecture'):R.verify(release)

    def test_frozen_plan_preserved_and_not_requalified(self):
        plan=self.root/'plan.json';plan.write_text(json.dumps({'stack':'/original/stack'}))
        contract=[{'plan':str(plan),'sha256':L.sha(plan),'original_stack':'/original/stack'}]
        R.verify_contract(contract);plan.write_text(json.dumps({'stack':'/relocated/stack'}))
        with self.assertRaisesRegex(ValueError,'Frozen'):R.verify_contract(contract)

    def test_checkpoint_mutation_refused_before_install(self):
        release=self.package();(self.cp/'build/connection.json').write_text('{}')
        with self.assertRaisesRegex(ValueError,'Corrupt'):R.install(release,self.root/'installation')
        self.assertFalse((self.root/'installation').exists())

    def test_install_uses_exact_release_code(self):
        release=self.package()
        with patch.object(L,'code_identity',return_value={'engine':'another release'}):
            with self.assertRaisesRegex(ValueError,'exact archived code'):R.install(release,self.root/'installation')

    def test_local_operations_policy_is_bound_and_other_modes_stay_restricted(self):
        release=self.package();out=self.root/'installation'
        R.install(release,out,local_operations=True);R.verify(out,installed=True)
        self.assertEqual(L.read(out/'installation.json')['mode'],'local_operations')
        for mode in ('inspection_only','owned_work_only'):
            gets,posts=L.recovery_routes(mode)
            self.assertNotIn('/api/stack/portable-intake/save',posts)
            self.assertNotIn('/api/stack/action-work/create',posts)
            self.assertNotIn('/api/stack/action-work/update',posts)
            for path in ('/action-center','/api/stack/action-contexts',
                         '/api/stack/action-context','/api/stack/action-deliverables',
                         '/api/stack/action-deliverable'):
                self.assertIn(path,gets)
        gets,posts=L.recovery_routes('local_operations')
        self.assertIn('/api/stack/portable-intake/save',posts)
        self.assertIn('/api/stack/decision/scoped-prepare',posts)
        self.assertIn('/api/stack/requirement-contracts',gets)
        self.assertIn('/api/stack/outcome-contracts',gets)
        self.assertIn('/api/stack/case-synthesis',gets)
        self.assertIn('/api/stack/action-work/create',posts)
        self.assertIn('/api/stack/action-work/update',posts)
        self.assertNotIn('/api/stack/action-deliverable',posts)
        for name in ('context/interpret','learning-cohorts/read','semantic/review','context/output-recover'):
            self.assertNotIn('/api/stack/'+name,posts)
        p=out/'installation.json';v=L.read(p);v['policy_sha256']='another policy';p.write_text(json.dumps(v))
        with self.assertRaisesRegex(ValueError,'policy changed'):R.verify(out,installed=True)

    def test_local_operation_request_cannot_select_hosted_or_candidate_planner(self):
        for path,body in [('/api/stack/compose',{'question':'Any free form'}),
                          ('/api/stack/decision/prepare',{'planner':'buyer_documentary_candidate'})]:
            with self.assertRaises(PermissionError):L.check_local_request(path,body)
        L.check_local_request('/api/stack/compose',{'planning':'scoped_local_candidate'})
        L.check_local_request('/api/stack/decision/prepare',{})

    def test_installed_cohort_status_preserves_origin_without_claiming_qualification(self):
        plan=self.root/'plan.json';plan.write_text(json.dumps({'stack':'/original/stack'}))
        contract=[{'cohort_id':'cohort','plan':str(plan),'sha256':L.sha(plan),'original_stack':'/original/stack','source_links':{}}]
        row=R.qualification_status(contract,'cohort')
        self.assertEqual(row['status'],'unavailable_in_this_installation')
        self.assertFalse(row['reading_enabled']);self.assertFalse(row['qualification_transferred'])
        self.assertEqual(row['original_stack'],'/original/stack')
        with self.assertRaisesRegex(ValueError,'Unknown'):R.qualification_status(contract,'other')
        plan.write_text('{}')
        with self.assertRaisesRegex(ValueError,'Frozen'):R.qualification_status(contract)


class ApplicationInventoryTests(unittest.TestCase):
    def test_model_dependency_survives_removal_of_checkout_alias(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp).resolve();checkout=root/'checkout';model=root/'physical-model';model.mkdir()
            (model/'config.json').write_text('{"model":"fixture"}')
            alias=checkout/'models/bge-reranker-v2-m3';alias.parent.mkdir(parents=True);alias.symlink_to(model,target_is_directory=True)
            build=root/'build';(build/'stack').mkdir(parents=True)
            (build/'stack/run.json').write_text(json.dumps({'encoder_path':str(model)}))
            ledger=root/'provider-budget.sqlite3';ledger.write_text('current accounting')
            with patch.object(L,'ROOT',checkout),patch.dict(L.os.environ,{'AGERE_LEDGER_PATH':str(ledger)}):
                dependencies=L.dependencies(build,{'source_release':str(model)})
                mounted=next(d for d in dependencies if d.get('application_mount'))
                self.assertEqual(mounted['path'],str(model))
                self.assertEqual(R.resource_mounts(dependencies),{'models/bge-reranker-v2-m3':str(model)})
                alias.unlink();L.check_dependencies(dependencies)
                self.assertEqual(R.resource_mounts(dependencies),{'models/bge-reranker-v2-m3':str(model)})
    def test_declared_model_mount_rejects_escape_and_nonmodel_paths(self):
        for name in ['../escape','/absolute','models/../../escape','artifacts/agere-full','models']:
            with self.subTest(name=name),self.assertRaises(ValueError):
                R.resource_mounts([{'path':'/physical/model','application_mount':name}])
    def test_rule_json_stylesheet_and_question_configuration_are_archived(self):
        code=L.code_identity()
        for path in ('clinical_core/agere/clinical/care_protocols.json',
                     'clinical_core/agere/clinical/clinical_input_maps.json',
                     'clinical_core/agere/clinical/assets/workspace.css','config/questions.json',
                     'config/agere/risk_adjustment/manifest.json',
                     'config/agere/risk_adjustment/v28_relative_factors.csv',
                     'config/agere/risk_adjustment/eligible_cpt_hcpcs_cy2026.txt',
                     'tools/training/datasets.py','tools/certify_gates.py',
                     'tools/normalization/jev_check.py'):
            self.assertEqual(code[path],L.sha(L.ROOT/path))

if __name__=='__main__':unittest.main()
