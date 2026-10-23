"""Metric-first hosted rung: the model chooses a GOVERNED metric and fills its typed slots; deterministic code computes.

Free-form SQL re-derives definitions per question, and correlated models make the same mistakes together (the hosted
ladder was right on 60% of 189 benchmark questions). Here one small-model tool call (same two-step pattern as
operator_router: choose from a compact menu, then fill one typed schema) picks a metric from the metric layer
(metrics.py: config/agere/metrics/*.json, including the parameterised families in metric_families.py and one
measure-backed metric per measure) and fills its slots. The slot schema is DERIVED from the metric definition: declared
slots (population atoms, visit classes, thresholds, group-by, ...) or, for a templated metric, its period when the time
anchor is a period. Slots are validated (types, enums, ranges, every number in the question accounted for) and the
metric's template / builder / measure engine computes the answer. Answers state "computed as metric <id>@<version>".

  no fitting metric        -> the model says no_metric; the question continues to operator_router / free-form SQL
  two metrics fit          -> both are filled and computed; differing results become two readings with one clarifying
                              question (verification_v2's readings structure); equal results are one answer
  invalid slots            -> one repair with the validator's message, then no_metric (never a guessed number)

acceptance() is the stricter policy for the free-form SQL rung that follows: an answer counts as verified only with
verification v2's independent agreement or a lint-clean governed-metric match; otherwise it is 'low_confidence'
(finalize_answer keeps the status and value but sets confidence 'low' with the reason, or withholds the number under
AGERE_FREEFORM_POLICY=abstain). Confidence is not completeness: status 'partial' is reserved for incomplete answers.
Synthetic data only. Tests inject the model call (ask=); nothing here spends money without a ledger.
"""
from __future__ import annotations

import copy, json, os, re, time

VERSION = 'metric-router/1'
MODEL = 'claude-haiku-5-5'
NO_METRIC = 'no_metric'
AMBIGUOUS = 'ambiguous_metrics'
SYSTEM = (
    'You answer analytics questions about a synthetic healthcare population ONLY by choosing a governed metric and filling its typed '
    'slots; deterministic code computes the number. Choose a metric only when its definition answers the WHOLE question as asked '
    '(same counting unit, population, dates, outputs). If none does, call no_metric with the reason - never force a near fit. '
    'If two metrics are equally defensible readings of the question (e.g. different date anchors or populations), call '
    'ambiguous_metrics with both ids and the difference. When filling slots use only what the question says plus the slot defaults; '
    'every number in the question must land in a slot. Periods: "last/past 12 months" = trailing_months 12; "this year"/"so far" = ytd; '
    '"in 2028" = year 2028; no date mentioned = all_time. Snapshot date: {as_of}. '
    'Date anchors: keep the clock the question names - "adjudicated in" (first adjudication date), "first paid in" (paid '
    'adjudication date, not cash), "cash received in" (cash date), "reversed in" (reversal event date), "discharged in" '
    '(discharge date); otherwise the metric default (service/admission/event date). Set the anchor slot when a metric has one.'
)


# Plain count questions vs measure questions. Shared word lists (local_compiler._plain_count_question uses the same rule):
# 'How many hypertensive patients had controlled BP ...' asks for a cohort count, never a measure RATE; a quality measure
# answers only when the question names a measure, a rate/percentage, compliance/performance, gaps or adherence.
MEASURE_ASKED = (r'\b(?:rates?|percent(?:age)?s?|%|share|proportion|fraction|compliance|compliant|non[- ]?compliant|performance|score|scores|'
                 r'measures?|hedis|stars?|star ratings?|ecqm|eqcm|quality|numerator|denominator|gaps?|due|overdue|missing|adherence|adherent|pdc|'
                 r'how (?:are|is|did|do) we (?:doing|perform))\b')
PLAIN_COUNT = r"^(?:how many|what (?:is|was) the (?:number|count) of|count (?:the |our )?|number of)\b"


# "by payer", "for each practice", "broken down by month", "through which channel": the answer is a table of groups.
BREAKDOWN = (r'\b(?:by|for each|each|per|across|broken down by|break(?:s|ing)? (?:it |this |that )?(?:down|out) by|split by|grouped by)\s+'
             r'(?:the\s+|our\s+|their\s+|its\s+)?(?:payers?|payors?|plans?|insurers?|practices?|clinics?|providers?|pcps?|clinicians?|physicians?|'
             r'hospitals?|facilit(?:y|ies)|sites?|locations?|months?|quarters?|weeks?|age(?: band| group)?s?|sex|gender|race|ethnicit(?:y|ies)|'
             r'languages?|channels?|departments?|specialt(?:y|ies)|regions?|count(?:y|ies)|lines? of business|products?|programs?|measures?)\b|'
             r'\b(?:through|via|by|in) which\b|\bbreak(?:s|ing)? (?:it |this |that )?(?:down|out)\b')


def plain_count_question(question):
    """True for 'how many ...' / 'number of ...' questions that name no measure, rate, compliance or gap wording."""
    low = (question or '').lower().strip()
    # short framing before the question ('quick one - how many ...', 'so, how many ...') is not part of it
    low = re.sub(r'^[^?]{0,40}?(?:[-:,]|\bso|\bok(?:ay)?)\s+(?=how many\b|number of\b|count\b|what (?:is|was) the (?:number|count)\b)', '', low)
    return bool(re.search(PLAIN_COUNT, low)) and not re.search(MEASURE_ASKED, low)


# ------------------------------------------------------------------------------------------------ library + schema
def library(directory=None):
    from . import metrics
    return metrics.library(directory)


def kind(m):
    if m.get('builder'): return 'builder'
    if m.get('sql_template'): return 'template'
    if (m.get('engine') or {}).get('measures'): return 'measure'
    return None


def routable(directory=None):
    return {mid: m for mid, m in library(directory).items() if kind(m)}


def tool_name(mid):
    return ('metric_' + re.sub(r'[^A-Za-z0-9_-]', '__', mid))[:64]


def _tool_index(lib):
    return {tool_name(mid): mid for mid in lib}


PERIOD_SCHEMA = {'type': 'object', 'properties': {
    'preset': {'type': 'string', 'enum': ['all_time', 'year', 'ytd', 'trailing_months', 'trailing_days', 'before_days', 'custom']},
    'year': {'type': 'integer'}, 'months': {'type': 'integer'}, 'days': {'type': 'integer'},
    'start': {'type': 'string', 'description': 'ISO date (custom)'}, 'end': {'type': 'string', 'description': 'ISO date (custom)'}},
    'required': ['preset']}


def atom_schema():
    from . import metric_families as mf
    return {'type': 'object', 'properties': {
        'kind': {'type': 'string', 'enum': list(mf.ATOM_KINDS)},
        'role': {'type': 'string', 'enum': list(mf.ROLES), 'description': 'all (default) | any | exclude'},
        'concepts': {'type': 'array', 'items': {'type': 'string', 'enum': mf.condition_concepts()}, 'description': 'condition atoms'},
        'concept': {'type': 'string', 'enum': mf.lab_concepts(), 'description': 'lab atoms'},
        'op': {'type': 'string', 'enum': ['exists', 'lt', 'le', 'gt', 'ge', 'eq']},
        'value': {'type': 'number'},
        'aggregate': {'type': 'string', 'enum': ['any', 'latest'], 'description': 'lab: any result vs the most recent dated result'},
        'systolic_below': {'type': 'number'}, 'diastolic_below': {'type': 'number'},
        'field': {'type': 'string', 'enum': ['age', 'sex', 'coverage_type'], 'description': 'demographic atoms'},
        'min': {'type': 'number'}, 'max': {'type': 'number'},
        'values': {'type': 'array', 'items': {'type': 'string'}, 'description': 'sex F/M; coverage_type commercial/medicaid/medicare/uninsured'},
        'groups': {'type': 'array', 'items': {'type': 'string', 'enum': mf.drug_groups()}, 'description': 'medication atoms'},
        'ingredients': {'type': 'array', 'items': {'type': 'string'}, 'description': 'medication: lower-case RxNorm ingredient names when no group fits (e.g. furosemide)'},
        'evidence': {'type': 'string', 'enum': ['dispensed', 'any_record'], 'description': 'medication: picked up vs on record anywhere'},
        'dated_only': {'type': 'boolean', 'description': 'lab: only results with a date count'},
        'classes': {'type': 'array', 'items': {'type': 'string', 'enum': mf.visit_classes()}, 'description': 'visit atoms'},
        'min_count': {'type': 'integer'},
        'needs': {'type': 'array', 'items': {'type': 'string', 'enum': mf.social_needs()}, 'description': 'social_need atoms'},
        'screened_only': {'type': 'boolean', 'description': 'social_need: screened at all (positive or not)'},
        'status': {'type': 'string', 'enum': ['no_show', 'kept', 'cancelled'], 'description': 'appointment atoms'},
        'period': PERIOD_SCHEMA}, 'required': ['kind']}


def slots_of(m):
    """Declared slots, or the slots a templated / measure-backed metric's definition implies."""
    if m.get('slots'): return m['slots']
    k = kind(m)
    if k == 'measure':
        from .measures import GROUP_BY
        return {'period': {'type': 'period', 'description': 'Measurement period (default: the 12 months ending at the snapshot date).'},
                'group_by': {'type': 'enum', 'values': list(GROUP_BY), 'default': 'none'}}
    if k == 'template' and m['time_anchor']['mode'] == 'period':
        out = {'period': {'type': 'period', 'description': f"Period on {m['time_anchor']['field']} (default: the metric's "
                                                           f"{m['time_anchor'].get('default', 'all')} window)."}}
        av = anchor_variants(m)
        if av:
            out['anchor'] = {'type': 'enum', 'values': ['default', *av], 'default': 'default',
                             'description': ('Which date the period applies to, as the question names it. default = '
                                             f"{m['time_anchor']['field']}; " +
                                             '; '.join(f"{n} = {(v.get('time_anchor') or {}).get('field') or v['anchor']}: {v['summary'][:160]}"
                                                       for n, v in av.items()))[:1200]}
        return out
    return {}


def anchor_variants(m):
    """Template variants that move the metric's clock (variant 'anchor' + its own sql_template), e.g. claims 'first paid in'
    (paid adjudication date), 'adjudicated in', 'reversed in', stays 'discharged in'."""
    return {n: v for n, v in (m.get('variants') or {}).items() if v.get('anchor') and v.get('sql_template')}


def chosen_variant(m, slots, question=''):
    """The template variant to compute: a variant the question's words name (deterministic, wins), else the anchor slot.
    Only variants carrying their own sql_template are computable; others change meaning and stay unrouted."""
    from . import metrics
    name, v = metrics._variant(m, question or '')
    if name and v.get('sql_template'): return name
    a = (slots or {}).get('anchor')
    if a and a != 'default' and a in anchor_variants(m): return a
    return None


def _vocab_values(decl):
    from . import metric_families as mf
    voc = decl.get('vocabulary')
    if voc: return sorted(mf.vocabulary()[voc])
    return list(decl.get('values') or [])


def slot_json_schema(decl):
    t = decl['type']
    desc = {'description': decl['description']} if decl.get('description') else {}
    if t == 'enum': return {'type': 'string', 'enum': _vocab_values(decl), **desc}
    if t == 'enum_list': return {'type': 'array', 'items': {'type': 'string', 'enum': _vocab_values(decl)}, **desc}
    if t in ('integer', 'number'):
        return {'type': t, **({'minimum': decl['min']} if 'min' in decl else {}), **({'maximum': decl['max']} if 'max' in decl else {}), **desc}
    if t == 'boolean': return {'type': 'boolean', **desc}
    if t == 'period': return {**copy.deepcopy(PERIOD_SCHEMA), **desc}
    if t == 'population': return {'type': 'array', 'items': atom_schema(), **desc}
    raise ValueError(f'unknown slot type {t}')


def input_schema(m):
    slots = slots_of(m)
    return {'type': 'object', 'properties': {k: slot_json_schema(v) for k, v in slots.items()},
            'required': [k for k, v in slots.items() if v.get('required')]}


def _definition_line(m):
    return f"{m['name']}: {m['summary']}"


def fill_tool(mid, m):
    return {'name': tool_name(mid), 'description': (f"Governed metric {mid}@v{m['version']}. " + _definition_line(m))[:1800],
            'input_schema': input_schema(m)}


def menu(lib=None):
    """Step 1: choose a metric from one-line definitions (or say no metric fits / two fit)."""
    lib = lib or routable()
    tools = [{'name': tool_name(mid), 'description': _definition_line(m)[:260], 'input_schema': {'type': 'object', 'properties': {}}}
             for mid, m in sorted(lib.items())]
    tools.append({'name': NO_METRIC, 'description': 'No governed metric answers the whole question as asked.',
                  'input_schema': {'type': 'object', 'properties': {'reason': {'type': 'string'}}, 'required': ['reason']}})
    tools.append({'name': AMBIGUOUS, 'description': 'Two governed metrics are equally defensible readings of the question.',
                  'input_schema': {'type': 'object', 'properties': {
                      'metrics': {'type': 'array', 'items': {'type': 'string', 'enum': [tool_name(x) for x in sorted(lib)]}},
                      'difference': {'type': 'string', 'description': 'What differs between the two readings (date anchor, population, ...)'}},
                      'required': ['metrics', 'difference']}})
    return tools


# ------------------------------------------------------------------------------------------------ validation
def _check_type(name, decl, value):
    t = decl['type']
    if t == 'enum':
        if value not in _vocab_values(decl): raise ValueError(f'{name} must be one of {_vocab_values(decl)}')
    elif t == 'enum_list':
        if not isinstance(value, list) or not value: raise ValueError(f'{name} must be a non-empty list')
        bad = [v for v in value if v not in _vocab_values(decl)]
        if bad: raise ValueError(f'{name}: {bad} not in {_vocab_values(decl)}')
    elif t == 'integer':
        if isinstance(value, bool) or not isinstance(value, int): raise ValueError(f'{name} must be an integer')
    elif t == 'number':
        if isinstance(value, bool) or not isinstance(value, (int, float)): raise ValueError(f'{name} must be a number')
    elif t == 'boolean':
        if not isinstance(value, bool): raise ValueError(f'{name} must be true or false')
    elif t == 'period':
        if not isinstance(value, dict): raise ValueError(f'{name} must be an object with a preset')
    elif t == 'population':
        if not isinstance(value, list) or not all(isinstance(a, dict) for a in value): raise ValueError(f'{name} must be a list of atoms')
    if t in ('integer', 'number'):
        if 'min' in decl and value < decl['min']: raise ValueError(f'{name} must be >= {decl["min"]}')
        if 'max' in decl and value > decl['max']: raise ValueError(f'{name} must be <= {decl["max"]}')


def validate_slots(m, slots):
    """Typed slot values with defaults filled (raises ValueError naming the problem)."""
    if slots is None: slots = {}
    if isinstance(slots, str):
        try: slots = json.loads(slots)
        except ValueError as exc: raise ValueError('slots must be a JSON object') from exc
    if not isinstance(slots, dict): raise ValueError('slots must be an object')
    decls = slots_of(m)
    unknown = sorted(set(slots) - set(decls))
    if unknown: raise ValueError(f'unknown slots {unknown}; this metric takes {sorted(decls)}')
    out = {}
    for name, decl in decls.items():
        if name in slots and slots[name] is not None:
            _check_type(name, decl, slots[name]); out[name] = slots[name]
        elif decl.get('required'): raise ValueError(f'slot {name} is required')
        elif 'default' in decl: out[name] = copy.deepcopy(decl['default'])
    return out


_NUM = re.compile(r'(?<![A-Za-z0-9.])\d+(?:\.\d+)?(?![A-Za-z0-9])')


def _numbers(text):
    text = re.sub(r'(?<=\d),(?=\d{3}\b)', '', text or '')
    return {float(x) for x in _NUM.findall(text)}


def unaccounted_numbers(question, m, slots, as_of, extra_text=''):
    """Numbers the question states that land in no slot, the period bounds or the metric's own definition."""
    from . import metric_families as mf
    have = _numbers(json.dumps(slots)) | _numbers(as_of) | _numbers(' '.join(str(as_of).split('-')))
    for v in slots.values():
        if isinstance(v, dict) and 'preset' in v:
            try: have |= {float(x) for b in mf.period_bounds(v, as_of) if b for x in re.findall(r'\d+', b)}
            except ValueError: pass
    for a in (slots.get('population') or []) + (slots.get('numerator') or []):
        if isinstance(a, dict) and isinstance(a.get('period'), dict):
            try: have |= {float(x) for b in mf.period_bounds(a['period'], as_of) if b for x in re.findall(r'\d+', b)}
            except ValueError: pass
    text = ' '.join(str(m.get(k) or '') for k in ('name', 'summary')) + ' ' + json.dumps(m.get('population')) + ' ' + \
        json.dumps(m.get('denominator')) + ' ' + json.dumps(m.get('numerator')) + ' ' + extra_text
    have |= _numbers(text)
    have |= {1000.0} if re.search(r'per\s*1,?000|/\s*1,?000', question or '', re.I) and 'per_1000' in json.dumps(slots) + json.dumps(m) else set()
    return sorted(n for n in _numbers(question) if n not in have)


# A question that subtracts a component ("payments minus refunds", "net of write-offs", "after deducting adjustments") asks
# for a quantity the metric must define; a metric whose definition never mentions that component answers a different question.
_SUBTRACT = re.compile(r"\b(?:minus|less|net of|after (?:subtracting|deducting|removing|netting out)|subtracting|deducting|netting out)\s+"
                       r"(?:the\s+|any\s+|all\s+|their\s+|our\s+)?([a-z][a-z \-]{2,60}?)"
                       r"(?=\s+(?:issued|received|paid|made|posted|during|in|for|from|on|of|by|and|that|which|to)\b|[,.;?)]|$)", re.I)


def unaccounted_components(question, m):
    """Components the question subtracts that the metric's definition never names (-> ['patient refunds'])."""
    from .answer_guard import terms
    text = json.dumps({k: m.get(k) for k in ('id', 'name', 'summary', 'numerator', 'denominator', 'population', 'event',
                                            'value_sets', 'sql_template', 'slots', 'engine')}, default=str).lower()
    have = set(terms(text.replace('\\n', ' ')))
    out = []
    for hit in _SUBTRACT.finditer(question or ''):
        words = [t for t in terms(hit.group(1)) if not re.fullmatch(r'\d+', t)]
        if words and not any(t in have for t in words):
            out.append(hit.group(1).strip())
    return out


def unaccounted_clinical_scope(question, m):
    """A fixed metric must not silently replace a broader clinical question.

    These are recognizable requirements, not definitions of clinical eligibility.
    Builders with explicit slots are checked by their own contracts.
    """
    if kind(m) != 'template':
        return []
    definition = json.dumps({k: m.get(k) for k in ('id', 'name', 'summary', 'sql_template')}).lower()
    requirements = (
        ('GDMT regimen assessment', r'\bgdmt\b|guideline.directed medical therapy', r'\bgdmt\b|guideline.directed|missing_pillars'),
        ('ejection fraction', r'\b(?:reduced ef|hfref|ejection fraction)\b', r'ejection_fraction|ejection fraction|hfref|reduced ef'),
        ('dose assessment', r'\b(?:max(?:imum)?|target|tolerated) dose\b', r'\bdose\b|dose_'),
    )
    return [label for label, wanted, provided in requirements
            if re.search(wanted, question or '', re.I) and not re.search(provided, definition, re.I)]


# ------------------------------------------------------------------------------------------------ compute
def compile_metric(mid, m, slots, as_of, question=''):
    """Validated slots -> an executable node: {'operation': 'sql', 'parameters': {query, returns}} or a measure-engine node."""
    from . import metrics, metric_families as mf
    k = kind(m)
    if k == 'builder':
        params = mf.build(m, slots, as_of)
        return {'operation': 'sql', 'parameters': params}
    if k == 'template':
        # The clock the question names (anchor variant) replaces the template; the period then applies to that clock.
        vname = chosen_variant(m, slots, question)
        if vname: m = metrics.apply_variant(m, vname)
        # A filled period slot is authoritative: all_time means every observed date up to the snapshot, never the
        # metric's default window (the computed-as statement would otherwise say all_time over a year-to-date number).
        period = None
        if slots.get('period'):
            start, end = mf.period_bounds(slots['period'], as_of)
            period = (start or '1900-01-01', end or as_of)
        params = metrics.parameters(m, question if period is None else '', as_of, period)
        sql = metrics.render_sql(m, params)
        returns = {'count': 'count', 'value': 'value', 'patient_set': 'patient_set', 'table': 'table'}[m['output']['returns']]
        node = {'operation': 'sql', 'parameters': {'query': sql, 'returns': returns}, 'period': params}
        if vname: node.update(variant=vname, definition=m['summary'], anchor=m['time_anchor']['field'])
        return node
    if k == 'measure':
        start, end = mf.period_bounds(slots.get('period'), as_of) if slots.get('period') else (None, None)
        if not start or not end:
            from .measures import default_period
            s0, e0 = default_period(as_of)
            start, end = start or s0, end or e0
        return {'operation': 'vbc', 'parameters': {'metric': 'quality', 'group_by': slots.get('group_by') or 'none',
                                                   'period': {'start': start, 'end': end}, 'measure': m['engine']['measures'],
                                                   'output': 'rates', 'attribution': 'prospective', 'terms': {}, 'program': ''}}
    raise ValueError(f'metric {mid} has no template, builder or measure engine')


def computed_as(mid, m, slots, node=None):
    shown = {k: v for k, v in slots.items() if v not in (None, [], {}, 'none', 'default')}
    variant = (node or {}).get('variant')
    summary = (node or {}).get('definition') or m['summary']
    return (f"computed as metric {mid}@v{m['version']}" + (f" ({variant} variant, anchored on {node.get('anchor')})" if variant else '')
            + f": {summary[:400]}" + (f" Slots: {json.dumps(shown, sort_keys=True)[:600]}" if shown else ''))


def to_graph(question, mid, m, node, slots):
    statement = computed_as(mid, m, slots, node)
    return {'interpretation': statement, 'coverage': 'supported_observation', 'unmet_requirements': [],
            'nodes': [{'id': 'answer', 'operation': node['operation'], 'inputs': [], 'parameters': node['parameters'],
                       'purpose': f"Governed metric {mid}@v{m['version']}"}]}


def resolution(mid, m, slots, node):
    return {'metric': mid, 'version': m['version'], 'slots': slots, 'operation': node['operation'],
            'query': (node['parameters'] or {}).get('query'), 'returns': (node['parameters'] or {}).get('returns'),
            'statement': computed_as(mid, m, slots, node), 'definition': node.get('definition') or m['summary'], 'layer': VERSION,
            **({'origin': m['origin'], 'certification': m.get('certification')} if m.get('origin') else {}),
            **({'variant': node['variant'], 'anchor': node.get('anchor')} if node.get('variant') else {})}


def execute(path, node):
    """Read-only execution of a SQL node (for readings comparison); measure nodes are not compared here."""
    if node['operation'] != 'sql': return None
    from . import sql_query
    return sql_query.execute(path, node['parameters'])


# ------------------------------------------------------------------------------------------------ model calls
def _ask(question, as_of, ledger, out, tools, forced=None, feedback=None, model=MODEL):
    from .context_student import send
    content = question if not feedback else (f'{question}\n\nYour previous tool call was rejected by the validator: {feedback[:700]}\n'
                                             'Call the tool again with corrected slots, or call no_metric if no metric fits.')
    body = {'model': model, 'max_tokens': 1500, 'system': SYSTEM.format(as_of=as_of), 'tools': tools,
            'tool_choice': {'type': 'any', 'disable_parallel_tool_use': True} if forced is None else
                           {'type': 'tool', 'name': forced, 'disable_parallel_tool_use': True},
            'messages': [{'role': 'user', 'content': content}]}

    def parse(raw):
        calls = [b for b in raw.get('content', []) if b.get('type') == 'tool_use']
        if len(calls) != 1: raise ValueError('Exactly one tool call required')
        return {'name': calls[0]['name'], 'input': calls[0].get('input') or {}}
    choice, receipt = send(body, 'claude', ledger, out, parse=parse)
    usd = 0.0 if receipt.get('cache_hit') else ((receipt.get('cost_estimate') or {}).get('estimated_usd') or 0.0)
    return choice, {'model': model, 'api_usd': usd, 'cache_hit': bool(receipt.get('cache_hit'))}


def _fill(question, as_of, mid, m, call, info):
    """Fill + validate one metric (one repair). -> (slots, node) or raises ValueError."""
    feedback = None
    for attempt in range(2):
        choice, receipt = call(tools=[fill_tool(mid, m)], forced=tool_name(mid), feedback=feedback)
        info['calls'].append({**receipt, 'step': 'fill', 'metric': mid})
        try:
            if choice.get('name') == NO_METRIC: raise LookupError((choice.get('input') or {}).get('reason') or 'no metric')
            slots = validate_slots(m, choice.get('input') or {})
            missing = unaccounted_numbers(question, m, slots, as_of)
            if missing: raise ValueError(f"numbers in the question are not in any slot: {', '.join(f'{x:g}' for x in missing)}")
            parts = unaccounted_components(question, m)
            if parts: raise ValueError(f"the question subtracts {', '.join(parts)}, which metric {mid} does not define; "
                                       'call no_metric unless a slot covers it')
            node = compile_metric(mid, m, slots, as_of, question)
            return slots, node
        except LookupError:
            raise
        except (ValueError, KeyError, TypeError) as exc:
            info.setdefault('rejected', []).append({'metric': mid, 'slots': choice.get('input'), 'error': str(exc)[:400]})
            feedback = f'{type(exc).__name__}: {exc}'
    raise ValueError(info['rejected'][-1]['error'])


def shape_misfit(question, mid, node, slots=None):
    """Why metric `mid`'s output cannot be the answer shape the question asks for (answer_shape.requested), or None.
    A rate per 1,000 never answers "how many"; a single count or value never answers "for each ... / one row per";
    a measure engine's rate table never answers a count. The rung declines and the ladder falls through."""
    from .answer_shape import requested
    from .capability_registry import answers
    want = requested(question)
    shape = want['shape']
    if shape is None: return None
    grouped = (slots or {}).get('group_by') not in (None, 'none')
    if node.get('operation') == 'sql':
        returns = (node.get('parameters') or {}).get('returns')
        bad = ((shape == 'count' and returns == 'value') or
               (shape == 'table' and returns in ('value', 'count', 'patient_set') and not grouped) or
               (shape == 'value' and returns == 'count' and want['cue'] != 'total') or
               (shape == 'patient_set' and returns in ('value', 'count')))
        return f'the question asks for a {shape}; metric {mid} returns a {returns}' if bad else None
    served = answers(node.get('operation'))
    if served is not None and shape not in served:
        return f"the question asks for a {shape}; metric {mid} runs the {node.get('operation')} engine, which answers {' / '.join(served)}"
    from .answer_shape import _LITERAL
    if served is not None and shape == 'count' and _LITERAL.search(question or ''):
        # a defined count ("count each covered day once", "use data received as of") is not the engine's measure rate
        return f"the question defines a count; metric {mid} runs the {node.get('operation')} engine, which reports its own measure"
    return None


def route(question, as_of, ledger=None, out=None, path=None, ask=None, directory=None, lib=None):
    """-> (graph | None, info). info['decision'] is 'metric', 'readings', 'no_metric' or 'fill_failed'.

    ask(question, as_of, tools=..., forced=..., feedback=...) -> (choice {'name','input'}, receipt) replaces the paid call
    (tests, offline replay). With path, the two readings of an ambiguous choice are executed (read-only) and compared.
    lib replaces the routable library (s1_governed offers the generated metrics of generated_metrics.py beside the authored ones)."""
    start = time.perf_counter()
    lib = routable(directory) if lib is None else lib
    index = _tool_index(lib)
    info = {'router': VERSION, 'calls': []}

    def call(tools, forced=None, feedback=None):
        if ask is not None: return ask(question, as_of, tools=tools, forced=forced, feedback=feedback)
        return _ask(question, as_of, ledger, out, tools, forced=forced, feedback=feedback)
    choice, receipt = call(tools=menu(lib))
    info['calls'].append({**receipt, 'step': 'choose'})
    name = choice.get('name')
    done = lambda: info.update(seconds=round(time.perf_counter() - start, 2))
    if name == NO_METRIC or (name not in index and name != AMBIGUOUS):
        info.update(decision=NO_METRIC, reason=(choice.get('input') or {}).get('reason') or f'unknown tool {name}'); done()
        return None, info
    chosen = [index[name]] if name in index else [index[t] for t in (choice.get('input') or {}).get('metrics') or [] if t in index][:2]
    if not chosen:
        info.update(decision=NO_METRIC, reason='ambiguous choice named no known metric'); done()
        return None, info
    filled = []
    if plain_count_question(question):  # a measure RATE never answers a plain count question; it falls through instead
        for mid in [x for x in chosen if kind(lib[x]) == 'measure']:
            info.setdefault('declined', []).append({'metric': mid, 'reason': 'plain count question: a quality-measure rate answers a '
                                                    'different question (count the cohort instead)'})
        chosen = [x for x in chosen if kind(lib[x]) != 'measure']
    breakdown = bool(re.search(BREAKDOWN, re.sub(r'\bper (?:member|patient) per (?:month|year)\b|\bper 1,?000\b', ' ', (question or '').lower())))
    for mid in dict.fromkeys(chosen):
        try:
            missing_scope = unaccounted_clinical_scope(question, lib[mid])
            if missing_scope:
                raise LookupError('fixed metric does not cover: ' + ', '.join(missing_scope))
            slots, node = _fill(question, as_of, mid, lib[mid], call, info)
            returns = (node.get('parameters') or {}).get('returns') if node.get('operation') == 'sql' else None
            if breakdown and returns in ('value', 'count') and (slots.get('group_by') in (None, 'none')):
                # "... by payer": one total answers a different question; the next rung keeps the grouping
                raise LookupError(f'the question asks for a breakdown ("by ..."); metric {mid} returns a single {returns}')
            misfit = shape_misfit(question, mid, node, slots)
            if misfit: raise LookupError(misfit)
            from . import exact_fit  # one receipted verdict: restrictions the question states must be in the definition or slots
            info.setdefault('exact_fit', {})[mid] = exact_fit.gate(question, mid, lib[mid], slots, node, as_of)
            filled.append((mid, slots, node))
        except LookupError as exc:
            info.setdefault('declined', []).append({'metric': mid, 'reason': str(exc)[:300]})
        except ValueError as exc:
            info.setdefault('failed', []).append({'metric': mid, 'error': str(exc)[:300]})
    if not filled:
        info.update(decision='fill_failed' if info.get('failed') else NO_METRIC,
                    reason='; '.join(x.get('error') or x.get('reason') for x in info.get('failed', []) + info.get('declined', []))[:500]); done()
        return None, info
    mid, slots, node = filled[0]
    info.update(decision='metric', metric=mid, version=lib[mid]['version'], resolution=resolution(mid, lib[mid], slots, node))
    if len(filled) == 2:
        info['ambiguity'] = {'metrics': [f[0] for f in filled], 'difference': (choice.get('input') or {}).get('difference')}
        readings_receipt = readings(question, lib, filled, path, (choice.get('input') or {}).get('difference'))
        if readings_receipt:
            info['decision'] = 'readings'
            info['readings'] = readings_receipt
    done()
    return to_graph(question, mid, lib[mid], node, slots), info


def readings(question, lib, filled, path, difference=None):
    """Two filled metrics: compute both; equal answers are one answer (None); different answers are two readings."""
    from . import verification_v2 as v2
    results = []
    for mid, slots, node in filled[:2]:
        try:
            res = execute(path, node) if path else None
        except (ValueError, OSError) as exc:
            res = {'error': str(exc)[:200]}
        results.append({'label': f"metric {mid}@v{lib[mid]['version']}", 'params': node['parameters'] if node['operation'] == 'sql' else None,
                        'definition': computed_as(mid, lib[mid], slots, node), 'result': res})
    a, b = results
    if a['result'] is not None and b['result'] is not None and not a['result'].get('error') and not b['result'].get('error') \
            and v2.same_answer(a['result'], b['result']):
        return None
    da, db = filled[0][0], filled[1][0]
    clarify = (f"Which do you mean: {lib[da]['name']} ({lib[da]['summary'][:160]}) or {lib[db]['name']} ({lib[db]['summary'][:160]})?"
               + (f" They differ by: {difference}" if difference else ''))

    def one(r, default):
        return {'definition': r['definition'], 'query': (r['params'] or {}).get('query'), 'returns': (r['params'] or {}).get('returns'),
                'result': v2._preview(r['result']) if r['result'] and not r['result'].get('error') else r['result'],
                'sources': [r['label']], 'default': default}
    return {'readings': [one(a, True), one(b, False)], 'differs_by': ['metric definition'], 'clarifying_question': clarify,
            'version': v2.VERSION, 'router': VERSION}


def cost(info):
    return sum(c.get('api_usd') or 0 for c in info.get('calls', []))


def enabled(stack=None):
    return os.environ.get('AGERE_METRIC_ROUTER', 'on') != 'off'


# ------------------------------------------------------------------------------------------------ free-form acceptance
def acceptance(question, sql, receipt, as_of, source=None):
    """Stricter acceptance for free-form SQL: 'verified' only with verification v2's independent agreement (>= 2 answer
    families) or a governed metric that the question resolves to deterministically and the SQL passes lint-clean;
    everything else (same-family agreement, judge picks, deciders) is 'low_confidence'."""
    from . import metrics
    ver = (receipt or {}).get('verification') or {}
    if ver.get('status') == 'accepted' and ver.get('basis') == 'independent':
        return {'status': 'verified', 'basis': 'independent agreement (verification v2): ' + str(receipt.get('agreement'))}
    r = metrics.resolve({'question': question, 'as_of': as_of})
    if r and sql:
        try:
            found = metrics.lint(question, sql, as_of, source=source, resolution=r)
        except Exception as exc:  # noqa: BLE001 - lint failure is not a pass
            found = [{'rule': 'error', 'message': str(exc)[:200]}]
        if not found:
            return {'status': 'verified', 'basis': f"lint-clean against governed metric {r['id']}@v{r['version']}"}
        return {'status': 'low_confidence', 'basis': f"departs from governed metric {r['id']}: " + '; '.join(f['message'] for f in found)[:400]}
    return {'status': 'low_confidence', 'basis': 'free-form SQL without independent agreement or a governed metric to check it against '
                                                 f"(agreement: {(receipt or {}).get('agreement')})"}


def policy():
    p = os.environ.get('AGERE_FREEFORM_POLICY', 'low_confidence')
    return p if p in ('low_confidence', 'abstain', 'off') else 'low_confidence'


CONFIDENCE = ('verified', 'unverified', 'low')


def set_confidence(answer, level, reason, **detail):
    """Answer contract: confidence ('verified' | 'unverified' | 'low') is separate from status. Status says whether the
    answer is complete; confidence says whether its error rate is certified (gate_policy): 'verified' only when every gate
    on the answer's path has a Learn-then-Test certificate and passed, 'low' when a check on the path failed,
    'unverified' otherwise."""
    if level not in CONFIDENCE: raise ValueError(f'confidence must be one of {CONFIDENCE}')
    answer['confidence'] = level
    answer['confidence_reason'] = str(reason or '')[:500]
    answer['confidence_detail'] = {'level': level, 'basis': reason, **detail}
    return answer


def _asserted(answer):
    return answer.get('answer_type') not in (None, 'unsupported') and answer.get('status') in ('answered', 'partial')


def _certified_confidence(out, receipt, encoder=None, basis=None, **detail):
    """Set the gate-policy confidence; basis is the route's own reason (governed metric, acceptance basis)."""
    from . import gate_policy
    from .answer_checks import _add
    verdict = gate_policy.assess(receipt, encoder)
    reason = verdict['reason'] + (f' [{basis}]' if basis else '')
    set_confidence(out, verdict['level'], reason, gates=verdict['gates'], rule=verdict['rule'], **detail)
    if verdict['level'] == 'unverified':
        _add(out, 'QC-CONFIDENCE', 'FM-13', ('Unverified: ' + verdict['reason'])[:500], 'info')
    return out


def finalize_answer(answer, receipt, encoder=None):
    """Answer contract for every auto-planned answer: governed answers state their metric; ambiguous ones carry both
    readings; unverified free-form SQL keeps its status and exact value but carries confidence 'low' with the reason
    (policy 'abstain' withholds the number instead). Every asserted answer carries confidence verified | unverified | low
    under gate_policy's rule (verified = every gate on the path certified and passed); abstentions carry none, except a
    contradicted missing-data claim (low). AGERE_CONFIDENCE_RULE=legacy restores the earlier labels."""
    if not isinstance(answer, dict) or not isinstance(receipt, dict): return answer
    from .answer_checks import _add
    from . import gate_policy
    legacy = gate_policy.legacy()
    out = dict(answer)
    mr = receipt.get('metric_router') or {}
    if receipt.get('route') == 'metric_router' and mr.get('resolution'):
        res = mr['resolution']
        out['computed_as'] = {'metric': res['metric'], 'version': res['version'], 'layer': VERSION, 'definition': res['definition'],
                              'slots': res['slots'], 'statement': res['statement'], 'conforms': True,
                              **({'origin': res['origin'], 'certification': res.get('certification')} if res.get('origin') else {}),
                              **({'anchor': res['anchor']} if res.get('anchor') else {}),
                              **({'evidence': out['computed_as']['evidence']} if isinstance(out.get('computed_as'), dict)
                                 and out['computed_as'].get('evidence') else {})}
        out['computed'] = res['statement']
        basis = f"computed deterministically as metric {res['metric']}@v{res['version']}"
        if res.get('origin') == 'generated':  # a definition generated from the semantic layer's structure is never certified as authored
            _certified_confidence(out, receipt, encoder, basis + ' (generated definition, not authored)', source='generated_metric')
        elif legacy: set_confidence(out, 'verified', basis, source='governed_metric')
        else: _certified_confidence(out, receipt, encoder, basis, source='governed_metric')
        _add(out, 'QC-METRIC', 'FM-05', res['statement'][:300].capitalize(), 'info', metric=res['metric'], metric_version=res['version'])
        if mr.get('readings'):
            from .verification_v2 import attach_readings
            out = attach_readings(out, mr['readings'])
        return out
    fam = receipt.get('cohort_family')
    if isinstance(fam, dict) and receipt.get('route') in ('cohort_family', 'learned_verified_sql') and _asserted(out):
        # cohort_families.py: the family and its parameters are the computed definition (what a reviewer checks)
        out['computed_as'] = {'cohort_family': fam.get('family'), 'version': fam.get('version'), 'parameters': fam.get('parameters'),
                              'statement': fam.get('statement'), 'defaults': fam.get('defaults') or [],
                              'model_filled': fam.get('model_filled') or [], 'conforms': True}
        out['computed'] = fam.get('statement')
        _add(out, 'QC-METRIC', 'FM-05', ('Computed deterministically as ' + str(fam.get('statement')))[:300], 'info',
             cohort_family=fam.get('family'))
    if receipt.get('route') == 'hosted_missing_data':
        check = receipt.get('missing_check') or {}
        if check.get('contradicted'):
            names = ', '.join(c['name'] for c in check['contradicted'])[:300]
            set_confidence(out, 'low', f"the analyst said the data is missing, but the warehouse has {names}; "
                                       'the retry with that field named did not produce an answer', source='missing_data_check')
            _add(out, 'QC-CONFIDENCE', 'FM-13', f"Low confidence abstention: the claimed-missing element exists ({names}).")
        return out
    acc = receipt.get('acceptance') or {}
    if receipt.get('route') == 'hosted_verified_sql' and acc.get('status') == 'low_confidence':
        if policy() == 'off': return out
        set_confidence(out, 'low', acc.get('basis'), policy=policy(), source='free_form_sql',
                       **({} if legacy else {'gates': gate_policy.assess(receipt, encoder)['gates'], 'rule': gate_policy.VERSION}))
        message = ('Low confidence: this number comes from free-form SQL that no independent source or governed metric confirmed '
                   f"({acc.get('basis')}). Treat it as a draft until it is checked.")
        if policy() == 'abstain':
            out.update(count=None, value=None, table=None, patient_ids=None, answer_type='unsupported', status='partial')
            out['missing'] = list(out.get('missing') or []) + ['Withheld: free-form SQL answer was not independently verified '
                                                               f"({acc.get('basis')})"]
        _add(out, 'QC-CONFIDENCE', 'FM-13', message[:500])
        return out
    if legacy:
        if acc.get('status') == 'verified':
            set_confidence(out, 'verified', acc.get('basis'), source='free_form_sql')
        return out
    if _asserted(out):
        _certified_confidence(out, receipt, encoder, acc.get('basis') if acc else None,
                              source='free_form_sql' if receipt.get('route') == 'hosted_verified_sql' else receipt.get('route'))
    return out
