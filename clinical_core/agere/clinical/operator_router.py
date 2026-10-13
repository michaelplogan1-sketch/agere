"""Any phrasing -> the right analytic engine. A cheap model chooses the tool; the engines do the math.

When the deterministic compiler cannot parse a question, one small-model call picks an operator from
capability_registry.PRODUCT and fills its typed parameters (the same schemas the frontier planner uses).
The plan is validated by the runtime validators before anything executes; one repair attempt is allowed
with the validator's error. Literal data questions are handed to the verified SQL ladder unchanged.
"""
import copy, json, time

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
        'kind': {'type': 'string', 'enum': ['concept', 'measurement', 'patient']},
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


def normalize_atom(atom):
    kind = atom.get('kind')
    window = {'basis': 'all_observed', 'start': (atom.get('window') or {}).get('start', ''), 'end': (atom.get('window') or {}).get('end', '')}
    if kind == 'patient':
        return {k: atom.get(k) for k in ('kind', 'field', 'op', 'value')}
    if kind == 'measurement':
        return {'kind': kind, 'concept': atom.get('concept'), 'op': atom.get('op'), 'value': atom.get('value'),
                'aggregate': atom.get('aggregate') or 'any', 'window': window}
    return {'kind': kind, 'concept': atom.get('concept'), 'window': window}


def normalize_spec(spec):
    if isinstance(spec, list):  # router form: atoms carrying a role
        out = {'all': [], 'any': [], 'exclude': []}
        for a in spec:
            if isinstance(a, dict): out[a.get('role') if a.get('role') in out else 'all'].append(normalize_atom(a))
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


def menu():
    """Step 1: choose the engine from one-line descriptions (stays small as capabilities grow)."""
    result = [{'name': op, 'description': spec['use'], 'input_schema': {'type': 'object', 'properties': {}}}
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


def route(question, as_of, ledger, out, validate):
    """-> (graph | None, info). info['decision'] is an operator, 'literal_lookup' or 'not_answerable'."""
    start = time.perf_counter()
    picked, call = _ask(question, as_of, ledger, out)
    info = {'router': VERSION, 'calls': [call], 'decision': picked['name']}
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
    info['seconds'] = round(time.perf_counter() - start, 2)
    return None, info


def cost(info):
    return sum(c.get('api_usd') or 0 for c in info.get('calls', []))
