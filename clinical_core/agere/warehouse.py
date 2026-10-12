"""Read observable streams only. Immutable evidence, bitemporal snapshots, corrections."""
from __future__ import annotations
import hashlib
import json
import sqlite3
from collections import Counter
from datetime import date
from pathlib import Path
from ..contracts import digest

TABLES = {'adt','allergies','coverage','documents','encounters','financial','households','labs',
          'life_events','medical_claims','medication_administration','medication_history',
          'medication_orders','notes','pharmacy_claims','problem_lists','procedure_orders',
          'purchased_sdoh','referrals','sdoh','vitals'}

def lines(path):
    with Path(path).open() as stream:
        for line in stream:
            if line.strip():
                yield json.loads(line)

def text_of(r):
    # Original/clean text must not bypass the received extraction.
    if r['source_table'] == 'documents':
        return r.get('extracted_text') or ''
    return r.get('text') or ''

class Warehouse:
    def __init__(self, path):
        Path(path).parent.mkdir(parents=True, exist_ok=True)
        self.db = sqlite3.connect(path)
        self.db.executescript('''
        PRAGMA journal_mode=WAL;
        CREATE TABLE IF NOT EXISTS releases (id TEXT PRIMARY KEY, manifest TEXT);
        CREATE TABLE IF NOT EXISTS patients (release TEXT, id TEXT, payload TEXT, PRIMARY KEY(release,id));
        CREATE TABLE IF NOT EXISTS records (release TEXT,id TEXT,patient TEXT,stream TEXT,event TEXT,arrival TEXT,
          identity TEXT,supersedes TEXT,hash TEXT,payload TEXT,PRIMARY KEY(release,id));
        CREATE INDEX IF NOT EXISTS patient_records ON records(release,patient,event,arrival);
        CREATE INDEX IF NOT EXISTS corrections ON records(release,supersedes);
        CREATE TABLE IF NOT EXISTS results (key TEXT PRIMARY KEY, payload TEXT);
        ''')
    def close(self): self.db.close()
    def ingest(self, root):
        root=Path(root); manifest=json.loads((root/'manifest.json').read_text())
        if manifest.get('synthetic') is not True: raise ValueError('This runtime is configured for synthetic sources')
        # Hash only observed files. Never read truth/ even to verify a release.
        hashes={}
        for name,spec in sorted(manifest['files'].items()):
            if not name.startswith('observed/'): continue
            path=root/name
            if path.resolve().parent != (root/'observed').resolve(): raise ValueError('Invalid observed path')
            with path.open('rb') as stream: sha=hashlib.file_digest(stream,'sha256').hexdigest()
            if sha != spec['sha256']: raise ValueError('Changed source: '+name)
            hashes[name]=sha
        release=digest(hashes)
        if self.db.execute('SELECT 1 FROM releases WHERE id=?',(release,)).fetchone():
            return release, {'cache_hit':True,'observed_files':len(hashes)}
        counts=Counter()
        with self.db:
            for p in lines(root/'observed/patients.jsonl'):
                self.db.execute('INSERT INTO patients VALUES (?,?,?)',(release,p['patient_id'],json.dumps(p)))
                counts['patients']+=1
            for table in sorted(TABLES):
                path=root/'observed'/f'{table}.jsonl'
                if not path.exists(): continue
                for r in lines(path):
                    if r.get('synthetic') is not True: raise ValueError('Mixed data mode')
                    for k in ('event_date','available_date'):
                        date.fromisoformat(r[k])
                    if not self.db.execute('SELECT 1 FROM patients WHERE release=? AND id=?',(release,r['patient_id'])).fetchone():
                        raise ValueError('Unknown roster identity')
                    r={**r,'source_table':table}
                    self.db.execute('INSERT INTO records VALUES (?,?,?,?,?,?,?,?,?,?)',(release,r['record_id'],r['patient_id'],
                        table,r['event_date'],r['available_date'],r.get('identity_status','unknown'),
                        r.get('supersedes_record_id'),digest(r),json.dumps(r)))
                    counts[table]+=1
            self.db.execute('INSERT INTO releases VALUES (?,?)',(release,json.dumps({'hashes':hashes,'as_of':manifest['as_of']})))
        return release, {'cache_hit':False,'counts':dict(counts),'observed_files':len(hashes)}
    def snapshot(self, release, cutoff):
        date.fromisoformat(cutoff)
        patients={r[0]:{'patient':json.loads(r[1]),'records':[]} for r in self.db.execute(
            'SELECT id,payload FROM patients WHERE release=? ORDER BY id',(release,))}
        visible={}; excluded=Counter()
        for rid,pid,event,arrival,identity,payload in self.db.execute(
                'SELECT id,patient,event,arrival,identity,payload FROM records WHERE release=? ORDER BY event,id',(release,)):
            if event is not None and event>cutoff: excluded['future']+=1;continue
            if arrival>cutoff: excluded['late_arrival']+=1;continue
            if identity!='matched': excluded['identity_quarantine']+=1;continue
            visible[rid]=json.loads(payload)
        superseded=set(); conflicts=[]
        for r in visible.values():
            prev=r.get('supersedes_record_id')
            if prev and prev in visible:
                old=visible[prev]
                if old['patient_id']!=r['patient_id'] or old['source_table']!=r['source_table'] or old['available_date']>r['available_date']:
                    conflicts.append(r['record_id'])
                else: superseded.add(prev)
        # A correction cycle or competing correction is ambiguous: retain provenance, quarantine chain.
        children={}
        for r in visible.values():
            if r.get('supersedes_record_id'): children.setdefault(r['supersedes_record_id'],[]).append(r['record_id'])
        for parent,kids in children.items():
            if len(kids)>1: conflicts.extend([parent,*kids])
        for rid in visible:
            seen=set();current=rid
            while current in visible and visible[current].get('supersedes_record_id'):
                if current in seen: conflicts.extend(seen);break
                seen.add(current);current=visible[current]['supersedes_record_id']
        blocked=set(conflicts)
        for rid,r in visible.items():
            if rid in blocked: excluded['correction_conflict']+=1;continue
            if rid in superseded: excluded['superseded']+=1;continue
            patients[r['patient_id']]['records'].append(r)
        source_meta=json.loads(self.db.execute('SELECT manifest FROM releases WHERE id=?',(release,)).fetchone()[0])
        extra={'source_dataset':{k:source_meta[k] for k in ('adapter','source','source_sha256','original_patient_id_digest')},
               'records_with_unknown_event_date':sum(r.get('event_date') is None for p in patients.values() for r in p['records'])} if source_meta.get('adapter') else {}
        return patients,{**extra,'release':release,'as_of':cutoff,'excluded':dict(excluded),
                         'visible_records':sum(len(p['records']) for p in patients.values()),
                         'identity_method':'Explicit source crosswalk; no inferred fuzzy merge',
                         'coverage':'Unknown outside the observed contributing sources'}
    def cached(self,key):
        row=self.db.execute('SELECT payload FROM results WHERE key=?',(key,)).fetchone()
        return json.loads(row[0]) if row else None
    def save(self,key,result):
        with self.db: self.db.execute('INSERT OR REPLACE INTO results VALUES (?,?)',(key,json.dumps(result,allow_nan=False)))
