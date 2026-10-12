"""Bind saved machine interpretations to exact internal evidence follow-ups.

These are machine output attachments, never reviewer decisions or care events.
History remains immutable when the packet, review, sources or answer changes.
"""
from pathlib import Path
import hashlib
import json
import sqlite3
from ...contracts import digest
from . import packet_followup as followup, source_synthesis, evidence_needs

VERSION='packet-followup-answer/1'


def identity():return digest([VERSION,hashlib.sha256(Path(__file__).read_bytes()).hexdigest()])


def database(path):
    db=sqlite3.connect(Path(path)/'followup-answers.sqlite3',timeout=30)
    db.executescript('''
      CREATE TABLE IF NOT EXISTS answers(id TEXT PRIMARY KEY,task TEXT NOT NULL,payload TEXT NOT NULL);
      CREATE INDEX IF NOT EXISTS answers_task ON answers(task);
      CREATE TRIGGER IF NOT EXISTS immutable_answer_update BEFORE UPDATE ON answers BEGIN SELECT RAISE(ABORT,'Follow-up answer history is immutable'); END;
      CREATE TRIGGER IF NOT EXISTS immutable_answer_delete BEFORE DELETE ON answers BEGIN SELECT RAISE(ABORT,'Follow-up answer history is immutable'); END;
    ''')
    return db


def binding(path,task_id,followup_id,answer_id):
    state=followup.inspect(path,task_id)
    item=next((f for f in state['followups'] if f['id']==followup_id),None)
    if item is None:raise ValueError('Follow-up does not belong to this task')
    original={k:v for k,v in item.items() if k not in ('current','stale_reasons','id')}
    if digest(original)!=item['id']:raise ValueError('Follow-up receipt integrity changed')
    if not item['current']:raise ValueError('Follow-up is stale: '+'; '.join(item['stale_reasons']))
    answer=source_synthesis.inspect(path,answer_id)
    if answer.get('status')!='complete':raise ValueError('A complete saved interpretation is required')
    if answer['context_id']!=item['acquisition']['context_id'] or answer['patient_id']!=item['acquisition']['target']['patient_id']:
        raise ValueError('Interpretation must use this exact follow-up context and patient')
    if len(answer.get('passes',[]))>1 and not answer.get('reconciliation',{}).get('groups'):
        raise ValueError('Source passes are saved but reconciliation is still pending')
    return item,answer,{'followup_id':followup_id,'followup_hash':digest(original),
        'answer_id':answer_id,'answer_hash':digest(answer),'context_id':answer['context_id'],
        'patient_id':answer['patient_id'],'target_hash':digest(item['acquisition']['target'])}


def attach(path,request):
    followup.execution.care.exact(request,'task_id followup_id answer_id')
    for key,value in request.items():
        if not isinstance(value,str) or len(value)!=64 or any(c not in '0123456789abcdef' for c in value):raise ValueError('Exact '+key+' required')
    item,answer,dependencies=binding(path,**request)
    needs=evidence_needs.prepare(path,answer['id'],[])
    result={'schema':VERSION,**request,'implementation':identity(),'dependencies':dependencies,
        'question':item['target']['question'],'evidence_needs_id':needs['id'],
        'summary':{'findings':len(answer['findings']),'exact_citations':sum(len(f['citations']) for f in answer['findings']),
            'review_questions':len(answer['review_steps']),**needs['summary']},
        'actor_kind':'machine_attachment','status':'interpretation_attached_for_review',
        'requirement_resolved':False,'human_review_recorded':False,'care_delivered':False,
        'clinical_execution_authorized':False,'new_paid_api_calls':0,
        'scope':'Saved interpretation for this exact evidence request. Binding sources does not resolve the requirement, close the review or establish care delivery.'}
    result['id']=digest(result)
    if binding(path,**request)[2]!=dependencies:raise ValueError('Follow-up dependencies changed during attachment')
    db=database(path)
    try:
        with db:db.execute('INSERT OR IGNORE INTO answers VALUES (?,?,?)',(result['id'],request['task_id'],json.dumps(result)))
        saved=json.loads(db.execute('SELECT payload FROM answers WHERE id=?',(result['id'],)).fetchone()[0])
        if saved!=result:raise ValueError('Follow-up answer receipt integrity changed')
        return saved
    finally:db.close()


def inspect(path,task_id):
    # Validate that the requested task is part of the local work store.
    followup.execution.care_readiness.task(path,task_id)
    db=database(path)
    try:rows=[json.loads(row[0]) for row in db.execute('SELECT payload FROM answers WHERE task=? ORDER BY rowid',(task_id,))]
    finally:db.close()
    for row in rows:
        reasons=[]
        if digest({k:v for k,v in row.items() if k!='id'})!=row['id']:reasons.append('Follow-up answer receipt integrity changed')
        if row['implementation']!=identity():reasons.append('Follow-up answer attachment contract changed')
        try:
            current=binding(path,row['task_id'],row['followup_id'],row['answer_id'])[2]
            if current!=row['dependencies']:reasons.append('Follow-up interpretation dependencies changed')
            evidence_needs.inspect(path,row['evidence_needs_id'])
        except (ValueError,KeyError,FileNotFoundError) as exc:reasons.append(str(exc))
        row['current']=not reasons;row['stale_reasons']=reasons
    return {'schema':VERSION,'task_id':task_id,'answers':rows,'new_paid_api_calls':0,
        'requirement_resolved':False,'human_review_recorded':False,'care_delivered':False,
        'scope':'Machine evidence attachments; current means dependency checks pass, not clinical approval.'}
