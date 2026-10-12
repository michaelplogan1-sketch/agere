"""Preserve attached machine answers across refreshes without rewriting engines.

Preparation inherits current attachments and unions explicitly added answers.
A transactional database guard rejects older proposals that would drop answers,
including a proposal prepared before a concurrent attachment was saved.
"""
import copy,hashlib,json,sqlite3
from pathlib import Path
from ...contracts import digest
from . import program_work_v1 as storage, synthesis_work

VERSION='work-context-preservation/1'
DROP_MESSAGE='Attached machine answers would be lost; prepare a current proposal'

def identity():return digest([VERSION,hashlib.sha256(Path(__file__).read_bytes()).hexdigest()])

def attached(path,program,patients=None):
    db=storage.database(path)
    try:
        allowed=None if patients is None else set(patients);result={}
        for pid,raw in db.execute('''SELECT w.patient,a.payload FROM work_items w
            JOIN work_assessments a ON a.id=json_extract(w.payload,'$.assessment_id')
            WHERE w.program=? ORDER BY w.patient''',(program,)):
            if allowed is not None and pid not in allowed:continue
            ctx=json.loads(raw).get('synthesis_context')
            if ctx is not None:result[pid]=ctx
        return result
    finally:db.close()

def inherit(path,program,patients,kwargs):
    existing=attached(path,program,patients)
    if not existing:return kwargs
    provided=kwargs.get('synthesis_contexts')
    if provided is not None and not isinstance(provided,dict):raise ValueError('Patient-scoped synthesis context mapping required')
    provided=provided or {};merged=copy.deepcopy(provided)
    for pid,old in existing.items():
        new=provided.get(pid)
        if new is None:
            merged[pid]=old
        elif old!=new:
            # Validate both inputs before constructing a union; rebuilding must
            # never sanitize a malformed, stale or cross-patient attachment.
            if not isinstance(new,dict) or new.get('patient_id')!=pid:
                raise ValueError('Synthesis context must match selected patient')
            synthesis_work.verify(path,[old,new])
            ids=sorted({a['answer_id'] for c in (old,new) for a in c['answers']})
            merged[pid]=synthesis_work.contexts(path,ids,[pid])[pid]
    return {**kwargs,'synthesis_contexts':merged}

def dependency(path,programs):
    return [identity(),[[p,attached(path,p)] for p in sorted(set(programs))]]

def install_guard(path):
    db=storage.database(path)
    try:
        db.executescript('''
          CREATE TRIGGER IF NOT EXISTS preserve_machine_answer_attachments_v1
          BEFORE UPDATE OF payload ON work_items
          WHEN EXISTS (
            SELECT 1 FROM work_assessments old_a,
              json_each(old_a.payload,'$.synthesis_context.answers') old_answer
            WHERE old_a.id=json_extract(OLD.payload,'$.assessment_id')
            AND NOT EXISTS (
              SELECT 1 FROM work_assessments new_a,
                json_each(new_a.payload,'$.synthesis_context.answers') new_answer
              WHERE new_a.id=json_extract(NEW.payload,'$.assessment_id')
                AND json_extract(new_answer.value,'$.answer_id')=json_extract(old_answer.value,'$.answer_id')
            )
          ) BEGIN SELECT RAISE(ABORT,'Attached machine answers would be lost; prepare a current proposal'); END;
        ''')
    finally:db.close()

def materialize(path,engine,proposal_id,actor):
    install_guard(path)
    try:return engine.materialize(path,proposal_id,actor)
    except sqlite3.IntegrityError as error:
        if DROP_MESSAGE in str(error):raise storage.WorkConflict(DROP_MESSAGE) from error
        raise
