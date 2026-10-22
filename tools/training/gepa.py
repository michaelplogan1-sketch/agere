"""GEPA-style reflective prompt optimization for the hosted planner prompts, against verified pairs (offline only).

GEPA (genetic-Pareto; DSPy's reflective optimizer): keep a pool of prompt candidates, track every candidate's score on
every case, sample parents from the Pareto front (candidates best on at least one case), mutate a parent by REFLECTING on
its failures on a minibatch, accept the child only when it beats the parent on that minibatch, then score it on the
validation cases; optionally merge two front candidates. Candidates are additions to the existing prompts
(learning_loop.prompt_optimizer.candidate: proposal / analysis_review / typed_review), never replacements, and are
'candidate_only': nothing activates itself.

Evaluators (cheapest that does the job):
  hosted      prompt_optimizer.trial: the real compiler, executor and review on the stack with the hosted model; the
              executed result must equal the verified pair's answer (exact), and System One's answer check grades partial
              answers (answers_fully 1.0 / partially 0.5). Paid; metered through the ledger like every hosted call.
  system_one  dry run without Claude credits: Jev/Perplexity judge whether a candidate's guidance addresses the failure
              recorded for each case (cascade rejection details, review issues, corrections), and the token cost of a live
              run is estimated from the real planner prompt size and pricing.json. Cheap (~$0.0001/judgment).
Reflectors: 'rules' (deterministic: failure patterns -> general guidance, no model) or 'hosted' (prompt_optimizer.REFLECT).
Cases: verified pairs from the training corpus (live traffic verified answers + exact factory labels), split by template
family; recorded failures (traffic gaps) give each case its feedback. Benchmarks are excluded (datasets.Bench).

Usage: python tools/training/gepa.py --dry-run --out artifacts/training/gepa/dry-run [--cases 16] [--budget 40]
"""
import argparse
import hashlib
import json
import random
import re
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

VERSION = 'gepa-prompt-optimization/1'
ROLES = ('proposal', 'analysis_review', 'typed_review')
# failure pattern -> general guidance (never question-specific SQL, answers or IDs)
RULES = [
    (r'answer shape|asks for a count|returned a table|single total', 'proposal',
     'Match the requested output exactly: a "how many" question returns one count; a breakdown returns a table with one row per group; a "which" question returns the identifiers.'),
    (r'unaccounted original request text|dropped|keeps all of it|literal', 'proposal',
     'Every phrase of the question must map to a filter, grouping, measure, window or output in the request; never drop a qualifier to make the request compile.'),
    (r'comparator|threshold', 'proposal',
     'Keep comparison operators and thresholds exactly as stated (above vs at least, under vs at most) and quote the phrase that states them.'),
    (r'window|period|month|year|days|within', 'proposal',
     'Bind every time phrase to the event clock it qualifies and use inclusive calendar bounds; a rolling window ends at the snapshot as-of date.'),
    (r'group|break ?down|by payer|per ', 'proposal',
     'Put every requested breakdown in group_by and select the grouping field once.'),
    (r'unresolved|cannot express|analysis', 'proposal',
     'Prefer the typed request; use {"analysis": ...} only when a requirement cannot be expressed, and keep registered engines for their calculations.'),
    (r'definition|convention|pqi|avoidable|adherence|pdc|gap', 'proposal',
     'When the question needs a definition it does not state, use the governed default definition and name it; do not invent a new one.'),
    (r'notes?|documented|mention|chart', 'proposal',
     'Questions about what notes document read extracted note facts or passages with their record ids; negated mentions are not documentation.'),
    (r'not equivalent|issues|review', 'typed_review',
     'Judge equivalence on population, filters, time window, grouping, measure and output only; list each concrete mismatch as one issue.'),
]


# ------------------------------------------------------------------------------------------------------- the engine
def pareto_front(scores):
    """scores: {cand: {case: score}} -> candidates that are best on at least one case and not dominated."""
    cases = sorted({c for s in scores.values() for c in s})
    best = {c: max(s.get(c, 0.0) for s in scores.values()) for c in cases}
    front = {k for k, s in scores.items() if any(s.get(c, 0.0) >= best[c] and best[c] > 0 for c in cases)} or set(scores)
    def dominated(a):
        return any(b != a and all(scores[b].get(c, 0) >= scores[a].get(c, 0) for c in cases) and
                   any(scores[b].get(c, 0) > scores[a].get(c, 0) for c in cases) for b in front)
    return sorted(k for k in front if not dominated(k))


def sample_parent(front, scores, rng):
    cases = sorted({c for s in scores.values() for c in s})
    weight = {k: 1 + sum(scores[k].get(c, 0) >= max(scores[j].get(c, 0) for j in scores) for c in cases) for k in front}
    return rng.choices(front, [weight[k] for k in front])[0]


def merge(a, b):
    """Union of two candidates' additions (per role, de-duplicated by sentence)."""
    out = {}
    for role in ROLES:
        parts = [s for x in (a.get(role, ''), b.get(role, '')) for s in re.split(r'(?<=\.)\s+', x) if s.strip()]
        if parts: out[role] = ' '.join(dict.fromkeys(parts))[:6000]
    return out


def optimize(train, valid, evaluate, reflect, *, budget=40, minibatch=4, seed=7, base=None, use_merge=True):
    """evaluate(additions, case) -> {'score', 'feedback', 'calls', 'usd'}; reflect(additions, failures) -> additions.
    -> report with the pool, per-case scores, Pareto front, selected candidate and spend."""
    from tools.learning_loop.prompt_optimizer import candidate
    rng = random.Random(seed)
    pool, scores, spend, log = {}, {}, {'evaluations': 0, 'calls': 0, 'usd': 0.0}, []

    def run(add, cases):
        out = {}
        for c in cases:
            r = evaluate(add, c)
            spend['evaluations'] += 1; spend['calls'] += r.get('calls', 0); spend['usd'] += r.get('usd') or 0.0
            out[c['id']] = r
        return out

    def add(adds):
        cand = candidate(adds)
        if cand['id'] in pool: return None
        res = run(adds, valid)
        pool[cand['id']] = {'additions': cand['additions'], 'results': res}
        scores[cand['id']] = {k: v['score'] for k, v in res.items()}
        return cand['id']
    root = add(base or {})
    while spend['evaluations'] < budget:
        front = pareto_front(scores)
        parent = sample_parent(front, scores, rng)
        batch = rng.sample(train, min(minibatch, len(train)))
        before = run(pool[parent]['additions'], batch)
        failures = [{'case': c, 'feedback': before[c['id']]['feedback'], 'score': before[c['id']]['score']} for c in batch
                    if before[c['id']]['score'] < 1.0]
        if not failures:
            log.append({'parent': parent, 'event': 'no failures on minibatch'}); continue
        child = reflect(pool[parent]['additions'], failures)
        after = run(child, batch)
        gain = sum(after[k]['score'] for k in after) - sum(before[k]['score'] for k in before)
        if gain <= 0:
            log.append({'parent': parent, 'event': 'child rejected on minibatch', 'gain': round(gain, 4)}); continue
        cid = add(child)
        log.append({'parent': parent, 'child': cid, 'event': 'child accepted' if cid else 'duplicate child', 'gain': round(gain, 4)})
        if use_merge and cid and len(front) > 1:
            other = rng.choice([f for f in front if f != parent] or [parent])
            mid = add(merge(pool[cid]['additions'], pool[other]['additions']))
            if mid: log.append({'merge': [cid, other], 'child': mid})
        if spend['evaluations'] >= budget: break
    mean = {k: sum(s.values()) / max(1, len(s)) for k, s in scores.items()}
    selected = max(scores, key=lambda k: (mean[k], -len(json.dumps(pool[k]['additions']))))
    return {'schema': VERSION, 'activation': 'candidate_only', 'baseline': root, 'selected': selected,
            'selected_additions': pool[selected]['additions'], 'validation_mean': {k: round(v, 4) for k, v in mean.items()},
            'pareto_front': pareto_front(scores), 'scores': scores, 'pool': {k: v['additions'] for k, v in pool.items()},
            'log': log, 'spend': {**spend, 'usd': round(spend['usd'], 6)}}


# ------------------------------------------------------------------------------------------------------ reflectors
def rule_reflector(additions, failures):
    """Deterministic reflection: add the general guidance whose pattern matches the observed failures (no model)."""
    text = ' '.join(f"{f['case']['question']} {f['feedback']}" for f in failures).lower()
    out = dict(additions)
    for pattern, role, guidance in RULES:
        if re.search(pattern, text) and guidance not in out.get(role, ''):
            out[role] = (out.get(role, '') + ' ' + guidance).strip()
            break  # one general correction per reflection step (GEPA mutates one thing at a time)
    return out


def hosted_reflector(model):
    from tools.learning_loop.prompt_optimizer import REFLECT
    from clinical_core.agere.clinical.shared_planner import decode

    def reflect(additions, failures):
        raw, _ = model(REFLECT, json.dumps({'current_additions': additions, 'training_failures': [
            {'question': f['case']['question'], 'feedback': f['feedback']} for f in failures]}, default=str), max_tokens=4000)
        got = decode(raw)
        return {k: v for k, v in got.items() if k in ROLES and isinstance(v, str)}
    return reflect


# ------------------------------------------------------------------------------------------------------ evaluators
def system_one_evaluator(as_of, ledger=None, allow_live=True):
    """Dry-run evaluator: Jev (then Perplexity) judges whether the candidate guidance addresses the case's recorded failure."""
    from clinical_core.agere.clinical import system_one
    options = {'addresses': 'The guidance directly corrects the recorded failure for this question.',
               'partly': 'The guidance is related but would only partly correct the failure.',
               'no': 'The guidance does not address this failure.'}

    def evaluate(additions, case):
        guidance = ' '.join(additions.get(r, '') for r in ROLES).strip() or '(no additional guidance: the base prompts only)'
        out = system_one.decide(f"Question: {case['question']}\nRecorded failure: {case['feedback'][:600]}\n"
                                'Does the planner guidance in the evidence correct this failure?', options,
                                [{'id': 'guidance', 'text': guidance[:3000], 'date': as_of}], as_of=as_of,
                                task_key='gepa_guidance_relevance', ledger=ledger, allow_live=allow_live)
        p = out.get('calibrated_probabilities') or out.get('probabilities') or {}
        score = float(p.get('addresses', 0.0)) + 0.5 * float(p.get('partly', 0.0))
        calls = sum(1 for s in out.get('steps') or [] if not s.get('cache_hit') and s.get('status') == 'answered')
        return {'score': round(score, 4), 'feedback': f"System One: {out.get('label')} ({out.get('decided_by') or out.get('provider')})",
                'calls': calls, 'usd': out.get('cost_usd') or 0.0}
    return evaluate


def hosted_evaluator(catalog, model, stack=None):
    """Live evaluator: prompt_optimizer.trial (compile, execute, review) must reproduce the verified answer exactly."""
    from tools.learning_loop.prompt_optimizer import trial, candidate

    def evaluate(additions, case):
        row = trial(catalog, {**case['trial'], 'split': 'train'}, candidate(additions), model)
        usd = sum(s.get('api_usd') or 0 for s in row['usage'])
        feedback = row.get('error') or '; '.join((row.get('review') or {}).get('issues') or []) or '; '.join(row.get('checks') or [])
        return {'score': 1.0 if row['passed'] else 0.0, 'feedback': feedback or ('passed' if row['passed'] else 'result differs'),
                'calls': row['calls'], 'usd': usd}
    return evaluate


# ------------------------------------------------------------------------------------------------------------ cases
def norm(q): return ' '.join(str(q).strip().rstrip('?.!').split()).casefold()


def cases_from(traffic_dirs, labels_dirs=(), limit=16, seed=7):
    """Cases with recorded failures: traffic gaps (cascade rejection details) joined to verified pairs when one exists.
    -> list of {'id', 'family', 'question', 'feedback', 'expected_answer_key'|None, 'split'}"""
    from tools.training.datasets import Bench, rows, split_of
    bench, verified = Bench(), {}
    for r in rows([*labels_dirs, *traffic_dirs]):
        verified[norm(r['canonical'])] = r
    out, seen = [], set()
    for d in traffic_dirs:
        for p in sorted(Path(d).glob('gaps-*.jsonl')):
            for line in p.read_text().splitlines():
                try: g = json.loads(line)
                except ValueError: continue
                q = g.get('question')
                if not isinstance(q, str) or q.startswith('{') or len(q.split()) < 5 or norm(q) in seen or bench.near(q): continue
                details = [f"{e.get('proposer')}: {e.get('detail')}" for e in g.get('proposers') or []
                           if e.get('outcome') in ('rejected', 'declined', 'error') and e.get('detail')]
                if not details and not g.get('error'): continue
                seen.add(norm(q))
                fam = 'gap/' + hashlib.sha256(norm(q).encode()).hexdigest()[:2]
                v = verified.get(norm(q))
                out.append({'id': hashlib.sha256(q.encode()).hexdigest()[:12], 'family': fam, 'question': q,
                            'feedback': ' | '.join(details)[:1200] or g.get('error', '')[:600],
                            'expected_answer_key': (v or {}).get('answer_key'), 'split': 'train' if split_of(fam) == 'train' else 'validation'})
    random.Random(seed).shuffle(out)
    out = out[:limit]
    if out and not any(c['split'] == 'validation' for c in out): out[-1]['split'] = 'validation'
    return out


def live_cost_estimate(catalog, cases, candidates, model='claude-sonnet-5-5', calls_per_case=2):
    """USD a hosted run would cost: the real planner prompt size (static catalog at the cache-read rate after the first
    call) x pricing.json, per case x candidate."""
    from clinical_core.agere.clinical import shared_planner as S
    prices = json.loads((ROOT / 'config' / 'agere' / 'pricing.json').read_text())[model]
    sizes = []
    for c in cases[:6]:
        user = S.planner_user(S.schema_slice(catalog, c['question']), question=c['question'])
        static, rest = S.cache_split(user) or ('', user)
        sizes.append((len(S.SYSTEM + static) / 4, len(rest) / 4))
    if not sizes: return None
    cached, fresh = (sum(x) / len(sizes) for x in zip(*sizes))
    per_call = (cached * prices['cache_read'] + fresh * prices['input'] + 600 * prices['output']) / 1e6
    n = len(cases) * len(candidates) * calls_per_case
    return {'model': model, 'est_prompt_tokens': round(cached + fresh), 'est_uncached_tokens': round(fresh), 'per_call_usd': round(per_call, 5),
            'calls': n, 'usd': round(n * per_call, 4), 'basis': 'chars/4 token estimate, static catalog billed at cache-read after the first call'}


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument('--dry-run', action='store_true', help='System One evaluator + rule reflector; no Claude call')
    ap.add_argument('--out', required=True); ap.add_argument('--cases', type=int, default=16); ap.add_argument('--budget', type=int, default=40)
    ap.add_argument('--traffic'); ap.add_argument('--seed', type=int, default=7)
    a = ap.parse_args(argv)
    from tools.training.self_improve import training_root
    from tools.training.question_factory import active_stack
    from clinical_core.agere.clinical import request_plan as R
    root = training_root()
    cases = cases_from([a.traffic or root / 'traffic'], [root / 'labels'] if (root / 'labels').exists() else [], a.cases, a.seed)
    if len(cases) < 2: raise SystemExit('fewer than two failure cases recorded')
    train = [c for c in cases if c['split'] == 'train'] or cases[:1]
    valid = [c for c in cases if c['split'] == 'validation']
    catalog = R.Catalog.load(active_stack())
    out = Path(a.out); out.mkdir(parents=True, exist_ok=True)
    if not a.dry_run: raise SystemExit('live GEPA uses hosted_evaluator(catalog, HostedModel) and hosted_reflector; run with --dry-run without Claude credits')
    from run_integrated_stack import keys
    keys()
    started = time.time()
    report = optimize(train, valid, system_one_evaluator(catalog.identity.get('as_of') or '2030-05-27'), rule_reflector,
                      budget=a.budget, seed=a.seed)
    report.update(mode='dry_run', evaluator='system_one', reflector='rules', cases={'train': len(train), 'validation': len(valid)},
                  case_questions=[c['question'] for c in cases], seconds=round(time.time() - started, 1),
                  live_run_estimate=live_cost_estimate(catalog, cases, report['pool']))
    (out / 'gepa.json').write_text(json.dumps(report, indent=1, default=str))
    (out / 'candidate.json').write_text(json.dumps({'additions': report['selected_additions'], 'activation': 'candidate_only'}, indent=1))
    print(json.dumps({k: report[k] for k in ('selected', 'validation_mean', 'pareto_front', 'spend', 'live_run_estimate')}, indent=1, default=str))
    return report


if __name__ == '__main__':
    main()
