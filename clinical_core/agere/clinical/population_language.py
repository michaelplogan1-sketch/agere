"""Population question forms for the free parser (request_language.Parser), added 2026-10-23.

The forms here are GENERAL shapes of population-health questions, not recipes for particular questions. Each one builds the same typed
request the rest of the grammar builds (source, select, where, group_by, order_by, limit, optional having), so the compiler's literal
and coverage checks, the decision context (living patients, latest labs, canonical condition evidence, the 12-month window) and the
read-back all apply unchanged:

  share            "what share / percent / proportion / fraction of <population> <predicate>"        -> ratio of patient counts
  blood pressure   "blood pressure under control" / "controlled blood pressure" / "uncontrolled BP"  -> latest systolic AND diastolic
  population       "adult diabetic patients", "women 50 to 74", "diabetics aged 40 to 75", "hypertensive patients"

Definitions used (each is stated in the answer's limitations):
  * a share whose predicate compares a LAB value reads the patients of the population that HAVE a latest result for that lab
    (the denominator is "patients with a result", and the answer says how many population patients have none);
  * blood pressure is controlled when the latest systolic is under 140 AND the latest diastolic is under 90 (the CBP thresholds of
    config/agere/measures/cbp.json), among patients 18-85 with both readings.
"""
import re

CONTROLLED_BP = (('systolic_bp', '<', 140), ('diastolic_bp', '<', 90))
UNCONTROLLED_BP = (('systolic_bp', '>=', 140), ('diastolic_bp', '>=', 90))
BP_AGE = (18, 85)  # the CBP measure's eligible ages (config/agere/measures/cbp.json: age.min / age.max)

SHARE_HEAD = re.compile(r'^(?:what|which)\s+(?:is\s+|was\s+|are\s+|were\s+)?(?:the\s+|our\s+)?(?:overall\s+)?'
                        r'(?:share|percent(?:age)?|proportion|fraction|rate)\s+of\s+', re.I)
PREDICATE_VERBS = re.compile(r"(?<!\bwho )(?<!\bthat )(?<!\bwhose )\b(?:have|has|had|are|is|were|was|filled|got|received|completed|underwent|"
                             r"did|do|does|ever|haven't|hasn't|aren't|isn't|didn't|don't|doesn't|screened|missed|visited|went|used|came|presented)\b", re.I)
ADJECTIVES = {'adult': ('patient.age', '>=', 18), 'adults': ('patient.age', '>=', 18)}


class PopulationForms:
    """Methods mixed into request_language.Parser."""

    # -- population ------------------------------------------------------------------------------------------------------
    def population_phrase(self, text):
        """-> where (None when the phrase adds no filter) for a phrase that names a population of patients, or None.

        "our adult diabetic patients", "patients 65 and older", "women 50 to 74", "Medicare patients", "COPD patients",
        "patients with hypertension", "diabetics aged 40 to 75". The phrase must be consumed completely."""
        from .request_language import atom, combine
        text = text.strip()
        start = text
        text = re.sub(r'^(?:(?:of\s+)?(?:all|the|our)\s+)+', '', text, flags=re.I)
        modifiers = []
        while True:
            m = re.match(r'^(living|alive|deceased|medicaid|medicare|commercial|adults?)\s+(?=\S)', text, re.I)
            if not m: break
            modifiers.append(m[1]); text = text[m.end():]
        source = self.prefix(text, self.sources)
        if not source:
            named = self.prefix(text, self.conditions)
            people = re.match(r'^(?:patients|members)\b', named[2], re.I) if named else None
            if people:
                quote = text[:len(text) - len(named[2])].strip()
                source = (quote, ('patient', ('condition', named[1])), named[2][people.end():])
        if not source or source[1][0] != 'patient': return None
        quote, (self.base, default), rest = source
        where = []
        if default: where.append(self.preset_filter(default, quote))
        note = self.medicare_note(rest) if any(w.lower() == 'medicare' for w in modifiers) else None
        if note: rest = rest[len(note):]
        for word in modifiers:
            if word.lower().startswith('adult'):
                where.append(atom('patient.age', '>=', 18, word))
                continue
            value, left = self.one(word)
            if value is None or left.strip(): return None
            if note and word.lower() == 'medicare': value['quote'] = [word, note.strip()]
            where.append(value)
        rest = rest.strip()
        # "patients 50 to 74" / "women 65 and older": an age range right after the noun needs no "aged"
        bare = re.match(r'^(\d+)\s*(?:to|-|–|through)\s*(\d+)\b', rest, re.I)
        if bare:
            q = bare.group()
            where.append(combine('and', [atom('patient.age', '>=', int(bare[1]), q), atom('patient.age', '<=', int(bare[2]), q)], q))
            rest = rest[bare.end():].strip()
        if rest:
            value, tail = self.expression(rest)
            if tail.strip(): return None
            if value: where.append(value)
        return combine('and', where, start) if where else False

    MEDICARE_NOTE = re.compile(r'^\s*\(\s*including\s+[^)]*\)', re.I)

    def medicare_note(self, text):
        """'(including dual and Medigap)' right after "Medicare patients": the decision context's Medicare already includes dual and Medigap, so the
        parenthetical only restates it. It is consumed only when it names BOTH (a narrower or wider note would change the population)."""
        m = self.MEDICARE_NOTE.match(text)
        if not m: return None
        names = set(re.findall(r'dual|medigap', m.group(), re.I))
        words = set(re.findall(r'[a-z]+', m.group().lower())) - {'including', 'and', 'or', 'the', 'dual', 'medigap', 'eligible', 'eligibles', 'duals', 'dually', 'plans', 'plan', 'members', 'patients'}
        return m.group() if {n.lower() for n in names} == {'dual', 'medigap'} and not words else None

    # -- shares ----------------------------------------------------------------------------------------------------------
    def split_share(self, body):
        """Split "<population> <predicate>" at the verb that starts the predicate -> (population where, predicate, population text) or None."""
        from .request_language import PlanError
        # a verb starts the predicate ("... have an A1c above 9"); failing that, the preposition "with" does ("diabetics with an A1c under 8")
        verbs = list(PREDICATE_VERBS.finditer(body))
        starts = []
        for verb in verbs:
            # ALSO belongs with the predicate, not the population noun phrase.
            # A later WITH split would otherwise move the cohort condition into
            # the numerator and silently widen its denominator.
            adverb = re.search(r'\balso\s+$', body[:verb.start()], re.I)
            starts.append(adverb.start() if adverb else verb.start())
        if not verbs:
            starts = [v.start() for v in re.finditer(r'\bwith\b', body, re.I)]
        starts += [v.start() for v in re.finditer(r'\b(?:on|taking|receiving)\s+(?:an?\s+|any\s+|the\s+)?[A-Za-z]', body, re.I)]  # "diabetics aged 40 to 75 on a statin": the drug starts the predicate
        if getattr(self, 'marker_lab', None):
            starts += [v.start() for v in re.finditer(r'\b(?:over|under|above|below|less than|greater than|at least|at most)\s+\d', body, re.I)]
        for at in starts:
            pop_text, pred_text = body[:at].strip(), body[at:].strip()
            pop_text = re.sub(r'\s+(?:also|then|both)$', '', pop_text, flags=re.I)  # "of the patients with hypertension, what share ALSO have diabetes": the population ends before "also"
            if not pop_text: continue
            gaps = list(self.gaps)
            self.bp_quote = None
            try:
                population = self.population_phrase(pop_text)
                if population is None: self.gaps[:] = gaps; continue
                pred, tail = self.expression(re.sub(r'^(?:ever\s+)?', '', pred_text))
            except PlanError:
                self.gaps[:] = gaps
                continue
            self.gaps[:] = gaps  # a share's "gap" predicate is the numerator, not a population filter
            if pred is None or tail.strip(): continue
            return population or None, pred, pop_text
        return None

    def share_form(self, text):
        """"What share of <population> <predicate> [by <dimension>]?" -> the ratio of the population's patients that meet the predicate (x100)."""
        head = SHARE_HEAD.match(text)
        if not head: return None
        body, groups = self.trailing_group(text[head.end():])
        split = self.split_share(body)
        if not split: return None
        population, pred, pop_text = split
        plan, ratio, _ = self.share_plan(head.group().strip(), population, pred, pop_text)
        if groups:
            self.base = 'patient'
            keys = []
            fields = []
            for quote, field in groups:
                key = field.partition('.')[2]
                fields.append({'kind': 'field', 'field': field, 'alias': key, 'quote': quote}); keys.append(key)
            plan['select'] = [*fields, ratio]
            plan['group_by'] = keys
        return plan

    def share_from_rest(self, head_quote, rest):
        split = self.split_share(rest)
        if not split: return None
        population, pred, pop_text = split
        return self.share_plan(head_quote, population, pred, pop_text)

    def share_plan(self, head_quote, population, pred, source_quote):
        """-> (plan, ratio measure, denominator measure)."""
        from .request_language import combine, related, atom
        self.base = 'patient'
        self.bind_parent_quotes(pred)
        if population: self.bind_parent_quotes(population)
        measured = lab_leaves(pred, conjunctive=True)  # only labs every numerator patient must have; a lab under OR / NOT cannot narrow the denominator
        denominator = {'kind': 'count_distinct', 'field': 'patient.patient_id', 'alias': 'patients', 'quote': head_quote}
        notes = []
        if getattr(self, 'bp_quote', None):  # blood pressure control is defined on adults 18-85 (config/agere/measures/cbp.json)
            from .request_language import atom
            ages = [atom('patient.age', '>=', BP_AGE[0], self.bp_quote), atom('patient.age', '<=', BP_AGE[1], self.bp_quote)]
            population = combine('and', [population, *ages] if population else ages, self.bp_quote)
            notes.append('Blood pressure control is measured on patients 18 to 85 with a latest systolic and diastolic reading (under 140 and under 90).')
        if measured:
            # the share of patients "with a result": the denominator is the population patients that have the lab's latest result
            leaves = [related('measurement_latest', combine('and', [atom(a['field'], a['op'], a['value'], a['quote']) for a in leaf['atoms']], leaf['quote']),
                              leaf['quote']) for leaf in measured]
            denominator['where'] = combine('and', leaves, head_quote)
            notes.append('Share of the population patients that have a latest result for the lab; patients without one are not in the denominator.')
        ratio = {'kind': 'ratio', 'alias': 'percent', 'quote': head_quote, 'scale': 100,
                 'numerator': {'kind': 'count_distinct', 'field': 'patient.patient_id', 'alias': 'meeting', 'quote': head_quote, 'where': pred},
                 'denominator': {**denominator, 'alias': 'group'}}
        plan = {'source': {'table': 'patient', 'quote': source_quote_of(self, source_quote)}, 'select': [ratio],
                'where': population or None, 'group_by': [], 'order_by': [], 'limit': None, 'unresolved': [], **({'notes': notes} if notes else {})}
        return plan, ratio, denominator

    # -- rates of a screening / vaccination / test -------------------------------------------------------------------------
    RATE_HEAD = re.compile(r"^(?:what(?:'s|\s+is|\s+was|\s+were)\s+)?(?:the\s+|our\s+)?(?:overall\s+)?(?P<thing>.+?)\s+(?:screening\s+|vaccination\s+|testing\s+|completion\s+)?"
                           r"(?:rate|coverage)\s+(?:for|among|in|of)\s+(?P<rest>.+)$", re.I)

    def rate_form(self, text):
        """"What's our <screening / vaccination / test> rate for <population> [in <window>]?" -> share of the population with that evidence."""
        from .request_language import PlanError
        m = self.RATE_HEAD.match(text)
        if not m: return None
        thing_text = m['thing'].strip()
        found = self.thing_evidence(thing_text)
        if not found or found[4].strip() not in ('', 'screening', 'vaccination', 'testing'): return None
        table, field, atoms, quote, _ = found
        rest = m['rest']
        # the population ends where the window begins ("women 50 to 74 over the last two years")
        window_at = re.search(r'\s+(?:in|during|over|within|for)\s+(?:the\s+)?(?:last|past|20\d\d|most\s+recent|latest|current|this)\b', rest, re.I)
        pop_text, window_text = (rest[:window_at.start()], rest[window_at.start():]) if window_at else (rest, '')
        gaps = list(self.gaps)
        try:
            population = self.population_phrase(pop_text)
            win, tail = self.window(window_text, table, field) if window_text else ([], '')
        except PlanError:
            self.gaps[:] = gaps
            return None
        if population is None or tail.strip():
            self.gaps[:] = gaps
            return None
        from .request_language import combine, related
        # the evidence leaf quotes the thing span plus the rate wording; the window carries its own words
        head = text[:m.start('rest')].strip()
        leaf_quote = ' '.join([head, window_text[:len(window_text) - len(tail)].strip()]).strip() if win else head
        atoms[0] = {**atoms[0], 'quote': [head] if isinstance(atoms[0]['quote'], str) else atoms[0]['quote']}
        pred = related(table, combine('and', atoms + win, leaf_quote), leaf_quote)
        return self.share_plan(head, population or None, pred, pop_text)[0]

    # -- a bare threshold after a condition whose control has one marker lab ("diabetics under 8" in a question about diabetes control) ----
    MARKER_THRESHOLD = re.compile(r'^(?:with\s+(?:an?\s+)?)?(over|under|above|below|less than|greater than|at least|at most|no more than|no less than)\s+'
                                  r'(\d+(?:\.\d+)?)\s*%?(?!\s*(?:years?|yrs?|y/o|days?|months?|visits?|times))', re.I)

    def marker_threshold(self, text):
        from .request_language import atom, combine, related
        if not getattr(self, 'marker_lab', None) or self.base != 'patient' or 'measurement_latest' not in self.catalog.tables: return None
        m = self.MARKER_THRESHOLD.match(text)
        if not m: return None
        ops = self.LAB_OPS
        quote = m.group().strip()
        number = float(m[2]) if '.' in m[2] else int(m[2])
        leaf = related('measurement_latest', combine('and', [atom('measurement_latest.concept', '=', self.marker_lab, quote),
                                                              atom('measurement_latest.value_canonical', ops[m[1].lower()], number, quote)], quote), quote)
        self.extra_notes.append(f'A bare threshold after a diabetes-control question reads as the latest HbA1c ("{quote}").')
        return leaf, text[m.end():]

    # -- blood pressure --------------------------------------------------------------------------------------------------
    BP_CONTROLLED = re.compile(r"^(?:(?:have|has|with)\s+)?(?:(?:their|the)\s+)?(?:blood\s+pressure|bp)(?:\s+(?:is|are))?\s+(?:under|in|well[- ]controlled)(?:\s+control)?\b|"
                               r"^(?:(?:have|has|with)\s+)?(?:their\s+)?(?:well[- ])?controlled\s+(?:blood\s+pressure|bp)\b|"
                               r"^(?:(?:have|has|with)\s+)?(?:their\s+)?(?:blood\s+pressure|bp)\s+controlled\b", re.I)
    BP_UNCONTROLLED = re.compile(r"^(?:(?:have|has|with)\s+)?(?:(?:their|the)\s+)?(?:blood\s+pressure|bp)(?:\s+(?:is|are))?\s+(?:not\s+(?:under|in)\s+control|out\s+of\s+control|uncontrolled)\b|"
                                 r"^(?:(?:have|has|with)\s+)?(?:their\s+)?uncontrolled\s+(?:blood\s+pressure|bp)\b", re.I)

    def blood_pressure(self, text):
        """-> (predicate, tail) for blood pressure under / not under control: the latest systolic and diastolic readings."""
        from .request_language import atom, combine, related
        if self.base != 'patient' or 'measurement_latest' not in self.catalog.tables: return None
        negative = self.BP_UNCONTROLLED.match(text)
        positive = None if negative else self.BP_CONTROLLED.match(text)
        m = negative or positive
        if not m: return None
        quote = m.group().strip()
        spec = UNCONTROLLED_BP if negative else CONTROLLED_BP
        joiner = 'or' if negative else 'and'
        leaves = [related('measurement_latest', combine('and', [atom('measurement_latest.concept', '=', c, quote),
                                                                atom('measurement_latest.value_canonical', op, v, quote)], quote), quote)
                  for c, op, v in spec]
        self.bp_quote = quote
        return combine(joiner, leaves, quote), text[m.end():]


def lab_leaves(node, conjunctive=False):
    """The latest-result lab comparisons inside a predicate tree: [{'atoms': [...], 'quote': str}] (one per measurement_latest EXISTS).
    conjunctive=True keeps only those reached through AND nodes alone, so every patient matching the tree has all of them."""
    found = []

    def walk(n):
        if not isinstance(n, dict): return
        if n.get('op') == 'exists' and n.get('table') == 'measurement_latest':
            atoms = []

            def leaves(w):
                if isinstance(w, dict) and 'field' in w and w['field'] == 'measurement_latest.concept': atoms.append(w)
                for v in (w.values() if isinstance(w, dict) else []):
                    if isinstance(v, list):
                        for x in v: leaves(x)
                    elif isinstance(v, dict): leaves(v)
            leaves(n.get('where'))
            if atoms: found.append({'atoms': atoms[:1], 'quote': atoms[0]['quote']})
            return
        if conjunctive and n.get('op') != 'and': return
        for v in n.values():
            if isinstance(v, list):
                for x in v: walk(x)
            elif isinstance(v, dict): walk(v)
    walk(node)
    return found


def source_quote_of(parser, population_text):
    """The shortest literal span naming the patient noun of a population phrase (a source quote must not carry filter words)."""
    m = re.search(r'\b(patients|members|people|diabetics|women|men)\b', population_text, re.I)
    return m.group() if m else population_text.split()[0]


SUPERLATIVE = {'most': 'desc', 'highest': 'desc', 'largest': 'desc', 'greatest': 'desc', 'biggest': 'desc', 'top': 'desc', 'worst': 'desc',
               'fewest': 'asc', 'lowest': 'asc', 'least': 'asc', 'smallest': 'asc', 'bottom': 'asc', 'best': 'asc'}
COUNT_WORDS = {'one': 1, 'two': 2, 'three': 3, 'four': 4, 'five': 5, 'six': 6, 'seven': 7, 'eight': 8, 'nine': 9, 'ten': 10}
SUPERLATIVE_WORDS = '|'.join(SUPERLATIVE)


def number_of(word):
    return int(word) if str(word).isdigit() else COUNT_WORDS[str(word).lower()]


class RankingForms:
    """Mixed into Parser: group-and-rank forms ("which three practices had the most X", "the lowest share of ... by practice")."""

    def dimension(self, text):
        """-> (quote, field) when the whole text names one grouping dimension of the current base ("practices", "payers"), else None."""
        words = text.strip().split()
        if not words or len(words) > 4: return None
        singular = lambda w: (re.sub(r'ies$', 'y', w, flags=re.I) if w.lower().endswith('ies') else re.sub(r'sses$', 'ss', w, flags=re.I) if w.lower().endswith('sses')
                              else re.sub(r'(?<!s)s$', '', w, flags=re.I))
        for candidate in (text.strip(), ' '.join(words[:-1] + [singular(words[-1])])):
            got = self.field_prefix(candidate)
            if got and not got[2]: return text.strip(), got[1]
        return None

    RANK_COUNT = re.compile(r"^(?:which|what)\s+(?:(?P<n>\d+|" + '|'.join(COUNT_WORDS) + r")\s+)?(?P<dim>[a-z][a-z ]*?)\s+"
                            r"(?:had|have|has|saw|recorded|reported|show(?:ed|s)?|sent|send|account(?:s|ed)?\s+for)\s+(?:the\s+)?"
                            r"(?P<sup>" + SUPERLATIVE_WORDS + r")\s+(?P<rest>.+)$", re.I)

    def rank_count(self, text):
        """"Which three practices had the most no-shows in the last year, and how many?" -> rows counted per group, ordered, limited."""
        from .request_language import PlanError
        m = self.RANK_COUNT.match(text)
        if not m: return None
        rest = re.sub(r',?\s*and\s+how\s+many(?:\s+each|\s+of\s+them)?\s*$', '', m['rest'], flags=re.I).strip()
        try:
            plan = self.parse(rest, governed=False)
        except PlanError:
            return None
        if plan.get('group_by') or any(e.get('kind') not in ('count', 'count_distinct') for e in plan['select']): return None
        dim = self.dimension(m['dim'])
        if not dim: return None
        quote_dim, field = dim
        head = text[:m.start('rest')].strip()
        count = plan['select'][0]
        alias = 'patients' if count['kind'] == 'count_distinct' else 'count'
        n = number_of(m['n']) if m['n'] else (None if m['dim'].rstrip().lower().endswith('s') else 1)
        key = field.partition('.')[2]
        out = {**plan, 'select': [{'kind': 'field', 'field': field, 'alias': key, 'quote': quote_dim}, {**count, 'alias': alias}],
               'group_by': [key], 'order_by': [{'alias': alias, 'direction': SUPERLATIVE[m['sup'].lower()], 'quote': head}],
               'limit': {'value': n, 'quote': head} if n else None}
        return out

    GROUP_TAIL = re.compile(r"[,;]?\s*(?:and\s+)?(?:for\s+each|per|by|grouped\s+by|broken\s+down\s+by|across)\s+(?P<dim>[a-z][a-z ]*?)\s*$", re.I)

    def trailing_group(self, text):
        """Split a trailing "by <dimension>" off a share question: -> (text, [(quote, field)]) or (text, [])."""
        m = self.GROUP_TAIL.search(text)
        if not m: return text, []
        dim = self.dimension(m['dim'])
        return (text[:m.start()].rstrip(' ,;'), [(m.group().strip(' ,;'), dim[1])]) if dim else (text, [])

    RANK_SHARE = re.compile(r"^(?:which|what|show|list|give\s+me|find)\s+(?:me\s+)?(?:the\s+)?(?:(?P<n>\d+|" + '|'.join(COUNT_WORDS) + r")\s+)?"
                            r"(?:(?P<dim>[a-z][a-z ]*?)\s+(?:have|has|had)\s+(?:the\s+)?)?(?:(?P<n2>\d+|" + '|'.join(COUNT_WORDS) + r")\s+)?"
                            r"(?P<sup>" + SUPERLATIVE_WORDS + r")\s+(?:share|percent(?:age)?|proportion|fraction|rate)\s+of\s+(?P<rest>.+)$", re.I)
    MIN_SIZE = re.compile(r"[,;(]?\s*(?:and\s+)?(?:(?P<dim>[a-z][a-z ]*?)\s+)?(?:with|having|that\s+have)\s+(?:at\s+least|a\s+minimum\s+of|more\s+than|over)\s+(?P<m>\d[\d,]*)\s+"
                          r"(?P<noun>[a-z][a-z ]*?)\s*\)?\s*$", re.I)

    def rank_share(self, text):
        """"Which practices have the lowest share of diabetics with an A1c under 8, practices with at least 15 diabetics?" -> the share per group,
        ordered, limited, groups below a minimum size dropped."""
        from .request_language import PlanError, atom
        m = self.RANK_SHARE.match(text)
        if not m: return None
        rest, dim_text, floor = m['rest'], m['dim'], None
        sized = self.MIN_SIZE.search(rest)
        if sized:
            floor = (int(sized['m'].replace(',', '')), sized.group().strip(' ,;()'))
            dim_text = dim_text or sized['dim']
            rest = rest[:sized.start()].rstrip(' ,;(')
        if not dim_text: return None
        self.base = 'patient'
        dim = self.dimension(dim_text)
        if not dim: return None
        head = text[:len(text) - len(m['rest'])].strip()
        shared = self.share_from_rest(head, rest)
        if not shared: return None
        plan, ratio, denominator = shared
        quote_dim, field = dim
        key = field.partition('.')[2]
        n = number_of(m['n'] or m['n2']) if (m['n'] or m['n2']) else None
        plan['select'] = [{'kind': 'field', 'field': field, 'alias': key, 'quote': quote_dim}, ratio,
                          {**denominator, 'alias': 'patients'}]
        plan['group_by'] = [key]
        plan['order_by'] = [{'alias': 'percent', 'direction': SUPERLATIVE[m['sup'].lower()], 'quote': head}]
        if n: plan['limit'] = {'value': n, 'quote': head}
        if floor:
            plan['having'] = atom('result.patients', '>=', floor[0], floor[1])
        else:  # a group with nobody to measure has no share to rank
            plan['having'] = atom('result.patients', '>=', 1, head)
        return plan
