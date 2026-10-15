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

def adjudication_step(parent,child):
    """True when a supersedes link is the next step of a claim's adjudication chain, not a correction of the record: the
    parent adjudication stays current (a reversal, recoupment or later adjudication sequence is an additional money event;
    a void plus its '-R' replacement keep the original payment that they reverse). Payer medical claims: another
    adjudication_sequence or payer_claim_id; payer pharmacy claims: a 'reversed' / 'recouped' child; provider medical
    claims: a 'reversed' child."""
    table=child.get('source_table')
    if table=='payer_medical_claims':
        return child.get('adjudication_sequence')!=parent.get('adjudication_sequence') or child.get('payer_claim_id')!=parent.get('payer_claim_id')
    if table=='payer_pharmacy_claims': return child.get('claim_status') in ('reversed','recouped')
    if table=='medical_claims': return child.get('status')=='reversed'
    return False


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
        # Supersession (warehouse v2.8+ supersedes_record_id; observed-release corrections): a record named in a VISIBLE
        # record's supersedes_record_id is no longer current; every superseding child stays current, including several
        # children of one parent (a claim void plus its replacement: the claim-version logic downstream decides what a
        # void means). Superseded parents stay in the record store, so a cutoff before the child's arrival still sees
        # them. Only cross-patient/cross-stream links and cycles are ambiguous: those records are quarantined.
        visible={}; excluded=Counter(); hidden=Counter(); stream_of={}
        for rid,pid,stream,event,arrival,identity,payload in self.db.execute(
                'SELECT id,patient,stream,event,arrival,identity,payload FROM records WHERE release=? ORDER BY event,id',(release,)):
            if event is not None and event>cutoff: excluded['future']+=1;hidden[stream]+=1;continue
            if arrival>cutoff: excluded['late_arrival']+=1;hidden[stream]+=1;continue
            if identity!='matched': excluded['identity_quarantine']+=1;hidden[stream]+=1;continue
            visible[rid]=json.loads(payload);stream_of[rid]=stream
        superseded=set(); conflicts=[]
        for r in visible.values():
            prev=r.get('supersedes_record_id')
            if prev and prev in visible:
                old=visible[prev]
                if old['patient_id']!=r['patient_id'] or old['source_table']!=r['source_table']:
                    conflicts.append(r['record_id'])
                elif not adjudication_step(old,r): superseded.add(prev)
        # A correction cycle is ambiguous: retain provenance, quarantine the chain.
        for rid in visible:
            seen=set();current=rid
            while current in visible and visible[current].get('supersedes_record_id'):
                if current in seen: conflicts.extend(seen);break
                seen.add(current);current=visible[current]['supersedes_record_id']
        blocked=set(conflicts); by_stream={'superseded':Counter(),'correction_conflict':Counter()}
        for rid,r in visible.items():
            if rid in blocked: excluded['correction_conflict']+=1;by_stream['correction_conflict'][stream_of[rid]]+=1;continue
            if rid in superseded: excluded['superseded']+=1;by_stream['superseded'][stream_of[rid]]+=1;continue
            patients[r['patient_id']]['records'].append(r)
        # Per-stream reconciliation (current + superseded + not visible at the cutoff + quarantined = ingested); only
        # recorded when non-empty, so snapshots of releases without supersession or late rows are unchanged.
        reconciliation={k:dict(v) for k,v in (('superseded_by_stream',by_stream['superseded']),('not_visible_by_stream',hidden),
                        ('correction_conflict_by_stream',by_stream['correction_conflict'])) if v}
        source_meta=json.loads(self.db.execute('SELECT manifest FROM releases WHERE id=?',(release,)).fetchone()[0])
        extra={'source_dataset':{k:source_meta[k] for k in ('adapter','source','source_sha256','original_patient_id_digest')},
               'records_with_unknown_event_date':sum(r.get('event_date') is None for p in patients.values() for r in p['records'])} if source_meta.get('adapter') else {}
        return patients,{**extra,**reconciliation,'release':release,'as_of':cutoff,'excluded':dict(excluded),
                         'visible_records':sum(len(p['records']) for p in patients.values()),
                         'identity_method':'Explicit source crosswalk; no inferred fuzzy merge',
                         'coverage':'Unknown outside the observed contributing sources'}
    def cached(self,key):
        row=self.db.execute('SELECT payload FROM results WHERE key=?',(key,)).fetchone()
        return json.loads(row[0]) if row else None
    def save(self,key,result):
        with self.db: self.db.execute('INSERT OR REPLACE INTO results VALUES (?,?)',(key,json.dumps(result,allow_nan=False)))
