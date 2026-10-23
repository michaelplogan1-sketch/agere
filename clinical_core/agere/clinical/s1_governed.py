"""System One in front of the Claude routers: typed choices for the governed metric and the analytic engine (Mike, 2026-10-22:
"System One goes BEFORE any Claude model wherever a typed choice can do the job").

metric_router.route() and operator_router.route() each make one choice from a menu and fill one typed schema. Both take an
`ask` hook that replaces the Claude call, so the routers' own gates (slot validation, every number accounted for, subtracted
components, clinical scope, answer shape, breakdown, exact fit) run unchanged on System One's picks. This module is that hook:

  metric   choose among a SHORT list of the governed metrics that fit the question (deterministic match rules, the stack's
           encoder, idf word overlap; AGERE_S1_METRIC_OPTIONS, default 10) plus none_of_these; then fill the slots:
             period    stated window -> one choice among all_time / year / ytd / trailing_months / trailing_days / other_window;
                       the year and the N are parsed from the question (a window that cannot be parsed is not guessed)
             group_by  only when the question asks for a breakdown; one choice among the metric's closed set
             other enum slots (anchor, measure, returns, ...) only when the question names one of the values
             required integers: the one number the question states and no slot has used
           Metrics with a population-atom slot (concept lists, lab thresholds) stay with the Claude router.
           GENERATED metrics (generated_metrics.py: count / share / by-column metrics derived from the semantic layer's own
           tables, dates and categorical values, origin 'generated', never certified as authored) compete for the same list:
           at most AGERE_S1_GENERATED_OPTIONS (default 5) of the options are generated, interleaved after the authored
           candidates (authored first on ties), and a generated metric is offered only when the question names its value
           (or its column). AGERE_S1_GENERATED=off removes them.
  engine   choose among the engines the learned router and the question's words point at (AGERE_S1_ENGINE_OPTIONS, default 8)
           plus literal_lookup and none_of_these; then the closed-set parameters the question names, at most three choices;
           numbers are parsed ("top 10", "90 days", "6 months") into the one parameter that can hold them. Any number left
           over, or a population restriction, is not guessed: the Claude router fills it.

A choice is accepted only when its (calibrated) probability reaches AGERE_S1_METRIC_ACCEPT / AGERE_S1_ENGINE_ACCEPT (default
0.6) and the provider decided (system_one.decide: Jev, then Perplexity below the bar). none_of_these, a low probability or a
slot System One is unsure about declines the tier and the cascade continues to the Claude routers, unchanged. Every call is
cached by request hash in the provider ledger; its cost is charged to the cascade; the plan carries the option lists,
probabilities, providers and costs (info['system_one']). Switch: AGERE_S1_GOVERNED (default on, live stacks only).
"""
import math
import os
import re

from . import metric_router as MR
from . import operator_router as OR
from .system_one_gate import cost as call_cost, ready as gate_ready, s1

VERSION = 'system-one-governed/1'
NONE_OF = 'none_of_these'
NOT_STATED = 'not_stated'
TASKS = {'metric': 's1_metric_choice', 'slot': 's1_metric_slot', 'engine': 's1_engine_choice', 'param': 's1_engine_param'}


def _env_float(name, default):
    try:
        return float(os.environ.get(name, default))
    except ValueError:
        return float(default)


def metric_accept():
    return _env_float('AGERE_S1_METRIC_ACCEPT', 0.6)


def engine_accept():
    return _env_float('AGERE_S1_ENGINE_ACCEPT', 0.6)


def option_count(name, default):
    try:
        return max(2, int(os.environ.get(name, default)))
    except ValueError:
        return default


def ready(stack):
    return gate_ready(stack, 'AGERE_S1_GOVERNED')


class Unfillable(Exception):
    """A slot or parameter System One cannot be trusted with: the tier declines and the Claude router fills it."""


# -- one typed call, recorded ---------------------------------------------------------------------------------------------
class Recorder:
    """Every System One call of one routing attempt: options, probabilities, provider, cost. steps are the cost receipts the
    cascade's economics() sums; unbilled is what has not yet been charged to the cascade."""

    def __init__(self, ledger, as_of):
        self.ledger, self.as_of = ledger, as_of
        self.calls, self.steps, self.notes = [], [], []
        self.paid_calls, self.usd = 0, 0.0
        self.billed = (0, 0.0)

    def decide(self, step, task, prompt, options, threshold):
        out = s1().decide(prompt, options, [], as_of=self.as_of, threshold=threshold, task_key=task, ledger=self.ledger, allow_live=True)
        calls, usd = call_cost(out)
        probs = out.get('calibrated_probabilities') or out.get('probabilities') or {}
        label = out.get('label') or 'unknown'
        p = float(probs.get(label, 0.0))
        provider = out.get('decided_by') or out.get('provider')
        accepted = out.get('decided_by') is not None and label in options and p >= threshold
        top = dict(sorted(((k, round(float(v), 4)) for k, v in probs.items()), key=lambda kv: -kv[1])[:4])
        entry = {'step': step, 'task': task, 'provider': provider, 'label': label, 'p': round(p, 4), 'accepted': accepted,
                 'threshold': threshold, 'options': list(options), 'probabilities': top, 'calls': calls, 'cost_usd': round(usd, 6),
                 'cache_hit': bool(out.get('cache_hit')) or (calls == 0 and bool(out.get('steps'))), 'status': out.get('status')}
        self.calls.append(entry)
        self.paid_calls += calls
        self.usd += usd
        if calls or usd:
            self.steps.append({'stage': step, 'receipt': {'cost_estimate': {'estimated_usd': usd}, 'provider': provider}})
        return {'label': label, 'p': p, 'accepted': accepted, 'entry': entry}

    def unbilled(self):
        """(calls, usd) not yet charged to the cascade; marks them charged."""
        out = (self.paid_calls - self.billed[0], self.usd - self.billed[1])
        self.billed = (self.paid_calls, self.usd)
        return out

    def receipt(self):
        return {'version': VERSION, 'calls': self.calls, 'new_paid_api_calls': self.paid_calls, 'cost_usd': round(self.usd, 6),
                'notes': self.notes}

    def reply(self, choice, note=None):
        """(choice, receipt) in the shape the Claude-backed routers' ask hook returns."""
        last = self.calls[-1] if self.calls else {}
        if note: self.notes.append(note)
        return choice, {'model': last.get('provider') or 'system_one', 'provider': last.get('provider'), 'api_usd': last.get('cost_usd') or 0.0,
                        'cache_hit': bool(last.get('cache_hit')), 'system_one': last}


# -- shortlists -----------------------------------------------------------------------------------------------------------
_STOP = frozenset('the and for with that this what which how many much who are was were our their have has had from into over per '
                  'than then them they any all can does did not out its but about patients patient members member rate number '
                  'total count'.split())


def stems(text):
    return {w[:6] for w in re.findall(r'[a-z0-9]{3,}', str(text).lower()) if w not in _STOP}


def overlap_rank(question, docs):
    """[(score, key)] best first: idf-weighted stem overlap between the question and each key's text."""
    q = stems(question)
    sets = {k: stems(t) for k, t in docs.items()}
    df = {}
    for s in sets.values():
        for w in s: df[w] = df.get(w, 0) + 1
    n = len(sets) or 1
    out = []
    for k, s in sets.items():
        hit = q & s
        if hit: out.append((sum(math.log(1 + n / df[w]) for w in hit) / math.sqrt(len(s) + 4), k))
    return sorted(out, key=lambda x: (-x[0], x[1]))


def fillable(m):
    """True when every slot of the metric is one System One can fill (population atoms and free numbers are Claude's)."""
    return not any(d['type'] in ('population', 'number') for d in MR.slots_of(m).values())


def generated_enabled():
    return os.environ.get('AGERE_S1_GENERATED', 'on').strip().lower() not in ('off', '0', 'false', 'no')


def is_generated(m):
    return (m or {}).get('origin') == 'generated'


def with_generated(lib, question, path, encoder=None):
    """(library + the generated metrics that may fit the question in rank order, their ids). The stack's generated metrics are
    built once per semantic digest (generated_metrics.build) and ranked here by word overlap; any failure leaves the authored
    library alone."""
    if not path or not generated_enabled(): return lib, []
    try:
        from . import generated_metrics
        index = generated_metrics.load(path)
        found = index.metrics(index.select(question, option_count('AGERE_S1_GENERATED_POOL', 12), encoder))
    except Exception:  # noqa: BLE001 - generated metrics are an addition; a failure never blocks the authored ones
        return lib, []
    found = {mid: m for mid, m in found.items() if mid not in lib}
    return {**lib, **found}, list(found)


def metric_shortlist(question, lib, n, encoder=None):
    """Governed metrics that may fit the question, best first, at most n: authored match rules, a quality-measure alias, the
    encoder's nearest metrics, then idf word overlap; generated metrics (origin 'generated', already in rank order in lib)
    interleave after the authored ones, at most AGERE_S1_GENERATED_OPTIONS of them. Only metrics whose slots System One can fill."""
    generated = [mid for mid, m in lib.items() if is_generated(m) and fillable(m)]
    if generated:
        a = metric_shortlist(question, {mid: m for mid, m in lib.items() if not is_generated(m)}, n, encoder)
        g = generated[:min(n, option_count('AGERE_S1_GENERATED_OPTIONS', 5))]
        out = []
        for i in range(n):  # authored first on ties: a1, g1, a2, g2, ...; slots one side leaves unused go to the other
            for pool in (a, g):
                if i < len(pool) and pool[i] not in out: out.append(pool[i])
        for pool in (a, g):
            out += [x for x in pool if x not in out]
        return out[:n]
    from . import metrics
    ranked = []
    try:
        ranked += [mid for _, mid in metrics.candidates(question)]
    except Exception:  # noqa: BLE001 - a malformed rule never blocks the shortlist
        pass
    try:
        from . import measures
        alias, _ = measures.match(question)
        if alias: ranked += [mid for mid, m in lib.items() if alias in ((m.get('engine') or {}).get('measures') or []) or mid == f'measure.{alias}']
    except Exception:  # noqa: BLE001
        pass
    if encoder is not None:
        try:
            ranked += [mid for _, mid in metrics._encoder_candidates(question, encoder=encoder, threshold=0.3)]
        except Exception:  # noqa: BLE001
            pass
    ranked += [mid for _, mid in overlap_rank(question, {mid: f"{m['name']} {m['summary']} {mid}" for mid, m in lib.items()})]
    out = []
    for mid in ranked:
        if mid in lib and mid not in out and fillable(lib[mid]): out.append(mid)
    return out[:n]


def engine_shortlist(question, n, encoder=None):
    """Engines that may fit: the learned router's top three (when the stack has its encoder), engines the question names, then
    idf word overlap with each engine's description. Never literal_lookup (always offered separately)."""
    from .capability_registry import PRODUCT
    ranked = []
    if encoder is not None:
        try:
            from . import learned_router
            ranked += [label for label, _ in learned_router.predict(question, encoder)['top'] if label in PRODUCT]
        except Exception:  # noqa: BLE001 - no model for this capability list
            pass
    low = question.lower()
    ranked += [op for op in PRODUCT if re.search(r'\b' + re.escape(op.replace('_', ' ')) + r'\b', low)]
    ranked += [op for _, op in overlap_rank(question, {op: f"{op.replace('_', ' ')} {spec['use']}" for op, spec in PRODUCT.items()})]
    out = []
    for op in ranked:
        if op not in out: out.append(op)
    return out[:n]


def key_of(text, taken):
    key = re.sub(r'[^A-Za-z0-9_]', '_', text)
    while key in taken: key += '_'
    return key


# -- metric: choose, then fill ------------------------------------------------------------------------------------------
TEMPORAL = re.compile(r'\b(?:19|20)\d\d\b|\b(?:last|past|prior|previous|trailing|recent(?:ly)?|since|between|during|ytd|year[- ]to[- ]date|so far|'
                      r'this (?:year|month|quarter)|fiscal|annual(?:ly)?|(?:month|quarter|year)ly|q[1-4]|jan(?:uary)?|feb(?:ruary)?|mar(?:ch)?|'
                      r'apr(?:il)?|june?|july?|aug(?:ust)?|sep(?:t(?:ember)?)?|oct(?:ober)?|nov(?:ember)?|dec(?:ember)?|'
                      r'\d+\s*(?:days?|weeks?|months?|years?))\b', re.I)
PERIOD_OPTIONS = {
    'all_time': 'No time window is stated: use every observed date.',
    'year': 'One calendar year is stated (for example 2027).',
    'ytd': 'This year so far (year to date).',
    'trailing_months': 'A trailing window counted back from today in months, quarters or years (last 6 months, past 12 months).',
    'trailing_days': 'A trailing window counted in days or weeks (last 90 days, past 2 weeks).',
    'other_window': 'A window none of the others can express: a named month or quarter, dates between or since a date, a fiscal '
                    'year, last calendar year, several periods, or a comparison of periods.',
}
_WORDS = {w: i for i, w in enumerate('zero one two three four five six seven eight nine ten eleven twelve'.split())}


def _count(text, unit):
    """The N of 'N <unit>' (digits or a number word up to twelve), else None."""
    m = re.search(r'\b(\d+|' + '|'.join(_WORDS) + r')\s*[- ]?\s*' + unit + r'\b', text)
    if not m: return None
    return int(m.group(1)) if m.group(1).isdigit() else _WORDS[m.group(1)]


def period_value(question, preset):
    """The typed period for a chosen preset, its numbers read from the question; Unfillable when they cannot be."""
    low = question.lower()
    if preset == 'all_time': return {'preset': 'all_time'}
    if preset == 'ytd': return {'preset': 'ytd'}
    if preset == 'year':
        years = sorted(set(re.findall(r'\b((?:19|20)\d\d)\b', low)))
        if len(years) != 1: raise Unfillable(f'the question names {len(years)} years, not one')
        return {'preset': 'year', 'year': int(years[0])}
    if preset == 'trailing_months':
        months = _count(low, r'months?')
        years = _count(low, r'years?')
        if months is None and years is not None: months = years * 12
        if months is None and re.search(r'\b(?:last|past|trailing)\s+quarter\b', low): months = 3
        if months is None and re.search(r'\b(?:past|trailing)\s+(?:year|12 months)\b', low): months = 12
        if not months: raise Unfillable('no month count could be read from the window')
        return {'preset': 'trailing_months', 'months': months}
    if preset == 'trailing_days':
        days = _count(low, r'days?')
        weeks = _count(low, r'weeks?')
        days = days if days is not None else (weeks * 7 if weeks is not None else None)
        if not days: raise Unfillable('no day count could be read from the window')
        return {'preset': 'trailing_days', 'days': days}
    raise Unfillable('the window is not one of the presets')


def fill_period(question, rec):
    if not TEMPORAL.search(question): return {'preset': 'all_time'}  # as the Claude router: no date mentioned = all_time
    prompt = f'Which time window does this question state? Question: {question}'
    got = rec.decide('metric_slot:period', TASKS['slot'], prompt, PERIOD_OPTIONS, metric_accept())
    if not got['accepted']: raise Unfillable(f"System One was unsure about the time window ({got['label']}, p={got['p']:.2f})")
    return period_value(question, got['label'])


def _cue(question, name, values):
    """The question names the slot or one of its values (stem match)."""
    return bool(stems(question) & stems(' '.join([name.replace('_', ' '), *[str(v).replace('_', ' ') for v in values]])))


def fill_enum(question, rec, mid, name, decl, *, multi=False):
    values = [v for v in MR._vocab_values(decl)]
    required = decl.get('required') or multi
    default = decl.get('default')
    breakdown = name == 'group_by'
    cue = bool(re.search(MR.BREAKDOWN, question.lower()) or re.search(r'\b(?:month|quarter|year|week)ly\b', question.lower())) \
        if breakdown else _cue(question, name, values)
    if not cue and not required:
        return default  # the slot default stands; no call
    options = {v: v.replace('_', ' ') + (' (no breakdown: one overall result)' if v == 'none' else '') for v in values}
    if multi: options['multiple'] = 'The question asks for more than one of these.'
    else: options[NOT_STATED] = 'The question does not say; use the default.'
    what = (decl.get('description') or name.replace('_', ' '))[:300]
    prompt = (f'For the governed metric {mid}, which value of "{name}" does this question ask for? {what}\nQuestion: {question}')
    got = rec.decide(f'metric_slot:{name}', TASKS['slot'], prompt, options, metric_accept())
    if not got['accepted']: raise Unfillable(f"System One was unsure about {name} ({got['label']}, p={got['p']:.2f})")
    if got['label'] == 'multiple': raise Unfillable(f'the question asks for several {name} values; a single-valued slot cannot answer it')
    if got['label'] == NOT_STATED:
        if default is None and required: raise Unfillable(f'{name} is required and the question does not state it')
        return default
    return got['label']


def fill_metric(question, as_of, mid, m, rec):
    """-> slots dict for validate_slots, or raises Unfillable."""
    slots, ints = {}, []
    for name, decl in MR.slots_of(m).items():
        t = decl['type']
        if t == 'period': slots[name] = fill_period(question, rec)
        elif t == 'enum': slots[name] = fill_enum(question, rec, mid, name, decl)
        elif t == 'enum_list':
            if decl.get('required') and 'default' not in decl:
                slots[name] = [fill_enum(question, rec, mid, name, decl, multi=True)]
        elif t == 'integer': ints.append(name)
        # booleans keep their default
    slots = {k: v for k, v in slots.items() if v is not None}
    # an integer slot takes the one number the question states that no other slot has used
    decls = MR.slots_of(m)
    if ints:
        base = {**{n: decls[n]['default'] for n in ints if 'default' in decls[n]}, **slots}
        left = MR.unaccounted_numbers(question, m, base, as_of)
        required = [n for n in ints if decls[n].get('required')]
        if len(left) == 1 and float(left[0]).is_integer() and (required or len(ints) == 1):
            slots[(required or ints)[0]] = int(left[0])
        elif required:
            raise Unfillable(f"{', '.join(required)} is required and the question states {len(left)} unplaced numbers")
    return slots


def metric_ask(question, rec, lib, encoder=None):
    """The ask hook for metric_router.route: (question, as_of, tools=, forced=, feedback=) -> (choice, receipt)."""
    index = MR._tool_index(lib)
    shown = {}

    def none(reason):
        return rec.reply({'name': MR.NO_METRIC, 'input': {'reason': reason}}, note=reason)

    def ask(q, as_of, tools=None, forced=None, feedback=None):
        if feedback:  # one System One attempt: the validator's complaint goes to the Claude router, which can repair
            return none('System One slots were rejected by the validator: ' + feedback[:200])
        if forced is None:
            cands = metric_shortlist(question, lib, option_count('AGERE_S1_METRIC_OPTIONS', 10), encoder)
            if not cands: return none('no governed metric resembles the question')
            options, taken = {}, set()
            for mid in cands:
                key = key_of(mid, taken)
                taken.add(key)
                shown[key] = mid
                options[key] = f"{lib[mid]['name']}: {lib[mid]['summary'][:240]}"
            options[NONE_OF] = ('No listed metric answers the whole question as asked: the population, measure, time window or kind of '
                                'output differs, or it is a plain lookup.')
            prompt = ('Which governed metric answers this question about a synthetic healthcare population exactly as asked? Choose a '
                      'metric only when its definition answers the WHOLE question (same counting unit, population, time window and '
                      f'requested output); otherwise choose none_of_these.\nQuestion: {question}')
            got = rec.decide('metric_choice', TASKS['metric'], prompt, options, metric_accept())
            if not got['accepted'] or got['label'] == NONE_OF or got['label'] not in shown:
                return none(f"System One chose {got['label']} (p={got['p']:.2f}, bar {metric_accept():.2f}); the Claude metric router decides")
            return rec.reply({'name': MR.tool_name(shown[got['label']]), 'input': {}})
        mid = index.get(forced)
        if mid is None: return none(f'unknown metric tool {forced}')
        try:
            slots = fill_metric(question, as_of, mid, lib[mid], rec)
        except Unfillable as exc:
            return none(f'{mid}: {exc}')
        return rec.reply({'name': forced, 'input': slots})
    return ask


def route_metric(stack, question, as_of, ledger, *, path=None, directory=None):
    """-> (graph | None, info, recorder). Same contract as metric_router.route; info['system_one'] has the receipts."""
    rec = Recorder(ledger, as_of)
    encoder = getattr(getattr(stack, 'retriever', None), 'encoder', None)
    lib, offered = with_generated(MR.routable(directory), question, getattr(stack, 'path', None) or path, encoder)
    ask = metric_ask(question, rec, lib, encoder)
    graph, info = MR.route(question, as_of, ledger, None, path=path, ask=ask, directory=directory, lib=lib)
    info.update(chosen_by='system_one', system_one=rec.receipt())
    if offered: info['generated_offered'] = offered
    return graph, info, rec


# -- engine: choose, then fill -------------------------------------------------------------------------------------------
_UNIT = r'(?:%|percent|days?|weeks?|months?|years?)'


def _int_props(props, pred):
    return [k for k, v in props.items() if pred(k) and 'integer' in (v.get('type') if isinstance(v.get('type'), list) else [v.get('type')])]


def parse_numbers(question, props):
    """Numbers the question states -> parameters that can hold them. Unfillable when one is left over (a restriction or
    value only the Claude router should place). Years/ages/percentages are never guessed into a parameter."""
    low = question.lower().replace(',', '')
    params, used = {}, set()

    def put(name, value, span):
        if name in props and name not in params:
            params[name] = value
            used.add(span)
            return True
        return False
    for m in re.finditer(r'\btop\s+(\d+)\b|\b(\d+)\s+(?:highest|largest|biggest|most|worst|best|top)\b', low):
        put('top', int(m.group(1) or m.group(2)), m.span())
    for m in re.finditer(r'\b(\d+)\s*[- ]?\s*(days?|weeks?|months?|years?)\b', low):
        n, unit = int(m.group(1)), m.group(2)
        if unit.startswith('year') and not _int_props(props, lambda k: 'year' in k):
            n, unit = n * 12, 'months'
        if unit.startswith('week') and not _int_props(props, lambda k: 'week' in k):
            n, unit = n * 7, 'days'
        word = unit[:-1] if unit.endswith('s') else unit
        cands = _int_props(props, lambda k: word in k and not k.startswith(('min_', 'max_')) and k not in params)
        if len(cands) == 1: put(cands[0], n, m.span())
    left = []
    for m in re.finditer(r'(?<![\w.])\d+(?:\.\d+)?(?![\w])', low):
        if not any(a <= m.start() < b for a, b in used): left.append(m.group(0))
    if left: raise Unfillable('numbers the question states fit no single parameter: ' + ', '.join(left[:4]))
    return params


def fill_engine(question, rec, op):
    from .capability_registry import PRODUCT  # noqa: F401 - op is validated by the caller
    props = OR.tool(op)['input_schema']['properties']['parameters'].get('properties', {})
    params = parse_numbers(question, props)
    asked = 0
    for name, spec in props.items():
        values = spec.get('enum')
        if not values or name in params or asked >= 3: continue
        if not _cue(question, name, values): continue
        asked += 1
        options = {str(v): str(v).replace('_', ' ') for v in values}
        options[NOT_STATED] = 'The question does not say; use the default.'
        what = (spec.get('description') or name.replace('_', ' '))[:300]
        prompt = f'For the analysis "{op}", which value of "{name}" does this question ask for? {what}\nQuestion: {question}'
        got = rec.decide(f'engine_param:{name}', TASKS['param'], prompt, options, engine_accept())
        if not got['accepted']: raise Unfillable(f"System One was unsure about {name} ({got['label']}, p={got['p']:.2f})")
        if got['label'] != NOT_STATED: params[name] = got['label']
    return params


def engine_ask(question, rec, encoder=None):
    """The ask hook for operator_router.route: (question, as_of, op=None, feedback=None) -> (choice, receipt)."""
    from .capability_registry import PRODUCT, LITERAL

    def none(reason):
        return rec.reply({'name': NONE_OF, 'input': {'reason': reason}}, note=reason)

    def ask(q, as_of, op=None, feedback=None):
        if feedback: return none('System One parameters were rejected by the validator: ' + feedback[:200])
        if op is None:
            cands = engine_shortlist(question, option_count('AGERE_S1_ENGINE_OPTIONS', 8), encoder)
            options = {c: OR.brief(PRODUCT[c]['use'], 200) for c in cands}
            options['literal_lookup'] = LITERAL
            options[NONE_OF] = 'None of these: the question needs a different method, or data this warehouse does not hold.'
            prompt = ('Which analysis does this question about a synthetic healthcare population need? Choose an analytic engine only '
                      'when the question needs that method; choose literal_lookup for plain counts, lists, sums, averages, simple '
                      f'breakdowns and shares.\nQuestion: {question}')
            got = rec.decide('engine_choice', TASKS['engine'], prompt, options, engine_accept())
            if not got['accepted'] or got['label'] == NONE_OF:
                return none(f"System One chose {got['label']} (p={got['p']:.2f}, bar {engine_accept():.2f}); the Claude operator router decides")
            return rec.reply({'name': got['label'], 'input': {}})
        try:
            params = fill_engine(question, rec, op)
        except Unfillable as exc:
            return none(f'{op}: {exc}')
        return rec.reply({'name': op, 'input': {'parameters': params}})
    return ask


def route_engine(stack, question, as_of, ledger, validate):
    """-> (graph | None, info, recorder). Same contract as operator_router.route."""
    rec = Recorder(ledger, as_of)
    encoder = getattr(getattr(stack, 'retriever', None), 'encoder', None)
    graph, info = OR.route(question, as_of, ledger, None, validate, encoder=encoder, ask=engine_ask(question, rec, encoder))
    info.update(system_one=rec.receipt())
    if info.get('chosen_by') == 'paid_router': info['chosen_by'] = 'system_one'
    return graph, info, rec
