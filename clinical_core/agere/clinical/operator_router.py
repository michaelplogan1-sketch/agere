"""Any phrasing -> the right analytic engine. A cheap model chooses the tool; the engines do the math.

When the deterministic compiler cannot parse a question, one small-model call picks an operator from
capability_registry.PRODUCT and fills its typed parameters (the same schemas the frontier planner uses).
The plan is validated by the runtime validators before anything executes; one repair attempt is allowed
with the validator's error. Literal data questions are handed to the verified SQL ladder unchanged.
"""
import copy, json, re, time

from .capability_registry import PRODUCT, LITERAL

VERSION = 'operator-router/2'
MODEL = 'claude-haiku-5-5'

SYSTEM = (
    'You route analytics questions about a synthetic healthcare population to exactly one tool. '
    'Choose an analytic tool only when the question needs that method (time-to-event curves, control charts, '
    'program effects, worklists, value-based-care contract math, why/where analyses, forecasts, fair comparisons). '
    'Choose literal_lookup for plain counts, lists, sums, averages, simple breakdowns and shares. '
    'Fill parameters only from what the question says plus the tool defaults; use exact vocabulary from the schemas; '
    'omit parameters the question does not determine. Put population restrictions (conditions, labs, age, sex, payer) '
    'in population using concept/measurement/patient atoms, never inside parameters. '
    'Use not_answerable only when the question needs data that clearly does not exist (death records, race, CPT/RVU, HCC/RAF, surveys). '
    'Choose knowledge for explanation or strategy questions about value-based care itself (how a program works, what a term means, '
    'how companies compare, what the evidence says) that do not ask about this population\'s data. '
    'Choose contract_strategy for decisions about OUR risk contracts (should we switch tracks, which option is best for us, compare offers, '
    'what to negotiate, what the contract is worth, how much downside we can take); one settlement amount under stated terms is vbc. '
    'Choose target_trial for drug A versus drug B comparative effectiveness (new users, active comparator); a single drug or program '
    'start with no comparator is program_effect. Choose line_of_therapy for treatment patterns, lines, switching, sequences and persistence. '
    'Choose trial_feasibility for counts of patients meeting trial inclusion/exclusion criteria, eligibility funnels, sites, diversity and enrolment. '
    'Snapshot date: {as_of}.'
)


def _loose(schema):
    """Model-facing copy: parameters may be partial (runtime validators fill defaults and stay authoritative)."""
    schema = copy.deepcopy(schema)
    def walk(node):
        if isinstance(node, dict):
            if node.get('type') == 'object': node.pop('required', None)
            for v in node.values(): walk(v)
        elif isinstance(node, list):
            for v in node: walk(v)
    walk(schema)
    return schema


def population_schema():
    """Compact population grammar for the router (validated by cohorts.validate after normalisation)."""
    from . import concepts, cohorts
    atom = {'type': 'object', 'properties': {
        'kind': {'type': 'string', 'enum': ['concept', 'measurement', 'patient', 'social_need']},
        'need': {'type': 'string', 'enum': sorted(SOCIAL_NEEDS), 'description': 'social_need atoms only: a positive social-needs screen'},
        'concept': {'type': 'string', 'enum': sorted({*concepts.CONDITIONS, *concepts.ENCOUNTERS, *concepts.MEASUREMENTS}),
                    'description': 'concept: a condition or encounter type; measurement: a lab/vital'},
        'field': {'type': 'string', 'enum': sorted(cohorts.PATIENT_FIELDS - {'patient_id', 'profile_captured_date'}), 'description': 'patient atoms only'},
        'op': {'type': 'string', 'enum': ['eq', 'ne', 'lt', 'le', 'gt', 'ge']},
        'value': {'description': 'number for measurement/age; string for sex/coverage'},
        'aggregate': {'type': 'string', 'enum': ['any', 'latest']},
        'window': {'type': 'object', 'properties': {'start': {'type': 'string'}, 'end': {'type': 'string'}}},
        'role': {'type': 'string', 'enum': ['all', 'any', 'exclude'], 'description': 'all = must match (default); any = at least one of the any-atoms; exclude = must not match'}},
        'required': ['kind']}
    return {'type': 'array', 'items': atom, 'description': 'Who the analysis covers, as predicate atoms; omit for everyone.'}


# Social needs -> the screening-record atoms the compiler uses (every instrument that reports the need).
SOCIAL_NEEDS = {'food_difficulty': [('social_screens', 'food_difficulty_reported', True)],
                'transport_difficulty': [('social_screens', 'transport_difficulty_reported', True), ('social_screens', 'transport', 'unreliable')],
                'financial_hardship': [('social_screens', 'difficulty_paying_for_care_reported', True),
                                       ('financial_screens', 'difficulty_paying_medical_bills', True)]}


def _social_atoms(need):
    return [{'kind': 'record', 'stream': stream, 'conditions': [{'field': field, 'op': 'eq', 'value': value}],
             'window': {'basis': 'all_observed', 'start': '', 'end': ''}} for stream, field, value in SOCIAL_NEEDS.get(need, [])]


def normalize_atom(atom):
    kind = atom.get('kind')
    w = atom.get('window') or {}
    start, end = w.get('start') or '', w.get('end') or ''
    # dated bounds use the source event date; all_observed means "any time" and cannot carry dates
    window = {'basis': 'source_event' if (start or end) else 'all_observed', 'start': start, 'end': end}
    if kind == 'patient':
        return {k: atom.get(k) for k in ('kind', 'field', 'op', 'value')}
    if kind == 'measurement':
        return {'kind': kind, 'concept': atom.get('concept'), 'op': atom.get('op'), 'value': atom.get('value'),
                'aggregate': atom.get('aggregate') or 'any', 'window': window}
    return {'kind': kind, 'concept': atom.get('concept'), 'window': window}


def normalize_spec(spec):
    if isinstance(spec, list):  # router form: atoms carrying a role
        out = {'all': [], 'any': [], 'exclude': []}
        needs = [a for a in spec if isinstance(a, dict) and a.get('kind') == 'social_need']
        for a in spec:
            if not isinstance(a, dict) or a.get('kind') == 'social_need': continue
            out[a.get('role') if a.get('role') in out else 'all'].append(normalize_atom(a))
        for a in needs:
            atoms = _social_atoms(a.get('need'))
            if not atoms: raise ValueError(f"Unknown social need: {a.get('need')}")
            role = a.get('role') if a.get('role') in out else 'all'
            if role == 'exclude': out['exclude'] += atoms  # excluded if any instrument reports the need
            elif role == 'any' or (len(atoms) > 1 and not out['any'] and len(needs) == 1): out['any'] += atoms  # any instrument
            else: out['all'].append(atoms[0])  # several needs required together: primary instrument each
        return out
    return {k: [normalize_atom(a) for a in (spec or {}).get(k) or [] if isinstance(a, dict)] for k in ('all', 'any', 'exclude')}


def tool(op):
    """The full typed tool for one operator (step 2: fill parameters)."""
    from .graph_contracts import parameter_schema
    from .graph_planner import CONTRACTS
    stub = {'cohort_fields': {'record_fields': {}}, 'evidence_catalog': {}}
    population = population_schema()
    parameters = _loose(parameter_schema(op, stub))
    for key, value in parameters.get('properties', {}).items():  # nested population specs use the compact grammar
        if isinstance(value, dict) and set(value.get('properties', {})) == {'all', 'any', 'exclude'}:
            parameters['properties'][key] = population
    properties = {'parameters': parameters}
    if PRODUCT[op]['population'] != 'none': properties['population'] = population
    return {'name': op, 'description': PRODUCT[op]['use'] + ' ' + CONTRACTS[op]['meaning'],
            'input_schema': {'type': 'object', 'properties': properties, 'required': ['parameters']}}


def brief(text, limit=260):
    """The menu line: whole sentences up to ~limit characters (the full description is in the step-2 tool)."""
    if len(text) <= limit: return text
    cut = text[:limit]
    end = max(cut.rfind('. '), cut.rfind('; '))
    return (cut[:end + 1] if end > 80 else cut.rsplit(' ', 1)[0] + '…').strip()


def menu():
    """Step 1: choose the engine from one-line descriptions (stays small as capabilities grow)."""
    result = [{'name': op, 'description': brief(spec['use']), 'input_schema': {'type': 'object', 'properties': {}}}
              for op, spec in PRODUCT.items()]
    result.append({'name': 'literal_lookup', 'description': LITERAL, 'input_schema': {'type': 'object', 'properties': {}}})
    result.append({'name': 'not_answerable', 'description': 'The data needed does not exist in this warehouse.',
                   'input_schema': {'type': 'object', 'properties': {'missing': {'type': 'array', 'items': {'type': 'string'}}},
                                    'required': ['missing']}})
    return result


def tools():
    """Every tool the router can expose (for size checks and docs)."""
    return [*menu(), *[tool(op) for op in PRODUCT]]


def _decode(text):
    """Best-effort parse of a stringified parameters object -> (parameters, siblings)."""
    try:
        value = json.loads(text)
        if isinstance(value, dict): return value, {}
    except ValueError:
        pass
    try:  # '{params}, "population": [...]}' -> wrap it as the object the model meant
        value = json.loads('{"parameters": ' + text)
        if isinstance(value, dict) and isinstance(value.get('parameters'), dict):
            return value.pop('parameters'), value
    except ValueError:
        pass
    try:  # trailing characters after one complete object
        value, _ = json.JSONDecoder().raw_decode(text.strip())
        if isinstance(value, dict): return value, {}
    except ValueError:
        pass
    raise ValueError('parameters must be an object')


def to_graph(question, choice):
    op, args = choice['name'], choice.get('input') or {}
    if op not in PRODUCT: raise ValueError(f'Not a routable operator: {op}')
    parameters = args.get('parameters') or {}
    if isinstance(parameters, str):  # heal: a JSON-encoded object, possibly with population appended or a stray brace
        parameters, extra = _decode(parameters)
        if extra.get('population') and not args.get('population'): args = {**args, 'population': extra['population']}
    if not isinstance(parameters, dict): raise ValueError('parameters must be an object')
    parameters = dict(parameters)
    if 'population' in parameters and 'population' not in (PRODUCT[op].get('parameter_names') or ()):
        args = {**args, 'population': args.get('population') or parameters.pop('population')}  # heal: population nested in parameters
    parameters = {k: normalize_spec(v) if (isinstance(v, dict) and v and set(v) <= {'all', 'any', 'exclude'}) or
                  (isinstance(v, list) and v and all(isinstance(a, dict) and 'kind' in a for a in v)) else v
                  for k, v in parameters.items()}
    spec = args.get('population')
    nodes = []
    if PRODUCT[op]['population'] == 'required' or (PRODUCT[op]['population'] == 'optional' and spec):
        spec = normalize_spec(spec)
        nodes.append({'id': 'population', 'operation': 'cohort', 'inputs': [], 'parameters': {'specification': spec},
                      'purpose': 'Population named in the question'})
    nodes.append({'id': 'answer', 'operation': op, 'inputs': ['population'] if nodes else [], 'parameters': parameters,
                  'purpose': f'{op} chosen by the operator router'})
    return {'interpretation': question, 'coverage': 'supported_observation', 'unmet_requirements': [], 'nodes': nodes}


def _ask(question, as_of, ledger, out, op=None, feedback=None, model=MODEL):
    """op None: choose an engine from the menu. op given: fill that engine's typed parameters."""
    from .context_student import send
    content = question if not feedback else (f'{question}\n\nYour previous tool call was rejected by the validator: {feedback[:600]}\n'
                                             'Call the tool again with corrected parameters.')
    body = {'model': model, 'max_tokens': 1200, 'system': SYSTEM.format(as_of=as_of),
            'tools': menu() if op is None else [tool(op)],
            'tool_choice': {'type': 'any', 'disable_parallel_tool_use': True} if op is None else
                           {'type': 'tool', 'name': op, 'disable_parallel_tool_use': True},
            'messages': [{'role': 'user', 'content': content}]}
    def parse(raw):
        calls = [b for b in raw.get('content', []) if b.get('type') == 'tool_use']
        if len(calls) != 1: raise ValueError('Exactly one tool call required')
        return {'name': calls[0]['name'], 'input': calls[0].get('input') or {}}
    choice, receipt = send(body, 'claude', ledger, out, parse=parse)
    usd = 0.0 if receipt.get('cache_hit') else ((receipt.get('cost_estimate') or {}).get('estimated_usd') or 0.0)
    return choice, {'model': model, 'api_usd': usd, 'cache_hit': bool(receipt.get('cache_hit')), 'step': 'choose' if op is None else 'fill'}


# Requires-literal rules: a chosen engine whose method the question never asks for is replaced by the SQL ladder.
# Each rule: (engines it overrides, the literal ask, words that DO ask for that engine's method).
MONEY_TOTAL = (r"\bhow much (?:did|do|does|have|has|was|were)\b|\bwhat (?:did|do|does) (?:\w+ ){0,3}pay\b|\btotal (?:net )?(?:paid|payments?|allowed|billed|"
               r"payer paid|dollars|amount)\b|\bsum of\b|\bnet of (?:reversals|take[- ]?backs|recoupments|clawbacks)\b|\bnet (?:payer[- ]?)?paid\b")
REVIEW_INTENT = (r"\b(?:leak\w*|under[- ]?pa\w*|short[- ]?pa\w*|losing|lost|left on the table|write[- ]?offs?|at stake|patterns?|pareto|reasons?|why|"
                 r"flags?|review|suspicious|fraud\w*|integrity|outliers?|unusual\w*|peers?|duplicates?|rework|resubmi\w*|recover\w*|which (?:providers?|payers?|claims?))\b")
SINGLE_RATE = (r"\b(?:share|percent(?:age)?|proportion|fraction|%) of (?:(?:our|the|all|\w+) ){0,3}(?:discharges|admissions|stays|visits|encounters|claims)\b|"
               r"\b(?:readmission|return|revisit) rate\b|\bhow many (?:(?:of )?(?:our|the|those|these|\w+) ){0,3}(?:discharges|admissions|stays|visits|encounters)\b")
TIME_TO_EVENT = (r"\b(?:how (?:quickly|soon|long|fast)|time (?:to|until)|days? (?:to|until)|median (?:days?|time)|kaplan|meier|survival|"
                 r"cumulative incidence|curves?|hazards?|cox|log[- ]rank|censor\w*|by day \d+|over time|when do)\b")
LITERAL_RULES = [
    ({'claims_review', 'monitor', 'market'}, MONEY_TOTAL, REVIEW_INTENT, 'a plain dollar total, not a review of leakage or flags'),
    ({'survival'}, SINGLE_RATE, TIME_TO_EVENT, 'a single observed share or count, not a time-to-event curve'),
]


def literal_required(question, op):
    """Reason the question must be answered literally although `op` was chosen, else None."""
    low = ' '.join(str(question).lower().split())
    for engines, ask, method, reason in LITERAL_RULES:
        if op in engines and re.search(ask, low) and not re.search(method, low):
            return reason
    return None


def route(question, as_of, ledger, out, validate, encoder=None):
    """-> (graph | None, info). info['decision'] is an operator, 'literal_lookup' or 'not_answerable'.

    Pre-step: with the stack's local encoder, the learned router (learned_router.py) may make the choose decision at
    $0 when its calibrated probability clears its threshold; otherwise the paid choose call runs. The fill step is
    always the paid typed call. If a locally chosen engine cannot be filled validly, the paid choose step decides."""
    start = time.perf_counter()
    from . import learned_router
    learned, learned_info = learned_router.decide(question, encoder) if encoder is not None else (None, None)
    info = {'router': VERSION, 'calls': []}
    if learned_info: info['learned_router'] = learned_info
    picked = {'name': learned, 'input': {}} if learned is not None else None
    while True:
        if picked is None:
            picked, call = _ask(question, as_of, ledger, out)
            info['calls'].append(call)
            info['chosen_by'] = 'paid_router'
        else:
            info.setdefault('chosen_by', 'learned_router')
        info['decision'] = picked['name']
        forced = literal_required(question, picked['name'])
        if forced:  # deterministic override of the learned or paid choice: the SQL ladder answers it exactly
            info['overridden'] = {'chosen': picked['name'], 'reason': forced}
            picked = {'name': 'literal_lookup', 'input': {}}
            info['decision'] = 'literal_lookup'
        if picked['name'] not in PRODUCT:
            info['missing'] = (picked['input'] or {}).get('missing')
            info['seconds'] = round(time.perf_counter() - start, 2)
            return None, info
        op, feedback = picked['name'], None
        for attempt in range(2):
            choice, call = _ask(question, as_of, ledger, out, op=op, feedback=feedback)
            info['calls'].append(call)
            try:
                graph = validate(to_graph(question, choice), as_of)
                info.update(choice=choice, seconds=round(time.perf_counter() - start, 2))
                return graph, info
            except (ValueError, KeyError, TypeError) as exc:
                info.setdefault('rejected', []).append({'choice': choice, 'error': str(exc)[:400]})
                feedback = f'{type(exc).__name__}: {exc}'
        if info['chosen_by'] != 'learned_router':
            break
        # the local choice could not be filled: let the paid router choose (once) unless it picks the same engine
        info['chosen_by'] = 'paid_router_after_learned_fill_failed'
        picked, call = _ask(question, as_of, ledger, out)
        info['calls'].append(call)
        if picked['name'] == op:
            info['decision'] = op
            break
    # The engine is right but the question lacks inputs only the user can supply (e.g. contract terms): say so, don't guess.
    errors = [r['error'] for r in info.get('rejected', [])]
    if errors and all(re.search(r'needs supplied|supplied by the user|user[- ]supplied', e) for e in errors):
        info['requires_input'] = errors[-1]
    info['seconds'] = round(time.perf_counter() - start, 2)
    return None, info


def cost(info):
    return sum(c.get('api_usd') or 0 for c in info.get('calls', []))
