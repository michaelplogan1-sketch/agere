"""Claims, pharmacy and spend forms for the free parser (request_language.Parser), added 2026-10-23.

General shapes of finance and utilization questions over the counted payer claims and the pharmacy claims. Each builds the same typed
request the rest of the grammar builds, so the compiler's literal-coverage check applies: every word of the question must sit in a
quoted fragment of the request or be a function word, and every number must be compiled.

  amount            "what did we pay out in medical claims in 2029 by line of business"       -> paid dollars, grouped
  share of amount   "what percentage of our 2029 medical spend went out of network"            -> ratio of paid dollars
  average amount    "what's the average paid amount on an ED professional claim in 2029"       -> average over paid claims
  rate of rows      "which payer has the highest denial rate in 2029 (payers with at least 500 claims)"  -> ratio of claims

Definitions used (each is stated in the answer's limitations):
  * medical claims are the payer-adjudicated claims with counts_in_metrics = 1 (current, non-duplicate version of each claim), dated by
    service_from; money is paid_cents / 100 (reversals and recoupments net out in paid_cents);
  * an ED claim is a claim with place of service 23 (emergency room, hospital); hospital claims are institutional claims;
  * the average paid amount is over claims whose final status is paid (a denied claim is not a payment of zero);
  * a rate of claims is claims meeting the condition over all counted claims, except where a rate has its own denominator below.
"""
import re

SRC_PHRASE = re.compile(r'\b(?:medical\s+claims?|payer[- ]feed\s+medical\s+claims|claims?|pharmacy\s+claims?|prescription\s+claims?|medical\s+spend(?:ing)?|'
                        r'paid\s+spend|spend(?:ing)?|pharmacy\s+spend(?:ing)?)\b', re.I)
YEAR = re.compile(r'\b(20\d\d)\b')
MONEY = {'payer_claim': ('paid_cents', 'paid_cents'), 'payer_pharmacy_claim': ('plan_paid_cents', 'plan_paid_cents')}  # cents in the plan; answer_format shows dollars
DEFAULT_CLAIM_FILTER = {'payer_claim': ('counts_in_metrics', '=', 1)}
CLOCK = {'payer_claim': 'service_from', 'payer_pharmacy_claim': 'fill_date'}

# phrase -> conditions on the claim row ((column, operator, value), ...). Data, not question recipes.
CLAIM_PREDICATES = {
    'payer_claim': [
        (r'out[- ]of[- ]network', (('network_status', '=', 'out_of_network'),)),
        (r'in[- ]network', (('network_status', '=', 'in_network'),)),
        (r'(?:hospital|institutional)(?:\s*\(institutional\))?(?:\s+claims?)?', (('claim_type', '=', 'institutional'),)),
        (r'(?:professional|physician)(?:\s+claims?)?', (('claim_type', '=', 'professional'),)),
        (r'(?:ED|emergency(?:\s+(?:department|room))?)', (('place_of_service', '=', '23'),)),
        (r'(?:denied|denials?)', (('final_status', 'in', ['denied', 'denied_upheld']),)),
    ],
    'payer_pharmacy_claim': [
        (r'rejected', (('claim_status', '=', 'rejected'),)),
        (r'generic', (('formulary_tier', '=', 'generic'),)),
        (r'brand(?:[- ]name)?', (('formulary_tier', '=', 'brand'),)),
        (r'specialty', (('formulary_tier', '=', 'specialty'),)),
    ],
}

# a rate of rows: name -> (table, numerator conditions, denominator conditions, label). The denominator is the rows the rate is defined over.
ROW_RATES = [
    (r'(?:first[- ]pass\s+)?(?:claim\s+)?denial', 'payer_claim', (('final_status', 'in', ['denied', 'denied_upheld']),), (), 'denial rate'),
    (r'no[- ]show', 'appointment', (('final_status', '=', 'no_show'),), (), 'no-show rate'),
    (r'(?:pharmacy\s+)?(?:claim\s+)?reject(?:ion)?', 'payer_pharmacy_claim', (('claim_status', '=', 'rejected'),),
     (('claim_status', 'in', ['paid', 'rejected']),), 'reject rate'),
    (r'generic\s+dispensing|generic\s+fill', 'payer_pharmacy_claim', (('formulary_tier', '=', 'generic'),),
     (('counts_as_fill', '=', 1), ('formulary_tier', 'not_null', None)), 'generic dispensing rate'),
    (r'out[- ]of[- ]network', 'payer_claim', (('network_status', '=', 'out_of_network'),), (), 'out-of-network rate'),
]
HEAD = r"(?:what|how\s+much)"
VERB = r'(?:went|go|goes|going|is|are|was|were|comes?|came|from)'


class ClaimForms:
    """Methods mixed into request_language.Parser."""

    # -- helpers ---------------------------------------------------------------------------------------------------------
    def claim_table(self, text, default=False):
        """The claims relation a text is about: pharmacy words -> pharmacy claims; claim / spend words -> medical claims; else None (or medical
        claims when the lead itself is about paying, default=True). A question about patients never becomes a claims question."""
        if re.search(r'pharmacy|prescription', text, re.I): t = 'payer_pharmacy_claim'
        elif default or re.search(r'\bclaims?\b|\bspend(?:ing)?\b|\bpaid\s+(?:amount|dollars)\b|\bpayments?\b', text, re.I): t = 'payer_claim'
        else: return None
        return t if t in self.catalog.tables else None

    def has_columns(self, table, *columns):
        return all(c in self.catalog.tables.get(table, []) for c in columns)

    def year_atoms(self, table, text):
        """Atoms for a calendar year named in the text ("in 2029" / "2029 medical spend"): -> (atoms, quote) or ([], None)."""
        from .request_language import atom
        m = YEAR.search(text)
        if not m or CLOCK[table] not in self.catalog.tables[table]: return [], None
        year = int(m[1]); field = f'{table}.{CLOCK[table]}'
        lead = re.search(r'(?:\b(?:in|during|for|of)\s+)?' + m[1], text, re.I)
        q = lead.group() if lead and lead.group().strip() == m[1] else m[1]
        q = m[1]
        return [atom(field, '>=', f'{year}-01-01', q), atom(field, '<', f'{year + 1}-01-01', q)], q

    def claim_predicates(self, table, text):
        """Every claim condition named in the text (search, not prefix): -> [(atoms, quote)] in text order."""
        from .request_language import atom
        out = []
        for pattern, conditions in CLAIM_PREDICATES.get(table, []):
            for m in re.finditer(r'(?<![\w-])(?:' + VERB + r'\s+)?' + pattern + r'(?![\w-])', text, re.I):
                if not all(c in self.catalog.tables[table] for c, _, _ in conditions): continue
                q = m.group().strip()
                out.append((m.start(), [atom(f'{table}.{c}', op, v, q) for c, op, v in conditions], q))
        out.sort(key=lambda x: x[0])
        return out

    def base_claim_atoms(self, table, quote):
        from .request_language import atom
        col = DEFAULT_CLAIM_FILTER.get(table)
        return [atom(f'{table}.{col[0]}', col[1], col[2], quote)] if col and col[0] in self.catalog.tables[table] else []

    def cut_group(self, text, table):
        """Take a trailing / inline "by <dimension>" out of the text -> (text, [(quote, field)], group quote)."""
        self.base = table
        dims = {**self.field_aliases()}
        try:
            rest, groups, quote = self.grouping(text, dims)
        except Exception:  # noqa: BLE001 - an unknown dimension is not a grouping this form knows
            return text, [], None
        return rest, groups, quote

    # -- amounts ---------------------------------------------------------------------------------------------------------
    AMOUNT_LEAD = re.compile(r"^(?:" + HEAD + r")\s+(?:did|do|have|has)\s+we\s+(?:pay|paid|spend|spent)(?:\s+out)?\b", re.I)

    def amount_form(self, text):
        """"What did we pay out in medical claims in 2029 by line of business?" -> paid dollars, optionally grouped."""
        from .request_language import combine
        lead = self.AMOUNT_LEAD.match(text)
        if not lead: return None
        rest = text[lead.end():]
        table = self.claim_table(rest, default=True)
        if not table or table not in MONEY or MONEY[table][0] not in self.catalog.tables[table]: return None
        self.base = table
        rest, groups, group_quote = self.cut_group(rest, table)
        source = SRC_PHRASE.search(rest)
        year_atoms, year_quote = self.year_atoms(table, rest)
        preds = self.claim_predicates(table, rest)
        src_quote = source.group() if source else lead.group().strip()
        where = [*self.base_claim_atoms(table, src_quote), *year_atoms]
        for _, atoms_, q in preds: where.append(combine('and', atoms_, q))
        field, alias = MONEY[table]
        measure = {'kind': 'sum', 'field': f'{table}.{field}', 'alias': alias,
                   'quote': [lead.group().strip(), *([source.group()] if source else [])]}
        select = [{'kind': 'field', 'field': f, 'alias': f.partition('.')[2], 'quote': w} for w, f in groups]
        if select and group_quote: select[0]['quote'] = [select[0]['quote'], group_quote]
        return {'source': {'table': table, 'quote': src_quote}, 'select': [*select, measure], 'where': combine('and', where, self.question),
                'group_by': [e['alias'] for e in select], 'order_by': [], 'limit': None, 'unresolved': []}

    # -- share of an amount ----------------------------------------------------------------------------------------------
    AMOUNT_SHARE = re.compile(r"^(?P<head>(?:what|which)\s+(?:percent(?:age)?|share|proportion|fraction)\s+of|how\s+much\s+of)\s+(?:our\s+|the\s+)?(?P<rest>.+)$", re.I)
    AMOUNT_NOUN = re.compile(r"\b(?:(?:medical|pharmacy|paid|total)\s+)*(?:spend(?:ing)?|paid\s+(?:spend|amount|dollars)|payments?)\b", re.I)

    def amount_share(self, text):
        """"What percentage of our 2029 medical spend went out of network?" -> paid dollars meeting the condition over all paid dollars."""
        from .request_language import combine
        m = self.AMOUNT_SHARE.match(text)
        if not m: return None
        rest = m['rest']
        noun = self.AMOUNT_NOUN.search(rest)
        if not noun: return None
        table = self.claim_table(rest)
        if not table or table not in MONEY or MONEY[table][0] not in self.catalog.tables[table]: return None
        self.base = table
        preds = self.claim_predicates(table, rest)
        if not preds: return None
        year_atoms, _ = self.year_atoms(table, rest)
        field, alias = MONEY[table]
        src_quote = noun.group()
        where = [*self.base_claim_atoms(table, src_quote), *year_atoms]
        num_where = combine('and', [combine('and', a, q) for _, a, q in preds], self.question)
        head = m['head']
        money = lambda kind_alias, w=None: {'kind': 'sum', 'field': f'{table}.{field}', 'alias': kind_alias, 'quote': [head, src_quote], **({'where': w} if w else {})}
        ratio = {'kind': 'ratio', 'alias': 'percent', 'quote': [head, src_quote], 'scale': 100, 'round': 2,
                 'numerator': money('meeting', num_where), 'denominator': money('total')}
        return {'source': {'table': table, 'quote': src_quote}, 'select': [ratio], 'where': combine('and', where, self.question),
                'group_by': [], 'order_by': [], 'limit': None, 'unresolved': [],
                'notes': ['Share of paid dollars on counted payer-adjudicated claims (counts_in_metrics = 1), by service date.']}

    # -- average amount --------------------------------------------------------------------------------------------------
    AVG_LEAD = re.compile(r"^(?:what(?:'s|\s+is|\s+was)|how\s+much\s+(?:is|was))\s+(?:the\s+|our\s+)?average\s+(?P<kind>paid|allowed|billed)?\s*(?:amount\s+)?"
                          r"(?:on|per|for)\s+(?:an?\s+|the\s+|each\s+)?(?P<rest>.+)$", re.I)

    def average_amount(self, text):
        """"What's the average paid amount on an ED professional claim in 2029?" -> mean paid dollars over paid claims that match."""
        from .request_language import combine, atom
        m = self.AVG_LEAD.match(text)
        if not m: return None
        rest = m['rest']
        table = self.claim_table(rest)
        if table != 'payer_claim' or not self.has_columns(table, 'paid_cents', 'allowed_cents', 'charge_cents', 'final_status'): return None
        self.base = table
        kind = (m['kind'] or 'paid').lower()
        field = {'paid': 'paid_cents', 'allowed': 'allowed_cents', 'billed': 'charge_cents'}[kind]
        noun = re.search(r'\b(?:claims?|visits?)\b', rest, re.I)
        year_atoms, _ = self.year_atoms(table, rest)
        preds = self.claim_predicates(table, rest)
        src_quote = noun.group() if noun else (preds[-1][2] if preds else rest.split()[0])
        where = [*self.base_claim_atoms(table, src_quote), *year_atoms, *[combine('and', a, q) for _, a, q in preds]]
        notes = ['Average over counted payer-adjudicated claims by service date.']
        if kind == 'paid':
            where.append(atom('payer_claim.final_status', '=', 'paid', src_quote))
            notes.append('The average paid amount is over claims whose final status is paid (a denied claim is not a payment of zero).')
        head = text[:m.start('rest')].strip()
        measure = {'kind': 'avg', 'field': f'payer_claim.{field}', 'alias': f'avg_{kind}_cents', 'quote': head}
        return {'source': {'table': table, 'quote': src_quote}, 'select': [measure], 'where': combine('and', where, self.question),
                'group_by': [], 'order_by': [], 'limit': None, 'unresolved': [], 'notes': notes}

    # -- rate of rows, optionally grouped and ranked -----------------------------------------------------------------------
    RANK_RATE = re.compile(r"^(?:which|what)\s+(?:(?P<n>\d+|one|two|three|four|five|six|seven|eight|nine|ten)\s+)?(?P<dim>[a-z][a-z ]*?)\s+"
                           r"(?:has|have|had)\s+the\s+(?P<sup>highest|lowest|best|worst)\s+(?P<rate>[a-z -]+?)\s+rate\b(?P<rest>.*)$", re.I)

    def rank_rate(self, text):
        """"Which payer has the highest denial rate in 2029 (payers with at least 500 claims)?" -> the rate per group, ordered, limited, small groups dropped."""
        from .request_language import combine, atom
        from .population_language import SUPERLATIVE, number_of
        m = self.RANK_RATE.match(text)
        if not m: return None
        found = next(((t, n, d, label) for pat, t, n, d, label in ROW_RATES if re.fullmatch(pat, m['rate'].strip(), re.I) and t in self.catalog.tables), None)
        if not found: return None
        table, numer, denom, label = found
        self.base = table
        dim = self.dimension(m['dim'])
        if not dim: return None
        quote_dim, field = dim
        key = field.partition('.')[2]
        tail = m['rest']
        floor = re.search(r"[(,]?\s*(?:[a-z]+\s+)?(?:with|having)\s+at\s+least\s+(\d[\d,]*)\s+([a-z]+)\)?", tail, re.I)
        year_atoms, _ = self.year_atoms(table, tail)
        head = text[:m.start('rest')].strip()
        base = self.base_claim_atoms(table, head)
        cond = lambda spec, q: [atom(f'{table}.{c}', op, v, q) for c, op, v in spec if c in self.catalog.tables[table]]
        den_where = combine('and', cond(denom, head), head) if denom else None
        row = {'kind': 'count', 'alias': 'rows', 'quote': head, **({'where': den_where} if den_where else {})}
        ratio = {'kind': 'ratio', 'alias': 'proportion', 'quote': head, 'round': 4,
                 'numerator': {'kind': 'count', 'alias': 'meeting', 'quote': head, 'where': combine('and', cond(numer, head) + cond(denom, head), head)},
                 'denominator': {'kind': 'count', 'alias': 'group', 'quote': head, **({'where': den_where} if den_where else {})}}
        n = number_of(m['n']) if m['n'] else (None if m['dim'].rstrip().lower().endswith('s') else 1)
        plan = {'source': {'table': table, 'quote': head}, 'select': [{'kind': 'field', 'field': field, 'alias': key, 'quote': quote_dim}, ratio, row],
                'where': combine('and', [*base, *year_atoms], self.question), 'group_by': [key],
                'order_by': [{'alias': 'proportion', 'direction': SUPERLATIVE[m['sup'].lower()], 'quote': head}],
                'limit': {'value': n, 'quote': head} if n else None, 'unresolved': []}
        if floor:
            plan['having'] = atom('result.rows', '>=', int(floor[1].replace(',', '')), floor.group().strip(' ,()'))
        return plan

    # -- shares and rates of rows ----------------------------------------------------------------------------------------
    ROW_SHARE = re.compile(r"^(?P<head>(?:what|which)\s+(?:percent(?:age)?|share|proportion|fraction)\s+of)\s+(?:our\s+|the\s+)?(?P<rest>.+)$", re.I)
    RATE_ON = re.compile(r"^(?:what(?:'s|\s+is|\s+was)\s+)?(?:the\s+|our\s+)?(?P<rate>[a-z][a-z -]*?)\s+rate\s+(?:on|for|of|across|in)\s+(?P<rest>.+)$", re.I)

    def row_rate(self, spec, head_quote, rest, text):
        from .request_language import combine, atom
        table, numer, denom, label = spec
        self.base = table
        year_atoms, _ = self.year_atoms(table, rest) if table in CLOCK else ([], None)
        cond = lambda s_, q: [atom(f'{table}.{c}', op, v, q) for c, op, v in s_ if c in self.catalog.tables[table]]
        noun = SRC_PHRASE.search(rest) or re.search(r'\b(?:appointments?|fills?)\b', rest, re.I)
        src_quote = noun.group() if noun else head_quote
        base = self.base_claim_atoms(table, src_quote)
        den_where = combine('and', cond(denom, src_quote), src_quote) if denom else None
        ratio = {'kind': 'ratio', 'alias': 'percent', 'quote': [head_quote], 'scale': 100, 'round': 2,
                 'numerator': {'kind': 'count', 'alias': 'meeting', 'quote': [head_quote], 'where': combine('and', cond(numer, head_quote) + cond(denom, head_quote), head_quote)},
                 'denominator': {'kind': 'count', 'alias': 'group', 'quote': [head_quote], **({'where': den_where} if den_where else {})}}
        return {'source': {'table': table, 'quote': src_quote}, 'select': [ratio], 'where': combine('and', [*base, *year_atoms], self.question),
                'group_by': [], 'order_by': [], 'limit': None, 'unresolved': [],
                'notes': [f'{label.capitalize()}: rows meeting the condition over the rows the rate is defined on (denominator stated in the counts).']}

    def row_share(self, text):
        """"What share of pharmacy claims were rejected in 2029?" / "What's our generic dispensing rate on pharmacy claims in 2029?"."""
        from .request_language import combine
        m = self.ROW_SHARE.match(text)
        if m:
            rest = m['rest']
            table = self.claim_table(rest)
            if not table: return None
            preds = self.claim_predicates(table, rest)
            if len(preds) != 1: return None
            _, atoms_, q = preds[0]
            spec = next(((t, n, d, label) for pat, t, n, d, label in ROW_RATES
                         if t == table and [(c, op, v) for c, op, v in n] == [(a['field'].split('.')[1], a['op'], a['value']) for a in atoms_]), None)
            if not spec:
                spec = (table, tuple((a['field'].split('.')[1], a['op'], a['value']) for a in atoms_), (), 'share')
            plan = self.row_rate(spec, m['head'], rest, text)
            # the condition's own words (the verb and phrase) sit in the numerator's quote
            plan['select'][0]['numerator']['quote'] = [m['head'], q]
            plan['select'][0]['numerator']['where'] = combine('and', atoms_ + cond_atoms(self, table, spec[2], m['head']), q)
            return plan
        m = self.RATE_ON.match(text)
        if m:
            spec = next(((t, n, d, label) for pat, t, n, d, label in ROW_RATES if re.fullmatch(pat, m['rate'].strip(), re.I) and t in self.catalog.tables), None)
            if not spec: return None
            head = text[:m.start('rest')].strip()
            return self.row_rate(spec, head, m['rest'], text)
        return None


def cond_atoms(parser, table, spec, quote):
    from .request_language import atom
    return [atom(f'{table}.{c}', op, v, quote) for c, op, v in spec if c in parser.catalog.tables[table]]


# purchased-SDOH vendor attributes (the patient's latest matched delivery, sdoh_latest). A 1-10 vendor risk score at or above this is "high".
VENDOR_HIGH_RISK = 7
SDOH_PHRASES = [
    (r'(?:also\s+)?(?:live|living|reside|residing)\s+in\s+(?:a\s+|an\s+)?(?:low[- ]food[- ]access|food[- ]desert)\s+(?:areas?|neighborhoods?|tracts?)',
     (('food_access_low', '=', 1),), 'low food access'),
    (r'(?:also\s+)?(?:have|has|with)\s+(?:a\s+)?(?:high|elevated)\s+transportation\s+(?:risk|barrier)s?', (('transportation_risk_score', '>=', VENDOR_HIGH_RISK),),
     'high transportation risk'),
    (r'(?:also\s+)?(?:have|has|with)\s+(?:a\s+)?(?:high|elevated)\s+financial\s+stress', (('financial_stress_score', '>=', VENDOR_HIGH_RISK),), 'high financial stress'),
    (r'(?:also\s+)?(?:have|has|with)\s+(?:a\s+)?(?:high|elevated)\s+housing\s+instability', (('housing_instability_score', '>=', VENDOR_HIGH_RISK),), 'high housing instability'),
    (r'(?:also\s+)?(?:have|has|with)\s+(?:a\s+)?(?:high|elevated)\s+social\s+isolation', (('social_isolation_score', '>=', VENDOR_HIGH_RISK),), 'high social isolation'),
]
SDOH_SOURCE = re.compile(r'^\s+(?:according\s+to|per|based\s+on|from|in)\s+the\s+(?:purchased\s+)?(?:sdoh|social\s+determinants?(?:\s+of\s+health)?|vendor)\s+data\b', re.I)


class SdohForms:
    """Mixed into Parser: predicates over the purchased SDOH data."""

    def sdoh_predicate(self, text):
        from .request_language import atom, combine, related
        if self.base != 'patient' or 'sdoh_latest' not in self.catalog.tables: return None
        for pattern, conditions, label in SDOH_PHRASES:
            m = re.match(pattern, text, re.I)
            if not m or not all(c in self.catalog.tables['sdoh_latest'] for c, _, _ in conditions): continue
            tail = text[m.end():]
            source = SDOH_SOURCE.match(tail)
            if source: tail = tail[source.end():]
            quote = text[:len(text) - len(tail)].strip()
            leaf = related('sdoh_latest', combine('and', [atom(f'sdoh_latest.{c}', op, v, quote) for c, op, v in conditions], quote), quote)
            self.extra_notes.append(f"Purchased SDOH: each patient's latest matched delivery (exact or partial address match); {label}"
                                    + (f' means a vendor score of {VENDOR_HIGH_RISK} or more (1-10 scale).' if label.startswith('high') else '.'))
            return leaf, tail
        return None


class DefinedForms:
    """Mixed into Parser: a question that names a group and defines it in the same breath."""

    DEFINED = re.compile(r"^(?P<lead>(?:who|which\s+(?:patients|members))\s+(?:are|were|is)\s+(?:our|the|these)\s+[^—–,]+?)\s*(?:—|–|--)\s*(?P<def>.+)$", re.I)

    def defined_term_form(self, text):
        """"Who are our frequent ED users -- patients with 3 or more ED visits in the last year?": the dash introduces the definition; the
        named group's own words are bound to the definition's predicate, so nothing in the question is left unaccounted."""
        from .request_language import PlanError
        m = self.DEFINED.match(text)
        if not m: return None
        try:
            plan = self.parse(m['def'])
        except PlanError:
            return None
        where = plan.get('where')
        if len(plan['select']) != 1 or plan['select'][0]['kind'] != 'count_distinct' or plan.get('group_by') or not isinstance(where, dict) or where.get('op') == 'concept':
            return None
        measure = plan['select'][0]
        plan['select'] = [{'kind': 'field', 'field': 'patient.patient_id', 'alias': 'patient_id', 'quote': measure['quote']}]
        lead = m['lead'].strip()
        quote = where['quote']
        where['quote'] = [lead, *([quote] if isinstance(quote, str) else quote)]
        return plan

    # -- patients whose total spend passes a stated amount -------------------------------------------------------------------
    HIGH_COST = re.compile(r"^(?P<lead>who\s+are\s+(?:our|the)\s+[^,]*?(?:claimants?|members|patients)\b[^,]*?)\s*(?:(?:for|in|during)\s+(?P<year>20\d\d))?\s*,?\s*"
                           r"(?:meaning|defined\s+as|i\.e\.,?|that\s+is)\s+(?P<amount>\$\s?[\d,]+(?:\.\d+)?)(?P<k>\s*k\b)?\s*(?P<cmp>or\s+more|or\s+higher|\+|and\s+(?:up|above))?\s*(?P<basis>paid|allowed|billed)\b$", re.I)

    def high_cost_form(self, text):
        """"Who are the high-cost claimants for 2029, meaning $50,000 or more paid?" -> patients whose summed paid dollars in the year reach the amount."""
        from .request_language import atom, combine
        m = self.HIGH_COST.match(text)
        if not m or 'payer_claim' not in self.catalog.tables: return None
        table = 'payer_claim'
        self.base = table
        year_atoms, _ = self.year_atoms(table, m['year'] or '') if m['year'] else ([], None)
        amount = float(m['amount'].replace('$', '').replace(',', '').strip()) * (1000 if m['k'] else 1)
        amount = int(amount) if amount == int(amount) else amount
        field = {'paid': 'paid_cents', 'allowed': 'allowed_cents', 'billed': 'charge_cents'}[m['basis'].lower()]
        lead = m['lead'].strip()
        threshold = text[m.start('amount'):m.end('basis')]
        definition = re.search(r'(?:meaning|defined\s+as|i\.e\.,?|that\s+is)\s+' + re.escape(threshold), text, re.I).group()
        where = [*self.base_claim_atoms(table, lead), *year_atoms]
        return {'source': {'table': table, 'quote': 'claimants' if 'claimants' in lead.lower() else lead.split()[-1]},
                'select': [{'kind': 'field', 'field': 'payer_claim.patient_id', 'alias': 'patient_id', 'quote': 'claimants' if 'claimants' in lead.lower() else lead.split()[-1]},
                           {'kind': 'sum', 'field': f'payer_claim.{field}', 'alias': f"{m['basis'].lower()}_dollars", 'scale': 0.01, 'round': 2, 'quote': threshold}],
                'where': combine('and', where, self.question) if where else None, 'group_by': ['patient_id'], 'order_by': [], 'limit': None, 'unresolved': [],
                'having': atom(f"result.{m['basis'].lower()}_dollars", '>=', amount, [lead, definition]),
                'notes': [f'High-cost claimants: patients whose summed {m["basis"].lower()} dollars on counted medical claims reach {amount:,} in the year.']}
