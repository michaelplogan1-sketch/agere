"""Triage trial, 2026-11-17 (research row: RS(10) plus agreement-gated abstention on the frontier SQL rung). Written and
committed before the three-run journal it reads was complete. Code only.

The stack answers each proof_v14 question (plain wording) three times. Gates:
  unanimous   answer (as run 0 answered) only when all three runs returned the same rows; otherwise decline
  majority    answer with the rows two or more runs share; otherwise decline
Compared with: a single run (run 0), always abstain (0), and the LLM alone (the best-configured assistant, its journal of
17 November). Scored RS(10) (+1 right, 0 declined, -10 wrong) with a paired cluster bootstrap by area (trial_stats).

    PYTHONPATH=. python eval/tiered/trial_abstention.py
"""
import json
import sys
from collections import Counter
from pathlib import Path

sys.path.insert(0, 'eval/tiered')
import trial_stats as T  # noqa: E402

J = Path('artifacts/agere-tiered-core/proof/journal-proof_v14-stack-runs3-strict-phrasings0.jsonl')
V = {'right': 1, 'asked_right': 1, 'wrong': -10}


def main():
    rs = [json.loads(l) for l in J.read_text().splitlines() if l.strip()]
    by = {}
    for r in rs: by.setdefault(r['id'], {})[int(r['run'])] = r
    full = {q: v for q, v in by.items() if len(v) == 3}
    def answered(r): return r['kind'] in ('right', 'wrong', 'asked_right', 'guessed')
    def key(r): return json.dumps(r['rows'], sort_keys=True, default=str)
    arms = {'single run': {}, 'unanimous gate': {}, 'majority gate': {}}
    for q, runs in full.items():
        r0 = runs[0]
        arms['single run'][q] = r0['kind']
        ks = [key(runs[i]) for i in range(3)]
        arms['unanimous gate'][q] = r0['kind'] if all(answered(runs[i]) for i in range(3)) and len(set(ks)) == 1 else 'declined'
        top, n = Counter(ks).most_common(1)[0]
        pick = next(runs[i] for i in range(3) if ks[i] == top)
        arms['majority gate'][q] = pick['kind'] if n >= 2 and answered(pick) else 'declined'
    assistant = {json.loads(l)['id']: json.loads(l)['kind'] for l in open(T.WS / 'journal-proof_v14-assistant-runs1-strict-phrasings0.jsonl')}
    dom = {q: full[q][0]['domain'] for q in full}
    usd = sum(r['usd'] for r in rs)
    print(f'{len(full)} questions with three runs; ${usd:.2f} for {len(rs)} answers (${usd / max(1, len(rs)):.4f} each)')
    consistent = sum(1 for q in full if len({full[q][i]["kind"] for i in range(3)}) == 1)
    print(f'same outcome on all three runs: {consistent} of {len(full)}')
    for name, ks in [*arms.items(), ('LLM alone (assistant)', {q: assistant.get(q, 'declined') for q in full})]:
        c = Counter(ks.values())
        print(f'  {name:22} right {c["right"] + c["asked_right"]}, wrong {c["wrong"]}, declined or 0 {len(ks) - c["right"] - c["asked_right"] - c["wrong"]}; '
              f'RS(10) {sum(V.get(k, 0) for k in ks.values())}')
    print('  always abstain         RS(10) 0')
    for name in ('unanimous gate', 'majority gate'):
        pairs = [(dom[q], V.get(arms[name][q], 0) - V.get(arms['single run'][q], 0)) for q in full]
        print(T.line(f'  {name} minus single run, RS(10) a question', T.cluster_ci(pairs)))
        pairs = [(dom[q], V.get(arms[name][q], 0) - V.get(assistant.get(q, 'declined'), 0)) for q in full]
        print(T.line(f'  {name} minus LLM alone, RS(10) a question', T.cluster_ci(pairs)))


if __name__ == '__main__':
    main()
