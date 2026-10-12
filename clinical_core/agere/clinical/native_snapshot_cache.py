"""Opt-in runtime acceleration preserving native snapshot values and identities.

Caches the unchanged native evaluator's result, never its freshness checks.
A private read-only SQLite connection tracks commits made by other connections;
file identity and exact small dependency contents cover replacement and metadata.
The guard is checked before and after copying/computing. No timer grants validity.
"""
from collections import Counter,OrderedDict
from copy import deepcopy
from functools import wraps
from pathlib import Path
from threading import RLock
import hashlib,json,os,sqlite3
from ...contracts import digest
from . import native_requirements as native,medication_accounts,medication_identity

VERSION='native-snapshot-runtime-cache/1'
MAX_ENTRIES=8


class NativeSnapshotCache:
 def __init__(self,path,original):
  self.path=Path(path).resolve();self.original=original;self.lock=RLock();self.db=None;self.file_id=None;self.epoch=0
  self.pid=os.getpid();self.entries=OrderedDict();self.counts=Counter()

 def close(self):
  with self.lock:
   if self.db:self.db.close()
   self.db=None;self.file_id=None;self.entries.clear()

 def generation(self,relations):
  from .analytic_registry import code_identity
  path=self.path/'stack.sqlite3';s=path.stat();file_id=(s.st_dev,s.st_ino)
  if self.pid!=os.getpid() or self.file_id!=file_id or self.db is None:
   self.close();self.pid=os.getpid();self.epoch+=1;self.file_id=file_id
   self.db=sqlite3.connect(path.as_uri()+'?mode=ro',uri=True,check_same_thread=False,isolation_level=None,timeout=30)
   self.db.execute('PRAGMA query_only=ON')
  row=self.db.execute('PRAGMA data_version').fetchone()
  if row is None or type(row[0]) is not int:raise ValueError('SQLite commit tracking unavailable')
  # Re-stat after opening/querying so file replacement cannot match a stale handle.
  after=path.stat()
  if (after.st_dev,after.st_ino)!=file_id:raise ValueError('Source database replaced during validation; retry')
  files=[]
  for name in ('run.json','operators.json'):
   p=self.path/name;files.append((name,hashlib.sha256(p.read_bytes()).hexdigest() if p.exists() else None))
  terminology=None
  if medication_accounts.RELATION in relations:
   link=self.path/'medication-terminology-link.json'
   if link.exists():
    raw=link.read_bytes();terminology=(hashlib.sha256(raw).hexdigest(),digest(medication_identity.attached(self.path)))
  return (self.epoch,file_id,row[0],after.st_size,after.st_mtime_ns,after.st_ctime_ns,tuple(files),terminology,native.identity(),code_identity())

 def snapshot(self,path,relations=None):
  if Path(path).resolve()!=self.path:raise ValueError('Cache is bound to a different warehouse')
  relations=tuple(sorted(set(relations or [])))
  with self.lock:
   for attempt in range(2):
    before=self.generation(relations);key=(relations,before)
    if key in self.entries:
     result=deepcopy(self.entries[key])
     if self.generation(relations)==before:
      self.entries.move_to_end(key);self.counts['hits']+=1;return result
     self.entries.clear();self.counts['changed_during_hit']+=1;continue
    self.counts['misses']+=1
    result=self.original(self.path,relations)
    if self.generation(relations)!=before:
     self.entries.clear();self.counts['changed_during_compute']+=1;continue
    # Retain only current generations for each relation scope. Return isolated data.
    for old in list(self.entries):
     if old[0]==relations:del self.entries[old]
    self.entries[key]=deepcopy(result)
    while len(self.entries)>MAX_ENTRIES:self.entries.popitem(last=False)
    return result
   raise ValueError('Source changed repeatedly during validation; refresh the request')

 def status(self):
  with self.lock:return {'schema':VERSION,'enabled':True,'entries':len(self.entries),'counters':dict(self.counts),
   'freshness':'SQLite commit version, database file identity, exact metadata/terminology and evaluator identities; checked before and after reuse',
   'cached_result_identity_changed':False,'time_to_live_used':False}


_INSTALL_LOCK=RLock()
_ACTIVE={}
_ORIGINAL=None
_DISPATCH=None


def install(path):
 """Explicit service-bound decorator; other stack paths retain the original route."""
 global _ORIGINAL,_DISPATCH
 root=Path(path).resolve()
 with _INSTALL_LOCK:
  if root in _ACTIVE:return _ACTIVE[root]
  if _ORIGINAL is None:
   _ORIGINAL=native.snapshot
   @wraps(_ORIGINAL)
   def dispatch(path,relations=None):
    cache=_ACTIVE.get(Path(path).resolve())
    return cache.snapshot(path,relations) if cache else _ORIGINAL(path,relations)
   _DISPATCH=dispatch;native.snapshot=dispatch
  cache=NativeSnapshotCache(root,_ORIGINAL);_ACTIVE[root]=cache;return cache


def uninstall(path):
 global _ORIGINAL,_DISPATCH
 with _INSTALL_LOCK:
  cache=_ACTIVE.pop(Path(path).resolve(),None)
  if cache:cache.close()
  if not _ACTIVE and _ORIGINAL is not None:
   if native.snapshot is _DISPATCH:native.snapshot=_ORIGINAL
   _ORIGINAL=None;_DISPATCH=None


def prepare_vocabulary_index(path):
 """Index only Agere's linked derived artifact; leave every event unchanged."""
 from . import longitudinal
 path=Path(path).resolve();link=path/'longitudinal-link.json'
 if not link.exists():return {'status':'not_attached'}
 root=Path(json.loads(link.read_text())['path']).resolve()
 if not root.is_relative_to(path.parent):return {'status':'skipped_artifact_outside_build'}
 checked,_=longitudinal.connect(path);checked.close()
 db=sqlite3.connect(root/'events.sqlite3',timeout=30)
 try:
  before=db.execute("SELECT sql FROM sqlite_master WHERE name='event_stream_vocabulary'").fetchone()
  sql='CREATE INDEX event_stream_vocabulary ON events(stream)'
  if before and before[0]!=sql:raise ValueError('Vocabulary index name is already used by another definition')
  with db:db.execute(sql.replace('CREATE INDEX ','CREATE INDEX IF NOT EXISTS ',1))
  plan=[list(r) for r in db.execute('EXPLAIN QUERY PLAN SELECT DISTINCT stream FROM events')]
  return {'status':'already_present' if before else 'created','database':str(root/'events.sqlite3'),'index':'event_stream_vocabulary','query_plan':plan,'source_rows_modified':0}
 finally:db.close()
