"""Send the development question set to an Agere service and score it against the independent keys.

    .venv/bin/python -m tools.dev_set.run --split dev --jobs 1            # whole dev split (slow: a question can take minutes)
    .venv/bin/python -m tools.dev_set.run --bucket insight --split heldout
    .venv/bin/python -m tools.dev_set.run --limit 5 --out /tmp/smoke.json  # smoke
    .venv/bin/python -m tools.dev_set.run --set compose                    # the composition set (split dev2; same as --split dev2)

Every request carries X-Agere-Evaluation: 1 (the service learns nothing from it). Follow-ups: the service links a follow-up to
the prior turn through `conversation_id` in the /api/stack/compose body (conversation.handle_compose); in evaluation mode the
service keeps the turn and the saved cohort in an evaluation scratch directory outside the stack (evaluation.scratch_dir), so a
chained follow-up sees its parent and nothing reaches the stack's stores. The default --follow-ups conversation sends the real
chain (conversation ids are unique per run); --follow-ups standalone
sends the key's fully-resolved question instead (measures the answer, not the context linking).

Output: per-bucket accuracy, coverage (answered / asked), precision on answered, refusals, cost (cost_receipt), latency p50/p90,
and the route (winning tier) histogram. Scoring is stated per answer type in score().
"""
from __future__ import annotations

import argparse
import json
import re
import statistics
import sys
import time
import urllib.error
import urllib.request
from collections import Counter, defaultdict
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

if __package__ in (None, ''):
    sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from tools.dev_set.keys import BUCKETS, read

DEFAULT_URL = 'http://localhost:8885/api/stack/compose'
HEADERS = {'Content-Type': 'application/json', 'X-Agere-Evaluation': '1'}
ANSWERED = {'answered', 'partial', 'low_confidence'}
ABSENCE_STATUSES = {'not_available', 'unsupported'}  # the data is stated absent (supported absence)
TEXT_KEYS = ('answer', 'narrative', 'summary', 'explanation', 'insight', 'insights', 'report', 'findings', 'text', 'message', 'table',
             'rows', 'program_results', 'workflow', 'grounding', 'missing')
NUM = re.compile(r'(?<![\w.])-?\d[\d,]*\.?\d*')


# --- transport ----------------------------------------------------------------------------------------------------------
def post(url, body, timeout=900):
    req = urllib.request.Request(url, data=json.dumps(body).encode(), headers=HEADERS, method='POST')
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return r.status, json.load(r)
    except urllib.error.HTTPError as exc:
        try:
            return exc.code, json.loads(exc.read().decode('utf-8', 'replace'))
        except ValueError:
            return exc.code, {'status': 'error', 'error': f'HTTP {exc.code}'}


def ask(url, question, conversation_id=None, timeout=900):
    body = {'question': question}
    if conversation_id:
        body['conversation_id'] = conversation_id
    t0 = time.time()
    try:
        code, resp = post(url, body, timeout)
    except (urllib.error.URLError, TimeoutError, OSError) as exc:
        return {'http': None, 'seconds': time.time() - t0, 'resp': {'status': 'error', 'error': str(exc)[:300]}}
    return {'http': code, 'seconds': time.time() - t0, 'resp': resp}


# --- reading a response -------------------------------------------------------------------------------------------------
def status_of(resp):
    a = resp.get('answer')
    return (a.get('status') if isinstance(a, dict) else None) or resp.get('status')


def visible_text(resp):
    parts = [json.dumps(resp[k], ensure_ascii=False, default=str) for k in TEXT_KEYS if resp.get(k) not in (None, [], {})]
    return '\n'.join(parts)


ANSWER_KEYS = ('headline', 'value', 'table', 'facts', 'answer', 'count')


def answer_text(a):
    """The answer's own statement (headline, value, table, facts): not the echoed question (interpretation / read_as), which repeats the
    user's words ("better or worse") and would match every label (2026-10-24, b1-03)."""
    return json.dumps({k: a.get(k) for k in ANSWER_KEYS if a.get(k) not in (None, [], {})}, ensure_ascii=False, default=str)


def numbers_in(text):
    out = []
    for m in NUM.findall(text):
        try:
            out.append(float(m.replace(',', '')))
        except ValueError:
            pass
    return out


def scalar_of(resp):
    """The single number an answer commits to: answer.count / answer.value, else a one-cell table."""
    a = resp.get('answer') if isinstance(resp.get('answer'), dict) else {}
    for k in ('count', 'value'):
        if isinstance(a.get(k), (int, float)) and not isinstance(a.get(k), bool):
            return float(a[k])
    t = a.get('table')
    if isinstance(t, list) and len(t) == 1 and len(t[0]) == 1:
        v = next(iter(t[0].values()))
        if isinstance(v, (int, float)):
            return float(v)
    return None


def route_of(resp):
    pr = resp.get('planner_receipt') or {}
    interp = pr.get('interpretation') or {}
    return (interp.get('proposer') or {}).get('name') or interp.get('interpreter') or pr.get('route') or 'none'


def _receipt_sources(resp):
    pr = resp.get('planner_receipt') if isinstance(resp.get('planner_receipt'), dict) else {}
    interp = pr.get('interpretation') if isinstance(pr.get('interpretation'), dict) else {}
    return [pr, interp, resp]


def _first(resp, key):
    for src in _receipt_sources(resp):
        if src.get(key):
            return src[key]
    return None


def trace_of(resp):
    """Compact per-row trace of a service response: answer_timing, per-tier log [{tier, outcome, seconds}], cost_receipt and
    claude_calls_by_model. Every field is optional (a response may carry none of them)."""
    tiers = []
    log = _first(resp, 'proposers')
    if isinstance(log, list):
        for e in log:
            if isinstance(e, dict):
                tiers.append({'tier': e.get('proposer'), 'outcome': e.get('outcome'), 'seconds': e.get('seconds'),
                              **({'rung': e['rung']} if e.get('rung') else {})})
    else:  # failures expose only the stage diagnostics' failed events (no seconds)
        sd = _first(resp, 'stage_diagnostics')
        for e in (sd.get('events') or []) if isinstance(sd, dict) else []:
            tiers.append({'tier': e.get('proposer'), 'outcome': e.get('outcome'), 'seconds': None})
    cr = resp.get('cost_receipt') if isinstance(resp.get('cost_receipt'), dict) else None
    tel = resp.get('cost_telemetry') if isinstance(resp.get('cost_telemetry'), dict) else {}
    models = (cr or {}).get('claude_calls_by_model') or tel.get('claude_calls_by_model')
    return {'answer_timing': _first(resp, 'answer_timing'), 'tiers': tiers, 'cost_receipt': cr, 'claude_calls_by_model': models}


def close(x, y, tol):
    tol = tol or {}
    if 'rel' in tol and abs(x - y) <= tol['rel'] * abs(y):
        return True
    return abs(x - y) <= tol.get('abs', 0)


# --- scoring ------------------------------------------------------------------------------------------------------------
def score(q, resp):
    """-> (correct: bool|None, detail: dict). correct is None only for a refused / unanswered item (counted in coverage)."""
    key, t = q['key'], q['answer_type']
    st = status_of(resp)
    answered = st in ANSWERED
    a = resp.get('answer') if isinstance(resp.get('answer'), dict) else {}
    text = visible_text(resp)
    low = text.lower()
    tol = key.get('tolerance')
    if t == 'absent':
        # only a returned answer that states the data is absent counts; an error, a timeout or a generic refusal (not_understood,
        # internal_error, ...) does not (2026-10-24: an error/timeout used to count as correct here)
        stated = st in ABSENCE_STATUSES or a.get('answer_type') == 'unsupported'
        return stated, {'expected': 'refusal: ' + key.get('reason', ''), 'status': st, 'stated_absent': stated}
    if not answered:
        return None, {'status': st}
    if t in ('count', 'value'):
        got = scalar_of(resp)
        exp = key['answer']
        ok = got is not None and (close(got, exp, tol) or (key.get('unit') == 'proportion' and close(got, exp * 100, {'abs': (tol or {}).get('abs', 0) * 100})))
        return ok, {'got': got, 'expected': exp}
    if t == 'share':
        got, exp = scalar_of(resp), key['answer']
        ok = got is not None and (close(got, exp, tol) or close(got, exp * 100, {'abs': (tol or {}).get('abs', 0) * 100}))
        if not ok and not got:  # number only in prose / table
            ok = any(close(n, exp, tol) or close(n, exp * 100, {'abs': (tol or {}).get('abs', 0) * 100}) for n in numbers_in(text))
        return ok, {'got': got, 'expected': exp, 'numerator': key.get('numerator'), 'denominator': key.get('denominator')}
    if t == 'patient_set':
        got = set(a.get('patient_ids') or [])
        exp = set(key['answer'])
        detail = {'got_n': len(got), 'expected_n': len(exp), 'missing': len(exp - got), 'extra': len(got - exp)}
        if not got and scalar_of(resp) is not None and int(scalar_of(resp)) == len(exp):
            detail['count_only_match'] = True
        return got == exp, detail
    if t == 'table':
        rows = a.get('table') if isinstance(a.get('table'), list) else []
        exp_rows = key['answer']
        used, hits, order = set(), 0, []
        for er in exp_rows:
            for i, r in enumerate(rows):
                if i in used:
                    continue
                vals = list(r.values())
                # a share key stored as a fraction matches the same share stated as a percent (as the scalar branches already allow)
                pct = lambda v, ev: abs(float(ev)) <= 1.0 and close(float(v), float(ev) * 100, {**(tol or {}), 'abs': (tol or {}).get('abs', 0) * 100})
                if all(any((isinstance(v, (int, float)) and isinstance(ev, (int, float)) and not isinstance(v, bool)
                            and (close(float(v), float(ev), tol) or (isinstance(ev, float) and pct(v, ev))))
                           or (isinstance(v, str) and isinstance(ev, str) and v.strip().lower() == ev.strip().lower()) for v in vals) for ev in er.values()):
                    used.add(i); hits += 1; order.append(i)
                    break
        ok = hits == len(exp_rows) and len(rows) == len(exp_rows) and (not key.get('order_matters') or order == sorted(order))
        return ok, {'rows_expected': len(exp_rows), 'rows_matched': hits, 'rows_returned': len(rows)}
    if t == 'list':
        flat = low
        miss = [x for x in key['answer'] if str(x).lower() not in flat]
        return not miss, {'missing': miss}
    if t == 'date':
        return str(key['answer']) in text, {'expected': key['answer']}
    if t == 'yes_no':
        exp = key['answer']
        v = a.get('value') if isinstance(a.get('value'), (int, float)) else None
        if v is not None:
            got = bool(v)
        elif a.get('count') is not None and isinstance(a.get('count'), int):
            got = a['count'] > 0
        else:
            got = _lead_yes_no(str((a or {}).get('headline') or '').lower())
            if got == UNDECIDED: got = None
            elif got is None:
                yes = re.search(r'\b(yes|true|is on|has had|were)\b', low)
                no = re.search(r'\b(no|false|not|never|none|without)\b', low)
                got = True if yes and not no else False if no and not yes else None
        return got is exp, {'got': got, 'expected': exp}
    if t == 'label':
        exp = str(key['answer'])
        accept = key.get('accept')
        if accept:
            pats = {lab: [re.compile(r'\b' + p, re.I) for p in ps] for lab, ps in accept.items()}
            said = answer_text(a) if a else text
            seen = {lab for lab, ps in pats.items() if any(p.search(said) for p in ps)}
            return seen == {exp}, {'expected': exp, 'matched_labels': sorted(seen)}
        alts = {exp.lower(), exp.lower().replace('_', ' '), str(key.get('display_name') or '').lower()} - {''}
        return any(x in low.replace('_', ' ') or x in low for x in alts), {'expected': exp}
    if t == 'note':
        return score_note(q, resp, text, low, a)
    if t == 'rubric':
        return score_rubric(key['answer'], text, low)
    return None, {'unscored_type': t}


UNDECIDED = 'undecided'  # an answer that says it cannot decide is neither yes nor no


def _lead_yes_no(low):
    """An answer that opens with Yes / No / Undecided is judged by that opening word, not by words inside quoted evidence."""
    m = re.match(r'\W*(yes|no|not documented|undecided)\b', low or '')
    if not m: return None
    return {'yes': True, 'no': False, 'not documented': False, 'undecided': UNDECIDED}[m.group(1)]


def score_note(q, resp, text, low, a):
    key, kind = q['key'], q['key']['kind']
    ok = False
    if kind == 'yes_no':
        lead = _lead_yes_no(str((a or {}).get('headline') or '').lower())
        if lead == UNDECIDED:
            got = None
        elif lead is not None:
            got = 'yes' if lead else 'no'
        else:
            yes = re.search(r'\b(yes|does|did|are|is|has|still|reports? taking)\b', low) and not re.search(r'\b(no|not|does not|did not|never|declin\w*|cannot)\b', low)
            no = re.search(r'\b(no|not|does not|did not|never|declin\w*|cannot|unable)\b', low)
            got = 'yes' if yes else 'no' if no else None
        ok = got == key['answer']
    elif kind == 'not_documented':
        ok = bool(re.search(r'not documented|no mention|not mentioned|does not (document|mention|address)|no documentation|nothing (documented|recorded)|not recorded|unknown', low))
    elif kind == 'value':
        ok = float(key['answer']) in numbers_in(text)
    else:
        ok = all(re.search(g, ocr_norm(text), re.I) for g in key.get('key_groups', []))
    cited = [e['record_id'] for e in q['evidence']]
    allowed = list(dict.fromkeys(cited + list(key.get('scope_records') or [])))  # any record the key lists as in scope may be the one cited
    has_cite = (not cited) or any(r in text for r in allowed)
    supported, why = (True, None) if not cited else passage_supports(q, resp)
    return ok and has_cite and supported, {'answer_match': ok, 'cited_supporting_record': has_cite, 'passage_supports_claim': supported, **({'passage_check': why} if why else {})}


# --- passage entailment (shared by every contestant) ----------------------------------------------------------------------
QUOTE_KEYS = ('quote', 'excerpt', 'passage', 'snippet', 'evidence_quote', 'text')
RECORD_KEYS = ('record_id', 'source_record_id', 'note_id', 'id')
STOP = frozenset('that this with from were have been which their there about after before when what into than then them they also only more some such will would could should does done'.split())


def ocr_norm(text):
    """A scanned passage's digit-for-letter swaps undone ("1owerlobe" -> "lowerlobe") before a pattern is matched against it."""
    out = re.sub(r'(?<=[a-z])(?<!\ba)1(?=[a-z])|\b1(?=[a-z]{3,})', 'l', str(text or ''))
    return re.sub(r'(?<=[a-z])0(?=[a-z])|\b0(?=[a-z]{3,})', 'o', out)


def quoted_passages(resp, ids):
    """-> {record_id: [quoted text]} for the allowed record ids, from the passages the response itself quotes: a source / grounding object that
    carries a record id and a quote, or `RECORD_ID ...: "quote"` in the answer's text. A bare record id carries no quote."""
    out = {i: [] for i in ids}

    def walk(o):
        if isinstance(o, dict):
            rid = next((o[k] for k in RECORD_KEYS if isinstance(o.get(k), str) and o[k] in out), None)
            if rid:
                out[rid] += [v for k in QUOTE_KEYS for v in [o.get(k)] if isinstance(v, str) and v.strip()]
            for v in o.values():
                walk(v)
        elif isinstance(o, (list, tuple)):
            for v in o:
                walk(v)
    walk(resp)
    flat = visible_text(resp).replace('\\"', '"').replace('\\n', '\n')
    for i in ids:
        for m in re.finditer(re.escape(i) + r'[^"]{0,120}?"([^"]{3,600})"', flat):
            out[i].append(m.group(1))
    return out


def _content_tokens(text):
    return {w for w in re.findall(r'[a-z0-9]{4,}', ocr_norm(str(text).lower())) if w not in STOP}


def passage_supports(q, resp):
    """Does a QUOTED passage of a record the answer cites (a key evidence record or a scope record) itself contain the key's evidence?
    key_groups: every group must match in the pooled quotes of the cited, quoted records (a key can need two passages); a key with no groups
    (yes/no, value): a cited quoted passage must carry half of the content words of a key evidence quote, or the question's own subject word. A cited record with no quoted
    passage proves nothing. Deterministic; the same for every contestant. -> (bool, why)"""
    key = q['key']
    ids = list(dict.fromkeys([e['record_id'] for e in q.get('evidence') or []] + list(key.get('scope_records') or [])))
    quotes = {i: v for i, v in quoted_passages(resp, ids).items() if v}
    if not quotes:
        return False, 'no cited record carries a quoted passage'
    groups = key.get('key_groups') or []
    if groups and key.get('kind') not in ('yes_no', 'not_documented', 'value'):
        pooled = ocr_norm(' '.join(' '.join(v) for v in quotes.values()).lower())
        miss = [g for g in groups if not re.search(g, pooled, re.I)]
        return not miss, (f'quoted passages lack: {miss}' if miss else None)
    want = [_content_tokens(e.get('quote')) for e in q.get('evidence') or [] if e.get('quote')]
    want = [w for w in want if w]
    if not want:
        return True, None
    # the subject of the question (its content words that the key evidence quote also uses: "morphine", "appointments") must be in the passage
    asked = _content_tokens(f"{q.get('question') or ''} {key.get('standalone') or ''}")
    anchors = set().union(*want) & asked
    for vs in quotes.values():
        for v in vs:
            have = _content_tokens(v)
            if any(len(w & have) / len(w) >= 0.5 for w in want) or (anchors and anchors & have):
                return True, None
    return False, 'no cited quoted passage carries the key evidence'


def _pat_in(text, pats):
    return any(re.search(p, text, re.I) for p in pats)


def score_rubric(rub, text, low):
    facts, results = rub['required_facts'], {}
    nums = numbers_in(text)
    for f in facts:
        ok = False
        if f['kind'] == 'terms':
            hits = [bool(re.search(p, text, re.I)) for p in f['patterns']]
            ok = all(hits) if f.get('mode') == 'all' else any(hits)
        elif f['kind'] == 'ranking':
            pos, last = [], -1
            ok = True
            for it in f['items']:
                m = re.search(it, text, re.I)
                if not m or m.start() < last:
                    ok = False
                    break
                last = m.start()
        elif f['kind'] == 'number':
            cands = [f['value']] + ([f['value'] * 100] if abs(f['value']) <= 1.0 and f.get('unit') == 'proportion' else [])
            tol = f.get('tolerance') or {}
            windows = []
            if f.get('near'):
                for p in f['near']:
                    for m in re.finditer(p, text, re.I):
                        windows.append(text[max(0, m.start() - 140): m.end() + 140])
            hay = [text] if not f.get('near') else windows
            for h in hay:
                for n in numbers_in(h):
                    for c in cands:
                        t2 = dict(tol)
                        if c != f['value'] and 'abs' in t2:
                            t2['abs'] = t2['abs'] * 100
                        if close(n, c, t2):
                            ok = True
        results[f['id']] = ok
    contradictions = [x['id'] for x in rub.get('forbidden', []) if re.search(x['pattern'], text, re.I)]
    share = sum(results.values()) / len(results) if results else 0.0
    return (share == 1.0 and not contradictions), {'fact_share': round(share, 3), 'facts': results, 'contradicted': contradictions}


# --- driver -------------------------------------------------------------------------------------------------------------
def load_set(args):
    """All questions of the chosen set: tools/dev_set/questions.jsonl, or compose_questions.jsonl (--set compose / --split dev2)."""
    if args.set == 'compose' or args.split == 'dev2':
        from tools.dev_set.compose_keys import read as read_compose
        return read_compose()
    return read()


def select(args, all_qs=None):
    qs = all_qs if all_qs is not None else load_set(args)
    split = args.split or ('dev2' if args.set == 'compose' else 'dev')
    if split != 'all':
        qs = [q for q in qs if q['split'] == split]
    if args.bucket:
        qs = [q for q in qs if q['bucket'] == args.bucket]
    if args.ids:
        qs = [q for q in qs if q['id'] in args.ids]
    return qs[: args.limit] if args.limit else qs


def chains(qs, all_qs):
    """Group the selected questions into chains, in order: each chain is a root (its ancestors included) followed by every
    selected follow-up under it, so a follow-up is always asked in the same conversation right after its parent. (Before
    2026-10-23 a parent seen first became a chain of one and its follow-up was asked in a fresh conversation.)"""
    by_id = {q['id']: q for q in all_qs}
    selected = {q['id'] for q in qs}

    def root(q):
        while q.get('follow_up_of') and q['follow_up_of'] in by_id:
            q = by_id[q['follow_up_of']]
        return q['id']

    def lineage(q):
        out = []
        while q:
            out.insert(0, q)
            q = by_id.get(q.get('follow_up_of'))
        return out

    out, done = [], set()
    for q in qs:
        r = root(q)
        if r in done: continue
        done.add(r)
        members = [x for x in qs if root(x) == r]
        chain, seen = [], set()
        for m in members:
            for c in lineage(m):  # ancestors first (asked for context even when not selected), each once
                if c['id'] not in seen:
                    seen.add(c['id']); chain.append(c)
        out.append(chain)
    return out


def run_chain(chain, args, run_id):
    rows, conv = [], f'devset-{run_id}-{chain[0]["id"]}'
    for q in chain:
        is_follow = bool(q['follow_up_of'])
        use_conv = is_follow and args.follow_ups == 'conversation'
        text = q['key'].get('standalone') if (is_follow and args.follow_ups == 'standalone') else q['question']
        # a chain's first question also opens the conversation, so the service can link what follows
        r = ask(args.url, text, conv if (use_conv or (args.follow_ups == 'conversation' and len(chain) > 1)) else None)
        resp = r['resp']
        correct, detail = score(q, resp)
        cr = resp.get('cost_receipt') or {}
        rows.append({'id': q['id'], 'bucket': q['bucket'], 'split': q['split'], 'asked': text, 'status': status_of(resp),
                     'answered': status_of(resp) in ANSWERED, 'correct': correct, 'detail': detail, 'seconds': round(r['seconds'], 2),
                     'usd': cr.get('usd'), 'paid_calls': cr.get('paid_calls', resp.get('new_paid_api_calls')), 'route': route_of(resp),
                     'followup_context': (resp.get('conversation') or {}).get('method') if is_follow else None,
                     'error': (resp.get('error') or '')[:300] if status_of(resp) not in ANSWERED else None,
                     'trace': trace_of(resp)})
        print(f"{q['id']:>6} {q['bucket'][:14]:14} {str(rows[-1]['status'])[:14]:14} correct={correct} {r['seconds']:.0f}s route={rows[-1]['route']}", flush=True)
    return rows


def pct(x, n):
    return f'{100 * x / n:.1f}%' if n else 'n/a'


def report(rows):
    print('\n=== per bucket ===')
    for b in BUCKETS:
        rs = [r for r in rows if r['bucket'] == b]
        if not rs:
            continue
        scored = [r for r in rs if r['correct'] is not None]
        ans = [r for r in rs if r['answered']]
        correct = [r for r in rs if r['correct'] is True]
        usd = [r['usd'] for r in rs if r['usd'] is not None]
        secs = sorted(r['seconds'] for r in rs)
        p = lambda f: secs[min(len(secs) - 1, int(f * len(secs)))]
        print(f"{b}\n  n={len(rs)} accuracy={pct(len(correct), len(rs))} coverage(answered)={pct(len(ans), len(rs))} "
              f"precision_on_answered={pct(sum(1 for r in ans if r['correct'] is True), len(ans))} refusals={len(rs) - len(ans)}\n"
              f"  cost_usd_known={sum(usd):.4f} over {len(usd)}/{len(rs)} questions; latency p50={statistics.median(secs):.1f}s p90={p(0.9):.1f}s\n"
              f"  routes={dict(Counter(r['route'] for r in rs))}")
    allc = [r for r in rows if r['correct'] is True]
    print(f"\nOVERALL n={len(rows)} accuracy={pct(len(allc), len(rows))} answered={pct(sum(r['answered'] for r in rows), len(rows))}")


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.split('\n')[0])
    ap.add_argument('--url', default=DEFAULT_URL)
    ap.add_argument('--set', choices=('dev', 'compose'), default='dev', help='question file: dev = questions.jsonl, compose = compose_questions.jsonl (split dev2)')
    ap.add_argument('--split', choices=('dev', 'dev2', 'heldout', 'all'), default=None, help='default dev (dev2 with --set compose); dev2 reads the compose set')
    ap.add_argument('--bucket', choices=BUCKETS)
    ap.add_argument('--ids', nargs='*')
    ap.add_argument('--limit', type=int, help='first N selected questions (smoke)')
    ap.add_argument('--jobs', type=int, default=1, help='parallel chains (the live service is shared: keep 1 unless told otherwise)')
    ap.add_argument('--follow-ups', choices=('conversation', 'standalone'), default='conversation')
    ap.add_argument('--out', help='write per-question rows as JSON')
    ap.add_argument('--report-card', metavar='RUN_JSON', help='print the per-tier report card of a saved run and exit (no service calls)')
    args = ap.parse_args(argv)
    if args.report_card:
        from tools.dev_set import report_card
        return report_card.main([args.report_card])
    all_qs = load_set(args)
    qs = select(args, all_qs)
    run_id = time.strftime('%Y%m%dT%H%M%S')
    cs = chains(qs, all_qs)
    with ThreadPoolExecutor(max_workers=max(1, args.jobs)) as ex:
        rows = [r for part in ex.map(lambda c: run_chain(c, args, run_id), cs) for r in part]
    report(rows)
    if args.out:
        Path(args.out).write_text(json.dumps(rows, indent=1))
    return 0


if __name__ == '__main__':
    sys.exit(main())
