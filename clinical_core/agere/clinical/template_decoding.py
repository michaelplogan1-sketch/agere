"""Template-constrained decoding (TeCoD, Wang et al., PACMMOD 2025) for recurring questions.

Many client questions are a verified question with different VALUES ("A1c above 9 in 2028" -> "A1c above 8 in 2029"). The semantic
cache refuses those (slots differ); the bottled model re-derives the whole query. TeCoD keeps the verified query's structure and only
fills the values:
  1. abstract(question, sql): literals of the question (dates, years, numbers) that the verified SQL uses become typed slots in BOTH
     the question and the SQL ('How many ... above {s0} in {s1}?' / 'WHERE value > {s0} AND year = {s1}'). A template is kept only if
     re-instantiating it with the original values reproduces the SQL byte for byte.
  2. match(question): the NLI-style matcher decides whether the new question ENTAILS a template: the masked new question must match the
     template question exactly (deterministic, regex) or -- for rewordings -- clear the encoder entailment score (calibrated semantic-cache
     gate) with an identical content signature (semantic_cache.signature on the masked text). No local NLI model is installed, so the
     entailment scorer is pluggable (AGERE_TECOD_NLI) and the default is the encoder + signature check.
  3. fill: values come from the new question's own literals in template order (exact path: the regex groups); when the literals do not
     align, the local bottled model fills the slots under a TemplateGrammar constraint (the SQL template with typed slots: it can only
     write digits/dates in slot positions). Every filled value must be a literal of the new question; otherwise the template declines.
The filled SQL is returned as a plan like the semantic cache's, and the planner lints/executes/checks it like any other proposal.
AGERE_TEMPLATE_DECODING=off disables.
"""
import json
import os
import re

import numpy as np

VERSION = 'template-decoding/1'
_DATE = r'\d{4}-\d{2}-\d{2}'
_NUM = r'(?<![\w.\-])-?\d+(?:\.\d+)?(?![\w.])'
LITERAL = re.compile(f'(?<![\\w-])({_DATE})(?![\\w-])|({_NUM})')
KIND_FULL = {'date': _DATE, 'integer': r'-?\d+', 'number': r'-?\d+(?:\.\d+)?'}


def enabled():
    return os.environ.get('AGERE_TEMPLATE_DECODING', 'on') != 'off'


def _kind(lit):
    return 'date' if re.fullmatch(_DATE, lit) else 'integer' if re.fullmatch(r'-?\d+', lit) else 'number'


def literals(question):
    """Distinct (text, kind) literals of a question in order of first appearance."""
    out, seen = [], set()
    for m in LITERAL.finditer(question):
        t = m.group(0)
        if t not in seen: seen.add(t); out.append((t, _kind(t)))
    return out


def _sql_sub(sql, lit, token):
    """Replace standalone occurrences of the literal in SQL (not identifiers, not after LIMIT/OFFSET); quotes around it are kept."""
    pat = re.compile(r"(?<![\w.\-])" + re.escape(lit) + r"(?![\w.])")
    parts, n, last = [], 0, 0
    for m in pat.finditer(sql):
        before = sql[:m.start()]
        if re.search(r'(?i)\b(?:limit|offset)\s+$', before): continue
        if not re.fullmatch(_DATE, lit) and not re.search(r"(?i)(?:[<>=!]=?|<>|\(|,|'|\b(?:between|and|in|or|then|else)\b)\s*$", before): continue  # a value position, not structure
        parts.append(sql[last:m.start()]); parts.append(token); last = m.end(); n += 1
    parts.append(sql[last:])
    return ''.join(parts), n


def abstract(question, sql):
    """-> template dict or None. {'question': 'text with {s0}', 'sql': '...{s0}...', 'slots': [{'name','kind','example'}]}"""
    q_t, s_t, slots = question, sql, []
    # dates first so '2030-05-27' is one slot, then years/numbers; a year inside an already-slotted date is gone by then
    for lit, kind in sorted(literals(question), key=lambda x: (x[1] != 'date', question.index(x[0]))):
        token = '{s%d}' % len(slots)
        new_sql, n = _sql_sub(s_t, lit, token)
        if n == 0:
            # a year may be used inside quoted dates ('2029-01-01'): slot it as a year part when SQL has YYYY-... forms
            if kind == 'integer' and re.fullmatch(r'(?:19|20)\d\d', lit):
                new_sql, n = re.subn(r"(?<!\d)" + lit + r"(?=-\d\d-\d\d)", token, s_t)
                if n == 0: continue
            else: continue
        s_t = new_sql
        if kind == 'integer' and re.fullmatch(r'(?:19|20)\d\d', lit):
            # period ends are written as the NEXT year's first day ('2029-01-01'): a derived slot, so the range moves with the year
            s_t = re.sub(r"(?<!\d)" + str(int(lit) + 1) + r"(?=-\d\d-\d\d)", '{s%d+1}' % len(slots), s_t)
        q_t = re.sub(r"(?<![\w.\-])" + re.escape(lit) + r"(?![\w.])", token, q_t)
        slots.append({'name': token[1:-1], 'kind': kind, 'example': lit})
    if not slots: return None
    t = {'question': q_t, 'sql': s_t, 'slots': slots}
    if instantiate(t, {s['name']: s['example'] for s in slots}) != sql: return None   # round trip must reproduce the verified SQL
    return t


def instantiate(template, values):
    out = template['sql']
    for s in template['slots']:
        out = out.replace('{%s}' % s['name'], str(values[s['name']]))
        if '{%s+1}' % s['name'] in out and re.fullmatch(r'-?\d+', str(values[s['name']])):
            out = out.replace('{%s+1}' % s['name'], str(int(values[s['name']]) + 1))
    return out


def _question_regex(template):
    parts = re.split(r'(\{s\d+\})', template['question'])
    kinds = {s['name']: s['kind'] for s in template['slots']}
    pat = ''.join(f"(?P<{p[1:-1]}>{KIND_FULL[kinds[p[1:-1]]]})" if re.fullmatch(r'\{s\d+\}', p) else re.escape(p) for p in parts)
    return re.compile(r'\s*' + pat.replace(r'\ ', r'\s+') + r'\s*$', re.I)


def _masked(question, kinds=None):
    return LITERAL.sub('{n}', question)


def sql_parts(template):
    """TemplateGrammar parts for the SQL template: literal text with typed slots (a derived '{s1+1}' is its own integer slot 's1p1')."""
    parts = []
    for p in re.split(r'(\{s\d+(?:\+1)?\})', template['sql']):
        if not p: continue
        m = re.fullmatch(r'\{(s\d+)(\+1)?\}', p)
        if m: parts.append(('slot', m.group(1) + ('p1' if m.group(2) else ''), 'integer' if m.group(2) else
                            next(s['kind'] for s in template['slots'] if s['name'] == m.group(1))))
        else: parts.append(('lit', p))
    return parts


# ---- the store (templates from verified SQL) --------------------------------------------------------------------------
_STORE = {}


def store(path, ctx):
    from . import semantic_cache
    rows, corrected = semantic_cache._verified(path, ctx)
    key = (str(path), ctx, len(rows), len(corrected))
    if key not in _STORE:
        templates = {}
        for r in rows:
            if r['plan_kind'] != 'sql' or semantic_cache._norm(r['question']) in corrected or semantic_cache.is_benchmark(r['question']): continue
            sql = (r['plan'] or {}).get('query')
            t = abstract(r['question'], sql) if sql else None
            if not t: continue
            k = (t['question'].casefold(), t['sql'])
            e = templates.setdefault(k, {**t, 'support': 0, 'source_questions': [], 'returns': (r['plan'] or {}).get('returns'),
                                         'agreement': r['agreement'], 'plan_kind': 'sql'})
            e['support'] += 1; e['source_questions'].append(r['question'])
        _STORE.clear(); _STORE[key] = list(templates.values())
    return _STORE[key], corrected


# ---- matching and filling -----------------------------------------------------------------------------------------------
def default_entails(encoder, premise, hypotheses):
    """Entailment scores of `premise` against each hypothesis (masked question texts): encoder cosine, 0 when the content
    signature (semantic_cache.signature: requested shape + ordered critical slots) differs."""
    from . import semantic_cache
    v = np.asarray(encoder.encode([premise] + list(hypotheses)), dtype=float)
    n = np.maximum(np.linalg.norm(v, axis=1), 1e-12)
    sims = v[1:] @ v[0] / (n[1:] * n[0])
    sig = semantic_cache.signature(premise)
    return [float(s) if semantic_cache.signature(h) == sig else 0.0 for s, h in zip(sims, hypotheses)]


def lookup(path, question, encoder, *, model=None, entails=None):
    """-> (hit | None, receipt). hit = {plan_kind, plan, agreement, matched_question, template}."""
    from . import learning, semantic_cache
    receipt = {'template_decoding': VERSION}
    if not enabled(): return None, {**receipt, 'reason': 'disabled'}
    if not (os.path.exists(os.path.join(str(path), 'learning.sqlite3'))): return None, {**receipt, 'reason': 'no verified store'}
    if semantic_cache.is_benchmark(question): return None, {**receipt, 'reason': 'benchmark question: never served from templates'}
    templates, corrected = store(path, learning.context(path))
    if any(semantic_cache._norm(question) == semantic_cache._norm(q) for t in templates for q in t['source_questions']):
        return None, {**receipt, 'reason': 'exact verified question: the learned_verified_sql rung owns it'}
    if semantic_cache._norm(question) in corrected: return None, {**receipt, 'reason': 'question was corrected by a user'}
    if not templates: return None, {**receipt, 'reason': 'no template with value slots', 'templates': 0}
    receipt['templates'] = len(templates)
    lits = literals(question)
    if not lits: return None, {**receipt, 'reason': 'question has no value literal to fill'}
    # 1. exact: the template question matches with typed groups (deterministic fill)
    for t in templates:
        m = _question_regex(t).match(question)
        if m and semantic_cache._norm(question) not in {semantic_cache._norm(q) for q in t['source_questions']}:
            return _hit(t, m.groupdict(), question, receipt, 'exact_template', 1.0)
    # 2. entailment matcher for rewordings, then literal alignment or grammar-constrained model fill
    masked = _masked(question)
    hyps = [_masked(re.sub(r'\{s\d+\}', '{n}', t['question'])) for t in templates]
    scores = (entails or (lambda p, h: default_entails(encoder, p, h)))(masked, hyps) if (entails or encoder is not None) else None
    if scores is None: return None, {**receipt, 'reason': 'no local encoder'}
    best = int(np.argmax(scores)); t = templates[best]
    gate = semantic_cache.similarity_gate(encoder)
    receipt.update(matched_question=t['source_questions'][0], entailment=round(float(scores[best]), 4), threshold=gate['threshold'])
    if scores[best] < gate['threshold']: return None, {**receipt, 'reason': 'no template is entailed by this question'}
    names = [s['name'] for s in t['slots']]
    kinds = [s['kind'] for s in t['slots']]
    if [k for _, k in lits] == kinds:
        values = {n: lit for n, (lit, _) in zip(names, lits)}
        return _hit(t, values, question, receipt, 'aligned_literals', float(scores[best]))
    if model is None:
        try:
            from . import bottled
            model = bottled.BottledModel.get() if bottled.BottledModel.available() else None
        except Exception:
            model = None
    if model is None: return None, {**receipt, 'reason': 'value literals do not align with the template slots and no local model'}
    values = fill_with_model(model, t, question)
    if values is None: return None, {**receipt, 'reason': 'grammar-constrained slot fill failed'}
    return _hit(t, values, question, receipt, 'model_slot_fill', float(scores[best]))


def fill_with_model(model, template, question):
    """The local model writes the SQL template with its slots filled, under a TemplateGrammar: it can only emit typed values in slot
    positions. -> {slot: value} or None. Every value must be a literal of the question."""
    from .constrained_decoding import TemplateGrammar
    grammar = TemplateGrammar(sql_parts(template))
    msgs = [{'role': 'system', 'content': 'Fill the typed value slots of this verified SQL for the new question. Output the SQL only.'},
            {'role': 'user', 'content': f'Verified question: {template["source_questions"][0]}\nNew question: {question}\nSQL:\n' + template['sql']}]
    try:
        texts, _ = model(msgs, k=1, grammar=grammar)
    except TypeError:
        texts, _ = model(msgs, k=1)
    values = grammar.slots(texts[0].strip()) if texts else None
    if not values: return None
    for name, v in list(values.items()):
        if name.endswith('p1'):   # a derived next-year slot must agree with its base slot
            base = values.get(name[:-2])
            if base is None or not re.fullmatch(r'-?\d+', str(base)) or int(v) != int(base) + 1: return None
            del values[name]
    return values


def _hit(t, values, question, receipt, how, score):
    qlits = {lit for lit, _ in literals(question)}
    for s in t['slots']:
        v = str(values.get(s['name']))
        if v not in qlits or not re.fullmatch(KIND_FULL[s['kind']], v):
            return None, {**receipt, 'reason': f'slot {s["name"]} value {v!r} is not a {s["kind"]} literal of the question'}
    sql = instantiate(t, values)
    plan = {'query': sql, **({'returns': t['returns']} if t.get('returns') else {})}
    receipt.update(reason='reused', fill=how, slots={k: values[k] for k in (s['name'] for s in t['slots'])}, template_support=t['support'],
                   similarity=round(score, 4))
    return {'plan_kind': 'sql', 'plan': plan, 'agreement': t['agreement'], 'matched_question': t['source_questions'][0], 'template': t['question']}, receipt
