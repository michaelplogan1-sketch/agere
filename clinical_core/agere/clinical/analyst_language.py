"""Analyst-wording predicates for the free parser (request_language.Parser), added 2026-10-24 from the independent dev set.

Every phrase here is a named, standard definition an analyst uses without stating its parts. Each builds the same typed leaves the rest of
the grammar builds (so the compiler's literal-coverage and number checks still apply), restates its own definition in the answer's notes, and
consumes a parenthetical restatement only when the restated number IS the definition's number ("positive depression screen (PHQ-9 of 10+)").

  positive screen    "positive depression screen", "screened positive for depression"  -> the latest PHQ-9 is 10 or higher (the published cut-off)
  active coverage    "active coverage as of today's data date", "currently enrolled"    -> an enrollment span open on the as-of date
"""
import re

SCREEN_POSITIVE = [  # (wording, lab concept, operator, cut-off, label, restated-number pattern)
    (r'(?:(?:a|an)\s+)?(?:positive|abnormal)\s+depression\s+screen(?:ing)?(?:\s+results?)?|screened\s+positive\s+for\s+depression|depression\s+screens?\s+(?:is\s+|are\s+)?positive',
     'phq9', '>=', 10, 'PHQ-9 of 10 or higher (the standard positive-screen cut-off)'),
]
RESTATED_NUMBER = r'\s*\(\s*(?:the\s+)?(?:phq[- ]?9)\s*(?:score\s+)?(?:of\s+|>=?\s*|at\s+least\s+)?(\d+)\s*(?:\+|or\s+(?:more|higher|above|greater))?\s*\)'
LATEST_RESULT = r'\s+(?:on|in)\s+(?:their|the|its)\s+(?:latest|most\s+recent|last)\s+(?:result|value|test|reading|measurement|screen(?:ing)?)\b'


class AnalystForms:
    """Methods mixed into request_language.Parser."""

    def screen_positive(self, text):
        from .request_language import atom, combine, related
        if self.base != 'patient' or 'measurement_latest' not in self.catalog.tables: return None
        lead = re.match(r'^(?:(?:have|has|had|with)\s+)?', text, re.I)
        for wording, concept, op, cutoff, label in SCREEN_POSITIVE:
            m = re.match(wording + r'(?![\w-])', text[lead.end():], re.I)
            if not m: continue
            end = lead.end() + m.end()
            restated = re.match(RESTATED_NUMBER, text[end:], re.I)
            if restated:
                if int(restated[1]) != cutoff: return None  # a different cut-off is a different question
                end += restated.end()
            latest = re.match(LATEST_RESULT, text[end:], re.I)  # the decision context already reads the latest result
            if latest: end += latest.end()
            quote = text[:end].strip()
            self.extra_notes.append(f'A positive screen is read as the latest {label}.')
            self.extra_notes.append('PHQ-9 screening-threshold reference: Kroenke et al. (2001), https://pubmed.ncbi.nlm.nih.gov/11556941/; a positive screen is not a diagnosis.')
            return related('measurement_latest', combine('and', [atom('measurement_latest.concept', '=', concept, quote),
                                                                  atom('measurement_latest.value_canonical', op, cutoff, quote)], quote), quote), text[end:]
        return None

    COVERAGE_ACTIVE = re.compile(r"^(?:(?:have|has|with)\s+(?:an?\s+)?(?:active|current)\s+(?:(?:insurance|health\s+plan|health)\s+)?(?:coverage|enrollment|eligibility|insurance)|"
                                 r"(?:are|is)\s+(?:currently\s+|actively\s+)?enrolled)"
                                 r"(?:\s+as\s+of\s+(?:today(?:'s|’s)?(?:\s+data\s+date)?|the\s+(?:data|as[- ]of|snapshot)\s+date|the\s+as[- ]of\s+date))?(?![\w-])", re.I)

    def active_coverage(self, text):
        from .request_language import atom, combine, related
        if self.base != 'patient' or 'enrollment_span' not in self.catalog.tables: return None
        m = self.COVERAGE_ACTIVE.match(text)
        cols = self.catalog.tables['enrollment_span']
        as_of = self.catalog.identity.get('as_of')
        if not m or not as_of or not {'coverage_start', 'coverage_end'} <= set(cols): return None
        quote = m.group().strip()
        span = combine('and', [atom('enrollment_span.coverage_start', '<=', as_of, quote),
                               atom('enrollment_span.coverage_end', 'null_or_gte', as_of, quote)], quote)  # no end date, or an end on or after the as-of date
        self.extra_notes.append(f'Active coverage means an enrollment span that started on or before {as_of} and has no end date or ends on or after it.')
        return related('enrollment_span', span, quote), text[m.end():]

    BOTH_LEAD = re.compile(r'^(?:(?:have|has|had)\s+)?(?:(?:ever\s+)?(?:got|gotten|received|completed|underwent|had)\s+)?both\s+(?:an?\s+)?', re.I)

    def both_evidence(self, text):
        """"got BOTH an eGFR and a urine albumin-creatinine ratio in 2029": two received tests, the trailing period applying to each (a period stated once
        after a conjoined pair covers the pair)."""
        from .request_language import atom, combine, related
        if self.base != 'patient': return None
        lead = self.BOTH_LEAD.match(text)
        if not lead: return None
        first = self.thing_evidence(text[lead.end():])
        if not first: return None
        sep = re.match(r'^\s*(?:and|plus)\s+(?:an?\s+)?', first[4], re.I)
        second = self.thing_evidence(first[4][sep.end():]) if sep else None
        if not second: return None
        window_, tail = self.window(second[4], second[0], second[1])
        quote = text[:len(text) - len(tail)].strip()
        leaves = []
        for i, (found, join) in enumerate(((first, lead.group().strip()), (second, sep.group().strip()))):
            table, field, atoms, _, _ = found
            win = window_ if i == 1 else self.window(second[4], table, field)[0]
            atoms = [dict(a) for a in atoms]
            atoms[0] = {**atoms[0], 'quote': [join, *([atoms[0]['quote']] if isinstance(atoms[0]['quote'], str) else atoms[0]['quote'])]}
            leaves.append(related(table, combine('and', [*atoms, *win], quote), quote))
        return combine('and', leaves, quote), tail

    # -- the share of an EVENT population: "what share of 2029 ED visits were followed by an office visit within 7 days" --------------------------------
    EVENT_SHARE_HEAD = re.compile(r'^(?:what|which)\s+(?:is\s+|was\s+|are\s+|were\s+)?(?:the\s+|our\s+)?(?:overall\s+)?(?:share|percent(?:age)?|proportion|fraction)\s+of\s+', re.I)

    def event_share(self, text):
        """The ratio of event rows (ED visits, admissions, discharges, appointments, claims) that meet a predicate over all the rows of the period: the period and
        the event's own definition define both sides, the predicate only the numerator."""
        from .request_language import CLOCKS, PlanError, combine
        head = self.EVENT_SHARE_HEAD.match(text.strip().rstrip('?.'))
        if not head: return None
        body = text.strip().rstrip('?.')[head.end():]
        lead_year = re.match(r'^((?:19|20)\d\d)\s+', body)
        rest = body[lead_year.end():] if lead_year else body
        rest = re.sub(r'^(?:(?:the|our|all)\s+)+', '', rest, flags=re.I)
        source = self.prefix(rest, self.sources)
        if not source or source[1][0] in ('patient', 'clinician') or not CLOCKS.get(source[1][0]): return None
        quote, (table, default), tail = source
        self.base = table
        if table == 'stay' and re.search(r'discharge', quote, re.I): self.clock = 'discharge_date'  # "discharges" are dated by their discharge
        clock = self.clock or CLOCKS[table]
        base_filter = self.preset_filter(default, quote) if default else None
        period = []
        if lead_year:
            value, left = self.one(lead_year[1])
            if value is None or left.strip(): return None
            period.append(value)
        try:
            where, rest_ = self.expression(tail)
        except PlanError:
            return None
        if where is None or rest_.strip(): return None
        parts = list(where['args']) if isinstance(where, dict) and where.get('op') == 'and' else [where]
        field = f'{table}.{clock}'

        def is_period(node):
            if not isinstance(node, dict): return False
            if node.get('field') == field and node.get('op') == 'within_last_months': return True
            return node.get('op') == 'and' and len(node.get('args', [])) == 2 and all(a.get('field') == field and a.get('op') in ('>=', '<') for a in node['args'])
        period += [p for p in parts if is_period(p)]
        predicate = [p for p in parts if not is_period(p)]
        if not predicate: return None
        numerator_where = combine('and', predicate, text)
        lead = head.group().strip()
        ratio = {'kind': 'ratio', 'alias': 'percent', 'quote': [lead], 'scale': 100, 'round': 2,
                 'numerator': {'kind': 'count', 'alias': 'meeting', 'quote': [lead], 'where': numerator_where},
                 'denominator': {'kind': 'count', 'alias': 'group', 'quote': [lead]}}
        plan = {'source': {'table': table, 'quote': quote}, 'select': [ratio], 'where': combine('and', [base_filter, *period], self.question) if (base_filter or period) else None,
                'group_by': [], 'order_by': [], 'limit': None, 'unresolved': [],
                'notes': [f'Share of {quote} rows meeting the condition over all {quote} rows of the period (numerator and denominator are stated in the counts).']}
        self.bind_parent_quotes(plan['where']); self.bind_parent_quotes(numerator_where)
        return plan

    # -- "average SVI for commercial, Medicare and Medicaid patients": the measure for each named group ---------------------------------------------
    CATEGORIES_FOR = re.compile(r"^(?P<head>.+?)\s+(?:for|among|in)\s+(?P<cats>[A-Za-z][\w-]*(?:\s+[\w-]+){0,2}(?:\s*,\s*(?:and\s+|or\s+)?[A-Za-z][\w-]*(?:\s+[\w-]+){0,2}|\s+(?:and|or)\s+[A-Za-z][\w-]*(?:\s+[\w-]+){0,2}){1,})\s+(?:patients|members)$", re.I)

    def measure_for_categories(self, text):
        """A request followed by a list of named groups of patients ("average SVI for commercial, Medicare and Medicaid patients"): the request's measure
        per group (a grouping by the one column the groups are values of, else named cohort groups). Declines unless every item is one recognised group."""
        from .request_language import PlanError
        t = text.strip().rstrip('?.')
        m = self.CATEGORIES_FOR.match(t)
        if not m: return None
        try:
            plan = self.parse(m['head'], governed=False)
        except PlanError:
            return None
        if plan.get('analyses') or plan.get('group_by') or plan.get('having') or plan.get('order_by') or any(e.get('kind') == 'field' for e in plan['select']): return None
        table = plan['source']['table']
        groups = self.named_groups(m['cats'], 'patient' if table == 'patient' else table)
        if not groups: return None
        fields = {g['where'].get('field') for g in groups if isinstance(g['where'], dict)}
        alias = 'payer_type' if fields == {'patient.coverage_type'} else 'group'
        lead = t[:m.start('head')] + t[m.start('head'):m.end('head')]
        return self.attach_groups(plan, groups, [t[m.end('head'):m.start('cats')].strip()], alias=alias)

    # -- "what share of brand pharmacy claims were rejected, versus generic": the second group is named alone ----------------------------------------
    VERSUS_TAIL = re.compile(r"\s*,?\s*\b(?:versus|vs\.?|compared\s+(?:with|to))\s+(?P<b>[A-Za-z][\w -]{1,40}?)\s*$", re.I)

    def contrast_ellipsis(self, text):
        """"<measure of A rows>, versus B": the measure per group, A and B being two values of one claim column (brand / generic, in / out of network)."""
        from .request_language import PlanError
        t = text.strip().rstrip('?.')
        m = self.VERSUS_TAIL.search(t)
        if not m: return None
        head = t[:m.start()]
        table = self.claim_table(head)
        if not table: return None
        self.base = table
        other = self.claim_conditions(table, m['b'].strip()) if hasattr(self, 'claim_conditions') else []
        if len(other) != 1: return None
        column = other[0][0][0]['field']
        mine = [p for p in self.claim_predicates(table, head) if p[1][0]['field'] == column]
        if len(mine) != 1: return None
        start, atoms_, quote = mine[0]
        at = head.lower().find(quote.lower())
        stripped = head[:at].rstrip() + ' ' + head[at + len(quote):].lstrip()
        try:
            plan = self.parse(stripped, governed=False)
        except PlanError:
            return None
        groups = self.named_groups(f'{quote} versus {m["b"].strip()}', table)
        if not groups: return None
        return self.attach_groups(plan, groups, [m.group().strip(' ,')])
