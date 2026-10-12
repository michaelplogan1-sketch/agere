"""Append-only machine follow-up answers for exact unresolved questions."""
from pathlib import Path
import hashlib
import json
import re
import sqlite3
from ...contracts import digest
from . import need_candidates as candidates, need_accounts as accounts, evidence_timing as timing
from . import source_synthesis, evidence_needs

VERSION='evidence-question-answer/1'


def identity():return digest([VERSION,hashlib.sha256(Path(__file__).read_bytes()).hexdigest()])


def database(path):
    db=sqlite3.connect(Path(path)/'evidence-answers.sqlite3',timeout=30)
    db.executescript('''CREATE TABLE IF NOT EXISTS answers(id TEXT PRIMARY KEY,receipt TEXT NOT NULL,need TEXT NOT NULL,payload TEXT NOT NULL);
      CREATE INDEX IF NOT EXISTS answers_question ON answers(receipt,need);
      CREATE TRIGGER IF NOT EXISTS immutable_answer_update BEFORE UPDATE ON answers BEGIN SELECT RAISE(ABORT,'Question answer history is immutable'); END;
      CREATE TRIGGER IF NOT EXISTS immutable_answer_delete BEFORE DELETE ON answers BEGIN SELECT RAISE(ABORT,'Question answer history is immutable'); END;''')
    return db


def target(request):
    if not isinstance(request,dict) or set(request)!={'receipt_id','need_id'}:raise ValueError('Exact original evidence question required')
    if not isinstance(request['receipt_id'],str) or not re.fullmatch('[a-f0-9]{64}',request['receipt_id']):raise ValueError('Exact original receipt ID required')
    if not isinstance(request['need_id'],str) or not 1<=len(request['need_id'])<=200:raise ValueError('Exact original need ID required')
    return request


def selection(path,kind,key):
    if kind=='candidates':
        row=candidates.inspect_selection(path,key);origin=candidates.inspect(path,row['candidate_id'])['binding']
    elif kind=='account':
        row=accounts.inspect_context(path,key);origin=accounts.inspect(path,row['account_receipt_id'])['binding']
    elif kind=='candidate_window':
        row=timing.inspect_selection(path,key);window=timing.inspect_window(path,row['window_id'])
        request=timing.inspect(path,window['timing_id'])['binding']['request']
        if request['kind']!='candidates':raise ValueError('Dated question follow-up requires a candidate question origin')
        origin=candidates.inspect(path,request['id'])['binding']
    else:raise ValueError('Unsupported question follow-up selection')
    return row,origin


def binding(path,request):
    if not isinstance(request,dict) or set(request)!={'receipt_id','need_id','selection_kind','selection_id','answer_id'}:raise ValueError('Exact question, evidence selection and saved answer required')
    root=target({k:request[k] for k in ('receipt_id','need_id')})
    _,need,_,original=candidates.input_state(path,**root)
    selected,origin=selection(path,request['selection_kind'],request['selection_id'])
    if any(origin[k]!=request[k] for k in ('receipt_id','need_id')):raise ValueError('Selection belongs to a different evidence question')
    answer=source_synthesis.inspect(path,request['answer_id'])
    if answer.get('status')!='complete' or (len(answer.get('passes',[]))>1 and not answer.get('reconciliation',{}).get('groups')):raise ValueError('Complete saved interpretation including required reconciliation needed')
    if answer['context_id']!=selected['context_id'] or answer['patient_id']!=original['patient_id']:raise ValueError('Saved interpretation must use the exact selected context and patient')
    return need,answer,{'original_question':original,'selection_hash':digest(selected),'context_id':selected['context_id'],'answer_hash':digest(answer)}


def attach(path,request):
    need,answer,deps=binding(path,request);needs=evidence_needs.prepare(path,answer['id'],[])
    result={'schema':VERSION,**request,'implementation':identity(),'dependencies':deps,'original_question':need['question'],
        'followup_needs_id':needs['id'],'summary':{'findings':len(answer['findings']),
            'exact_citations':sum(len(f['citations']) for f in answer['findings']),'review_questions':len(answer['review_steps']),**needs['summary']},
        'status':'machine_interpretation_attached_for_review','requirement_resolved':False,'human_review_recorded':False,
        'clinical_execution_authorized':False,'new_paid_api_calls':0,
        'scope':'This exact evidence selection produced a saved machine interpretation. It does not establish that the original question is answered.'}
    result['id']=digest(result)
    if binding(path,request)[2]!=deps:raise ValueError('Question answer dependencies changed during attachment')
    db=database(path)
    try:
        with db:db.execute('INSERT OR IGNORE INTO answers VALUES (?,?,?,?)',(result['id'],request['receipt_id'],request['need_id'],json.dumps(result)))
        if json.loads(db.execute('SELECT payload FROM answers WHERE id=?',(result['id'],)).fetchone()[0])!=result:raise ValueError('Question answer history integrity changed')
    finally:db.close()
    return result


def inspect(path,request):
    target(request);db=database(path)
    try:rows=[json.loads(r[0]) for r in db.execute('SELECT payload FROM answers WHERE receipt=? AND need=? ORDER BY rowid',(request['receipt_id'],request['need_id']))]
    finally:db.close()
    if not rows:candidates.input_state(path,**request)
    for row in rows:
        reasons=[]
        if digest({k:v for k,v in row.items() if k!='id'})!=row['id'] or row['implementation']!=identity():reasons.append('Question attachment identity changed')
        try:
            if binding(path,{k:row[k] for k in ('receipt_id','need_id','selection_kind','selection_id','answer_id')})[2]!=row['dependencies']:reasons.append('Question interpretation dependencies changed')
            evidence_needs.inspect(path,row['followup_needs_id'])
        except (ValueError,KeyError,FileNotFoundError) as exc:reasons.append(str(exc))
        row['current']=not reasons;row['stale_reasons']=reasons
    return {'schema':VERSION,**request,'answers':rows,'requirement_resolved':False,'human_review_recorded':False,'new_paid_api_calls':0}
