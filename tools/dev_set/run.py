"""Send the development question set to an Agere service and score it against the independent keys.

    .venv/bin/python -m tools.dev_set.run --split dev --jobs 1            # whole dev split (slow: a question can take minutes)
    .venv/bin/python -m tools.dev_set.run --bucket insight --split heldout
    .venv/bin/python -m tools.dev_set.run --limit 5 --out /tmp/smoke.json  # smoke
    .venv/bin/python -m tools.dev_set.run --set compose                    # the composition set (split dev2; same as --split dev2)
    .venv/bin/python -m tools.dev_set.run --set dev3                       # the independent set (split dev3; same as --split dev3)

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
import threading
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

# Scorer version. v1 = the scorer of 2026-10-24 (kept byte-for-byte reproducible with --scorer v1). v2 (2026-10-25) closes three false positives an independent
# audit reproduced: (a) a quoted passage that NEGATES the answered proposition no longer supports a yes answer (and an affirming passage no longer supports a
# no); (b) a table's expected columns are bound to returned columns by name / key column order before values are compared; (c) a 'terms' direction fact fails when
# the answer negates the term ("saying they fell is incorrect") or asserts the opposite motion in the same sentence.
SCORER_VERSION = 2
ACTIVE_SCORER = 'v2'


def set_scorer(version):
    global ACTIVE_SCORER
    if version not in ('v1', 'v2'):
        raise ValueError(f'unknown scorer {version!r}')
    ACTIVE_SCORER = version


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
def score(q, resp, scorer=None):
    """-> (correct: bool|None, detail: dict). correct is None only for a refused / unanswered item (counted in coverage).
    scorer: 'v1' | 'v2' (default: the active scorer, v2)."""
    v2 = (scorer or ACTIVE_SCORER) == 'v2'
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
        if v2:
            return score_table_v2(key, rows, tol)
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
        return score_note(q, resp, text, low, a, v2)
    if t == 'rubric':
        return score_rubric(key['answer'], text, low, v2)
    return None, {'unscored_type': t}


UNDECIDED = 'undecided'  # an answer that says it cannot decide is neither yes nor no


def _lead_yes_no(low):
    """An answer that opens with Yes / No / Undecided is judged by that opening word, not by words inside quoted evidence."""
    m = re.match(r'\W*(yes|no|not documented|undecided)\b', low or '')
    if not m: return None
    return {'yes': True, 'no': False, 'not documented': False, 'undecided': UNDECIDED}[m.group(1)]


def score_note(q, resp, text, low, a, v2=False):
    key, kind = q['key'], q['key']['kind']
    ok, claim = False, None
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
        claim = {'yes': True, 'no': False}.get(got)
    elif kind == 'not_documented':
        ok = bool(re.search(r'not documented|no mention|not mentioned|does not (document|mention|address)|no documentation|nothing (documented|recorded)|not recorded|unknown', low))
    elif kind == 'value':
        ok = float(key['answer']) in numbers_in(text)
    else:
        ok = all(re.search(g, ocr_norm(text), re.I) for g in key.get('key_groups', []))
    cited = [e['record_id'] for e in q['evidence']]
    allowed = list(dict.fromkeys(cited + list(key.get('scope_records') or [])))  # any record the key lists as in scope may be the one cited
    has_cite = (not cited) or any(r in text for r in allowed)
    supported, why = (True, None) if not cited else passage_supports(q, resp, claim if v2 else None)
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


def passage_supports(q, resp, claim=None):
    """Does a QUOTED passage of a record the answer cites (a key evidence record or a scope record) itself contain the key's evidence?
    key_groups: every group must match in the pooled quotes of the cited, quoted records (a key can need two passages); a key with no groups
    (yes/no, value): a cited quoted passage must carry half of the content words of a key evidence quote, or the question's own subject word. A cited record with no quoted
    passage proves nothing. Deterministic; the same for every contestant. -> (bool, why)
    claim (scorer v2, yes/no only): the answer's own polarity, True (yes) / False (no) / None. A passage that negates the subject word cannot support a yes;
    one that affirms it cannot support a no."""
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
                if claim is None:
                    return True, None
                pol = passage_polarity(v, (anchors & have) or set().union(*want) & have)
                if (claim is True and pol == 'neg') or (claim is False and pol == 'pos'):
                    conflict = f'quoted passage is {"negated" if pol == "neg" else "affirmed"} for the subject but the answer says {"yes" if claim else "no"}'
                    continue
                return True, None
    if claim is not None and 'conflict' in locals():
        return False, conflict
    return False, 'no cited quoted passage carries the key evidence'


NEG_CUE = re.compile(r"\b(not|no|never|none|denies|denied|denying|stopped|discontinued|ceased|without|nor|cannot|unable|declin\w*|refus\w*)\b|n't\b|\bdoes not recogni[sz]e", re.I)
AFFIRM_CUE = re.compile(r"\b(takes|taking|taken|continues?|continued|reports?|endorses|started|prescribed|active|current(?:ly)?|increased|bumped|on)\b", re.I)


def passage_polarity(passage, anchors):
    """'neg' | 'pos' | 'mixed' | None for the subject words (anchors) of a quoted passage: an occurrence is negated when a negation cue (not, no, denies,
    stopped, discontinued, never, does not recognize, ...) sits within 6 words before it or 3 after it in the same sentence; 'pos' needs an affirming cue as well."""
    neg = pos = 0
    for sent in re.split(r'[.;\n!?]+', ocr_norm(str(passage).lower())):
        words = list(re.finditer(r"[a-z0-9']+", sent))
        for i, m in enumerate(words):
            if m.group(0) not in anchors:
                continue
            ctx_before = sent[words[max(0, i - 6)].start():m.start()]
            ctx_after = sent[m.end():words[min(len(words) - 1, i + 3)].end()]
            if NEG_CUE.search(ctx_before) or NEG_CUE.search(ctx_after):
                neg += 1
            elif AFFIRM_CUE.search(sent):
                pos += 1
    if neg and not pos:
        return 'neg'
    if pos and not neg:
        return 'pos'
    return 'mixed' if neg and pos else None


MOTION_UP = re.compile(r'\b(rose|rise|risen|rising|increas\w*|jump\w*|grew|grow\w*|growth|climb\w*|surg\w*|spik\w*|went up)\b', re.I)
MOTION_DOWN = re.compile(r'\b(fell|fall|falls|fallen|falling|declin\w*|decreas\w*|dropp?\w*|dipp?\w*|shr[au]nk|shrink\w*|went down)\b', re.I)
CMP_WORD = re.compile(r'\b(more|higher|greater|larger|less|lower|fewer|smaller)\b', re.I)
NEG_BEFORE = re.compile(r"(\bnot|\bno(?!-)|\bnever|\bnor|n't|\brather than|\binstead of|\bopposite of|\bcontrary to|\bnot a|\bnot an)\W+(?:\w+\W+){0,4}$", re.I)
NEG_AFTER = re.compile(r"^\W*(?:\w+\W+){0,6}?(incorrect|wrong|false|untrue|inaccurate|mistaken|not true|a myth)\b", re.I)


def _sentence_bounds(text, pos):
    start = max(text.rfind(c, 0, pos) for c in '.\n?!;') + 1
    ends = [e for e in (text.find(c, pos) for c in '.\n?!;') if e >= 0]
    return start, (min(ends) if ends else len(text))


def _negated_at(text, m):
    before = re.split(r'[.;:!?,\n]', text[max(0, m.start() - 60):m.start()])[-1]  # the clause the term sits in
    return bool(NEG_BEFORE.search(before) or NEG_AFTER.search(text[m.end():m.end() + 80]))


def _terms_hit_v2(pattern, text):
    """Scorer v2: does `pattern` hit the answer as an ASSERTED term? A direction word (rose / fell / higher / lower ...) counts only when it is not negated
    ("did not fall", "saying they fell is incorrect") and, for a motion word, when its sentence does not also assert the opposite motion.
    A match that itself contains a negation ("did not jump") or a word that is not a direction word keeps the v1 meaning."""
    for m in re.finditer(pattern, text, re.I):
        w = m.group(0)
        up, down, cmp_ = MOTION_UP.search(w), MOTION_DOWN.search(w), CMP_WORD.search(w)
        if not (up or down or cmp_) or re.search(r"\b(not|no|never)\b|n't", w, re.I):
            return True
        if _negated_at(text, m):
            continue
        if up or down:
            s0, s1 = _sentence_bounds(text, m.start())
            opp = MOTION_DOWN if up else MOTION_UP
            if any(not _negated_at(text, o) for o in opp.finditer(text, s0, s1)):
                continue
        return True
    return False


def _pat_in(text, pats):
    return any(re.search(p, text, re.I) for p in pats)


def score_rubric(rub, text, low, v2=False):
    facts, results = rub['required_facts'], {}
    nums = numbers_in(text)
    for f in facts:
        ok = False
        if f['kind'] == 'terms':
            hits = [(_terms_hit_v2(p, text) if v2 else bool(re.search(p, text, re.I))) for p in f['patterns']]
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


# --- table scoring v2 ---------------------------------------------------------------------------------------------------
def _col_tokens(name):
    return {w for w in re.split(r'[^a-z0-9]+', str(name).lower()) if w and w not in ('usd', 'total', 'amount', 'n', 'of', 'the')}


def _col_sim(a, b):
    import difflib
    ta, tb = _col_tokens(a), _col_tokens(b)
    if str(a).lower() == str(b).lower():
        return 1.0
    jac = len(ta & tb) / len(ta | tb) if ta | tb else 0.0
    return max(jac, difflib.SequenceMatcher(None, str(a).lower(), str(b).lower()).ratio() if (ta & tb) or str(a).lower() in str(b).lower() or str(b).lower() in str(a).lower() else 0.0)


def bind_columns(exp_cols, got_cols, key_cols=None, sample=None, exp_sample=None):
    """expected column -> returned column: exact (case-insensitive) name, else best name similarity (>= 0.34) among unclaimed columns of a compatible value
    type, else the same position when the key lists its columns and the arity matches. None when a column cannot be bound."""
    def kind(v):
        return 'n' if isinstance(v, (int, float)) and not isinstance(v, bool) else 's'
    bound, taken = {}, set()
    for ec in exp_cols:
        best, bs = None, 0.0
        for gc in got_cols:
            if gc in taken:
                continue
            if sample is not None and exp_sample is not None and kind(sample.get(gc)) != kind(exp_sample.get(ec)):
                continue
            sc = _col_sim(ec, gc)
            if sc > bs:
                best, bs = gc, sc
        if best is not None and bs >= 0.34:
            bound[ec] = best
            taken.add(best)
    if len(bound) < len(exp_cols) and key_cols and len(key_cols) == len(got_cols) and set(exp_cols) <= set(key_cols):
        bound, taken = {}, set()
        for ec in exp_cols:
            gc = got_cols[list(key_cols).index(ec)]
            if sample is not None and exp_sample is not None and kind(sample.get(gc)) != kind(exp_sample.get(ec)):
                return None
            bound[ec] = gc
    return bound if len(bound) == len(exp_cols) else None


def score_table_v2(key, rows, tol):
    """Each expected row must match one returned row COLUMN BY COLUMN (expected column bound to a returned column by name, else key column order),
    numbers within tolerance (a fraction key may match a percent), strings equal. Row count must equal; order checked when the key says so."""
    exp_rows = key['answer']
    exp_cols = list(exp_rows[0].keys()) if exp_rows else []
    got_cols = list(rows[0].keys()) if rows and isinstance(rows[0], dict) else []
    bound = bind_columns(exp_cols, got_cols, key.get('columns'), rows[0] if rows else None, exp_rows[0] if exp_rows else None) if exp_rows else {}
    if exp_rows and bound is None:
        return False, {'rows_expected': len(exp_rows), 'rows_matched': 0, 'rows_returned': len(rows), 'unbound_columns': exp_cols, 'returned_columns': got_cols}

    def cell_ok(v, ev):
        if isinstance(v, (int, float)) and isinstance(ev, (int, float)) and not isinstance(v, bool) and not isinstance(ev, bool):
            if close(float(v), float(ev), tol):
                return True
            return isinstance(ev, float) and abs(float(ev)) <= 1.0 and close(float(v), float(ev) * 100, {**(tol or {}), 'abs': (tol or {}).get('abs', 0) * 100})
        return isinstance(v, str) and isinstance(ev, str) and v.strip().lower() == ev.strip().lower()
    used, hits, order = set(), 0, []
    for er in exp_rows:
        for i, r in enumerate(rows):
            if i not in used and all(cell_ok(r.get(bound[ec]), ev) for ec, ev in er.items()):
                used.add(i); hits += 1; order.append(i)
                break
    ok = hits == len(exp_rows) and len(rows) == len(exp_rows) and (not key.get('order_matters') or order == sorted(order))
    return ok, {'rows_expected': len(exp_rows), 'rows_matched': hits, 'rows_returned': len(rows), 'column_binding': bound}


# --- answer keys v2 -------------------------------------------------------------------------------------------------------
def _walk(node, path):
    """Resolve `a.b[id=x].c` below `node`; returns (parent, last key)."""
    parts = re.findall(r'[^.\[\]]+(?:\[[^\]]+\])?', path)
    for part in parts[:-1]:
        m = re.fullmatch(r'(\w+)\[(\w+)=([^\]]+)\]', part)
        if m:
            node = next(x for x in node[m.group(1)] if x.get(m.group(2)) == m.group(3))
        else:
            node = node[part]
    return node, parts[-1]


def apply_keys_v2(qs, rows=None):
    """A deep copy of the dev3 questions with the v2 key fields (tools/dev_set/dev3_keys_v2.jsonl) written over the v1 ones. dev3_questions.jsonl is untouched."""
    import copy
    from tools.dev_set.dev3_keys_v2 import read as read_v2
    by_id = {}
    for r in (rows if rows is not None else read_v2()):
        by_id.setdefault(r['id'], []).append(r)
    out = []
    for q in qs:
        if q['id'] in by_id:
            q = copy.deepcopy(q)
            for r in by_id[q['id']]:
                parent, last = _walk(q, r['field'])
                parent[last] = r['v2']
        out.append(q)
    return out


# --- driver -------------------------------------------------------------------------------------------------------------
def load_set(args):
    """All questions of the chosen set: tools/dev_set/questions.jsonl, or compose_questions.jsonl (--set compose / --split dev2)."""
    if args.set == 'dev3' or args.split == 'dev3':
        from tools.dev_set.dev3_keys import read as read_dev3
        qs = read_dev3()
        return apply_keys_v2(qs) if getattr(args, 'keys', 'v2') == 'v2' else qs
    if args.set == 'compose' or args.split == 'dev2':
        from tools.dev_set.compose_keys import read as read_compose
        return read_compose()
    return read()


def select(args, all_qs=None):
    qs = all_qs if all_qs is not None else load_set(args)
    split = args.split or {'compose': 'dev2', 'dev3': 'dev3'}.get(args.set, 'dev')
    if split != 'all':
        qs = [q for q in qs if q['split'] == split]
    if getattr(args, 'half', None):
        from tools.dev_set.halves import keep
        qs = keep(qs, args.half)
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


# A Claude outage is not an accuracy result (2026-10-26: a subscription weekly limit and an unfunded API key turned two runs into hours of
# refusals). Two outage responses in a row stop the run with the provider's message instead of scoring it.
OUTAGE = re.compile(r'claude CLI error|weekly limit|usage limit|rate limit|credit balance is too low|provider_unavailable|overloaded', re.I)
_outages = {'streak': 0, 'last': None}
_outage_lock = threading.Lock()


class ProviderOutage(RuntimeError):
    pass


def outage_check(resp):
    text = json.dumps({k: (resp or {}).get(k) for k in ('error', 'tiers_tried', 'stage_diagnostics')}, default=str)
    m = OUTAGE.search(text)
    with _outage_lock:
        if m and status_of(resp) not in ANSWERED:
            _outages['streak'] += 1
            i = m.start()
            _outages['last'] = text[max(0, i - 80): i + 220]
        else:
            _outages['streak'] = 0
        if _outages['streak'] >= 2:
            raise ProviderOutage('Claude is unavailable to the stack; the run stopped instead of scoring an outage: ' + str(_outages['last']))


def run_chain(chain, args, run_id):
    rows, conv = [], f'devset-{run_id}-{chain[0]["id"]}'
    for q in chain:
        is_follow = bool(q['follow_up_of'])
        use_conv = is_follow and args.follow_ups == 'conversation'
        text = q['key'].get('standalone') if (is_follow and args.follow_ups == 'standalone') else q['question']
        # a chain's first question also opens the conversation, so the service can link what follows
        r = ask(args.url, text, conv if (use_conv or (args.follow_ups == 'conversation' and len(chain) > 1)) else None)
        resp = r['resp']
        outage_check(resp)
        correct, detail = score(q, resp)
        cr = resp.get('cost_receipt') or {}
        rows.append({'id': q['id'], 'bucket': q['bucket'], 'split': q['split'], 'asked': text, 'status': status_of(resp),
                     'answered': status_of(resp) in ANSWERED, 'correct': correct, 'detail': detail, 'seconds': round(r['seconds'], 2),
                     'usd': cr.get('usd'), 'paid_calls': cr.get('paid_calls', resp.get('new_paid_api_calls')), 'route': route_of(resp),
                     'followup_context': (resp.get('conversation') or {}).get('method') if is_follow else None,
                     'error': (resp.get('error') or '')[:300] if status_of(resp) not in ANSWERED else None,
                     'trace': trace_of(resp), 'response': resp})
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
        shares = [r['detail']['fact_share'] for r in rs if isinstance(r.get('detail'), dict) and 'fact_share' in r['detail']]
        if shares:
            print(f"  rubric questions={len(shares)} mean fact_share={sum(shares) / len(shares):.3f} (fraction of required facts present; correct needs all)")
    allc = [r for r in rows if r['correct'] is True]
    print(f"\nscorer v{SCORER_VERSION if ACTIVE_SCORER == 'v2' else 1}")
    print(f"OVERALL n={len(rows)} accuracy={pct(len(allc), len(rows))} answered={pct(sum(r['answered'] for r in rows), len(rows))}")


def rescore(args):
    """Re-score a saved run (rows carrying 'response') under --scorer / --keys. A row without a stored response keeps its saved verdict and is counted
    as 'unrescored'. Questions are looked up in every set file by id."""
    saved = json.loads(Path(args.rescore).read_text())
    ns = argparse.Namespace(**{**vars(args), 'set': 'dev3', 'split': 'dev3'})
    qs = {q['id']: q for q in load_set(ns)}
    for name in ('compose',):  # the dev set file also holds held-out rows: never loaded here
        try:
            ns2 = argparse.Namespace(**{**vars(args), 'set': name, 'split': 'dev2'})
            for q in load_set(ns2):
                qs.setdefault(q['id'], q)
        except Exception:  # noqa: BLE001
            pass
    rows, skipped, flips = [], 0, []
    for r in saved:
        q = qs.get(r['id'])
        if q is None or 'response' not in r:
            skipped += 1
            rows.append(r)
            continue
        correct, detail = score(q, r['response'])
        if correct != r.get('correct'):
            flips.append((r['id'], r.get('correct'), correct))
        rows.append({**r, 'correct': correct, 'detail': detail})
    report(rows)
    print(f"\nrescored {len(rows) - skipped}/{len(rows)} rows (scorer {args.scorer}, keys {args.keys}); {skipped} without a stored response keep their saved verdict")
    for i, was, now in flips:
        print(f"  changed {i}: {was} -> {now}")
    if args.out:
        Path(args.out).write_text(json.dumps(rows, indent=1))
    return 0


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.split('\n')[0])
    ap.add_argument('--url', default=DEFAULT_URL)
    ap.add_argument('--set', choices=('dev', 'compose', 'dev3'), default='dev', help='question file: dev = questions.jsonl, compose = compose_questions.jsonl (split dev2), dev3 = dev3_questions.jsonl (split dev3, independent wording)')
    ap.add_argument('--split', choices=('dev', 'dev2', 'dev3', 'heldout', 'all'), default=None, help='default dev (dev2 with --set compose, dev3 with --set dev3); dev2 reads the compose set, dev3 the independent set')
    ap.add_argument('--bucket', choices=BUCKETS)
    ap.add_argument('--half', choices=('build', 'check', 'all'), help='dev3 only: the build half (develop) or the check half (measure transfer only)')
    ap.add_argument('--ids', nargs='*')
    ap.add_argument('--limit', type=int, help='first N selected questions (smoke)')
    ap.add_argument('--quick', type=int, help='quick check: the first N questions of each bucket (5 buckets x N)')
    ap.add_argument('--jobs', type=int, default=1, help='parallel chains (the live service is shared: keep 1 unless told otherwise)')
    ap.add_argument('--follow-ups', choices=('conversation', 'standalone'), default='conversation')
    ap.add_argument('--out', help='write per-question rows as JSON')
    ap.add_argument('--scorer', choices=('v1', 'v2'), default='v2', help='scoring rules: v2 (default) closes the 2026-10-25 audit false positives; v1 reproduces the 2026-10-24 scorer')
    ap.add_argument('--keys', choices=('v1', 'v2'), default='v2', help='dev3 answer keys: v2 (default) = definitions decided 2026-10-25 (dev3_keys_v2.jsonl); v1 = dev3_questions.jsonl as written')
    ap.add_argument('--rescore', metavar='RUN_JSON', help='re-score the saved rows of a run (their stored responses) with --scorer / --keys; no service calls')
    ap.add_argument('--report-card', metavar='RUN_JSON', help='print the per-tier report card of a saved run and exit (no service calls)')
    args = ap.parse_args(argv)
    if args.report_card:
        from tools.dev_set import report_card
        return report_card.main([args.report_card])
    set_scorer(args.scorer)
    if args.rescore:
        return rescore(args)
    all_qs = load_set(args)
    qs = select(args, all_qs)
    if args.quick:  # a quick check: the first N questions of each bucket (chains keep their parents)
        per = Counter()
        qs = [q for q in sorted(qs, key=lambda q: q['id']) if per.update([q['bucket']]) or per[q['bucket']] <= args.quick]
    run_id = time.strftime('%Y%m%dT%H%M%S')
    cs = chains(qs, all_qs)
    try:
        with ThreadPoolExecutor(max_workers=max(1, args.jobs)) as ex:
            rows = [r for part in ex.map(lambda c: run_chain(c, args, run_id), cs) for r in part]
    except ProviderOutage as exc:
        print('\nSTOPPED: ' + str(exc), flush=True)
        return 3
    report(rows)
    if args.out:
        Path(args.out).write_text(json.dumps(rows, indent=1))
    return 0


if __name__ == '__main__':
    sys.exit(main())
