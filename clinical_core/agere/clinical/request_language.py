"""Compositional, fully consuming front end for warehouse requests.

Vocabulary names sources/fields/concepts, not complete questions. The same
boolean grammar combines any available fields and related-record predicates.
Unconsumed text declines the parse; it is never discarded by a rewrite.
"""
import re
import sqlite3
import time
from .request_plan import PlanError, AmbiguousRequest

SOURCES = {
    'patients': ('patient', None), 'people': ('patient', None), 'members': ('patient', None),
    'ed visits': ('utilization_event', ('kind', 'ed_visit')),
    'er visits': ('utilization_event', ('kind', 'ed_visit')),
    'emergency visits': ('utilization_event', ('kind', 'ed_visit')),
    'inpatient admissions': ('stay', ('type', 'inpatient')),
    'admissions': ('stay', ('type', 'inpatient')), 'hospital stays': ('stay', None),
    # Mike, 2026-10-21: medical-claim finance questions use payer-adjudicated claims, counted rows only.
    'claims': ('payer_claim', ('counts_in_metrics', 1)), 'medical claims': ('payer_claim', ('counts_in_metrics', 1)),
    'visits': ('visit', None), 'appointments': ('appointment', None),
    # Adapter vocabulary, not whole-question recipes. These families have
    # distinct received-source coverage and must never be silently combined.
    'payer-feed medical claims': ('payer_claim', ('counts_in_metrics', 1)),
    'payer medical claims': ('payer_claim', ('counts_in_metrics', 1)),
    'provider medical claims': ('claim', ('claim_source', 'medical_claims')),
    'diabetics': ('patient', ('condition', 'diabetes')),
}
FIELDS = {
    'age': 'patient.age', 'sex': 'patient.sex_reported',
    'pcp': 'patient.pcp_clinician_id', 'practice': 'patient.pcp_practice_id',
    'coverage': 'patient.coverage_type', 'coverage type': 'patient.coverage_type',
    'payer': 'patient.payer_id', 'language': 'patient.preferred_language',
    'allowed cents': 'claim.allowed_cents', 'billed cents': 'claim.billed_cents',
    'net paid cents': 'claim.net_payer_paid_cents', 'length of stay': 'stay.los_days',
    'admission date': 'stay.admit_date',
}
CLOCKS = {'utilization_event': 'event_date', 'visit': 'event_date', 'stay': 'admit_date', 'claim': 'service_date',
          'payer_claim': 'service_from',
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
        self.value_bindings = {}
        self.sources = dict(SOURCES)
        for table in catalog.tables:
            phrase = table.replace('_', ' ')
            self.sources.setdefault(table, (table, None))
            self.sources.setdefault(phrase, (table, None))
            self.sources.setdefault(phrase+'s', (table, None))
        if 'counts_in_metrics' not in catalog.tables.get('payer_claim', []):  # no counted payer claims received
            self.sources.update({'claims': ('claim', None), 'medical claims': ('claim', None),
                                 'payer-feed medical claims': ('claim', ('claim_source', 'payer_medical_claims')),
                                 'payer medical claims': ('claim', ('claim_source', 'payer_medical_claims'))})
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
        clock_aliases = {}
        if self.base == 'claim': fields['payer'] = 'claim.payer'
        if self.base == 'payer_claim': fields['payer'] = 'payer_claim.payer_id'
        for table in ('patient', self.base):
            for column in self.catalog.tables.get(table, []):
                fields[column] = fields[column.replace('_', ' ')] = table+'.'+column
                if column.endswith('_cents'):
                    fields[column[:-6].replace('_',' ')+' amount'] = table+'.'+column
                # An explicitly named clock can omit its storage suffix, e.g.
                # "scheduled in 2029" for scheduled_date. Never choose an
                # unnamed clock merely because it is the only date column.
                if table == self.base and column.endswith(('_date', '_time', '_at')):
                    short = column.rsplit('_', 1)[0].replace('_', ' ')
                    clock_aliases.setdefault(short,set()).add(table+'.'+column)
        for table, columns in self.catalog.tables.items():
            for column in columns: fields[table+'.'+column] = table+'.'+column
        for short, candidates in clock_aliases.items():
            if short in fields:candidates.add(fields[short])
            if len(candidates)==1:fields[short]=next(iter(candidates))
            else:fields.pop(short,None)  # A short clock name cannot choose between two fields.
        return {k: v for k, v in fields.items() if v.split('.')[0] in self.catalog.tables and v.split('.')[1] in self.catalog.tables[v.split('.')[0]]}

    def prefix(self, text, options):
        for phrase in sorted(options, key=len, reverse=True):
            m = re.match(re.escape(phrase)+r'(?![\w.])', text, re.I)
            if m: return m.group(), options[phrase], text[m.end():].lstrip()
        return None

    def field(self, text):
        got = self.field_prefix(text.strip())
        if not got or got[2]: raise PlanError('Unrecognized or ambiguous dimension: '+text)
        return got[1]

    def field_prefix(self, text):
        """Resolve actual schema words with grammatical observation qualifiers.

        The match retains the original span. No generated rewrite, embedding
        similarity or guessed field synonym is an execution authority.
        """
        matches = []
        for phrase, field in self.field_aliases().items():
            if '.' in phrase or '_' in phrase:
                pattern = re.escape(phrase)
            else:
                words = phrase.split()
                pattern = r'(?:(?:recorded|reported|listed|documented)\s+)?' + r'\s+(?:(?:recorded|reported|listed|documented)\s+)?'.join(
                    re.escape(word) for word in words)
                if words[-1] in ('date','time'):
                    pattern += 's?'
            match = re.match(pattern+r'(?![\w.])', text, re.I)
            if match:
                matches.append((match.end(), match.group(), field))
        if not matches: return None
        size = max(m[0] for m in matches)
        best = [m for m in matches if m[0] == size]
        if len({m[2] for m in best}) != 1:
            raise AmbiguousRequest('Which field do you mean by '+best[0][1]+'? Choose '+', '.join(sorted({m[2] for m in best})))
        _, phrase, field = best[0]
        return phrase, field, text[size:].lstrip()

    def observed_literal(self, field, value, *, quoted=False):
        """Resolve an unquoted category's case to a unique received value.

        Explicit quoted literals and entity keys keep exact storage semantics.
        A bounded failed probe never establishes that a value is absent.
        """
        from .request_plan import identifier
        if quoted or not isinstance(value,str):return value
        table,column=field.split('.')
        if column == 'id' or column.endswith(('_id','_key','_code')):return value
        key=(field,value)
        if key in self.value_bindings:return self.value_bindings[key]
        with sqlite3.connect(self.catalog.target.resolve().as_uri()+'?mode=ro',uri=True) as db:
            started=time.monotonic();db.set_progress_handler(lambda:int(time.monotonic()-started>.1),1000)
            try:
                rows=[r[0] for r in db.execute('SELECT DISTINCT '+identifier(column)+' FROM '+identifier(table)+
                     ' WHERE lower('+identifier(column)+')=lower(?) LIMIT 3',(value,))]
            except sqlite3.OperationalError:return value
        if value in rows:return value
        if len(rows)>1:raise AmbiguousRequest('Several case-distinct received values match '+value+'; quote the exact value')
        resolved=rows[0] if rows else value
        self.value_bindings[key]=resolved
        return resolved

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
        # Fronted scope and units are parsed as separate clauses, preserving
        # their original spans rather than generating a rewritten question.
        front = re.match(r'^(?:For|Among)\s+(.+?),\s*((?:what|average|mean|total|sum)\b.+)$',text,re.I)
        front_source = self.prefix(front[1],self.sources) if front else None
        if front and not front_source: raise PlanError('Fronted scope needs a recognized source')
        if front:
            text=front[2]; self.base=front_source[1][0]
        unit = re.search(r',?\s+in\s+(cents|days)\s*$',text,re.I)
        if unit: text=text[:unit.start()].strip()
        lead = re.match(r'(?i)(?:(?:please\s+)|(?:(?:can|could|would|will)\s+you\s+)|(?:tell\s+me\s+)|(?:(?:give|show|return)(?:\s+me)?\s+both\s+)|(?:I\s+(?:want|would like)\s+(?:you\s+to|to)\s+))*'
                        r'(how many|(?:the )?number of|(?:a )?count(?: of)?|tally(?: up)?|list|enumerate|which|show|find|group|break down|what (?:is|was) the total|what (?:is|was) the average|total|sum|average|mean)\s+', text)
        # Business questions often name the entity without an imperative.
        # Retain original spans; do not rewrite the question to add "count".
        if not lead and not self.prefix(text, self.sources):
            raise PlanError('Specify the requested calculation and warehouse source')
        verb = lead.group(1).lower() if lead else 'count'
        rest = text[lead.end():] if lead else text
        fn = ('list' if verb in ('list', 'enumerate', 'which', 'show', 'find') else
              'avg' if verb in ('average', 'mean', 'what is the average', 'what was the average') else
              'sum' if verb in ('total', 'sum', 'what is the total', 'what was the total') else 'count')
        field_name, source = None, None
        rest = re.sub(r'^(?:all|the|our)\s+', '', rest, flags=re.I)
        if fn in ('sum', 'avg'):
            # A field name may itself contain "of" (e.g. length of stay).
            # Resolve the catalog field and source together instead of splitting
            # at the first preposition and silently changing the measure.
            split = None
            for m in re.finditer(r'\s+(?:for|of|from|on|across)\s+', rest, re.I):
                candidate = self.prefix(rest[m.end():], self.sources)
                if not candidate: continue
                self.base = candidate[1][0]
                try: resolved = self.field(rest[:m.start()])
                except PlanError: continue
                split = (rest[:m.start()], rest[m.end():], resolved)
                break
            if split is None:
                # A catalog field supplies its relation: e.g. "average age".
                field = self.field_prefix(rest)
                if not field: raise PlanError('Aggregation needs a recognized field and source')
                field_text, field_name, rest = field
                if front_source:
                    if rest.strip():raise PlanError('Unconsumed measure wording: '+rest)
                    source=front_source
                else:
                    source = (field_text, (field_name.partition('.')[0], None), rest)
            else:
                field_text, rest, field_name = split
        source = source or self.prefix(rest, self.sources)
        if front_source and fn not in ('sum','avg'):
            raise PlanError('Use an explicit measure for a fronted source scope')
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
            measure = {'kind': 'field', 'field': self.base+'.patient_id', 'alias': 'patient_id', 'quote': lead.group().strip() if lead else source_quote}
        elif fn in ('sum', 'avg'):
            measure = {'kind': fn, 'field': field_name, 'alias': fn+'_'+field_name.split('.')[-1], 'quote': [lead.group().strip(), field_text]}
        else:
            measure = {'kind': 'count_distinct' if self.base == 'patient' else 'count', 'alias': 'patients' if self.base == 'patient' else 'count', 'quote': lead.group().strip() if lead else source_quote}
            if self.base == 'patient': measure['field'] = 'patient.patient_id'
        measures.append(measure)
        if unit:
            if field_name is None or not field_name.endswith('_'+unit[1].lower()):
                raise PlanError('Requested units do not match the received measure; explicit conversion needed')
            old=measure['quote'];measure['quote']=([old] if isinstance(old,str) else old)+[unit.group().strip(' ,')]
        return {'source': {'table': self.base, 'quote': source_quote}, 'select': measures, 'where': where,
                'group_by': groups, 'order_by': [], 'limit': None, 'unresolved': []}

    COMPARISON_FRAMES = [  # (head, tail) around one governed measure; the tail/head years are the compared periods
        (r'^compared (?:with|to)\s+(20\d{2}),?\s+(?:did|was|were|is|has)\s+', r'\s+(?:go|gone|went)\s+up\s+or\s+down$'),
        (r'^how did\s+', r'\s+change\s+from\s+(20\d{2})\s+to\s+(20\d{2})$'),
        (r'^year over year,?\s+(?:were|was|did)\s+', r'\s+(?:go\s+)?up\s+or\s+down\s+in\s+(20\d{2})\s+(?:versus|vs\.?|compared (?:with|to))\s+'
         r'(20\d{2})(?:,?\s+and\s+by\s+how\s+much)?$'),
        (r'^', r'\s+(?:in\s+)?(20\d{2})\s+(?:versus|vs\.?|compared (?:with|to))\s+(20\d{2})$'),
    ]

    def comparison_frame(self, text):
        """(measure text, compared years, frame quotes) for an explicit two-period comparison, else None."""
        for head, tail in self.COMPARISON_FRAMES:
            h = re.match(head, text, re.I)
            m = re.search(tail, text[h.end():], re.I) if h else None
            if not m: continue
            years = [y for y in (*h.groups(), *m.groups()) if y]
            inner = text[h.end():h.end() + m.start()]
            return inner, years, [q for q in (h.group().strip(' ,'), m.group().strip(' ,')) if q]
        return None

    def governed(self, text):
        """One metric name plus independently consumed scope/period/group slots."""
        from . import request_vocabulary as V, metrics
        frame = self.comparison_frame(text)
        if frame: text, compared, frame_quotes = frame
        rest = re.sub(r"^(?:(?:what|how much) (?:is|was|are)|what'?s|show(?: me)?|give me|tell me)\s+", '', text, flags=re.I)
        rest = re.sub(r'^(?:(?:the|our|overall)\s+)+', '', rest, flags=re.I)
        # A leading year ("the 2029 denial rate") is the measure period, kept with its original span.
        leading = re.match(r'^(20\d{2})\s+', rest)
        if leading: rest = rest[leading.end():]
        rest = re.sub(r'^(?:(?:the|our|overall)\s+)+', '', rest, flags=re.I)
        # "What share of 2029 claims were denied?" names the same governed rate as "denial rate".
        event = re.match(r'^(?:what\s+|which\s+)?(?:share|percent(?:age)?|proportion|fraction)\s+of\s+(.+?)\s+(?:were|was)\s+('
                         + '|'.join(V.EVENT_METRICS) + r')((?:\s+(?:in|during)\s+20\d{2})?)$', text if not leading else rest, re.I)
        if event:
            ident = next(v for k, v in V.EVENT_METRICS.items() if re.fullmatch(k, event[2], re.I))
            measure_quote, rest = [text[:event.start(1)].strip(), event[2]], event[1] + event[3]
            got = True
        else:
            got = self.prefix(rest, V.aliases())
            if not got: return None
            measure_quote, ident, rest = got
        if leading: rest = rest + ' ' + leading[1]
        # A parenthetical that names a metric restates or narrows it: "medical PMPM (paid per member per month)".
        paren = re.match(r'^\s*\(([^()]+)\)', rest)
        if paren and paren[1].strip().lower() in V.aliases():
            ident = V.aliases()[paren[1].strip().lower()]; measure_quote = [measure_quote, paren.group().strip()]
            rest = rest[paren.end():]
        basis = re.search(r'\s*\bon an? (paid|allowed) basis\b', rest, re.I)
        if basis and ident in V.BASIS_VARIANTS:
            ident = V.BASIS_VARIANTS[ident][basis[1].lower()]
            measure_quote = [*([measure_quote] if isinstance(measure_quote, str) else measure_quote), basis.group().strip()]
            rest = rest[:basis.start()] + rest[basis.end():]
        ident = V.preferred(ident, measure_quote, self.catalog.tables)
        if ident is None: return None
        if re.match(r'^(?:for|of|from)\s+(?:admissions|hospital stays)\b', rest, re.I):
            return None  # explicit field/source aggregation uses the relational grammar
        definition = V.definitions()[ident]
        rest, group_fields, group_quote = self.grouping(rest, {k: k for k in V.GROUPS})
        groups, quotes = [field for _, field in group_fields], list(measure_quote) if isinstance(measure_quote, list) else [measure_quote]
        if group_quote: quotes.append(group_quote)
        # A single explicit period belongs to the measure, never to patient DOB.
        periods = list(re.finditer(
            r'\b(?:(?:in|during|for)\s+)?20\d{2}\b|\b(?:last|past|trailing)\s+(?:12|twelve)\s+months\b|\b(?:last year|this year|ytd)\b',
            syntax_text(rest), re.I))
        if frame:
            years = set(compared)
            for match in reversed(periods):
                year = re.search(r'20\d{2}', match.group())
                if not year: raise PlanError('A comparison needs two calendar years')
                years.add(year.group()); quotes.append(match.group())
                rest = rest[:match.start()] + ' ' + rest[match.end():]
            if len(years) != 2: raise PlanError('A comparison needs exactly two calendar years')
            quotes += frame_quotes
            periods = []
        elif len(periods) > 1: raise PlanError('Multiple periods need an explicit comparison')
        spoken = periods[0].group() if periods else ''
        start, end, _ = metrics.period_for(spoken, self.catalog.identity['as_of'],
                                          definition['time_anchor'].get('default', 'all'))
        if periods:
            match = periods[0]
            quotes.append(match.group())
            rest = rest[:match.start()]+' '+rest[match.end():]
        self.base = 'patient'
        # Wording that restates the measure's own population ("for 2029 appointments", "on medical claims",
        # "for inpatient discharges" when the measure is discharge-anchored) adds no constraint.
        own = V.population_words(definition)
        if own:
            restated = re.search(r'(?:^|\s)(?:(?:for|on|of|among|across)\s+)?(?:(?:the|our|all)\s+)?(?:' + own + r')\b', rest, re.I)
            if restated:
                quotes.append(restated.group().strip())
                rest = rest[:restated.start()] + ' ' + rest[restated.end():]
        where, tail = self.expression(rest.strip())
        if tail.strip(): raise PlanError('Unconsumed metric scope: '+tail)
        self.bind_parent_quotes(where)
        spans = ([metrics.period_for(y, self.catalog.identity['as_of'], 'all')[:2] for y in sorted(years)]
                 if frame else [(start, end)])
        analyses = []
        for s, e in spans:
            params = V.parameters(ident, {'start': s.isoformat(), 'end': e.isoformat()}, groups)
            try: V.validate(params)
            except ValueError as exc: raise PlanError(str(exc)) from exc
            analyses.append({'operation': 'governed_metric', 'parameters': params, 'quote': quotes})
        return {'source': {'table': 'patient', 'quote': measure_quote},
                'select': [{'kind': 'field', 'field': 'patient.patient_id', 'alias': 'patient_id', 'quote': measure_quote}],
                'where': where, 'group_by': [], 'order_by': [], 'limit': None, 'unresolved': [],
                'analyses': analyses}

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
        while True:
            filler = re.match(r'^(?:among|for) (?:the )?(?:patients|members)\s+|^who\s+|^(?:where|for|among)\s+|^that\s+|^(?:are|were|is|was)\s+', text, re.I)
            if not filler: break
            text = text[filler.end():]
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
            return atom('patient.coverage_type', '=', value, quote), re.sub(r'^\s*(?:patients|members)\b', '', tail, flags=re.I)
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
        event = re.match(r'^(?:with|having|have|has|had)\s+(?:an?\s+)?', text, re.I)
        if event:
            got = self.prefix(text[event.end():], self.sources)
            if got and got[1][0] != 'patient':
                _, (table, preset), tail = got
                quote = text[:len(text)-len(tail)].strip()
                return related(table, atom(table+'.'+preset[0], '=', preset[1], quote) if preset else None, quote), tail
        # Generic received-field comparisons. The field vocabulary comes from the
        # catalog, so a newly delivered column does not need a question handler.
        inverse = re.match(r'^(?:have|has|had|with)\s+(.+?)\s+(?:as|for)\s+(?:(?:their|an?|the)\s+)?',text,re.I)
        if inverse:
            field_match=self.field_prefix(text[inverse.end():])
            if field_match:
                _,field,tail=field_match
                literal_text=inverse[1].strip();quoted=len(literal_text)>1 and literal_text[0] in '\"\'' and literal_text[-1]==literal_text[0]
                if not quoted and re.search(r'\b(?:and|or|not|no|without|over|under|above|below)\b|[<>=]',literal_text,re.I):
                    raise AmbiguousRequest('Express that category predicate with an explicit comparison or quoted literal')
                value=self.observed_literal(field,literal_text[1:-1] if quoted else literal_text,quoted=quoted)
                return atom(field,'=',value,start[:len(start)-len(tail)].strip()),tail
        intro = re.match(r'^(?:with|having|have|has|had|whose)\s+(?:(?:an?|the|their)\s+)?', text, re.I)
        field_text = text[intro.end():] if intro else text
        got = self.field_prefix(field_text)
        age = re.match(r'^(?:aged?\s+)?(over|under|older than|younger than|at least|at most)\s+', text, re.I)
        explicit_people = self.base == 'patient' or bool(re.match(r'^(?:among|for) (?:the )?(?:patients|members)\b',start,re.I))
        if not got and age and explicit_people:
            got = ('', 'patient.age', text)
        if got:
            _, field, remainder = got
            period = re.match(r'^(?:(?:is|was|were)\s+)?(?:in|during)\s+(\d{4})(?![\d-])', remainder, re.I)
            if period and field.rsplit('.',1)[1].endswith(('_date','_time','_at')):
                tail = remainder[period.end():]; quote = start[:len(start)-len(tail)].strip(); year = int(period[1])
                return combine('and',[atom(field,'>=',f'{year}-01-01',quote),atom(field,'<',f'{year+1}-01-01',quote)],quote),tail
            comp = re.match(r'^\s*((?:is|was|were)\s+(?:at least|at most|no more than|no less than|more than|fewer than|greater than|less than|over|under|above|below)|is not null|is null|is not|was not|were not|not equal to|equal to|no more than|no less than|more than|fewer than|older than|younger than|is|was|were|of|equals?|>=|<=|!=|>|<|=|at least|at most|over|under|above|below|greater than|less than)\s*', remainder, re.I)
            if comp:
                label = comp.group(1).lower()
                label=re.sub(r'^(?:is|was|were)\s+(?=at least|at most|no more than|no less than|more than|fewer than|greater than|less than|over|under|above|below)','',label)
                ops = {'is not null': 'not_null', 'is null': 'is_null', 'is not': '!=', 'was not':'!=','were not':'!=','not equal to': '!=', 'is': '=', 'was':'=','were':'=','of':'=','equal to':'=', 'equal': '=', 'equals': '=', 'at least': '>=', 'at most': '<=', 'over': '>', 'under': '<', 'above': '>', 'below': '<', 'greater than': '>', 'less than': '<','no more than':'<=','no less than':'>=','more than':'>','fewer than':'<'}
                ops.update({'older than':'>','younger than':'<'})
                op = ops.get(label, label)
                rem = remainder[comp.end():]
                if op in ('is_null', 'not_null'): value, tail = None, rem
                else:
                    value_match = re.match(r'''^(?:"([^"]*)"|'([^']*)'|([+-]?\d+(?:\.\d+)?)(?![\w-])|([^\s()]+))''', rem)
                    if not value_match: raise PlanError('Comparison needs a literal value')
                    value = next(x for x in value_match.groups() if x is not None)
                    if value_match.group(3): value = float(value) if '.' in value else int(value)
                    else:value=self.observed_literal(field,value,quoted=value_match.group(1) is not None or value_match.group(2) is not None)
                    tail = rem[value_match.end():]
                quote = start[:len(start)-len(tail)].strip()
                return atom(field, op, value, quote), tail
        return None, start


def parse(catalog, question, *, bound=False):
    return Parser(catalog, question, bound=bound).parse()


def parse_program(catalog, question, *, bound=False):
    """Coordinate explicitly separate analytical commands without a model.

    Split only before a new analytical imperative outside literals/parentheses.
    Every clause must independently parse; shared temporal/group scope that
    cannot be established from the wording requires clarification.
    """
    mask = syntax_text(question, hide_parentheses=True)
    boundaries = list(re.finditer(r'(?:\s+and\s+|;\s*)(?=(?:the\s+)?(?:number of|count(?: of)?|how many|average|mean|total|sum|list|show|find)\s+)', mask, re.I))
    if not boundaries: return None
    clauses, cursor = [], 0
    for boundary in boundaries:
        clauses.append(question[cursor:boundary.start()].strip());cursor=boundary.end()
    clauses.append(question[cursor:].strip().rstrip('?.').strip())
    requests = [parse(catalog, clause, bound=bound) for clause in clauses]
    def fields(expr):
        if not isinstance(expr,dict):return []
        return ([expr['field']] if 'field' in expr else []) + [f for value in expr.values()
            for child in (value if isinstance(value,list) else [value]) if isinstance(child,dict) for f in fields(child)]
    dated = [any(f.endswith(('_date','_time','_at')) for f in fields(r['where'])) for r in requests]
    if any(dated) and not all(dated):
        for clause, request, has_period in zip(clauses,requests,dated):
            table=request['source']['table']
            if not has_period and any(c.endswith(('_date','_time','_at')) for c in catalog.tables[table]):
                raise AmbiguousRequest('State the date scope for each dated output; it is unclear whether the period applies to '+clause)
    if len({r['source']['table'] for r in requests}) == 1 and len({bool(r['group_by']) for r in requests}) > 1:
        raise AmbiguousRequest('State whether grouping applies to both outputs or only the grouped output')
    return {'nodes':[{'id':f'output_{i+1}','quote':clause,'inputs':[],'request':request}
                     for i,(clause,request) in enumerate(zip(clauses,requests))], 'unresolved':[]}
