"""Composition constructs for the free parser (request_language.Parser), added 2026-10-24.

These are GENERAL shapes in which a population is built from named conditions, medications and events -- not recipes for particular
questions. Each one builds the same typed predicate the rest of the grammar builds (concept / exists / not / and / or / at_least_of),
every quote a literal slice of the question, so the compiler, the literal check, the decision context and the read-back apply unchanged.

  condition lists   "both A and B", "A, B and C all at once", "A or B", "A and B but not C", "carry / flagged with / diagnosis" wording
  negation          "no A", "without A", "do not have A", "is not flagged with A", "neither A nor B"
  exclusive         "A or B but not both"
  k of n            "at least two of these three: A, B, C"  (op at_least_of)
  medication        "on neither X nor Y"
Quoting rule (the compiler's coverage check): a leaf quote is the content words that leaf's predicate stands for (its lead verb such as
"carry" or "flagged with", the named concept, a trailing "diagnosis"/"on record"); a Boolean operation's quote holds only closed-class
logic words ("both", "no", "neither", "nor", "all at once", "at least two of these three"), which request_plan.LOGIC_WORDS lets it cover.
Every construct declines (returns None) rather than guess: an unrecognised word leaves the old grammar to accept or refuse the text.
"""
import itertools
import re

NUMBER_WORDS = {w: i for i, w in enumerate('zero one two three four five six seven eight nine ten'.split())}
ARTICLE = r'(?:(?:an?|any|the|their|his|her)\s+)?'
LEAD_VERB = r'(?:flagged|diagnosed|labell?ed|coded|documented|listed)\s+with|carry|carries|carrying'
POS_LEAD = (r'(?:(?:who|that)\s+)?(?:(?:are|is|were|was)\s+)?(?:(?:' + LEAD_VERB + r')|(?:have|has|had|having|with))')
NEG_LEAD = (r"(?:(?:who|that)\s+)?(?:(?:have|has|had)\s+(?:neither|no)|with\s+no|(?:do|does|did)\s+not\s+(?:have|carry)|"
            r"(?:do|does|did)n't\s+(?:have|carry)|(?:is|are|was|were)\s+not\s+(?:flagged|diagnosed|labell?ed|coded|documented|listed)\s+with|"
            r"(?:is|are|was|were)n't\s+(?:flagged|diagnosed|labell?ed|coded|documented|listed)\s+with|"
            r"not\s+(?:flagged|diagnosed|labell?ed|coded|documented|listed)\s+with|neither|no|without)")
NEGATION_TOKEN = re.compile(r"\b(?:neither|no|not|without)\b|\b\w+n't\b", re.I)
SUFFIX_ITEM = re.compile(r'\s*(?:\((?:either(?:\s+one)?|both|any(?:\s+of\s+them)?|all(?:\s+of\s+them)?)\)|'
                         r'(?:diagnos[ie]s|diagnosed|dx|flagged|on\s+(?:record|file|the\s+chart|their\s+chart)|in\s+(?:their|the)\s+chart|'
                         r'all\s+at\s+once|at\s+(?:once|the\s+same\s+time)|together|combined|as\s+well|too)\b)', re.I)
SEPARATOR = re.compile(r'^\s*(?:,\s*(and|or|nor)\b\s*|,\s*|\b(and|or|nor)\b\s*(?:also\s+)?)', re.I)
LOGIC_SUFFIX = re.compile(r'^\s*(?:\((?:either(?:\s+one)?|both|any(?:\s+of\s+them)?|all(?:\s+of\s+them)?)\)|all\s+at\s+once|at\s+(?:once|the\s+same\s+time)|together|combined|as\s+well|too)$', re.I)


def suffix_items(tail):
    """-> ([fragment, ...], rest): the trailing filler words after a condition list, each a literal slice."""
    out, rest = [], tail
    while True:
        m = SUFFIX_ITEM.match(rest)
        if not m: break
        out.append(m.group().strip()); rest = rest[m.end():]
    return out, rest


class CompositionForms:
    """Methods mixed into request_language.Parser."""

    def condition_phrase(self, text):
        """(concept id, phrase, tail) for a condition named after an optional article, else None."""
        article = re.match(r'^' + ARTICLE, text, re.I)
        got = self.prefix(text[article.end():], self.conditions)
        if not got: return None
        phrase, concept, tail = got
        return concept, text[article.end():len(text) - len(tail)].strip(), tail

    def condition_list(self, text):
        """-> (items [(concept, phrase)], connectors [and|or|nor|','], tail) for 'A, B and C', else None. Stops before a separator that is
        not followed by another condition (the old grammar owns it)."""
        first = self.condition_phrase(text)
        if not first: return None
        items, connectors, tail = [(first[0], first[1])], [], first[2]
        while True:
            fillers, probe = suffix_items(tail)
            sep = SEPARATOR.match(probe)
            if not sep: break
            nxt = self.condition_phrase(probe[sep.end():])
            if not nxt: break
            items.append((nxt[0], nxt[1])); connectors.append((sep.group(1) or sep.group(2) or ',').lower())
            tail = nxt[2]
        return items, connectors, tail

    def composition_predicate(self, text):
        """The composition constructs of the module docstring, as (predicate, tail), else None."""
        if self.base != 'patient': return None
        return (self.k_of_conditions(text) or self.neither_medications(text) or self.sdoh_composition(text) or self.money_threshold(text)
                or self.evidence_chain(text) or self.condition_construct(text))

    # -- events: "both X and Y [window]", "X in W1 but not at all in W2" ---------------------------------------------------------
    def event_part(self, text):
        """-> (table, field, atoms, window atoms, quote, tail) for one named event / evidence kind with its trailing window, else None."""
        found = self.thing_evidence(text)
        if not found or found[0] == 'measurement': return None
        table, field, atoms, quote, after = found
        win, tail = self.window(after, table, field)
        return table, field, atoms, win, quote, tail, after

    def evidence_chain(self, text):
        """A positive lead, one event, then 'and|or <event>' (a trailing window shared by both when the first has none) or
        'but not [at all] <window>' (the same event, absent in the second window). Declines when neither continuation follows."""
        from .request_language import combine, related
        lead = re.match(r'^(?:(?:who|that)\s+)?' + self.POSITIVE_LEAD + r'\s+', text, re.I)
        if not lead: return None
        body = text[lead.end():]
        quant = re.match(r'^(both|either)\s+', body, re.I)
        if quant: body = body[quant.end():]
        first = self.event_part(body)
        if not first: return None
        table, field, atoms, win, quote, tail, after1 = first
        lead_q = lead.group().strip()
        stop_lead = re.fullmatch(r'(?:who|that|have|has|had|with|whose|got|been|were|was|are|is|ever|the)(?:\s+(?:have|has|had|ever|been|the))*', lead_q, re.I)
        def leaf(tbl, atom_list, windows, q, extra=()):
            first_atom = {**atom_list[0], 'quote': [*extra, *([atom_list[0]['quote']] if isinstance(atom_list[0]['quote'], str) else atom_list[0]['quote'])]}
            where = combine('and', [first_atom, *atom_list[1:], *[dict(w) for w in windows]], q)
            return related(tbl, where, q)
        and_or = re.match(r'^\s*,?\s*(and|or)\s+(?:also\s+)?(?!not\b)', tail, re.I)
        but_not = re.match(r'^\s*,?\s*(?:but|and)\s+(not|never|no)\s+(?:at\s+all\s+)?', tail, re.I)
        extra = () if stop_lead else (lead_q,)
        if and_or:
            second = self.event_part(tail[and_or.end():])
            if not second: return None
            t2, f2, atoms2, win2, quote2, tail2, after2 = second
            shared = self.window(after2, table, field)[0] if (win2 and not win) else []  # the one trailing window, re-bound to this event's own clock
            end = text[:len(text) - len(tail2)].strip()
            leaves = [leaf(table, atoms, win or shared, end, extra), leaf(t2, atoms2, win2, end)]
            if not (win or win2): return None  # an unwindowed pair is the plain conjunction the old grammar builds
            op = and_or[1].lower()
            if op == 'and' and (quant and quant[1].lower() == 'either'): return None
            quote = [quant.group().strip()] if quant and quant[1].lower() == 'both' else []
            parent = {'op': op, 'args': leaves, 'quote': quote[0] if quote else self.question}
            if op == 'or': parent['quote'] = self.question if not quote else parent['quote']
            return parent, tail2
        if but_not:
            win2, tail2 = self.window(' ' + tail[but_not.end():], table, field)
            if not win2 or not win: return None
            end = text[:len(text) - len(tail2)].strip()
            again = leaf(table, atoms, win2, end)
            return {'op': 'and', 'args': [leaf(table, atoms, win, end, extra), {'op': 'not', 'arg': again, 'quote': but_not[1]}], 'quote': self.question}, tail2
        return None

    # -- conditions: lists, negation, exclusive-or ----------------------------------------------------------------------------
    def leaf_quote(self, *parts):
        out = [p for p in parts if p]
        return out[0] if len(out) == 1 else out

    def condition_construct(self, text):
        neg = re.match(r'^' + NEG_LEAD + r'\s+', text, re.I)
        lead = neg or re.match(r'^' + POS_LEAD + r'\s+', text, re.I)
        lead_text = lead.group().strip() if lead else ''
        body = text[lead.end():] if lead else text
        quant = re.match(r'^(both|all|either|any)\b\s*(?:of\s+)?', body, re.I)
        if quant: body = body[quant.end():]
        parsed = self.condition_list(body)
        if not parsed: return None
        items, connectors, tail = parsed
        fillers, after = suffix_items(tail)
        plain = (not neg and len(items) == 1 and not fillers and not quant
                 and (lead is None or re.match(r'^(?:with|having|have|has)$', lead_text, re.I)))
        if plain: return None  # one condition after a plain lead: the old grammar builds it (with its period words)
        neither = bool(neg and re.search(r'\bneither\b', lead_text, re.I))
        kinds = {c for c in connectors if c != ','}
        if neither:
            if kinds - {'nor'} or (len(items) > 1 and kinds != {'nor'}): return None
            kind = 'and'  # one NOT per condition (each carries its own logic word)
        elif len(kinds) > 1 or 'nor' in kinds: return None
        else:
            kind = next(iter(kinds), 'and')
            if kind == 'and' and quant and quant[1].lower() == 'either': return None
            if len(items) > 2 and not kinds: return None  # a bare comma list needs a closing and / or
        # the lead's content words (carry, flagged with) belong to the first leaf; its negation word and the filler logic words to the operators
        lead_words = re.search(LEAD_VERB, lead_text, re.I)
        lead_words = lead_words.group() if lead_words else ''
        content = [f for f in fillers if not LOGIC_SUFFIX.match(f)]
        logic = [f for f in fillers if LOGIC_SUFFIX.match(f)]
        neg_token = NEGATION_TOKEN.search(lead_text) if neg else None
        leaves = [self.condition(c, self.leaf_quote(*( [lead_words] if i == 0 else []), p, *(content if i == len(items) - 1 else [])))
                  for i, (c, p) in enumerate(items)]
        # exclusive: "A or B but not both"
        excl = re.match(r'^\s*,?\s*(?:but|and|though)\s+not\s+both\b', after, re.I)
        and_quote = [q for q in ([quant.group().strip()] if quant and quant[1].lower() == 'both' else []) + logic if q]
        if kind == 'or':
            if len(items) > 1 or content:
                value = self.condition(items[0][0], self.leaf_quote(lead_words, items[0][1], *content) if len(items) == 1 else
                                       [q for q in [lead_words, text_span(body, items[0][1], items[-1][1]), *content] if q])
                if len(items) > 1:
                    value['names'] = list(dict.fromkeys(c for c, _ in items))
                    value['quote'] = [q for q in [lead_words, text_span(body, items[0][1], items[-1][1]), *content] if q]
                    if len(value['quote']) == 1: value['quote'] = value['quote'][0]
            else:
                value = leaves[0]
            for extra in logic:  # filler logic words with no operator of their own ride on the leaf
                value['quote'] = [*([value['quote']] if isinstance(value['quote'], str) else value['quote']), extra]
        else:
            if len(leaves) == 1:
                value = leaves[0]
                for extra in logic: value['quote'] = [*([value['quote']] if isinstance(value['quote'], str) else value['quote']), extra]
            else:
                value = {'op': 'and', 'args': leaves, 'quote': and_quote[0] if len(and_quote) == 1 else (and_quote or self.question)} if and_quote else \
                        {'op': 'and', 'args': leaves, 'quote': self.question}
                self.cover_with(value, and_quote)
        tail_after = after
        if excl and kind == 'or' and not neg and len(items) >= 2:
            tail_after = after[excl.end():]
            both = {'op': 'and', 'args': [self.condition(c, p) for c, p in items], 'quote': 'both'}
            union = value
            return {'op': 'and', 'args': [union, {'op': 'not', 'arg': both, 'quote': 'not both'}], 'quote': self.question}, tail_after
        if neg:
            nots = self.negations(value, lead_text, neither, items)
            return nots, tail_after
        if value.get('op') == 'concept':
            value, tail_after = self.concept_period(value, tail_after)
        return value, tail_after

    def cover_with(self, value, frags):
        """Set a Boolean operation's quote to its logic-word fragments (list), or the question (never covering) when it has none."""
        frags = [f for f in frags if f]
        value['quote'] = frags[0] if len(frags) == 1 else frags if frags else self.question

    def negations(self, value, lead_text, neither, items):
        """Wrap `value` (the condition predicate) in its negation(s); 'neither A nor B' is one NOT per condition, each carrying one logic word."""
        token = NEGATION_TOKEN.search(lead_text)
        word = token.group() if token else 'not'
        if neither and value.get('op') == 'and':
            return {'op': 'and', 'args': [{'op': 'not', 'arg': a, 'quote': 'neither' if i == 0 else 'nor'} for i, a in enumerate(value['args'])],
                    'quote': self.question}
        return {'op': 'not', 'arg': value, 'quote': word}

    # -- k of n --------------------------------------------------------------------------------------------------------------
    def k_of_conditions(self, text):
        """'(have) at least two of these three: A, B, C' -> at_least_of over the listed conditions."""
        m = re.match(r'^(?:(?:with|having|have|has|had)\s+)?((?:at\s+least|any|a\s+minimum\s+of)\s+(?:\d+|' + '|'.join(NUMBER_WORDS) +
                     r')\s+(?:of|from)\s+(?:the\s+following|these(?:\s+(?:\d+|' + '|'.join(NUMBER_WORDS) + r'))?|those(?:\s+(?:\d+|' +
                     '|'.join(NUMBER_WORDS) + r'))?|the)(?:\s+(?:conditions|diagnoses|diseases))?)\s*:?\s*', text, re.I)
        if not m: return None
        number = re.search(r'\d+|' + '|'.join(NUMBER_WORDS), m[1].split(' of ')[0] if ' of ' in m[1] else m[1], re.I)
        k = int(number.group()) if number.group().isdigit() else NUMBER_WORDS[number.group().lower()]
        parsed = self.condition_list(text[m.end():])
        if not parsed: return None
        items, connectors, tail = parsed
        n = len(items)
        if n < 2 or k < 1 or k > n or set(connectors) - {'and', 'or', ','}: return None
        leaves = [self.condition(c, p) for c, p in items]
        return {'op': 'at_least_of', 'n': k, 'args': leaves, 'quote': m[1]}, tail

    # -- medications: neither / nor ------------------------------------------------------------------------------------------
    def neither_medications(self, text):
        """'(are) on|taking neither X nor Y' -> no picked-up fill of X and none of Y (current use = the 12 months ending at the as-of date)."""
        m = re.match(r'^(?:(?:who|that)\s+)?(?:(?:are|is|were|was)\s+)?(?:currently\s+)?(on|taking|receiving)\s+neither\s+' + ARTICLE, text, re.I)
        if not m or 'medication' not in self.catalog.tables: return None
        first = self.prefix(text[m.end():], self.drugs)
        if not first: return None
        nor = re.match(r'^nor\s+' + ARTICLE, first[2], re.I)
        if not nor: return None
        second = self.prefix(first[2][nor.end():], self.drugs)
        if not second: return None
        tail = second[2]
        more = re.match(r'^\s*(?:medications?|meds?|drugs?|therapy|agents?)\b', tail, re.I)
        extra = more.group().strip() if more else ''
        if more: tail = tail[more.end():]
        parts = []
        for i, (drug, verb) in enumerate(((first, m[1]), (second, m[1]))):
            concept = {'op': 'concept', 'kind': 'drug_fill', 'names': drug[1], 'version': 1, 'period': None,
                       'quote': [verb, drug[0], *([extra] if extra and i == 1 else [])]}
            concept, _ = self.current_use(concept, '', verb)
            parts.append({'op': 'not', 'arg': concept, 'quote': 'neither' if i == 0 else 'nor'})
        return {'op': 'and', 'args': parts, 'quote': self.question}, tail


class FallbackForms:
    """The composition frames, tried in order only when the ordinary grammar has declined the text (so nothing it already reads changes)."""

    def composition_fallback(self, text, error):
        from .request_language import PlanError
        for frame in self.FRAMES:
            try:
                plan = getattr(self, frame)(text)
            except PlanError:
                plan = None
            if plan and self.plan_covers(text, plan): return plan
        raise error

    def plan_covers(self, text, plan):
        """Do the plan's quotes account for every content word of the text? (the compiler's coverage rule, so a frame that reads only part of a
        sentence yields to the next frame instead of failing at compile time)"""
        from .request_plan import STOP_WORDS, LOGIC_WORDS
        covered = [False] * len(text)
        def mark(q):
            for frag in ([q] if isinstance(q, str) else q if isinstance(q, list) else []):
                if isinstance(frag, str) and frag.strip():
                    for m in re.finditer(re.escape(frag), text): covered[m.start():m.end()] = [True] * (m.end() - m.start())
        def walk(node):
            if isinstance(node, list):
                for x in node: walk(x)
            elif isinstance(node, dict):
                quote = node.get('quote')
                parent = node.get('op') in ('and', 'or', 'not', 'exists', 'at_least_of') and (node.get('args') or node.get('arg') or node.get('where') is not None)
                if quote is not None:
                    frags = [quote] if isinstance(quote, str) else quote if isinstance(quote, list) else []
                    if not parent or all(w.lower() in LOGIC_WORDS or w.lower() in STOP_WORDS for f in frags if isinstance(f, str) for w in re.findall(r'\w+', f)): mark(frags)
                for key, v in node.items():
                    if key != 'quote' and isinstance(v, (list, dict)): walk(v)
        walk(plan)
        left = [m.group() for m in re.finditer(r'[\w%]+', text) if not all(covered[m.start():m.end()]) and m.group().lower() not in STOP_WORDS]
        self.uncovered_words = left
        return not left

    FRAMES = ('rate_per_form', 'cost_per_patient', 'needs_breakdown', 'compare_frame', 'claims_share_frame', 'most_filled', 'rank_cost', 'rank_floor_first', 'claims_frame', 'rank_measure', 'grouping_wrapper')


class RateForms:
    """Events per patient: "ED visits per 1,000 patients over the last 12 months" -> the events of each counted patient summed (count_related) over the
    patients counted, scaled. The patients are the living, de-duplicated patients of the group (the base population), never only those with an event."""

    PER = re.compile(r'\bper\s+(?P<n>1,?000|100|patient|member|capita)\b(?:\s+(?P<noun>patients?|members?|population|enrollees?))?', re.I)
    DEFINITION_NOTE = re.compile(r'\s*\(\s*(?:patients|members)\s*=\s*(?:living\s+)?(?:patients|members)(?:\s+of\s+(?:the|each)\s+\w+)?\s*\)', re.I)

    def rate_per_form(self, text):
        from .request_language import combine
        m = self.PER.search(text)
        if not m: return None
        before = re.sub(r'^(?:(?:what|how\s+many|which)\s+(?:is|are|was|were|many)\s+)?(?:the\s+|our\s+|total\s+)*', '', text[:m.start()].strip(), flags=re.I)
        event = self.thing(before) if before else None
        if not event or event[0] == 'measurement' or event[4].strip(): return None
        table, field, atoms, event_quote, _ = event
        if 'patient_id' not in self.catalog.tables.get(table, []) or 'patient' not in self.catalog.tables: return None
        scale = {'1000': 1000, '1,000': 1000, '100': 100}.get(m['n'].lower(), 1)
        after = text[m.end():]
        note = self.DEFINITION_NOTE.search(after)
        note_text = note.group().strip() if note else ''
        if note: after = after[:note.start()] + after[note.end():]
        win, tail = self.window(after if after[:1].isspace() or not after else ' ' + after, table, field)
        if tail.strip(' ,?.'): return None
        noun = m['noun'] or ''
        if not noun and m['n'].lower() not in ('patient', 'member', 'capita'): return None
        unit_quote = m.group().strip()
        head_quote = text[:m.start()].strip()
        self.base = 'patient'
        events = {'kind': 'count_related', 'alias': 'events', 'table': table, 'quote': [before] if before in text else [event_quote],
                  'where': combine('and', [*atoms, *win], event_quote)}
        denominator = {'kind': 'count_distinct', 'field': 'patient.patient_id', 'alias': 'patients', 'quote': [unit_quote, *([note_text] if note_text else [])]}
        ratio = {'kind': 'ratio', 'alias': 'rate', 'quote': [unit_quote], 'scale': scale, 'round': 2, 'numerator': events, 'denominator': denominator}
        return {'source': {'table': 'patient', 'quote': noun or unit_quote}, 'select': [ratio], 'where': None, 'group_by': [], 'order_by': [], 'limit': None,
                'unresolved': [], 'notes': [f'A rate of {event_quote} per {"patient" if scale == 1 else f"{scale:,} patients"}: the events of the living patients counted, over those patients '
                                            '(patients with no event stay in the denominator).']}


class RankForms:
    """Ranking groups by any single measure, in both directions: "which three practices have the most diabetic patients", "top three practices by
    ED visits per 1,000 patients over the last 12 months", "which payer has the lowest denial rate". The measure clause is parsed by the ordinary
    grammar (delegation on a literal slice of the question); this form adds the dimension, the order, the limit and an optional minimum group size."""

    SUP = {'most': 'desc', 'highest': 'desc', 'largest': 'desc', 'greatest': 'desc', 'biggest': 'desc', 'top': 'desc', 'worst': 'desc',
           'fewest': 'asc', 'lowest': 'asc', 'least': 'asc', 'smallest': 'asc', 'bottom': 'asc', 'best': 'asc'}
    NUM = r'(?:\d+|one|two|three|four|five|six|seven|eight|nine|ten)'
    RANK_HEAD = re.compile(
        r"^(?:(?:and|now|then|also|next)\s+)?(?:"
        r"(?:which|what)\s+(?:are\s+(?:the\s+)?)?(?:(?P<n>" + NUM + r")\s+)?(?P<dim>[a-z][a-z -]*?)\s+(?:has|have|had|saw|recorded|reported|show(?:ed|s)?|account(?:s|ed)?\s+for)\s+(?:the\s+)?"
        r"(?:(?P<n3>" + NUM + r")\s+)?(?P<sup>most|highest|largest|greatest|biggest|worst|fewest|lowest|least|smallest|best)\s+(?P<rest>.+)"
        r"|(?P<top>top|bottom)\s+(?P<n2>" + NUM + r")\s+(?P<dim2>[a-z][a-z -]*?)\s+by\s+(?P<rest2>.+)"
        r"|(?:which|what)\s+(?P<dim3>[a-z][a-z -]*?)\s+(?:is|are)\s+(?P<drive>driving)\s+(?:our\s+|the\s+)?(?P<rest3>.+))$", re.I)
    FLOOR = re.compile(r"[,;(]?\s*(?:and\s+)?(?:(?P<dim>[a-z][a-z ]*?)\s+)?(?:with|having|that\s+have)\s+at\s+least\s+(?P<m>\d[\d,]*)\s+(?P<noun>[a-z][a-z ]*?)\s*\)?\s*$", re.I)

    def rank_measure(self, text):
        from .request_language import PlanError, atom
        from .population_language import number_of
        t = text.strip().rstrip('?.').strip()
        m = self.RANK_HEAD.match(t)
        if not m: return None
        rest = next(x for x in (m['rest'], m['rest2'], m['rest3']) if x is not None)
        dim_text = next(x for x in (m['dim'], m['dim2'], m['dim3']) if x is not None).strip()
        sup = (m['sup'] or m['top'] or 'most').lower()  # "which payers are driving our out-of-network spend": the groups, biggest first
        n_word = m['n'] or m['n3'] or m['n2']
        trailer = re.search(r',?\s*(?:and\s+)?(?:how\s+many|what\s+was\s+it|with\s+the\s+totals?)(?:\s+(?:each|of\s+them|times\s+each\s+occurred))?\s*$', rest, re.I)
        extra_quote = []
        if trailer:
            extra_quote = [trailer.group().strip(' ,')]  # "with the totals": the output the ranking already carries; its words sit in the ordering quote
            rest = rest[:trailer.start()].strip()
        floor = None
        sized = self.FLOOR.search(rest)
        if sized and sized['noun']:
            floor = (int(sized['m'].replace(',', '')), sized.group().strip(' ,;()'))
            rest = rest[:sized.start()].rstrip(' ,;(')
        plan = self.rank_share_measure(rest)
        if plan is None:
            try:
                plan = self.parse(rest, governed=True)
            except PlanError:
                try: plan = self.parse(rest, governed=False)
                except PlanError: return None
        head = t[:m.start('rest') if m['rest'] is not None else m.start('rest2') if m['rest2'] is not None else m.start('rest3')].strip(' ,') or t
        out = self.build_ranking(plan, dim_text, sup, n_word, head, extra_quote, floor)
        if out and m['drive'] and not n_word: out['limit'] = None
        return out

    def build_ranking(self, plan, dim_text, sup, n_word, head, extra_quote=(), floor=None):
        """Group `plan`'s single measure by the dimension named in `dim_text`, order it, limit it, and drop groups under a minimum size."""
        from .request_language import atom, combine
        from .population_language import number_of
        if plan.get('analyses') or plan.get('group_by') or plan.get('having') or plan.get('order_by') or len(plan['select']) != 1: return None
        measure = plan['select'][0]
        if measure.get('kind') not in ('count', 'count_distinct', 'sum', 'avg', 'ratio'): return None
        table = plan['source']['table']
        self.base = table
        dim = self.dimension(dim_text)
        joins = []
        if not dim and table != 'patient':
            self.base = 'patient'
            dim = self.dimension(dim_text)
            self.base = table
            if dim and 'patient_id' in self.catalog.tables.get(table, []):
                joins = [{'table': 'patient', 'left': f'{table}.patient_id', 'right': 'patient.patient_id', 'kind': 'inner', 'quote': dim[0]}]
        if not dim: return None
        quote_dim, field = dim
        extra_where = None
        if field == 'patient.practice' and re.search(r'\bprimary[- ]care\b|\bpcp\b', ' '.join(self.as_list(quote_dim)), re.I):  # explicit primary-care scope uses the recorded PCP practice
            field, extra_where = 'patient.pcp_practice_id', atom('patient.pcp_practice_id', 'not_null', None, quote_dim)
            plan = {**plan, 'notes': [*plan.get('notes', []), "Practices are the patients' primary-care practices (the PCP practice on the patient row); patients without one are in no practice."]}
        key = field.partition('.')[2]
        alias = measure['alias'] if measure['alias'] not in (key, 'rows') else 'value'
        select = [{'kind': 'field', 'field': field, 'alias': key, 'quote': quote_dim}, {**measure, 'alias': alias}]
        order_quote = [head, *extra_quote] if extra_quote else head
        out = {**plan, 'select': select, 'group_by': [key], 'order_by': [{'alias': alias, 'direction': self.SUP[sup], 'quote': order_quote}],
               'limit': {'value': number_of(n_word), 'quote': head} if n_word else None}
        if joins: out['joins'] = joins
        if extra_where: out['where'] = combine('and', [plan.get('where'), extra_where], self.question)
        elif not field.endswith('patient_id'):  # a group with no value for the dimension is not a rank item (no tier, no payer on the row)
            out['where'] = combine('and', [plan.get('where'), atom(field, 'not_null', None, quote_dim)], self.question)
        if floor:
            out['select'].append({'kind': 'count', 'alias': 'rows', 'quote': floor[1]} if plan['source']['table'] != 'patient' else
                                 {'kind': 'count_distinct', 'field': 'patient.patient_id', 'alias': 'patients', 'quote': floor[1]})
            out['having'] = atom('result.' + out['select'][-1]['alias'], '>=', floor[0], floor[1])
        elif not n_word and not dim_text.rstrip().lower().endswith('s'):
            out['limit'] = {'value': 1, 'quote': head}
        return out

    FLOOR_FIRST = re.compile(
        r"^(?:among|of)\s+(?P<dim>[a-z][a-z -]*?)\s+with\s+at\s+least\s+(?P<m>\d[\d,]*)\s+(?P<noun>[a-z][a-z ]*?)(?P<win>(?:\s+(?:in|during|over|within|for)\s+(?:calendar\s+)?"
        r"(?:(?:19|20)\d\d|the\s+(?:last|past|previous)\s+(?:\d+|\w+)\s+(?:months?|years?)|the\s+(?:last|past)\s+year))*)\s*,\s*which\s+(?:(?P<n>" + NUM + r")\s+)?(?:has|have)\s+the\s+"
        r"(?P<sup>most|highest|largest|greatest|biggest|worst|fewest|lowest|least|smallest|best)\s+(?P<measure>.+)$", re.I)

    def rank_floor_first(self, text):
        """'Among <dimension> with at least N <rows> [window], which <n> have the <highest|lowest> <measure>?' -> the ranking, small groups dropped."""
        from .request_language import PlanError
        t = text.strip().rstrip('?.').strip()
        m = self.FLOOR_FIRST.match(t)
        if not m: return None
        plan = self.parse_measure_for_rank(m['measure'])
        if not plan: return None
        win = m['win'].strip()
        if win:
            plan = self.apply_tail(plan, win)
            if not plan: return None
        floor_quote = re.sub(r'^with\s+', '', t[m.end('dim'):m.end('win')].strip(), flags=re.I)  # the measure quote may not carry the filter word "with"
        head = t[:m.start('measure')].strip()
        return self.build_ranking(plan, m['dim'].strip(), m['sup'].lower(), m['n'], head, floor=(int(m['m'].replace(',', '')), floor_quote))

    def parse_measure_for_rank(self, text):
        from .request_language import PlanError
        for governed in (True, False):
            try:
                plan = self.parse(text, governed=governed)
            except PlanError:
                continue
            if plan.get('analyses') or plan.get('group_by') or plan.get('order_by') or plan.get('having') or len(plan['select']) != 1: continue
            return plan
        return None

    RANK_COST = re.compile(r"^(?:which|what)\s+(?:(?P<n>" + NUM + r")\s+)?(?P<dim>[a-z][a-z -]*?)\s+cost\s+(?:the\s+plan|us)\s+the\s+(?P<sup>most|least)"
                           r"(?P<win>\s+(?:in|during|over)\s+[^(]+?)?\s*(?:\((?P<money>[^,)]+?)(?:,\s*by\s+(?P<by>[^)]+))?\))?$", re.I)

    def rank_cost(self, text):
        """'Which five drugs cost the plan the most in 2029 (plan-paid, by drug name)?' -> plan-paid dollars per group, ranked."""
        t = text.strip().rstrip('?.').strip()
        m = self.RANK_COST.match(t)
        if not m or not m['money']: return None
        plan = self.parse_measure_for_rank(m['money'].strip())
        if not plan or plan['select'][0].get('kind') != 'sum': return None
        if m['win']:
            plan = self.apply_tail(plan, m['win'].strip())
            if not plan: return None
        dim_text = (m['by'] or m['dim']).strip()
        head = t[:m.end('sup')].strip()
        extra = [m['dim'].strip()] if m['by'] else []
        return self.build_ranking(plan, dim_text, m['sup'].lower(), m['n'], head, extra_quote=extra)

    MOST_FILLED = re.compile(r"^(?:what|which)\s+(?:are|is)\s+the\s+(?P<n>" + NUM + r")\s+(?P<sup>most|least)[- ](?P<verb>filled|prescribed|dispensed)\s+(?P<dim>drug\s+classes?|drugs?|medications?)"
                             r"(?P<win>\s+(?:in|during|over|within)\s+the\s+(?:last|past|previous)\s+(?:\d+|\w+)\s+(?:months?|years?)|\s+in\s+(?:calendar\s+)?(?:19|20)\d\d)?\s*,?\s*(?P<by>by\s+(?:the\s+)?number\s+of\s+fills)?$", re.I)

    def most_filled(self, text):
        """'What are the five most-filled drug classes in the last 12 months, by number of fills?' -> fills per drug class, ranked."""
        t = text.strip().rstrip('?.').strip()
        m = self.MOST_FILLED.match(t)
        if not m or 'fill' not in self.catalog.tables: return None
        plan = self.parse_measure_for_rank('fills' if not m['by'] else t[m.start('by') + m['by'].index('fills'):m.end('by')])
        if not plan or plan['source']['table'] != 'fill': return None
        if m['win']:
            plan = self.apply_tail(plan, m['win'].strip())
            if not plan: return None
        head = t[:m.end('dim')].strip()
        extra = [m['by'].strip()] if m['by'] else []
        return self.build_ranking(plan, m['dim'].strip(), m['sup'].lower() if m['sup'].lower() == 'least' else 'most', m['n'], head, extra_quote=extra)


class ShareMeasureForms:
    """The measure clause of a ranking that is a share: "share of its diabetics 40 to 75 on a statin", "statin share among its diabetics 40 to 75"."""

    def rank_share_measure(self, rest):
        from .request_language import PlanError
        self.base = 'patient'
        lead = re.match(r'^(?P<head>(?:share|percent(?:age)?|proportion|fraction|rate))\s+(?:of|among)\s+(?:its\s+|their\s+)?(?P<body>.+)$', rest, re.I)
        try:
            if lead:
                got = self.share_from_rest(rest[:lead.start('body')].strip(), lead['body'])
                return got[0] if got else None
            drug = re.match(r'^(?P<drug>.+?)\s+(?P<head>share|percent(?:age)?|proportion|fraction|rate)\s+(?:among|of|in)\s+(?:its\s+|their\s+|the\s+|our\s+)?(?P<pop>.+)$', rest, re.I)
            if not drug: return None
            pred, tail = self.one(drug['drug'])
            if pred is None or tail.strip() or pred.get('kind') != 'drug_fill': return None
            population = self.population_phrase(drug['pop'])
            if population is None: return None
            plan, _, _ = self.share_plan(rest[drug.start('head'):drug.start('pop')].strip(), population or None, pred, drug['pop'])
            return plan
        except PlanError:
            return None


class GroupingForms(ShareMeasureForms, RankForms, FallbackForms):
    """Grouping wrappers: a request grouped by a dimension, optionally with named categories ("by payer type: commercial, Medicare and Medicaid",
    "split by age group: under 40, 40-64, 65+", "by payer type (commercial, Medicare, Medicaid), how many ..."). The rest of the sentence is
    parsed by the ordinary grammar (delegation on a literal slice of the question); the wrapper adds the dimension."""

    GROUP_MARKER = (r'(?:(?:out|down)\s+by|split\s+(?:up\s+)?(?:by|into)|broken\s+(?:down|out)\s+by|grouped\s+by|segmented\s+by|stratified\s+by|'
                    r'for\s+each|in\s+each|within\s+each|per|by)')
    BREAK_LEAD = re.compile(r'^(?:break|split|segment|slice|stratify)\s+(?:down\s+|out\s+|up\s+)?', re.I)
    ALIAS = {'age': 'age_band', 'age group': 'age_band', 'age band': 'age_band', 'age range': 'age_band', 'age bracket': 'age_band',
             'payer type': 'payer_type', 'payer': 'payer', 'coverage': 'coverage', 'coverage type': 'coverage', 'line of business': 'line_of_business',
             'insurance': 'coverage', 'insurance type': 'coverage'}
    DIM_WORDS = r'[a-z][a-z-]*(?:\s+[a-z][a-z-]*){0,3}'

    def grouping_wrapper(self, text):
        """-> a plan with the dimension added, or None. Declines (None) on anything it cannot take whole."""
        text = text.strip().rstrip('?.').strip()
        fronted = re.match(r'^(?P<marker>' + self.GROUP_MARKER + r')\s+(?:the\s+|our\s+)?(?P<dim>' + self.DIM_WORDS + r')\s*'
                           r'(?:(?::|—|–|\s-\s)\s*(?P<cats>[^,]+?(?:,[^?]*?)?)\s*|\((?P<cats2>[^)]*)\)\s*)?[,:]\s*(?P<q>.+)$', text, re.I)
        if fronted and not self.dimension_of(fronted['dim']): fronted = None
        if fronted:
            rest = fronted['q']
            cats = fronted['cats'] or fronted['cats2']
            clause = text[:fronted.start('q')].strip(' ,:')
            return self.grouped(rest, fronted['dim'], cats, text, [fronted['marker'] + ' ' + fronted['dim']] + ([cats] if False else []))
        leading = self.BREAK_LEAD.match(text)
        body = text[leading.end():] if leading else text
        best = None
        from .comparison_composition import _WINDOW
        for m in re.finditer(r'(?:^|\s)(?P<marker>' + self.GROUP_MARKER + r')\s+(?:the\s+|our\s+)?(?P<dim>' + self.DIM_WORDS + r')(?P<win>(?:\s+' + _WINDOW + r')*)\s*'
                             r'(?:(?::|—|–|\s-\s)\s*(?P<cats>.+?)|\((?P<cats2>[^)]*)\))?\s*$', body, re.I):
            if self.dimension_of(m['dim']):
                best = m; break
        if not best: return None
        head = body[:best.start()].strip()  # "Break hypertensive patients out by age": the head is the population noun phrase
        cats = best['cats'] or best['cats2']
        quote = [(leading.group().strip() if leading else ''), best['marker'].strip() + ' ' + best['dim'].strip()]
        return self.grouped(head, best['dim'], cats, text, [q for q in quote if q], tail=(best['win'] or '').strip())

    def dimension_of(self, words):
        """The dimension a phrase names: ('band', alias) for an age grouping, ('payer', alias) for payer groupings, ('field', field) for a catalog field."""
        key = re.sub(r'\s+', ' ', words.strip().lower())
        key = re.sub(r'^(?:primary[- ]care|pcp)\s+(?=practices?$)', '', key)  # "each primary-care practice": the practice dimension (the practice words stay in the quote)
        if key in ('age', 'age group', 'age band', 'age range', 'age bracket'): return ('band', 'age_band')
        if key in ('payer type', 'coverage', 'coverage type', 'insurance', 'insurance type', 'line of business', 'payer'):
            return ('payer', 'payer_type' if key == 'payer type' else key.replace(' ', '_'))
        if hasattr(self, 'dimension'):
            self.base = getattr(self, 'base', 'patient')
            got = self.dimension(key)
            if got: return ('field', got[1], got[0])
        return None

    def grouped(self, head, dim_words, cats, whole, quote, tail=''):
        """Parse `head` as the ordinary request, then group its measure(s) by the dimension (named categories -> a cohort grouping)."""
        from .request_language import PlanError
        if self.bound and re.fullmatch(r'(?:that|those|them|these)(?:\s+(?:group|cohort|list|patients|members))?', head.strip(), re.I):
            # a follow-up: "break that down by practice" counts the saved cohort itself
            self.base = 'patient'
            plan = {'source': {'table': 'patient', 'quote': head.strip()},
                    'select': [{'kind': 'count_distinct', 'field': 'patient.patient_id', 'alias': 'patients', 'quote': head.strip()}],
                    'where': None, 'group_by': [], 'order_by': [], 'limit': None, 'unresolved': []}
        else:
            try:
                plan = self.parse(head, governed=False)
            except PlanError:
                return None
        if tail:  # "... by formulary tier over the last 12 months": the period after the dimension belongs to the measure sentence
            plan = self.apply_tail(plan, tail)
            if not plan: return None
        if plan.get('analyses') or plan.get('group_by') or plan.get('having') or any(e.get('kind') == 'field' for e in plan['select']): return None
        dim = self.dimension_of(dim_words)
        if cats and re.search(r'(?:and\s+so\s+on|etc\.?|and\s+others|for\s+example)\s*$', cats, re.I) and dim and dim[0] == 'field':
            quote = [*quote, cats.strip()]  # "by product (Medicare Advantage vs FFS, marketplace, and so on)": examples, not a closed list
            cats = None
        if cats:
            groups = self.category_groups(cats)
            if not groups: return None
            select = self.cohort_dimension(plan, dim, groups, quote, dim_words)
        elif dim[0] == 'field':
            field = dim[1]
            extra_where = None
            if field == 'patient.practice' and re.search(r'\bprimary[- ]care\b|\bpcp\b', ' '.join(self.as_list(quote)), re.I):  # explicit primary-care scope
                from .request_language import atom, combine
                field = 'patient.pcp_practice_id'
                extra_where = atom(field, 'not_null', None, quote)
                plan = {**plan, 'where': combine('and', [plan.get('where'), extra_where], self.question)}
            key = field.partition('.')[2]
            select = {'kind': 'field', 'field': field, 'alias': key, 'quote': quote}
            if plan['source']['table'] in ('payer_claim', 'payer_pharmacy_claim'):  # claims with no value for the dimension are not assigned to a group (no formulary tier on the claim)
                from .request_language import atom, combine
                plan = {**plan, 'where': combine('and', [plan.get('where'), atom(field, 'not_null', None, quote)], self.question)}
            if field.startswith('patient.') and plan['source']['table'] != 'patient':
                select['_joins'] = [{'table': 'patient', 'left': f"{plan['source']['table']}.patient_id", 'right': 'patient.patient_id', 'kind': 'inner', 'quote': quote}]
        else:
            return None
        if select is None: return None
        return {**plan, 'select': [select, *plan['select']], 'group_by': [select['alias']],
                **({'joins': [*plan.get('joins', []), *select.pop('_joins', [])]} if select.get('_joins') else {})}

    def category_groups(self, text):
        """'commercial, Medicare (including dual and Medigap) and Medicaid' -> [{'label', 'where', 'quote'}]; None unless every item is one
        recognised population predicate."""
        from .request_language import PlanError
        text = re.sub(r'\s*(?:,?\s*and so on|,?\s*etc\.?)\s*$', '', text.strip(), flags=re.I)
        parts, depth, cur = [], 0, ''
        for ch in text:
            depth += (ch == '(') - (ch == ')')
            cur += ch
        protected = re.sub(r'(\d)\s+(and|or)\s+(older|over|above|up|younger|under|below)\b', lambda m: f'{m[1]}\u0001{m[2]}\u0001{m[3]}', text, flags=re.I)
        pieces = re.split(r'\s*(?:,\s*(?:and\s+|or\s+)?|\s+(?:and|or|vs\.?|versus)\s+(?:of\s+)?)(?![^(]*\))', protected)
        pieces = [re.sub(r'^(?:of\s+|the\s+|a\s+|an\s+)+', '', p.strip().replace('\u0001', ' '), flags=re.I) for p in pieces if p.strip()]
        if len(pieces) < 2: return None
        base, groups = self.base, []
        self.base = 'patient'
        try:
            for item in pieces:
                note = self.medicare_note(item[re.match(r'^\S+', item).end():]) if re.match(r'^medicare\b', item, re.I) else None
                core = item[:len(item) - len(note)].strip() if note else item
                value, tail = self.one(core)
                if value is None or tail.strip(' ,'): return None
                if note: value['quote'] = [value['quote'] if isinstance(value['quote'], str) else ' '.join(value['quote']), note.strip()]
                label = re.sub(r'\s*\([^)]*\)', '', item).strip()
                groups.append({'label': label, 'where': value, 'quote': item if item in text else core})
        except PlanError:
            return None
        finally:
            self.base = base
        return groups

    def band_label(self, item):
        """Numeric band wording -> a machine label: 'under 40' -> under_40, '40 to 64' / '40-64' -> 40_to_64, '65 and older' / '65+' -> 65_plus."""
        t = re.sub(r'\s+', ' ', item.strip().lower())
        m = re.fullmatch(r'(?:aged?\s+)?(?:under|younger than|below|less than)\s+(\d+)', t)
        if m: return f'under_{m[1]}'
        m = re.fullmatch(r'(?:aged?\s+)?(\d+)\s*(?:to|-|–|through)\s*(\d+)', t)
        if m: return f'{m[1]}_to_{m[2]}'
        m = re.fullmatch(r'(?:aged?\s+)?(\d+)\s*(?:\+|(?:and|or)\s+(?:older|over|above|up))|(?:aged?\s+)?(?:over|above|older than)\s+(\d+)', t)
        if m: return f'{m[1] or m[2]}_plus'
        return None

    def cohort_dimension(self, plan, dim, groups, quote, dim_words):
        """The cohort_group dimension for named categories; adds a patient join when the request is over another relation."""
        for g in groups:
            band = self.band_label(g['label']) if dim[0] == 'band' else None
            if dim[0] == 'band' and not band: return None
            g['label'] = band or g['label'].lower()
        alias = dim[1] if dim[0] in ('band', 'payer') else dim[1].partition('.')[2]
        if len({g['label'] for g in groups}) != len(groups): return None
        select = {'kind': 'cohort_group', 'alias': alias, 'quote': quote, 'groups': groups}
        if plan['source']['table'] != 'patient':
            table = plan['source']['table']
            select['_joins'] = [{'table': 'patient', 'left': f'{table}.patient_id', 'right': 'patient.patient_id', 'kind': 'inner', 'quote': quote}]
        return select


def text_span(body, first_phrase, last_phrase):
    """The literal slice of `body` from the first phrase through the last phrase."""
    i = body.index(first_phrase)
    j = body.index(last_phrase, i) + len(last_phrase)
    return body[i:j]


# Everyday surface forms of the drug classes (vocabulary, not recipes): the class id with its digits hyphenated ("glp1_agonist" ->
# "glp-1 agonist"), plus the spelled-out and abbreviated names clinicians use for the same class.
DRUG_SYNONYMS = {
    'long-acting muscarinic antagonist': ['lama'], 'long acting muscarinic antagonist': ['lama'], 'lama': ['lama'],
    'long-acting beta agonist': ['laba'], 'long acting beta agonist': ['laba'], 'laba': ['laba'],
    'short-acting beta agonist': ['short_acting_beta_agonist'], 'short acting beta agonist': ['short_acting_beta_agonist'],
    'saba': ['short_acting_beta_agonist'], 'rescue inhaler': ['short_acting_beta_agonist'],
    'glp-1': ['glp1_agonist'], 'glp1': ['glp1_agonist'], 'glp-1 agonist': ['glp1_agonist'], 'glp-1 receptor agonist': ['glp1_agonist'],
    'glp-1 ra': ['glp1_agonist'], 'glp1 agonist': ['glp1_agonist'],
    'sglt-2': ['sglt2_inhibitor'], 'sglt-2 inhibitor': ['sglt2_inhibitor'], 'sglt2 inhibitor': ['sglt2_inhibitor'],
    'ace inhibitor': ['ace_inhibitor'], 'acei': ['ace_inhibitor'], 'angiotensin-converting enzyme inhibitor': ['ace_inhibitor'],
    'angiotensin converting enzyme inhibitor': ['ace_inhibitor'], 'angiotensin receptor blocker': ['arb'],
    'angiotensin ii receptor blocker': ['arb'], 'loop diuretic': ['loop_diuretic'], 'loop diuretics': ['loop_diuretic'],
    'inhaled steroid': ['inhaled_corticosteroid'], 'ics': ['inhaled_corticosteroid'], 'inhaled corticosteroid': ['inhaled_corticosteroid'],
    'blood thinner': ['anticoagulant'], 'anticoagulant': ['anticoagulant'], 'ppi': ['proton_pump_inhibitor'],
    'proton pump inhibitor': ['proton_pump_inhibitor'], 'dpp-4 inhibitor': ['dpp4_inhibitor'], 'dpp4 inhibitor': ['dpp4_inhibitor'],
    'antiplatelet': ['antiplatelet'], 'mineralocorticoid receptor antagonist': ['mra'],
}


def drug_surface_forms(classes, groups):
    """{phrase: [class ids]} for the surface forms above (kept only when every class exists) and the hyphenated digit forms of class ids."""
    out = {}
    for phrase, ids in DRUG_SYNONYMS.items():
        if all(i in classes for i in ids):
            out[phrase] = list(ids)
            if not phrase.endswith('s'): out[phrase + 's'] = list(ids)
    for k in classes:
        if re.search(r'[a-z]\d', k):
            hyphenated = re.sub(r'([a-z])(\d)', r'\1-\2', k).replace('_', ' ')
            out.setdefault(hyphenated, [k])
            if not hyphenated.endswith('s'): out.setdefault(hyphenated + 's', [k])
    return out
