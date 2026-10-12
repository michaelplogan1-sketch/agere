"""Carry machine case interpretations into existing program assessments as context."""
from collections import defaultdict
from pathlib import Path
import hashlib
from ...contracts import digest
from . import source_synthesis as case_synthesis
from . import evidence_needs

VERSION='case-synthesis-work-context/1'
AUTHORITY='machine_proposal_context_only'

def identity():
    return digest([VERSION,hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),case_synthesis.code_identity(),evidence_needs.identity()])

def contexts(path, answer_ids, patient_ids):
    if not isinstance(answer_ids,list) or not 1 <= len(answer_ids) <= 8 or any(not isinstance(k,str) for k in answer_ids) or len(set(answer_ids))!=len(answer_ids):
        raise ValueError('One to eight distinct saved synthesis IDs required')
    allowed=set(patient_ids);groups=defaultdict(list)
    for key in sorted(answer_ids):
        answer=case_synthesis.inspect(path,key)
        if answer['patient_id'] not in allowed:raise ValueError('Synthesis belongs to a patient outside this review selection')
        needs=evidence_needs.prepare(path,key)
        groups[answer['patient_id']].append({'answer_id':key,'answer_hash':digest(answer),
            'context_id':answer['context_id'],'question':answer['question'],
            'finding_ids':[f['id'] for f in answer['findings']],
            'review_proposals':answer['review_steps'],
            'evidence_needs_id':needs['id'],'evidence_needs_hash':digest(needs),'evidence_needs_summary':needs['summary']})
    return {pid:{'schema':VERSION,'patient_id':pid,'code_identity':identity(),
                'authority':AUTHORITY,'answers':answers} for pid,answers in groups.items()}

def verify(path, values):
    for value in values:
        if not isinstance(value,dict) or set(value)!={'schema','patient_id','code_identity','authority','answers'} or value['schema']!=VERSION or value['authority']!=AUTHORITY or value['code_identity']!=identity():
            raise ValueError('Synthesis review-context contract changed')
        if not isinstance(value['answers'],list) or not value['answers']:
            raise ValueError('Synthesis review context requires saved answers')
        expected=contexts(path,[a['answer_id'] for a in value['answers']],[value['patient_id']])
        if expected.get(value['patient_id'])!=value:
            raise ValueError('Synthesis review context changed; prepare a new assessment')
