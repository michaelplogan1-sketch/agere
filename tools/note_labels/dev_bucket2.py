"""End-to-end check of the note-frame executor on the dev set's single_patient_unstructured bucket, DEV split only.

Each dev question is expressed BY HAND as a note predicate (this tests the executor, not the parser). Two measurements:
  retrieval  (no provider needed): does the retrieved passage set contain the key's evidence quote (exact record id + quote text)?
             For `not_documented` keys the check is that the status ends up not_mentioned / unknown, and the passages read are listed.
  answer     (--live, needs Jev/Perplexity keys): run note_frames.answer_patient and score against the key: yes_no from the status of the
             combined reading, value from the detail count, short_text when every key_group regex matches the supporting quotes + details.

    .venv/bin/python -m tools.note_labels.dev_bucket2 --db <semantic.sqlite3> [--live --ledger <path>] [--out result.json]
heldout rows are never read (the file is filtered on split == 'dev' before anything else).
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

if __package__ in (None, ''):
    sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from clinical_core.agere.clinical import note_evidence as ne
from clinical_core.agere.clinical import note_frames as nf

ROOT = Path(__file__).resolve().parents[2]
AS_OF = '2030-05-27'


def P(frame, term, statuses=('documented',), **kw):
    return {'op': 'note', 'frame': frame, 'concept': {'term': term}, 'statuses': list(statuses), **kw}


# question id -> (predicate, how the answer is read)
PREDICATES = {
    'b2-02': (P('medication_event', 'aspirin', detail={'event': 'stopped'}, return_detail=['reason'], combine='any'), 'text'),
    'b2-03': (P('social_need', 'financial', return_detail=['domain', 'response'], combine='any'), 'text'),
    'b2-04': (P('preference_refusal', 'statin', detail={'decision': 'declined'}, combine='any'), 'no_if_declined'),
    'b2-05': (P('care_process', 'goals of care', detail={'kind': 'goals_of_care_discussion'}, return_detail=['outcome'], combine='any'), 'text'),
    'b2-06': (P('care_process', 'chest compressions', return_detail=['code_status'], combine='any'), 'text'),
    'b2-09': (P('function_cognition', 'falls', return_detail=['count'], combine='latest'), 'count'),
    'b2-10': (P('function_cognition', 'memory concern', combine='any'), 'yes_no'),
    'b2-11': (P('social_need', 'transportation', combine='any'), 'yes_no'),
    'b2-12': (P('medication_event', 'lisinopril', window={'start': '2029-08-06'}, combine='any'), 'yes_no'),
    'b2-14': (P('adverse_event', 'intolerance', return_detail=['agent', 'reaction'], combine='any'), 'text'),
    'b2-16': (P('plan_followup', 'follow up', window={'start': '2027-10-28', 'end': '2027-10-28'}, return_detail=['timeframe', 'disposition'],
                combine='any'), 'text'),
    'b2-17': (P('medication_event', 'oxycodone', combine='any'), 'yes_no'),
    'b2-18': (P('medication_event', 'morphine', combine='any'), 'yes_no'),
    'b2-19': (P('social_need', 'smoking', statuses=('documented', 'denied', 'historical'), combine='any'), 'not_documented'),
    'b2-20': (P('function_cognition', 'falls', detail={'domain': 'falls'}, combine='any'), 'not_documented'),
    'b2-21': (P('care_process', 'code status', statuses=('documented', 'denied'), combine='any'), 'not_documented'),
    'b2-22': (P('medication_event', 'losartan', detail={'event': 'stopped'}, return_detail=['reason'], combine='any'), 'text'),
    'b2-24': (P('medication_event', 'started', window={'start': '2026-08-27'}, detail={'event': 'started'}, combine='any'), 'text'),
    'b2-26': (P('procedure_test', 'chest x-ray', return_detail=['result_text', 'result'], combine='any'), 'text'),
    'b2-27': (P('procedure_test', 'stress test', return_detail=['result_text', 'result'], combine='any'), 'text'),
}


def dev_rows():
    rows = []
    for line in (ROOT / 'tools/dev_set/questions.jsonl').open():
        q = json.loads(line)
        if q.get('split') != 'dev':   # heldout rows are dropped before any other use
            continue
        if q.get('bucket') == 'single_patient_unstructured':
            rows.append(q)
    return rows


def retrieval_check(db, q, pred):
    key, ev = q['key'], q.get('evidence') or []
    got = ne.retrieve(db, key['patient_id'], pred['concept'], window=pred.get('window'), sources=pred.get('sources'), as_of=AS_OF)
    hits = []
    for e in ev:
        norm = re.sub(r'\s+', ' ', e['quote']).strip().lower()
        hit = any(c['record_id'] == e['record_id'] and norm[:40] in re.sub(r'\s+', ' ', c['text']).lower() for c in got)
        hits.append(hit)
    return {'passages': len(got), 'key_evidence': len(ev), 'evidence_retrieved': sum(hits),
            'recall': (sum(hits) / len(hits)) if hits else None, 'not_documented_key': key.get('kind') == 'not_documented',
            'record_ids': sorted({c['record_id'] for c in got})}


def score_answer(q, pred, mode, ans):
    key, kind = q['key'], q['key']['kind']
    holds, status = ans.get('holds'), ans['status']
    quotes = ' '.join(e['quote'] for e in ans.get('evidence', []) if e.get('supports')) + ' ' + json.dumps(ans.get('detail', {}))
    if status == 'unknown':
        return 'unknown'
    if kind == 'not_documented':
        return 'correct' if status in ('not_mentioned',) else 'wrong'
    if kind == 'yes_no':
        want = key['answer'].strip().lower().startswith('y')
        if mode == 'no_if_declined':
            want = not want
        return 'correct' if bool(holds) == want else 'wrong'
    if kind == 'value':
        return 'correct' if str(key['answer']) in re.findall(r'\d+', quotes) or str(ans.get('detail', {}).get('count')) == str(key['answer']) else 'wrong'
    if kind == 'short_text':
        return 'correct' if all(re.search(g, quotes, re.I) for g in key['key_groups']) else 'wrong'
    return 'unscored'


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--db', required=True)
    ap.add_argument('--live', action='store_true')
    ap.add_argument('--ledger', default=None)
    ap.add_argument('--out', default=None)
    a = ap.parse_args()
    db = ne.connect(a.db)
    out = []
    for q in dev_rows():
        if q['id'] not in PREDICATES:   # follow-ups ("and morphine?") are expressed already; unmapped ids are reported
            out.append({'id': q['id'], 'skipped': 'no hand predicate'})
            continue
        pred, mode = PREDICATES[q['id']]
        pred = nf.validate_predicate(pred)
        row = {'id': q['id'], 'question': q['question'], 'frame': pred['frame'], 'retrieval': retrieval_check(db, q, pred)}
        if a.live:
            ans = nf.answer_patient(a.db, pred, q['key']['patient_id'], as_of=AS_OF, ledger=a.ledger)
            row['answer'] = {k: ans[k] for k in ('status', 'holds', 'p', 'undecided', 'detail')}
            row['score'] = score_answer(q, pred, mode, ans)
        out.append(row)
    scored = [r for r in out if 'retrieval' in r]
    rec = [r['retrieval']['recall'] for r in scored if r['retrieval']['recall'] is not None]
    nd = [r for r in scored if r['retrieval']['not_documented_key']]
    summary = {'questions': len(out), 'with_predicate': len(scored), 'mean_evidence_recall': round(sum(rec) / len(rec), 3) if rec else None,
               'evidence_recall_perfect': sum(1 for x in rec if x == 1.0), 'evidence_questions': len(rec),
               'not_documented_keys': len(nd), 'not_documented_with_zero_passages': sum(1 for r in nd if r['retrieval']['passages'] == 0)}
    if a.live:
        summary['score'] = {s: sum(1 for r in scored if r.get('score') == s) for s in ('correct', 'wrong', 'unknown', 'unscored')}
    print(json.dumps(summary, indent=1))
    for r in out:
        if 'retrieval' in r:
            x = r['retrieval']
            print(r['id'], r['frame'], f"passages={x['passages']} evidence {x['evidence_retrieved']}/{x['key_evidence']}",
                  r.get('score', ''), r.get('answer', {}).get('status', ''))
        else:
            print(r['id'], r['skipped'])
    if a.out:
        Path(a.out).write_text(json.dumps({'summary': summary, 'rows': out}, indent=1))


if __name__ == '__main__':
    main()
