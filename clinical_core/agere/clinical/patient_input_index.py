"""Exact incremental fingerprints for derived warehouse patient inputs.

SQLite mutation triggers mark both sides of moved records. Hashes are rebuilt
inside the same transaction as the dirty-set read, so writers cannot race a
fingerprint. No timestamps, file-size guesses or model judgments decide reuse.
The received input warehouse is never modified.
"""
from pathlib import Path
import hashlib,json,sqlite3,time
from ...contracts import digest

VERSION='patient-input-fingerprints/1'
OPERATORS=('heart_failure_review','affordability_review')
PREFIX='agere_inputs_'


def identity():return digest([VERSION,hashlib.sha256(Path(__file__).read_bytes()).hexdigest()])


def trigger_definitions(*,legacy=False):
    statements={}
    for table,column in [('patients','id'),('records','patient'),('analyses','patient')]:
        for event,aliases in [('INSERT',['NEW']),('DELETE',['OLD']),('UPDATE',['OLD','NEW'])]:
            name=PREFIX+table+'_'+event.lower()
            condition=''
            if table=='analyses':condition=' WHEN '+' OR '.join(a+".operator IN ('heart_failure_review','affordability_review')" for a in aliases)
            body=' '.join('INSERT OR IGNORE INTO agere_inputs_dirty(patient) VALUES ('+a+'.'+column+');' for a in aliases)
            timing='AFTER'
            if table=='records' and event=='INSERT' and not legacy:
                # REPLACE does not reliably fire delete triggers when recursive
                # triggers are disabled. Capture the prior owner before removal.
                timing='BEFORE'
                body='INSERT OR IGNORE INTO agere_inputs_dirty SELECT patient FROM records WHERE id=NEW.id; '+body
            statements[name]=f'CREATE TRIGGER {name} {timing} {event} ON {table}{condition} BEGIN {body} END'
    return statements


def parts(patient_raw,record_rows,analysis_rows):
    records=[digest(raw) for raw in record_rows]
    analyses=[[op,digest(raw)] for op,raw in analysis_rows]
    patient=digest(patient_raw)
    # The protocol fingerprint is byte-identical to its original full scan.
    record_hash=hashlib.sha256(''.join(records).encode()).hexdigest() if records else ''
    return {'protocol_hash':digest([patient,record_hash,analyses]),
            'baseline_components':[patient,digest(records),digest(analyses)],
            'record_count':len(records),'analysis_count':len(analyses)}


def snapshot(path):
    target=Path(path)/'stack.sqlite3'
    if not target.is_file():raise ValueError('Existing derived warehouse required')
    started=time.perf_counter();db=sqlite3.connect(target,timeout=120);code=identity()
    stats={'patients_recomputed':0,'patient_cache_hits':0,'record_payloads_hashed':0,'analysis_payloads_hashed':0,
           'trigger_set_reinstalled':False,'received_source_modified':False}
    try:
        db.execute('BEGIN IMMEDIATE')
        db.execute('CREATE TABLE IF NOT EXISTS agere_inputs_meta(id INTEGER PRIMARY KEY CHECK(id=1),code TEXT NOT NULL)')
        db.execute('CREATE TABLE IF NOT EXISTS agere_inputs_dirty(patient TEXT PRIMARY KEY)')
        db.execute('CREATE TABLE IF NOT EXISTS agere_inputs_cache(patient TEXT PRIMARY KEY,payload TEXT NOT NULL,checksum TEXT NOT NULL)')
        previous=db.execute('SELECT code FROM agere_inputs_meta WHERE id=1').fetchone()
        reinstall=previous is None or previous[0]!=code
        for name,statement in trigger_definitions().items():
            existing=db.execute("SELECT sql FROM sqlite_master WHERE type='trigger' AND name=?",(name,)).fetchone()
            if existing and existing[0]!=statement:
                if existing[0]!=trigger_definitions(legacy=True)[name]:raise ValueError('Unexpected input-fingerprint trigger definition')
                db.execute('DROP TRIGGER '+name);existing=None
            if not existing:db.execute(statement);reinstall=True;stats['trigger_set_reinstalled']=True
        if reinstall:
            db.execute('INSERT OR IGNORE INTO agere_inputs_dirty SELECT id FROM patients')
            db.execute('INSERT OR IGNORE INTO agere_inputs_dirty SELECT patient FROM agere_inputs_cache')
            db.execute('INSERT OR REPLACE INTO agere_inputs_meta VALUES (1,?)',(code,))
        patients={r[0] for r in db.execute('SELECT id FROM patients')}
        dirty={r[0] for r in db.execute('SELECT patient FROM agere_inputs_dirty')}
        cached={r[0] for r in db.execute('SELECT patient FROM agere_inputs_cache')}
        dirty|=patients-cached
        # Sources outside the roster must not disappear from a supposedly complete
        # population fingerprint, including after deleting a patient row.
        if reinstall:
            unknown=db.execute('SELECT patient FROM records WHERE patient NOT IN (SELECT id FROM patients) LIMIT 1').fetchone()
            if unknown:raise ValueError('Clinical input patient absent from roster')
            unknown=db.execute("SELECT patient FROM analyses WHERE operator IN ('heart_failure_review','affordability_review') AND patient NOT IN (SELECT id FROM patients) LIMIT 1").fetchone()
            if unknown:raise ValueError('Clinical analysis patient absent from roster')
        for pid in sorted(dirty):
            if pid not in patients:
                if db.execute('SELECT 1 FROM records WHERE patient=? LIMIT 1',(pid,)).fetchone():raise ValueError('Clinical input patient absent from roster')
                if db.execute("SELECT 1 FROM analyses WHERE patient=? AND operator IN ('heart_failure_review','affordability_review') LIMIT 1",(pid,)).fetchone():raise ValueError('Clinical analysis patient absent from roster')
                db.execute('DELETE FROM agere_inputs_cache WHERE patient=?',(pid,));continue
            raw=db.execute('SELECT payload FROM patients WHERE id=?',(pid,)).fetchone()[0]
            records=(r[0] for r in db.execute('SELECT payload FROM records WHERE patient=? ORDER BY id',(pid,)))
            analyses=db.execute("SELECT operator,payload FROM analyses WHERE patient=? AND operator IN ('heart_failure_review','affordability_review') ORDER BY operator",(pid,)).fetchall()
            value=parts(raw,records,analyses);payload=json.dumps(value,sort_keys=True,separators=(',',':'))
            db.execute('INSERT OR REPLACE INTO agere_inputs_cache VALUES (?,?,?)',(pid,payload,digest(payload)))
            stats['patients_recomputed']+=1;stats['record_payloads_hashed']+=value['record_count'];stats['analysis_payloads_hashed']+=value['analysis_count']
        result={}
        for pid,payload,checksum in db.execute('SELECT patient,payload,checksum FROM agere_inputs_cache ORDER BY patient'):
            if digest(payload)!=checksum:raise ValueError('Input fingerprint cache checksum mismatch')
            if pid in patients:result[pid]=json.loads(payload)
        if set(result)!=patients:raise ValueError('Incomplete patient input fingerprints')
        db.execute('DELETE FROM agere_inputs_dirty');db.commit()
        stats['patient_cache_hits']=len(patients)-stats['patients_recomputed'];stats['elapsed_seconds']=time.perf_counter()-started
        return {'schema':VERSION,'patients':result,'processing':stats,'identity':digest([code,result])}
    finally:db.close()
