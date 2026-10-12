"""Cache hits must retain the same evidence and every invalidation boundary."""
import json,os,sqlite3,tempfile,unittest
from pathlib import Path
from concurrent.futures import ThreadPoolExecutor
from unittest.mock import patch
from clinical_core.agere.clinical import native_snapshot_cache as cache,native_requirements as native,medication_accounts as ma
from clinical_core.agere.clinical import evidence_portfolio as ep
import test_native_requirements as fixtures


class SnapshotCacheTests(unittest.TestCase):
 setUp=fixtures.NativePersistenceTests.setUp
 save=fixtures.NativePersistenceTests.save
 def tearDown(self):
  cache.uninstall(self.root)
  if hasattr(self,'cached'):self.cached.close()
  fixtures.NativePersistenceTests.tearDown(self)
 def setup_cache(self,original=None):
  self.cached=cache.NativeSnapshotCache(self.root,original or native.snapshot);return self.cached
 def test_equal_legacy_result_and_mutation_isolation(self):
  legacy=native.snapshot(self.root,[]);c=self.setup_cache();first=c.snapshot(self.root,[]);self.assertEqual(first,legacy)
  first['patient_hashes']['P-1']='mutated';self.assertEqual(c.snapshot(self.root,[]),legacy);self.assertEqual(c.counts['hits'],1)
 def test_update_insert_delete_invalidate_source_hashes(self):
  c=self.setup_cache();a=c.snapshot(self.root,[])
  self.save('P-1',[fixtures.use('a',reported_intent='using')]);b=c.snapshot(self.root,[]);self.assertNotEqual(a['identity'],b['identity'])
  self.save('P-3',[]);d=c.snapshot(self.root,[]);self.assertNotEqual(b['identity'],d['identity'])
  with self.db:self.db.execute("DELETE FROM analyses WHERE patient='P-3'")
  self.assertEqual(c.snapshot(self.root,[])['identity'],b['identity'])
 def test_wal_commit_seen_with_unchanged_main_database_file(self):
  self.db.execute('PRAGMA journal_mode=WAL');c=self.setup_cache();a=c.snapshot(self.root,[]);st=(self.root/'stack.sqlite3').stat()
  self.save('P-1',[fixtures.use('a',reported_intent='using')]);later=(self.root/'stack.sqlite3').stat()
  self.assertEqual((st.st_size,st.st_mtime_ns),(later.st_size,later.st_mtime_ns))
  self.assertNotEqual(c.snapshot(self.root,[])['identity'],a['identity'])
 def test_rollback_does_not_expose_uncommitted_source(self):
  c=self.setup_cache();a=c.snapshot(self.root,[])
  self.db.execute("UPDATE analyses SET payload='{}'");self.db.rollback()
  self.assertEqual(c.snapshot(self.root,[]),a)
 def test_database_replacement_never_reuses_old_connection(self):
  c=self.setup_cache();a=c.snapshot(self.root,[]);replacement=self.root/'replacement.sqlite3'
  with sqlite3.connect(replacement) as db:self.db.backup(db)
  self.db.close();os.replace(replacement,self.root/'stack.sqlite3');self.db=sqlite3.connect(self.root/'stack.sqlite3')
  self.save('P-1',[fixtures.use('a',reported_intent='using')]);b=c.snapshot(self.root,[])
  self.assertNotEqual(a['identity'],b['identity']);self.assertEqual(c.epoch,2)
 def test_exact_metadata_change_is_rejected_despite_preserved_mtime(self):
  c=self.setup_cache();c.snapshot(self.root,[]);p=self.root/'run.json';st=p.stat();r=json.loads(p.read_text());r['synthetic']=False;p.write_text(json.dumps(r));os.utime(p,ns=(st.st_atime_ns,st.st_mtime_ns))
  with self.assertRaisesRegex(ValueError,'Synthetic'):c.snapshot(self.root,[])
 def test_operator_contract_change_is_not_cached(self):
  c=self.setup_cache();c.snapshot(self.root,[]);p=self.root/'operators.json';r=json.loads(p.read_text());r['code_identity']='changed';p.write_text(json.dumps(r))
  with self.assertRaisesRegex(ValueError,'stale'):c.snapshot(self.root,[])
 def test_terminology_catalog_change_is_rejected(self):
  from clinical_core.contracts import digest
  catalog={'schema':'rxnorm-exact-subset/1','entries':[]};(self.root/'catalog.json').write_text(json.dumps(catalog));(self.root/'medication-terminology-link.json').write_text(json.dumps({'path':str(self.root),'catalog_hash':digest(catalog)}))
  c=self.setup_cache();c.snapshot(self.root,[ma.RELATION]);catalog['entries']=[{'changed':True}];(self.root/'catalog.json').write_text(json.dumps(catalog))
  with self.assertRaisesRegex(ValueError,'Terminology'):c.snapshot(self.root,[ma.RELATION])
 def test_code_identity_change_forces_recomputation(self):
  original=native.snapshot;c=self.setup_cache();a=c.snapshot(self.root,[])
  with patch.object(native,'identity',return_value='changed'):
   self.assertNotEqual(c.snapshot(self.root,[])['identity'],a['identity'])
  self.assertEqual(c.counts['misses'],2)
 def test_changed_source_during_compute_retries_with_current_data(self):
  original=native.snapshot;calls=[]
  def compute(path,relations):
   result=original(path,relations);calls.append(1)
   if len(calls)==1:self.save('P-1',[fixtures.use('a',reported_intent='using')])
   return result
  c=self.setup_cache(compute);r=c.snapshot(self.root,[]);self.assertEqual(r,original(self.root,[]));self.assertEqual(len(calls),2)
 def test_continuously_changing_source_fails_closed(self):
  original=native.snapshot
  def compute(path,relations):
   result=original(path,relations)
   with self.db:self.db.execute("INSERT OR REPLACE INTO patients VALUES ('changed',?)",(os.urandom(8).hex(),))
   return result
  c=self.setup_cache(compute)
  with self.assertRaisesRegex(ValueError,'changed repeatedly'):c.snapshot(self.root,[])
  self.assertFalse(c.entries)
 def test_changed_source_during_hit_cannot_return_stale_result(self):
  c=self.setup_cache();c.snapshot(self.root,[]);real=c.generation;calls=[]
  def generation(relations):
   calls.append(1)
   if len(calls)==2:self.save('P-1',[fixtures.use('a',reported_intent='using')])
   return real(relations)
  with patch.object(c,'generation',side_effect=generation):r=c.snapshot(self.root,[])
  self.assertEqual(r,native.snapshot(self.root,[]));self.assertEqual(c.counts['changed_during_hit'],1)
 def test_concurrent_requests_single_flight_and_isolated_results(self):
  c=self.setup_cache()
  with ThreadPoolExecutor(max_workers=4) as pool:rows=list(pool.map(lambda _:c.snapshot(self.root,[]),range(8)))
  self.assertEqual(c.counts['misses'],1);self.assertEqual(c.counts['hits'],7)
  rows[0]['patient_hashes'].clear();self.assertTrue(rows[1]['patient_hashes'])
 def test_cache_bound_and_path_isolation(self):
  c=self.setup_cache()
  for i in range(12):c.snapshot(self.root,[str(i)])
  self.assertEqual(len(c.entries),cache.MAX_ENTRIES)
  with self.assertRaisesRegex(ValueError,'different warehouse'):c.snapshot(self.root/'other',[])
 def test_install_idempotent_restores_original(self):
  original=native.snapshot;c=cache.install(self.root);self.assertIs(cache.install(self.root),c)
  native.snapshot(self.root,[]);native.snapshot(self.root,[]);self.assertEqual(c.counts['hits'],1)
  cache.uninstall(self.root);self.assertIs(native.snapshot,original)
 def test_existing_portfolio_stales_after_native_source_change(self):
  cache.install(self.root);r=ep.execute(self.root,['P-2'],{'contracts':['native_medication_nonuse'],'review_slots':1})
  self.assertEqual(ep.inspect(self.root,r['id'])['id'],r['id'])
  self.save('P-2',[fixtures.use('b',patient_id='P-2')])
  with self.assertRaisesRegex(ValueError,'source or review changed'):ep.inspect(self.root,r['id'])
 def test_deep_source_inspection_still_rejects_raw_field_tampering(self):
  cache.install(self.root);snap=native.snapshot(self.root,[])
  self.db.execute('UPDATE records SET payload=? WHERE id=?',(json.dumps(fixtures.use('a',reported_intent='using')),'a'));self.db.commit()
  with self.assertRaisesRegex(ValueError,'hash changed'):native.inspect(self.root,'P-1',snap['patient_hashes']['P-1'])

if __name__=='__main__':unittest.main()
