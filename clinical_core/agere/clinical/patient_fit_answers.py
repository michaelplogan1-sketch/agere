"""Return saved bounded interpretations to exact patient-fit source work."""
from pathlib import Path
import hashlib,json,sqlite3
from ...contracts import digest
from . import patient_fit_work as work,source_synthesis,evidence_needs

VERSION='patient-fit-answer/1'
def identity():return digest([VERSION,hashlib.sha256(Path(__file__).read_bytes()).hexdigest()])
def database(path):
    db=sqlite3.connect(Path(path)/'patient-fit-answers.sqlite3',timeout=30)
    db.executescript('''CREATE TABLE IF NOT EXISTS answers(id TEXT PRIMARY KEY,target TEXT NOT NULL,payload TEXT NOT NULL);
      CREATE INDEX IF NOT EXISTS answers_target ON answers(target);
      CREATE TRIGGER IF NOT EXISTS immutable_answers_update BEFORE UPDATE ON answers BEGIN SELECT RAISE(ABORT,'Patient-fit answers are immutable'); END;
      CREATE TRIGGER IF NOT EXISTS immutable_answers_delete BEFORE DELETE ON answers BEGIN SELECT RAISE(ABORT,'Patient-fit answers are immutable'); END;''')
    return db

def binding(path,request):
    work.care.exact(request,'target acquisition_id answer_id');target=work.resolve(path,request['target'])
    acquisition=next((a for a in work.acquisitions(path,request['target']) if a['id']==request['acquisition_id']),None)
    if acquisition is None:raise ValueError('Acquisition must belong to this exact patient-fit question')
    if acquisition['implementation']!=work.identity():raise ValueError('Patient-fit acquisition contract changed')
    context=work.record_context.verified(path,acquisition['acquisition']['context_id'])
    if digest(context)!=acquisition['context_hash'] or context['patient_id']!=target['patient_id'] or context['parameters']['question']!=target['question']:
        raise ValueError('Patient-fit acquisition evidence changed')
    answer=source_synthesis.inspect(path,request['answer_id'])
    if answer.get('status')!='complete' or (len(answer.get('passes',[]))>1 and not answer.get('reconciliation',{}).get('groups')):
        raise ValueError('Complete saved interpretation and any required reconciliation needed')
    if answer['context_id']!=context['id'] or answer['patient_id']!=target['patient_id']:
        raise ValueError('Saved interpretation must use this exact question context and patient')
    return target,answer,{'target_hash':digest(target),'acquisition_hash':digest(acquisition),'context_id':context['id'],'answer_hash':digest(answer)}

def attach(path,request):
    target,answer,deps=binding(path,request);needs=evidence_needs.prepare(path,answer['id'],[])
    row={'schema':VERSION,**request,'implementation':identity(),'dependencies':deps,'question':target['question'],
        'task_id':target['task_id'],'followup_needs_id':needs['id'],
        'summary':{'findings':len(answer['findings']),'exact_citations':sum(len(f['citations']) for f in answer['findings']),
            'review_questions':len(answer['review_steps']),**needs['summary']},
        'status':'machine_interpretation_attached_for_review','human_review_recorded':False,**work.LIMITS}
    row['id']=digest(row);db=database(path)
    try:
        with db:
            db.execute('BEGIN IMMEDIATE')
            if binding(path,request)[2]!=deps:raise ValueError('Patient-fit answer dependencies changed during attachment')
            db.execute('INSERT OR IGNORE INTO answers VALUES (?,?,?)',(row['id'],target['id'],json.dumps(row,allow_nan=False)))
            saved=work.verified(json.loads(db.execute('SELECT payload FROM answers WHERE id=?',(row['id'],)).fetchone()[0]))
            if saved!=row:raise ValueError('Patient-fit answer differs from original receipt')
    finally:db.close()
    return saved

def inspect(path,request):
    work.care.exact(request,'target')
    target=request['target'];work.care.exact(target,'kind manifest_id question_id')
    manifest=work.load(path,target['manifest_id'])
    if target['kind']!=work.KIND or not any(q['id']==target['question_id'] for q in manifest['questions']):raise ValueError('Exact original patient-fit question required')
    db=database(path)
    try:rows=[work.verified(json.loads(r[0])) for r in db.execute('SELECT payload FROM answers WHERE target=? ORDER BY rowid',(digest(target),))]
    finally:db.close()
    for row in rows:
        reasons=[]
        if row['implementation']!=identity():reasons.append('Patient-fit answer contract changed')
        try:
            if binding(path,{k:row[k] for k in ('target','acquisition_id','answer_id')})[2]!=row['dependencies']:reasons.append('Saved interpretation changed')
            evidence_needs.inspect(path,row['followup_needs_id'])
        except (ValueError,KeyError,FileNotFoundError) as exc:reasons.append(str(exc))
        row['current']=not reasons;row['stale_reasons']=reasons
    return {'schema':VERSION,'target':target,'answers':rows,'human_review_recorded':False,**work.LIMITS}
