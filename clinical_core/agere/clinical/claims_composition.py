"""Claims and pharmacy measures as noun phrases, for the composition frames (2026-10-24).

The claims forms of claims_language read whole sentences ("What did we pay out in medical claims in 2029 by line of business"). The composition
frames (ranking, comparison, grouping) delegate to the ordinary grammar for the MEASURE clause of a sentence, and that clause is often only a
noun phrase: "2029 paid amount", "out-of-network paid dollars in 2029", "the average paid amount per medical claim in 2029", "plan-paid
dollars", "paid fills in the last 12 months". This module reads such a clause into the same typed request:

  measure     a sum of a money column (paid / allowed / billed / plan-paid / patient responsibility / patient pay), an average of one (or of
              days' supply), or a count of rows (claims, fills, pharmacy claims)
  rows        counted medical claims (counts_in_metrics = 1); for pharmacy money and "fills", the claims that count as fills (paid, not reversed)
  conditions  network status, claim type, place of service, final status, claim status, formulary tier, pharmacy type -- each a phrase of
              CLAIM_PREDICATES / EXTRA_PREDICATES, never a free word
  period      a calendar year, two consecutive years (grouped by year) or a rolling window ("over the last 12 months")
Every phrase it uses is placed in a quote, so the compiler's coverage check still declines a clause with a word nothing here recognises.
"""
import re

from .claims_language import CLAIM_PREDICATES, CLOCK, MONEY, DEFAULT_CLAIM_FILTER

PHARMACY_WORDS = re.compile(r'pharmacy|prescription|\bfills?\b|plan[- ]paid|formulary|\bdrugs?\b|reject(?:ion)?\s+codes?|mail[- ]order|\bretail\b', re.I)
CLAIM_WORDS = re.compile(r'\bclaims?\b|\bspend(?:ing)?\b|\bpaid\b|\bpayments?\b|\ballowed\b|\bbilled\b|\bout[- ]of[- ]pocket\b|\bresponsibility\b', re.I)
# (phrase, {table: column}) in priority order: the first phrase present whose column exists for the relation
MONEY_PHRASES = [
    (r'(?:patient|member)s?\s+(?:responsibility|pay(?:ment)?s?|owe[ds]?)|owe[ds]?\s+out[- ]of[- ]pocket|out[- ]of[- ]pocket(?:\s+(?:cost|spend|amount|dollars))?|patients?\s+pay',
     {'payer_claim': 'patient_responsibility_cents', 'payer_pharmacy_claim': 'patient_pay_cents'}),
    (r'plan[- ]paid(?:\s+(?:amount|dollars|spend|cost))?|(?:does|do|did)\s+the\s+plan\s+pay|the\s+plan\s+pays?|cost(?:s|ing)?\s+the\s+plan',
     {'payer_pharmacy_claim': 'plan_paid_cents', 'payer_claim': 'paid_cents'}),
    (r'allowed(?:\s+(?:amount|dollars))?', {'payer_claim': 'allowed_cents'}),
    (r'billed(?:\s+(?:amount|dollars))?|charges?', {'payer_claim': 'charge_cents'}),
    (r'paid(?:\s+(?:amount|dollars))?(?:\s+on)?|spend(?:ing)?|cost(?:s)?|payments?', {'payer_claim': 'paid_cents', 'payer_pharmacy_claim': 'plan_paid_cents'}),
]
# conditions beyond claims_language.CLAIM_PREDICATES: phrase -> ((column, op, value), ...)
EXTRA_PREDICATES = {
    'payer_claim': [
        (r'(?:emergency[- ]room|ED|emergency)\s+claims?', (('place_of_service', '=', '23'),)),
        (r'office\s+claims?', (('place_of_service', '=', '11'),)),
        (r'paid\s+claims?', (('final_status', '=', 'paid'),)),
    ],
    'payer_pharmacy_claim': [
        (r'mail[- ]order(?:\s+pharmac(?:y|ies))?', (('pharmacy_type', '=', 'mail_order_pharmacy'),)),
        (r'retail(?:\s+pharmac(?:y|ies))?', (('pharmacy_type', '=', 'retail_pharmacy'),)),
        (r'specialty\s+pharmac(?:y|ies)', (('pharmacy_type', '=', 'specialty_pharmacy'),)),
        (r'reversed', (('claim_status', '=', 'reversed'),)),
    ],
}
POS = re.compile(r'\(?\s*place\s+of\s+service\s+(\d{2})\s*\)?', re.I)


class ClaimFrames:
    """Mixed into request_language.Parser."""

    def claim_rolling_atoms(self, table, text):
        """-> (atoms, quote) for a rolling window ("over the last 12 months", "in the past year"), else ([], None)."""
        from .request_language import atom, NUMBER_WORDS
        clock = CLOCK.get(table)
        if not clock or clock not in self.catalog.tables.get(table, []): return [], None
        m = re.search(r'(?:\b(?:in|during|over|within|for)\s+)?the\s+(?:last|past|previous|trailing)\s+(?:(?P<n>\d+|' + '|'.join(NUMBER_WORDS) + r')\s+)?(?P<u>months?|years?)\b'
                      r'|(?:\b(?:in|during|over|within|for)\s+)?\b(?:last|past)\s+year\b', text, re.I)
        if not m: return [], None
        if m.groupdict().get('u'):
            n = int(m['n']) if m['n'] and m['n'].isdigit() else int(NUMBER_WORDS[m['n'].lower()]) if m['n'] else 1
            months = n * (12 if m['u'].lower().startswith('year') else 1)
        else:
            months = 12
        q = m.group().strip()
        return [atom(f'{table}.{clock}', 'within_last_months', months, q)], q

    def claim_period(self, table, text):
        """-> (atoms, quotes, years) for the period named in the text: a calendar year / two consecutive years, or a rolling window."""
        from .request_language import atom
        clock = CLOCK.get(table)
        if not clock or clock not in self.catalog.tables.get(table, []): return [], [], []
        rolling, rq = self.claim_rolling_atoms(table, text)
        if rolling:
            if re.search(r"\b(?:19|20)\d\d\b", text):
                from .request_plan import AmbiguousRequest
                raise AmbiguousRequest('A rolling window and calendar year need an explicit comparison')
            return rolling, [rq], []
        years = re.findall(r'\b(?:in|during|for|of)?\s*(?:calendar\s+)?((?:19|20)\d\d)\b', text, re.I)
        if not years: return [], [], []
        years = sorted({int(y) for y in years})
        if len(years) == 1:
            q = str(years[0]); lead = re.search(r'(?:\b(?:in|during|for|of)\s+)?(?:calendar\s+)?' + q, text, re.I)
            return [atom(f'{table}.{clock}', '>=', f'{years[0]}-01-01', q), atom(f'{table}.{clock}', '<', f'{years[0] + 1}-01-01', q)], [q], years
        if years[-1] - years[0] + 1 != len(years): return [], [], []  # non-consecutive years cannot be one range
        qs = [str(y) for y in years]
        return [atom(f'{table}.{clock}', '>=', f'{years[0]}-01-01', qs[0]), atom(f'{table}.{clock}', '<', f'{years[-1] + 1}-01-01', qs[-1])], qs, years

    def claim_conditions(self, table, text):
        """Every claim condition named in the text -> [(atoms, quote)] (CLAIM_PREDICATES plus EXTRA_PREDICATES plus 'place of service NN')."""
        from .request_language import atom
        out = []
        have = set()
        for pattern, conditions in [*EXTRA_PREDICATES.get(table, []), *CLAIM_PREDICATES.get(table, [])]:
            for m in re.finditer(r'(?<![\w-])(?:(?:came|comes|come|went|go|goes)\s+(?:through|to|via)\s+|(?:were|was|are|is)\s+|from\s+|via\s+)?' + pattern + r'(?![\w-])', text, re.I):
                if not all(c in self.catalog.tables[table] for c, _, _ in conditions): continue
                key = repr(conditions)
                q = m.group().strip()
                out.append((m.start(), [atom(f'{table}.{c}', op, v, q) for c, op, v in conditions], q, key))
        for m in POS.finditer(text):
            if 'place_of_service' in self.catalog.tables.get(table, []):
                q = m.group().strip(' ()')
                out.append((m.start(), [atom(f'{table}.place_of_service', '=', m[1], q)], q, repr((('place_of_service', '=', m[1]),))))
        out.sort(key=lambda x: x[0])
        merged, seen = [], {}
        for _, atoms, q, key in out:  # the same condition stated twice ("emergency-room claims (place of service 23)"): one predicate, both phrases quoted
            if key in seen:
                seen[key][1] = [*seen[key][1], q]
                for a in seen[key][0]: a['quote'] = [*([a['quote']] if isinstance(a['quote'], str) else a['quote']), q]
                continue
            seen[key] = [atoms, [q]]
            merged.append((atoms, q))
        return merged

    SHARE_OF = re.compile(r'^(?:what|which)\s+(?P<head>share|percent(?:age)?|proportion|fraction)\s+of\s+(?:our\s+|the\s+)?(?P<rest>.+)$', re.I)

    def claims_share_frame(self, text):
        """'What share of fills in the last year came through mail order?' -> rows meeting a claim condition over all the counted rows."""
        from .request_language import atom, combine
        t = text.strip().rstrip('?.').strip()
        m = self.SHARE_OF.match(t)
        if not m: return None
        rest = m['rest']
        table = 'payer_pharmacy_claim' if PHARMACY_WORDS.search(rest) else 'payer_claim' if CLAIM_WORDS.search(rest) else None
        if not table or table not in self.catalog.tables: return None
        self.base = table
        noun = re.search(r'\b(?:(?:medical|pharmacy|prescription|institutional|professional)\s+)*(?:claims?|fills?)\b', rest, re.I)
        conditions = self.claim_conditions(table, rest)
        if not noun or len(conditions) != 1: return None
        period_atoms, period_quotes, years = self.claim_period(table, rest)
        if len(years) > 1: return None
        base_quote = noun.group().strip()
        base = []
        if table == 'payer_pharmacy_claim':
            if re.search(r'\bfills?\b', rest, re.I) and 'counts_as_fill' in self.catalog.tables[table]: base.append(atom(f'{table}.counts_as_fill', '=', 1, base_quote))
        elif DEFAULT_CLAIM_FILTER.get(table): base.append(atom(f'{table}.{DEFAULT_CLAIM_FILTER[table][0]}', DEFAULT_CLAIM_FILTER[table][1], DEFAULT_CLAIM_FILTER[table][2], base_quote))
        atoms, q = conditions[0]
        head = t[:m.start('rest')].strip()
        ratio = {'kind': 'ratio', 'alias': 'percent', 'quote': [head], 'scale': 100, 'round': 2,
                 'numerator': {'kind': 'count', 'alias': 'meeting', 'quote': [head, q], 'where': combine('and', atoms, q)},
                 'denominator': {'kind': 'count', 'alias': 'group', 'quote': [head]}}
        return {'source': {'table': table, 'quote': base_quote}, 'select': [ratio], 'where': combine('and', [*base, *period_atoms], self.question) if (base or period_atoms) else None,
                'group_by': [], 'order_by': [], 'limit': None, 'unresolved': [],
                'notes': ['Share of the counted rows (fills that count as fills / counted medical claims) that meet the condition.']}

    def claims_frame(self, text):
        """A claims / pharmacy measure clause (see the module docstring) -> typed request, else None."""
        from .request_language import atom, combine
        t = text.strip().rstrip('?.').strip()
        t = re.sub(r'^(?:(?:and|now|also)\s+)+', '', t, flags=re.I)
        table = ('payer_pharmacy_claim' if PHARMACY_WORDS.search(t) else 'payer_claim' if CLAIM_WORDS.search(t) else None)
        if not table or table not in self.catalog.tables: return None
        if re.search(r'\bper\s+member\s+per\s+month\b|\bpmpm\b|\bper\s+(?:1,?000|100)\b', t, re.I): return None  # governed rates keep their own engines
        self.base = table
        from .claims_language import ROW_RATES
        for pat, tbl, numer, denom, label in ROW_RATES:  # a named rate of rows ("claim denial rate", "rejected-claim rate")
            rate = re.search(r'(?<![\w-])(?:' + pat + r')\s+rate\b', t, re.I)
            if rate and tbl in self.catalog.tables and (tbl == table or table == 'payer_claim' and not PHARMACY_WORDS.search(rate.group())):
                return self.row_rate((tbl, numer, denom, label), rate.group().strip(), t, t)
        window_lead = re.match(r'^(?:in|during|for|over|within)\s+(?:calendar\s+)?(?:(?:19|20)\d\d|the\s+(?:last|past)\s+\w+\s+(?:months?|years?)|the\s+(?:last|past)\s+year)\s*,?\s*', t, re.I)
        lead_offset = window_lead.end() if window_lead else 0  # "In 2029, how much was paid ...": the period leads, the question follows
        lead = re.match(r"^(?:(?:how\s+much|what(?:'s|\s+is|\s+was|\s+were|\s+are)|how\s+many|which|give\s+me|show\s+me|tell\s+me)\s+)?"
                        r"(?:(?:(?:did|do|does|have|has|were|was|is|are)\s+(?:we|the\s+plan|members|patients|our\s+patients|they)\s+)|(?:(?:was|were|is|are)\s+))?(?:(?:pay|paid|spend|spent|owe|owed)\s+)?(?:on\s+)?", t[lead_offset:], re.I)
        frags = []
        # measure kind
        average = re.search(r"\b(?:average|mean|avg)\b|\bper\s+(?:paid\s+)?(?:claim|fill|visit)\b|\bon\s+average\b", t, re.I)
        counting = re.search(r'\bhow\s+many\b|\bnumber\s+of\b|\bcount(?:\s+of)?\b|\btally\b', t, re.I)
        days = re.search(r"\bdays['’]?\s+supply\b", t, re.I)
        money = None
        for pattern, fields in MONEY_PHRASES:
            if table not in fields or fields[table] not in self.catalog.tables[table]: continue
            mm = re.search(r'(?<![\w-])(?:' + pattern + r')(?![\w])', t, re.I)
            if mm:
                money = (mm, fields[table], [x.group().strip() for x in re.finditer(r'(?<![\w-])(?:' + pattern + r')(?![\w])', t, re.I)])
                break
        noun = re.search(r'\b(?:(?:medical|pharmacy|prescription|institutional|professional|paid)\s+)*(?:claims?|fills?|visits?)\b', t, re.I)
        if days and 'days_supply' in self.catalog.tables.get(table, []) and average:
            kind, field, measure_span = 'avg', 'days_supply', days
        elif money and not (counting and not average):
            kind = 'avg' if average else 'sum'
            field, measure_span = money[1], money[0]
            extra_money = money[2]
        elif noun and (counting or not money):
            kind, field, measure_span = 'count', None, noun
        else:
            return None
        period_atoms, period_quotes, years = self.claim_period(table, t)
        conditions = self.claim_conditions(table, t)
        # the measure word itself must not double as a condition ("paid" is the money word of a sum; "denied claims" is a condition of a count)
        if kind != 'count':
            conditions = [(a, q) for a, q in conditions if q.lower() not in ('paid',)]
        extra_money = locals().get('extra_money') or []
        adjectives = list(dict.fromkeys(x.group().strip() for x in re.finditer(r'(?<![\w-])(?:medical|pharmacy|prescription)(?![\w-])', t, re.I)))
        measure_quote = list(dict.fromkeys(q for q in [lead.group().strip(), measure_span.group().strip(), *extra_money, *adjectives,
                                                       *([noun.group().strip()] if noun and kind != 'count' else [])] if q))
        # paid fills: rows that count as fills (paid, not reversed / recouped / rejected) -- "fills" and "paid" together, or pharmacy money
        base_atoms = []
        base_quote = noun.group().strip() if noun else measure_span.group().strip()
        paid_word = re.search(r'(?<![\w-])paid(?![\w-])', t, re.I)
        if kind == 'count' and paid_word and table == 'payer_pharmacy_claim' and noun and paid_word.group() not in noun.group():
            measure_quote = list(dict.fromkeys([*measure_quote, paid_word.group()]))  # "fills were paid": the paid fills (counts_as_fill)
        if table == 'payer_pharmacy_claim':
            fills = re.search(r'\bfills?\b', t, re.I)
            explicit_status = any(a[0]['field'].endswith('claim_status') for a, _ in conditions)
            if (fills or kind in ('sum', 'avg')) and not explicit_status and 'counts_as_fill' in self.catalog.tables[table]:
                base_atoms.append(atom(f'{table}.counts_as_fill', '=', 1, base_quote))
        else:
            default = DEFAULT_CLAIM_FILTER.get(table)
            if default and default[0] in self.catalog.tables[table]: base_atoms.append(atom(f'{table}.{default[0]}', default[1], default[2], base_quote))
            if kind == 'avg' and field.endswith('paid_cents') and not any(a[0]['field'].endswith('final_status') for a, _ in conditions):
                base_atoms.append(atom(f'{table}.final_status', '=', 'paid', measure_span.group().strip()))  # the average over claims that paid (a denial is not a payment of zero)
            if kind == 'sum' and field == 'patient_responsibility_cents' and not any(a[0]['field'].endswith('final_status') for a, _ in conditions):
                base_atoms.append(atom(f'{table}.final_status', '=', 'paid', measure_span.group().strip()))  # member cost sits on paid claims; denials and reversals carry none
        where = [*base_atoms, *period_atoms, *[combine('and', a, q) for a, q in conditions]]
        alias = {'sum': f'{field}', 'avg': f'avg_{field}', 'count': 'count'}[kind] if field else 'count'
        measure = ({'kind': 'count', 'alias': 'count', 'quote': measure_quote or [base_quote]} if kind == 'count' else
                   {'kind': kind, 'field': f'{table}.{field}', 'alias': alias, 'quote': measure_quote or [base_quote]})
        select, group = [measure], []
        if len(years) > 1:
            clock = CLOCK[table]
            select = [{'kind': 'field', 'field': f'{table}.{clock}', 'alias': 'year', 'grain': 'year', 'quote': period_quotes}, measure]
            group = ['year']
            measure = select[1]
            period_atoms_quote = None
        notes = ['Counted payer-adjudicated claims (counts_in_metrics = 1), by service date; money is paid_cents / 100.' if table == 'payer_claim' else
                 'Pharmacy claims by fill date; fills are the claims that count as fills (paid, not reversed or recouped).']
        return {'source': {'table': table, 'quote': noun.group().strip() if noun else base_quote}, 'select': select,
                'where': combine('and', where, self.question) if where else None, 'group_by': group, 'order_by': [], 'limit': None, 'unresolved': [], 'notes': notes}
