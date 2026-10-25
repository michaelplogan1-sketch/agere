"""Human-governed claim-count source selection, independent of parser wording.

Counts alone use this policy. Money/rates retain their own governed definitions.
Overlapping source readings are separate outputs, never an additive total.
"""
import copy
import re

VERSION = 'claim-count-context/1'
REPORT = re.compile(r'\b(?:for|in)\s+(?:(?:a|an|the|our)\s+)?(?P<kind>payer|insurer|insurance|vbc|value[- ]based(?:\s+care)?|clinical\s+utilization|clinical|utilization)\s+(?:contract\s+)?reports?\b', re.I)
PAYER = re.compile(r'\b(?:payer[- ]feed|payer[- ]reported|payer[- ]adjudicated|payer|insurer|insurance|eligible\s+payer|eligible\s+insurer)\s+(?:medical\s+)?claims?\b', re.I)
ALL = re.compile(r'\b(?:all[- ](?:source|received)|all\s+received|merged)\s+(?:medical\s+)?claims?\b', re.I)
PROVIDER = re.compile(r'\bprovider\s+(?:medical\s+)?claims?\b', re.I)
LABELS = {'all_received': 'All received medical claims (deduplicated)',
          'eligible_insurer': 'Eligible insurer claims (current, nonduplicate)'}


def report_matches(question):
    from .request_language import syntax_text
    return REPORT.finditer(syntax_text(question))


def context(question):
    from .request_language import syntax_text
    question = syntax_text(question)
    kinds = {m['kind'].lower() for m in REPORT.finditer(question)}
    readings = set()
    if any(k in ('clinical', 'utilization', 'clinical utilization') for k in kinds) or ALL.search(question):
        readings.add('all_received')
    if kinds - {'clinical', 'utilization', 'clinical utilization'} or PAYER.search(question):
        readings.add('eligible_insurer')
    if PROVIDER.search(question):
        return None  # an explicit provider-only source keeps its existing definition
    return sorted(readings) if readings else ['all_received', 'eligible_insurer']


def eligible(plan):
    if not isinstance(plan, dict) or plan.get('source', {}).get('table') not in ('claim', 'payer_claim'):
        return False
    if any(plan.get(k) for k in ('analyses', 'joins', 'subject')):
        return False
    measures = [e for e in plan.get('select', []) if e.get('kind') != 'field']
    return bool(measures) and all(e.get('kind') in ('count', 'count_distinct') and not e.get('scale') and
        not str(e.get('field', '')).endswith('.patient_id') for e in measures)


def without_report(text):
    chars = list(text)
    for match in report_matches(text):
        chars[match.start():match.end()] = ' ' * (match.end() - match.start())
    return ''.join(chars).strip().strip(' ,')


def compile_counts(catalog, question, plan, population, compiler, error, fingerprint):
    if not eligible(plan) or context(question) is None:
        return None
    readings = context(question)
    original = plan['source']['table']
    # Absence is not zero. Keep the available reading and disclose the missing one.
    available, missing = [], []
    def field_names(table):
        return ({'service_date': 'service_from', 'claim_id': 'payer_claim_id', 'payer': 'payer_id'} if table == 'payer_claim'
                else {'service_from': 'service_date', 'payer_claim_id': 'claim_id', 'payer_id': 'payer'})
    fields = set()
    def collect(node):
        if isinstance(node, list):
            for item in node: collect(item)
        elif isinstance(node, dict):
            field = node.get('field')
            if isinstance(field, str) and field.startswith(original + '.'):
                fields.add(field.split('.', 1)[1])
            for value in node.values(): collect(value)
    collect(plan)
    if len(readings) > 1 and any(e.get('alias') == 'claim_scope' for e in plan['select']):
        raise error('claim_scope is reserved for the source-reading label')
    for reading in readings:
        table = 'claim' if reading == 'all_received' else 'payer_claim'
        if table not in catalog.tables or (table == 'payer_claim' and 'counts_in_metrics' not in catalog.tables[table]):
            missing.append(LABELS[reading] + ': source not received')
        else:
            names = field_names(table)
            absent = sorted(names.get(f, f) for f in fields - {'counts_in_metrics'} if names.get(f, f) not in catalog.tables[table])
            if absent:
                missing.append(LABELS[reading] + ': required fields not received: ' + ', '.join(absent))
            else:
                available.append((reading, table))
    if not available:
        raise error('Claim-count sources not received: ' + '; '.join(missing))
    compiled = []
    for reading, table in available:
        wire = copy.deepcopy(plan)
        wire['source']['table'] = table
        names = field_names(table)
        def rewrite(node):
            if isinstance(node, list):
                return [rewrite(v) for v in node]
            if not isinstance(node, dict): return node
            if node.get('op') == 'exists' and node.get('table') == original:
                raise error('Self-correlated claim counts require a separately scoped source reading')
            # This atom is the grammar's source eligibility, not an additional requested filter.
            if node.get('field') == original + '.counts_in_metrics':
                if node.get('op') != '=' or node.get('value') != 1 or 'counts_in_metrics' in str(node.get('quote', '')):
                    raise error('Explicit claim-version selection needs its own source definition')
                if table == 'claim': return None
            out = {k: rewrite(v) for k, v in node.items()}
            field = node.get('field')
            if isinstance(field, str) and field.startswith(original + '.'):
                col = field.split('.', 1)[1]
                col = names.get(col, col)
                if col not in catalog.tables[table]:
                    raise error('Cannot preserve claim-count filter/output on ' + table + '.' + col)
                out['field'] = table + '.' + col
            if out.get('op') in ('and', 'or'):
                args = [a for a in out['args'] if a is not None]
                if len(args) != len(out['args']) and out['op'] != 'and':
                    raise error('Claim eligibility under OR cannot be removed')
                if len(args) == 1: return args[0]
                if not args: return None
                out['args'] = args
            if out.get('op') == 'not' and out.get('arg') is None:
                raise error('Negated claim eligibility cannot be removed')
            return out
        wire = rewrite(wire)
        if table == 'payer_claim':
            atom = {'field': 'payer_claim.counts_in_metrics', 'op': '=', 'value': 1, 'quote': plan['source']['quote']}
            wire['where'] = {'op': 'and', 'args': [wire['where'], atom], 'quote': plan['source']['quote']} if wire['where'] else atom
        c = compiler(catalog, question, population=population).compile(wire)
        compiled.append((reading, c))
    result = copy.deepcopy(compiled[0][1])
    result['claim_count_policy'] = {'version': VERSION, 'authority': 'Mike, 2026-10-25',
        'readings': [r for r, c in compiled], 'missing_readings': missing,
        'queries': {r: c['query'] for r, c in compiled}}
    result['notes'] = list(dict.fromkeys([*result.get('notes', []),
        'Claim counts follow report context. Source readings overlap and must not be added.',
        *[LABELS[r] for r, c in compiled], *missing]))
    if len(compiled) > 1:
        # Each compiled query retains its own grouping/order/limit. The label is observed scope, not an invented extra count.
        result['query'] = ' UNION ALL '.join("SELECT '" + LABELS[r] + "' AS claim_scope, reading.* FROM (" + c['query'] + ') AS reading' for r, c in compiled)
        result['returns'] = 'table'
        # Separate readings cannot authorize one undifferentiated follow-up cohort.
        result['membership_query'] = result['unknown_membership_query'] = None
    if missing:
        result['notes'].append('Only received sources are shown; missing source counts are unknown, not zero.')
    result.pop('id', None)
    return {**result, 'id': fingerprint(result)}


def validated_dual_counts(contract, answer):
    """The shape guard accepts two counts only for a sealed, labeled source comparison."""
    from .request_plan import assert_sealed, PlanError
    try:
        assert_sealed(contract)
    except (PlanError, TypeError, KeyError):
        return False
    policy = contract.get('claim_count_policy') or {}
    if policy.get('version') != VERSION or set(policy.get('readings', [])) != set(LABELS):
        return False
    measures = [e for e in contract['request']['select'] if e.get('kind') != 'field']
    if not measures or any(e.get('kind') not in ('count', 'count_distinct') for e in measures):
        return False
    rows = answer.get('table')
    if not isinstance(rows, list) or {r.get('claim_scope') for r in rows} != set(LABELS.values()):
        return False
    return all(type(r.get(e['alias'])) is int and r[e['alias']] >= 0 for r in rows for e in measures)
