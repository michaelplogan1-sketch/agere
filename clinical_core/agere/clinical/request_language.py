"""Compositional, fully consuming front end for warehouse requests.

Vocabulary names sources/fields/concepts, not complete questions. The same
boolean grammar combines any available fields and related-record predicates.
Unconsumed text declines the parse; it is never discarded by a rewrite.
"""
import re
from .request_plan import PlanError, AmbiguousRequest

SOURCES = {
    'patients': ('patient', None), 'people': ('patient', None), 'members': ('patient', None),
    'ed visits': ('utilization_event', ('kind', 'ed_visit')),
    'er visits': ('utilization_event', ('kind', 'ed_visit')),
    'emergency visits': ('utilization_event', ('kind', 'ed_visit')),
    'inpatient admissions': ('stay', ('type', 'inpatient')),
    'admissions': ('stay', ('type', 'inpatient')), 'hospital stays': ('stay', None),
    'claims': ('claim', None), 'visits': ('visit', None), 'appointments': ('appointment', None),
    'diabetics': ('patient', ('condition', 'diabetes')),
}
FIELDS = {
    'age': 'patient.age', 'sex': 'patient.sex_reported',
    'pcp': 'patient.pcp_clinician_id', 'practice': 'patient.pcp_practice_id',
    'coverage': 'patient.coverage_type', 'coverage type': 'patient.coverage_type',
    'payer': 'patient.payer_id', 'language': 'patient.preferred_language',
    'allowed cents': 'claim.allowed_cents', 'billed cents': 'claim.billed_cents',
    'net paid cents': 'claim.net_payer_paid_cents', 'length of stay': 'stay.los_days',
}
CLOCKS = {'utilization_event': 'event_date', 'visit': 'event_date', 'stay': 'admit_date', 'claim': 'service_date',
          'appointment': 'scheduled_date', 'medication': 'event_date',
          'measurement': 'event_date', 'condition': 'event_date', 'record': 'event_date',
          'claim_adjudication': 'event_date', 'member_month': 'month'}
CONDITION_ALIASES = {'diabetes': 'diabetes', 'ckd': 'chronic_kidney_disease', 'chronic kidney disease': 'chronic_kidney_disease',
                     'heart failure': 'heart_failure', 'hypertension': 'hypertension',
                     'copd': 'copd', 'asthma': 'asthma'}
DRUG_ALIASES = {'a statin': ['statin'], 'statins': ['statin'], 'statin': ['statin'],
                'an ace inhibitor or arb': ['ace_inhibitor', 'arb'],
                'ace inhibitor or arb': ['ace_inhibitor', 'arb'],
                'acei/arb': ['ace_inhibitor', 'arb'], 'ace inhibitor': ['ace_inhibitor'],
                'an arb': ['arb'], 'arb': ['arb'], 'insulin': ['insulin'], 'metformin': ['metformin']}


def atom(field, op, value, quote):
    return {'field': field, 'op': op, 'value': value, 'quote': quote}


def combine(op, values, quote):
    values = [v for v in values if v]
    return None if not values else values[0] if len(values) == 1 else {'op': op, 'args': values, 'quote': quote}


def related(table, predicate, quote):
    return {'op': 'exists', 'table': table, 'where': predicate, 'quote': quote}


def syntax_text(text, *, hide_parentheses=False):
    """Same offsets, with literals (and optionally nested predicates) masked."""
    chars, quote, depth = list(text), None, 0
    for i, char in enumerate(text):
        if quote:
            chars[i] = ' '
            if char == quote: quote = None
        elif char in ('"', "'"):
            quote = char
            chars[i] = ' '
        elif hide_parentheses and char == '(':
            depth += 1
            chars[i] = ' '
        elif hide_parentheses and char == ')':
            depth -= 1
            chars[i] = ' '
        elif depth:
            chars[i] = ' '
    return ''.join(chars)


class Parser:
    def __init__(self, catalog, question, *, bound=False):
        self.catalog, self.question, self.bound = catalog, question, bound
        self.base = 'patient'
        self.sources = dict(SOURCES)
        for table in catalog.tables:
            phrase = table.replace('_', ' ')
            self.sources.setdefault(phrase, (table, None))
            self.sources.setdefault(phrase+'s', (table, None))
        self.sources = {k: v for k, v in self.sources.items() if v[0] in catalog.tables}
        from . import concepts
        self.conditions = dict(CONDITION_ALIASES)
        self.conditions.update({k.replace('_', ' '): k for k in concepts.CONDITIONS})
        from .drug_classes import CLASSES, GROUPS
        self.drugs = {k.replace('_', ' '): [k] for k in CLASSES}
        self.drugs.update({k.replace('_', ' '): list(v) for k, v in GROUPS.items()})
        self.drugs.update(DRUG_ALIASES)

    def field_aliases(self):
        fields = dict(FIELDS)
        if self.base == 'claim': fields['payer'] = 'claim.payer'
        for table in ('patient', self.base):
            for column in self.catalog.tables.get(table, []):
                fields[column] = fields[column.replace('_', ' ')] = table+'.'+column
        for table, columns in self.catalog.tables.items():
            for column in columns: fields[table+'.'+column] = table+'.'+column
        return {k: v for k, v in fields.items() if v.split('.')[0] in self.catalog.tables and v.split('.')[1] in self.catalog.tables[v.split('.')[0]]}

    def prefix(self, text, options):
        for phrase in sorted(options, key=len, reverse=True):
            m = re.match(re.escape(phrase)+r'(?![\w.])', text, re.I)
            if m: return m.group(), options[phrase], text[m.end():].lstrip()
        return None

    def field(self, text):
        got = self.prefix(text.strip(), self.field_aliases())
        if not got or got[2]: raise PlanError('Unrecognized or ambiguous dimension: '+text)
        return got[1]

    def grouping(self, text, dimensions):
        """Consume only named group dimensions; leave later clauses to parse."""
        markers = list(re.finditer(r'(?<!\S)(?:grouped by|group by|broken down by|by|per)\s+',
                                   syntax_text(text, hide_parentheses=True), re.I))
        if not markers: return text, [], None
        if len(markers) != 1: raise PlanError('Use one explicit grouping clause')
        marker = markers[0]
        cursor, groups = marker.end(), []
        while True:
            got = self.prefix(text[cursor:], dimensions)
            if not got: raise PlanError('Unknown grouping dimension: '+text[cursor:])
            word, field, _ = got
            if any(value == field for _, value in groups): raise PlanError('Duplicate grouping dimension')
            groups.append((word, field))
            cursor += len(word)
            separator = re.match(r'\s*(?:,\s*|\band\s+)', text[cursor:], re.I)
            if not separator: break
            next_cursor = cursor + separator.end()
            if not self.prefix(text[next_cursor:], dimensions):
                # "by payer and excluding ckd" starts another predicate.
                if separator.group().strip().lower() == 'and': break
                raise PlanError('Missing grouping dimension after comma')
            cursor = next_cursor
        quoted = text[marker.start():cursor]
        return text[:marker.start()]+' '+text[cursor:], groups, quoted

    def bind_parent_quotes(self, value):
        """Parent booleans do not cover words; leaves keep their literal spans."""
        if not isinstance(value, dict): return
        if value.get('op') in ('and', 'or', 'not', 'exists') and value.get('quote') not in self.question:
            value['quote'] = self.question
        for child in value.values():
            if isinstance(child, list):
                for item in child: self.bind_parent_quotes(item)
            elif isinstance(child, dict): self.bind_parent_quotes(child)

    def condition(self, concept_id, quote):
        return {'op': 'concept', 'kind': 'condition', 'names': [concept_id], 'version': 1, 'period': None, 'quote': quote}

    def concept_period(self, value, tail):
        match = re.match(r'^\s*(?:in|during)\s+(20\d\d)\b', tail, re.I)
        if match and self.base == 'patient':
            year = int(match[1])
            value['period'] = {'start': f'{year}-01-01', 'end': f'{year}-12-31'}
            value['quote'] = [value['quote'], match.group().strip()]
            tail = tail[match.end():]
        return value, tail

    def parse(self):
        text = self.question.strip().rstrip('?.').strip()
        governed = self.governed(text)
        if governed is not None:
            return governed
        if self.bound and re.match(r'^(?:only|where)\s+', text, re.I):
            marker = re.match(r'^(?:only|where)\s+', text, re.I)
            where, tail = self.expression(text[marker.end():])
            if not where or tail.strip(): raise PlanError('Unconsumed cohort edit: '+tail)
            return {'source': {'table': 'patient', 'quote': marker.group().strip()},
                    'select': [{'kind': 'field', 'field': 'patient.patient_id', 'alias': 'patient_id', 'quote': marker.group().strip()}],
                    'where': where, 'group_by': [], 'order_by': [], 'limit': None, 'unresolved': []}
        lead = re.match(r'(?i)(how many|number of|count|list|which|show|find|group|break down|what is the total|what is the average|total|sum|average|mean)\s+', text)
        # Business questions often name the entity without an imperative.
        # Retain original spans; do not rewrite the question to add "count".
        if not lead and not self.prefix(text, self.sources):
            raise PlanError('No complete compositional parse; language interpretation required')
        verb = lead.group(1).lower() if lead else 'count'
        rest = text[lead.end():] if lead else text
        fn = 'list' if verb in ('list', 'which', 'show', 'find') else 'avg' if verb in ('average', 'mean', 'what is the average') else 'sum' if verb in ('total', 'sum', 'what is the total') else 'count'
        field_name, source = None, None
        if fn in ('sum', 'avg'):
            # A field name may itself contain "of" (e.g. length of stay).
            # Resolve the catalog field and source together instead of splitting
            # at the first preposition and silently changing the measure.
            split = None
            for m in re.finditer(r'\s+(?:for|of|from)\s+', rest, re.I):
                candidate = self.prefix(rest[m.end():], self.sources)
                if not candidate: continue
                self.base = candidate[1][0]
                try: resolved = self.field(rest[:m.start()])
                except PlanError: continue
                split = (rest[:m.start()], rest[m.end():], resolved)
                break
            if split is None:
                # A catalog field supplies its relation: e.g. "average age".
                field = self.prefix(rest, self.field_aliases())
                if not field: raise PlanError('Aggregation needs a recognized field and source')
                field_text, field_name, rest = field
                source = (field_text, (field_name.partition('.')[0], None), rest)
            else:
                field_text, rest, field_name = split
        source = source or self.prefix(rest, self.sources)
        if not source and self.bound:
            source = self.prefix(rest, {'them': ('patient', None), 'those patients': ('patient', None), 'these patients': ('patient', None)})
        if not source: raise PlanError('Explicit source or saved cohort required')
        source_quote, (self.base, default), rest = source
        rest, group_fields, group_quote = self.grouping(rest, self.field_aliases())
        rest = re.sub(r'^\s*(?:are|were|is|was)(?:\s+there)?\s+', '', rest, flags=re.I)
        base_filter = None
        if default:
            field, value = default
            base_filter = (self.condition(value, source_quote)
                           if field == 'condition' else atom(self.base+'.'+field, '=', value, source_quote))
        where, tail = self.expression(rest)
        if tail.strip(): raise PlanError('Unconsumed request: '+tail)
        where = combine('and', [base_filter, where], self.question)
        self.bind_parent_quotes(where)
        measures = [{'kind': 'field', 'field': field, 'alias': field.split('.')[-1], 'quote': word}
                    for word, field in group_fields]
        if measures: measures[0]['quote'] = [measures[0]['quote'], group_quote]
        groups = [m['alias'] for m in measures]
        if fn == 'list' and not groups:
            if 'patient_id' not in self.catalog.tables[self.base]: raise PlanError('List the required columns explicitly')
            measure = {'kind': 'field', 'field': self.base+'.patient_id', 'alias': 'patient_id', 'quote': lead.group(1) if lead else source_quote}
        elif fn in ('sum', 'avg'):
            measure = {'kind': fn, 'field': field_name, 'alias': fn+'_'+field_name.split('.')[-1], 'quote': lead.group(1)+' '+field_text}
        else:
            measure = {'kind': 'count_distinct' if self.base == 'patient' else 'count', 'alias': 'patients' if self.base == 'patient' else 'count', 'quote': lead.group(1) if lead else source_quote}
            if self.base == 'patient': measure['field'] = 'patient.patient_id'
        measures.append(measure)
        return {'source': {'table': self.base, 'quote': source_quote}, 'select': measures, 'where': where,
                'group_by': groups, 'order_by': [], 'limit': None, 'unresolved': []}

    def governed(self, text):
        """One metric name plus independently consumed scope/period/group slots."""
        from . import request_vocabulary as V, metrics
        rest = re.sub(r'^(?:(?:what|how much) (?:is|was|are)|show(?: me)?|give me|tell me)\s+(?:(?:the|our)\s+)?',
                      '', text, flags=re.I)
        got = self.prefix(rest, V.aliases())
        if not got: return None
        measure_quote, ident, rest = got
        if re.match(r'^(?:for|of|from)\s+(?:admissions|hospital stays)\b', rest, re.I):
            return None  # explicit field/source aggregation uses the relational grammar
        definition = V.definitions()[ident]
        rest, group_fields, group_quote = self.grouping(rest, {k: k for k in V.GROUPS})
        groups, quotes = [field for _, field in group_fields], [measure_quote]
        if group_quote: quotes.append(group_quote)
        # A single explicit period belongs to the measure, never to patient DOB.
        periods = list(re.finditer(
            r'\b(?:(?:in|during|for)\s+)?20\d{2}\b|\b(?:last|past|trailing)\s+(?:12|twelve)\s+months\b|\b(?:last year|this year|ytd)\b',
            syntax_text(rest), re.I))
        if len(periods) > 1: raise PlanError('Multiple periods need an explicit comparison')
        spoken = periods[0].group() if periods else ''
        start, end, _ = metrics.period_for(spoken, self.catalog.identity['as_of'],
                                          definition['time_anchor'].get('default', 'all'))
        if periods:
            match = periods[0]
            quotes.append(match.group())
            rest = rest[:match.start()]+' '+rest[match.end():]
        self.base = 'patient'
        where, tail = self.expression(rest.strip())
        if tail.strip(): raise PlanError('Unconsumed metric scope: '+tail)
        self.bind_parent_quotes(where)
        params = V.parameters(ident, {'start': start.isoformat(), 'end': end.isoformat()}, groups)
        try: V.validate(params)
        except ValueError as exc: raise PlanError(str(exc)) from exc
        return {'source': {'table': 'patient', 'quote': measure_quote},
                'select': [{'kind': 'field', 'field': 'patient.patient_id', 'alias': 'patient_id', 'quote': measure_quote}],
                'where': where, 'group_by': [], 'order_by': [], 'limit': None, 'unresolved': [],
                'analyses': [{'operation': 'governed_metric', 'parameters': params, 'quote': quotes}]}

    def expression(self, text):
        """OR of AND terms; parentheses and explicit NOT preserve boolean scope."""
        first, tail = self.conjunction(text)
        terms = [first]
        fragments = [text[:len(text)-len(tail)].strip()]
        while re.match(r'^or\b', tail, re.I):
            rest = re.sub(r'^or\s*', '', tail, flags=re.I)
            part, tail = self.conjunction(rest)
            if part is None: raise PlanError('Missing OR operand')
            terms.append(part)
            fragments.append(rest[:len(rest)-len(tail)].strip())
        def enclosed(fragment):
            if not fragment.startswith('('): return False
            depth = 0
            for i, char in enumerate(fragment):
                depth += (char == '(') - (char == ')')
                if depth == 0: return i == len(fragment)-1
            return False
        if len(terms) > 1 and any(t and t.get('op') == 'and' and not enclosed(f) for t, f in zip(terms, fragments)):
            raise AmbiguousRequest('This mixes AND and OR without a clear scope. Add parentheses, for example age > 65 and (diabetes or ckd), or (age > 65 and diabetes) or ckd.')
        return combine('or', terms, text[:len(text)-len(tail)].strip()), tail

    def conjunction(self, text):
        values, tail = [], text.strip()
        while tail and not tail.startswith(')') and not re.match(r'^or\b', tail, re.I):
            join = re.match(r'^(?:and|but)\s+', tail, re.I)
            if join: tail = tail[join.end():]
            value, remaining = self.one(tail)
            if value is None:
                if join: raise PlanError('Missing conjunct after '+join.group())
                break
            values.append(value)
            tail = remaining.lstrip()
        return combine('and', values, text[:len(text)-len(tail)].strip()), tail

    def one(self, text):
        start = text
        filler = re.match(r'^(?:among|for) (?:the )?(?:patients|members)\s+|^who (?:are|have)\s+|^(?:where|for|among)\s+|^that\s+', text, re.I)
        if filler: text = text[filler.end():]
        if text.startswith('('):
            value, tail = self.expression(text[1:])
            if not tail.startswith(')'): raise PlanError('Unclosed predicate group')
            return value, tail[1:]
        neg = re.match(r'^(?:not|without|excluding|exclude)\s+', text, re.I)
        if neg:
            child, tail = self.one(text[neg.end():])
            if child is None: raise PlanError('Missing negated predicate')
            return {'op': 'not', 'arg': child, 'quote': start[:len(start)-len(tail)].strip()}, tail
        # Source-specific clock, never a blanket patient-year filter.
        m = re.match(r'^(?:(?:in|during)\s+)?(\d{4})(?![\d-])', text, re.I)
        if m:
            clock = CLOCKS.get(self.base)
            if not clock: raise PlanError('This period needs an explicit event/date field')
            quote, year = m.group(), int(m.group(1))
            return combine('and', [atom(self.base+'.'+clock, '>=', f'{year}-01-01', quote), atom(self.base+'.'+clock, '<', f'{year+1}-01-01', quote)], quote), text[m.end():]
        coverage = self.prefix(text, {'medicare': 'medicare', 'medicaid': 'medicaid',
                                      'commercial': 'commercial', 'uninsured': 'uninsured'})
        if coverage:
            quote, value, tail = coverage
            return atom('patient.coverage_type', '=', value, quote), re.sub(r'^\s+(?:patients|members)\b', '', tail, flags=re.I)
        deceased = re.match(r'^deceased(?:\s+(?:patients|members))?\b', text, re.I)
        if deceased and 'current_record' in self.catalog.tables and 'death_records' in self.catalog.stream_fields:
            quote = deceased.group()
            return {'op': 'exists', 'table': 'current_record', 'stream': 'death_records', 'where': None, 'quote': quote}, text[deceased.end():]
        m = re.match(r'^(?:with |having |have |has )', text, re.I)
        concept_text = text[m.end():] if m else text
        concept = self.prefix(concept_text, self.conditions)
        if concept:
            quote, concept_id, tail = concept
            quote = text[:len(text)-len(tail)].strip()
            return self.concept_period(self.condition(concept_id, quote), tail)
        med = re.match(r'^(?:on|taking|receiving|prescribed|filled|dispensed)\s+', text, re.I)
        if med:
            got = self.prefix(text[med.end():], self.drugs)
            if got:
                _, drugs, tail = got
                quote = text[:len(text)-len(tail)].strip()
                if med.group().strip().lower() == 'prescribed':
                    return related('medication', combine('and', [
                        atom('medication.drug_class', 'in', drugs, quote),
                        atom('medication.evidence', '=', 'ordered', quote)], quote), quote), tail
                return self.concept_period({'op': 'concept', 'kind': 'drug_fill', 'names': drugs,
                                            'version': 1, 'period': None, 'quote': quote}, tail)
        # Presence of another source: same-patient semijoin, not a duplicating join.
        event = re.match(r'^(?:with|having|had)\s+(?:an?\s+)?', text, re.I)
        if event:
            got = self.prefix(text[event.end():], self.sources)
            if got and got[1][0] != 'patient':
                _, (table, preset), tail = got
                quote = text[:len(text)-len(tail)].strip()
                return related(table, atom(table+'.'+preset[0], '=', preset[1], quote) if preset else None, quote), tail
        # Generic received-field comparisons. The field vocabulary comes from the
        # catalog, so a newly delivered column does not need a question handler.
        got = self.prefix(text, self.field_aliases())
        age = re.match(r'^(?:aged?\s+)?(over|under|at least|at most)\s+', text, re.I)
        if not got and age:
            got = ('', 'patient.age', text)
        if got:
            _, field, remainder = got
            comp = re.match(r'^\s*(is not null|is null|is not|not equal to|is|equals?|>=|<=|!=|>|<|=|at least|at most|over|under|above|below|greater than|less than)\s*', remainder, re.I)
            if comp:
                label = comp.group(1).lower()
                ops = {'is not null': 'not_null', 'is null': 'is_null', 'is not': '!=', 'not equal to': '!=', 'is': '=', 'equal': '=', 'equals': '=', 'at least': '>=', 'at most': '<=', 'over': '>', 'under': '<', 'above': '>', 'below': '<', 'greater than': '>', 'less than': '<'}
                op = ops.get(label, label)
                rem = remainder[comp.end():]
                if op in ('is_null', 'not_null'): value, tail = None, rem
                else:
                    value_match = re.match(r'''^(?:"([^"]*)"|'([^']*)'|([+-]?\d+(?:\.\d+)?)(?![\w-])|([^\s()]+))''', rem)
                    if not value_match: raise PlanError('Comparison needs a literal value')
                    value = next(x for x in value_match.groups() if x is not None)
                    if value_match.group(3): value = float(value) if '.' in value else int(value)
                    tail = rem[value_match.end():]
                quote = start[:len(start)-len(tail)].strip()
                return atom(field, op, value, quote), tail
        return None, start


def parse(catalog, question, *, bound=False):
    return Parser(catalog, question, bound=bound).parse()
