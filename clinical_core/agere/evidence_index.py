"""Local full-text source index. Search results are evidence leads, not answers.

Stores full received fields once, indexes overlapping windows without truncating
long notes, and returns exact field-relative spans. No remote calls or PHI egress.
"""
from collections import Counter
from pathlib import Path
import json
import re
import sqlite3
from .narrative_sources import text_fields, coverage

VERSION = 'narrative-evidence-index/1'
STOP = set('a an the is are was were be been being and or of for to in on at by with from that this these those who which what where when why how find show me all patient patients have has had do does did can could should would need needs their them they we our i it its'.split())


def windows(text, width=1400, overlap=200):
    start=0
    while start < len(text):
        end=min(len(text),start+width)
        # Prefer a whitespace boundary without dropping any characters.
        if end < len(text):
            space=text.rfind(' ',start+width//2,end)
            if space>start:end=space
        yield start,end,text[start:end]
        if end==len(text):break
        start=max(start+1,end-overlap)


def build(path, patients, snapshot):
    path=Path(path)
    if path.exists():raise ValueError('Use a new evidence index; historical snapshots are immutable')
    db=sqlite3.connect(path)
    try:
        db.executescript('''
        CREATE TABLE fields (id INTEGER PRIMARY KEY,patient TEXT,record TEXT,stream TEXT,field TEXT,event TEXT,arrival TEXT,text TEXT);
        CREATE INDEX patient_fields ON fields(patient);
        CREATE TABLE chunks (id INTEGER PRIMARY KEY,field_id INTEGER,start INTEGER,end INTEGER);
        CREATE VIRTUAL TABLE search USING fts5(text, tokenize='unicode61');
        CREATE TABLE metadata (key TEXT PRIMARY KEY,payload TEXT);
        ''')
        fields=0; chunks=0; totals=Counter(); unmapped=Counter(); field_counts=Counter(); unread=[]; text_records=0;chars=0
        with db:
            for pid,p in patients.items():
                cov=coverage(p['records']);totals.update(cov['records_by_source']);field_counts.update(cov['text_fields'])
                unmapped.update(cov['unmapped_string_fields']);unread.extend(cov['documents_without_received_text'])
                text_records+=cov['records_with_received_text'];chars+=cov['text_characters']
                for r in p['records']:
                    if r['patient_id']!=pid:raise ValueError('Cross-patient indexing input')
                    if r['event_date']>snapshot['as_of'] or r['available_date']>snapshot['as_of'] or r.get('identity_status')!='matched':
                        raise ValueError('Index requires a visible identity-matched snapshot')
                    for part in text_fields(r):
                        fields+=1
                        db.execute('INSERT INTO fields VALUES (?,?,?,?,?,?,?,?)',(fields,pid,r['record_id'],r['source_table'],part['field'],r['event_date'],r['available_date'],part['text']))
                        for start,end,text in windows(part['text']):
                            chunks+=1
                            db.execute('INSERT INTO chunks VALUES (?,?,?,?)',(chunks,fields,start,end))
                            db.execute('INSERT INTO search(rowid,text) VALUES (?,?)',(chunks,text))
            report={'version':VERSION,'synthetic':True,'snapshot':snapshot,'fields':fields,'indexed_chunks':chunks,
                    'records_with_received_text':text_records,'text_characters':chars,'records_by_source':dict(totals),
                    'text_fields':dict(field_counts),'unmapped_string_fields':dict(unmapped),
                    'documents_without_received_text':unread,'complete_semantic_understanding':False,
                    'method':'SQLite FTS5 BM25; overlapping full-length source fields; no embeddings in this population index',
                    'limitations':['Search is not clinical entailment or an exhaustive cohort','Negation and other-person text remain searchable for contradictory evidence',
                                   'Unmapped source fields require explicit adapter review','Searchable does not mean clinically normalized']}
            db.execute('INSERT INTO metadata VALUES (?,?)',('report',json.dumps(report)))
        return report
    finally:db.close()


def search(path, question, *, patient_id=None, limit=8):
    if not isinstance(question,str) or not question.strip() or len(question)>2000:raise ValueError('A question of 1..2000 characters is required')
    if type(limit) is not int or not 1<=limit<=50:raise ValueError('Limit must be 1..50')
    terms=list(dict.fromkeys(t.lower() for t in re.findall(r'\w+',question) if t.lower() not in STOP))[:64]
    expression=' OR '.join('"'+t.replace('"','""')+'"' for t in terms)
    result={'question':question,'synthetic':True,'status':'evidence_leads_only','question_fully_answered':False,
            'method':'local FTS5 lexical BM25', 'complete_evidence_guarantee':False,
            'cohort_count':None,'evidence':[],
            'meaning':'Ranked source passages, including contradictions; neither clinical conclusions nor an exhaustive population count'}
    db=sqlite3.connect(Path(path).resolve().as_uri()+'?mode=ro',uri=True)
    try:
        report=json.loads(db.execute('SELECT payload FROM metadata WHERE key=?',('report',)).fetchone()[0])
        result['snapshot']=report['snapshot'];result['indexed_fields']=report['fields']
        if not terms:result['status']='no_search_terms';return result
        query='''SELECT f.patient,f.record,f.stream,f.field,f.event,f.arrival,f.text,c.start,c.end,bm25(search)
                 FROM search JOIN chunks c ON c.id=search.rowid JOIN fields f ON f.id=c.field_id
                 WHERE search MATCH ?'''
        args=[expression]
        if patient_id is not None:query+=' AND f.patient=?';args.append(patient_id)
        query+=' ORDER BY bm25(search),f.record,f.field,c.start LIMIT ?';args.append(limit*8)
        seen={}
        for pid,rid,stream,field,event,arrival,text,start,end,score in db.execute(query,args):
            key=(pid,rid,field)
            if any(start<e and end>s for s,e in seen.get(key,[])):continue
            seen.setdefault(key,[]).append((start,end))
            result['evidence'].append({'patient_id':pid,'record_id':rid,'source_table':stream,'field':field,
                'event_date':event,'available_date':arrival,'start':start,'end':end,'quote':text[start:end],
                'rank_score':-score,'source_verified':True})
            if len(result['evidence'])==limit:break
        if not result['evidence']:result['status']='no_lexical_matches_not_no_care'
        return result
    finally:db.close()
