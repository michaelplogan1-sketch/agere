"""Sample real passages from the warehouse for labeling: >= N per frame, diverse concepts, stratified to include negated, historical,
family and planned wording. Reads the semantic layer read-only; writes items.jsonl (no labels, no model output).

    .venv/bin/python -m tools.note_labels.sample_items --db <semantic.sqlite3> --out tools/note_labels/items.jsonl --per-frame 44
Seeded and deterministic. Patients named in the dev set's single_patient_unstructured DEV questions are excluded so the end-to-end
check on that bucket is independent of the calibration items.
"""
from __future__ import annotations

import argparse
import json
import random
import re
import sys
from pathlib import Path

if __package__ in (None, ''):
    sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from clinical_core.agere.clinical import note_evidence as ne

SEED = 20261023
CONCEPTS = {
    'condition': ['heart failure', 'diabetes', 'hypertension', 'chronic kidney disease', 'COPD', 'depression', 'atrial fibrillation',
                  'anemia', 'hypothyroidism', 'asthma', 'pneumonia', 'urinary tract infection', 'coronary artery disease', 'obesity',
                  'osteoarthritis', 'cancer', 'dementia', 'stroke'],
    'symptom': ['chest pain', 'shortness of breath', 'edema', 'cough', 'dizziness', 'fatigue', 'pain', 'fever', 'nausea', 'palpitations'],
    'medication_event': ['metformin', 'lisinopril', 'aspirin', 'statin', 'losartan', 'spironolactone', 'empagliflozin', 'insulin',
                         'furosemide', 'carvedilol', 'oxycodone', 'amlodipine', 'atorvastatin', 'warfarin'],
    'procedure_test': ['echocardiogram', 'chest x-ray', 'stress test', 'colonoscopy', 'mammogram', 'MRI', 'CT scan', 'biopsy',
                       'EKG', 'A1c test', 'ultrasound', 'dialysis'],
    'finding_value': ['A1c', 'blood pressure', 'LDL', 'eGFR', 'weight', 'ejection fraction', 'PHQ-9', 'BMI', 'potassium', 'creatinine',
                      'heart rate'],
    'adverse_event': ['allergy', 'intolerance', 'fall', 'cough', 'hyperkalemia', 'angioedema', 'rash', 'side effects', 'bleeding'],
    'care_process': ['goals of care', 'advance directive', 'code status', 'referral', 'discharge instructions', 'medication reconciliation',
                     'education', 'home health', 'hospice', 'vaccination', 'rehabilitation', 'palliative'],
    'social_need': ['transportation', 'food', 'housing', 'financial', 'isolation', 'caregiver', 'cost', 'language'],
    'function_cognition': ['falls', 'memory', 'walking', 'balance', 'ADL', 'vision', 'hearing', 'cognition', 'mobility'],
    'preference_refusal': ['declines', 'refuses', 'does not want', 'comfort-focused', 'prefers'],
    'plan_followup': ['follow-up', 'referral', 'return precautions', 'discharge to', 'recheck', 'schedule'],
}
CUES = {
    'negated': r'\b(denies|denied|no |not |without|negative for|none)\b',
    'historical': r'\b(history of|h/o|previous|prior|past|resolved|was on|until)\b',
    'family': r'\b(mother|father|brother|sister|family history|sibling|daughter|son)\b',
    'planned': r'\b(plan|will |recommend|refer|schedul|discuss|declin|due|consider|start)\b',
}


def dev_excluded(root):
    ex = set()
    for line in (root / 'tools/dev_set/questions.jsonl').open():
        q = json.loads(line)
        if q.get('split') == 'dev':
            pid = (q.get('key') or {}).get('patient_id')
            if pid:
                ex.add(pid)
    return ex


def load_texts(db):
    streams = list(ne.NOTE_STREAMS + ne.HIE_STREAMS + ne.OTHER_STREAMS)
    q = ','.join('?' * len(streams))
    rows = db.execute(f"select record_id, patient_id, stream, event_date, json_extract(payload,'$.text') t, "
                      f"coalesce(json_extract(payload,'$.note_type'), json_extract(payload,'$.document_type')) nt "
                      f"from record where stream in ({q}) and event_date<=?", (*streams, '2030-05-27')).fetchall()
    out = [(r['record_id'], r['patient_id'], r['stream'], r['event_date'], r['nt'] or r['stream'], r['t']) for r in rows if r['t']]
    for r in db.execute('select t.record_id, t.patient_id, t.text, d.document_type, d.event_date from scanned_document_text t '
                        'left join scanned_document d on d.record_id=t.record_id'):
        if r['text']:
            out.append((r['record_id'], r['patient_id'], 'scanned_document_text', r['event_date'], r['document_type'] or 'scanned_document', r['text']))
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--db', required=True)
    ap.add_argument('--out', required=True)
    ap.add_argument('--per-frame', type=int, default=44)
    a = ap.parse_args()
    root = Path(__file__).resolve().parents[2]
    rng = random.Random(SEED)
    db = ne.connect(a.db)
    excluded = dev_excluded(root)
    texts = [t for t in load_texts(db) if t[1] not in excluded and t[3]]
    print(f'{len(texts)} text records', file=sys.stderr)
    items, used = [], set()
    for frame, concepts in CONCEPTS.items():
        pool = {}
        for term in concepts:
            pats = ne.concept_patterns({'term': term})
            for rid, pid, stream, day, nt, text in rng.sample(texts, len(texts)):
                if len(pool.setdefault(term, [])) >= 400:
                    break
                for ps in ne.passages_in(text, pats):
                    pool[term].append({'record_id': rid, 'patient_id': pid, 'stream': stream, 'date': day, 'note_type': nt, **ps})
                    break
        # stratified draw: round-robin over concepts, alternating cue strata and random
        want, got, k = a.per_frame, [], 0
        strata = list(CUES) + ['any']
        seen_text = set()
        while len(got) < want and any(pool.values()):
            term = concepts[k % len(concepts)]
            stratum = strata[(k // len(concepts) + k) % len(strata)]
            k += 1
            cand = pool.get(term, [])
            pick = None
            for i, c in enumerate(cand):
                key = re.sub(r'\W+', ' ', c['text'].lower())
                if key in seen_text or (c['record_id'], c['start']) in used:
                    continue
                if stratum == 'any' or re.search(CUES[stratum], c['text'], re.I):
                    pick = i
                    break
            if pick is None:
                if k > want * len(concepts) * 6:
                    break
                continue
            c = cand.pop(pick)
            seen_text.add(re.sub(r'\W+', ' ', c['text'].lower()))
            used.add((c['record_id'], c['start']))
            got.append({'id': f'{frame[:4]}-{len(got) + 1:03d}', 'frame': frame, 'concept': {'term': term}, 'stratum': stratum,
                        'patient_id': c['patient_id'], 'record_id': c['record_id'], 'date': c['date'], 'note_type': c['note_type'],
                        'stream': c['stream'], 'start': c['start'], 'end': c['end'], 'text': c['text']})
        items += got
        print(frame, len(got), file=sys.stderr)
    Path(a.out).write_text(''.join(json.dumps(i, sort_keys=True) + '\n' for i in items))


if __name__ == '__main__':
    main()
