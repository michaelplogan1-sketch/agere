"""Comparisons of named groups, for the composition frames (2026-10-24).

"Compare women and men with diabetes on the share whose latest A1c is above 9", "How do ED visits per 1,000 patients compare between CKD
patients and diabetics without CKD", "What share of diabetics have an A1c in the last 12 months: Medicare versus commercial", "average paid
amount per claim, out of network versus in network", "Do Medicare patients or Medicaid patients have more ED visits per patient".

One mechanism: the measure sentence is read by the ordinary grammar (delegation on text with the group phrases taken out), and the named groups
become a grouping dimension of the same request -- the groups are values of one column (grouped by that column, restricted to the named values) or
named predicates (a cohort_group dimension; groups may overlap). The words that only frame the contrast ("compare", "versus", "between") sit in the
dimension's quote, so nothing in the question is left unaccounted.
"""
import re

from .claims_language import CLOCK

CONTRAST_TOKEN = re.compile(r'\s+(?:versus|vs\.?|v\.|compared\s+(?:with|to)|against)\s+', re.I)
_WINDOW = r'(?:(?:in|during|over|within|for)\s+(?:calendar\s+)?(?:(?:19|20)\d\d|the\s+(?:last|past|previous)\s+(?:\d+|\w+)\s+(?:months?|years?)|the\s+(?:last|past)\s+year)|on\s+average)'
TRAILING_WINDOW = re.compile(r'((?:\s+' + _WINDOW + r')+)\s*$', re.I)
SENTENCE_SPLIT = re.compile(r'^(?P<a>[^?]+\?)\s*(?P<b>(?:give|show|report|list|and)\b.+)$', re.I)
GENERIC_NOUN = re.compile(r'\s+(?:claims?|drugs?|pharmac(?:y|ies)|patients?|members?|fills?|areas?)$', re.I)


def snake(label):
    label = GENERIC_NOUN.sub('', label.strip())  # "Medicare patients" -> medicare, "heart failure patients" -> heart_failure
    return re.sub(r'[^a-z0-9]+', '_', label.lower()).strip('_')


class ComparisonForms:
    """Mixed into request_language.Parser."""

    # -- named groups -------------------------------------------------------------------------------------------------------
    def split_items(self, text):
        text = re.sub(r'\s*(?:,?\s*and so on|,?\s*etc\.?)\s*$', '', text.strip(), flags=re.I)
        protected = re.sub(r'(\d)\s+(and|or)\s+(older|over|above|up|younger|under|below)\b', lambda m: f'{m[1]}\u0001{m[2]}\u0001{m[3]}', text, flags=re.I)
        pieces = re.split(r'\s*(?:,\s*(?:and\s+|or\s+)?|\s+(?:and|or|vs\.?|versus)\s+(?:of\s+)?)(?![^(]*\))', protected)
        return [re.sub(r'^(?:of\s+|the\s+|a\s+|an\s+)+', '', p.strip().replace('\u0001', ' '), flags=re.I) for p in pieces if p.strip()], text

    def claim_category(self, table, item):
        """One group of claim rows named by a claim condition ("out of network", "emergency-room claims (place of service 23)", "brand drugs")."""
        from .request_language import combine
        found = self.claim_conditions(table, item)
        if not found:  # a generic noun after the condition ("brand drugs", "retail pharmacies") belongs to its quote
            core = GENERIC_NOUN.sub('', item.strip())
            found = self.claim_conditions(table, core) if core != item else []
        if len(found) != 1: return None
        atoms, q = found[0]
        return combine('and', atoms, item), q

    def named_groups(self, text, table='patient'):
        """'women and men' / 'out of network versus in network' -> [{'label', 'where', 'quote'}], or None unless every item is one recognised group."""
        from .request_language import PlanError
        items, whole = self.split_items(text)
        if len(items) < 2: return None
        base, groups = self.base, []
        try:
            for item in items:
                if table in ('payer_claim', 'payer_pharmacy_claim'):
                    self.base = table
                    got = self.claim_category(table, item)
                    if got is None: return None
                    where = got[0]
                else:
                    self.base = 'patient'
                    note = self.medicare_note(item[re.match(r'^\S+', item).end():]) if re.match(r'^medicare\b', item, re.I) else None
                    core = item[:len(item) - len(note)].strip() if note else item
                    where = self.population_phrase_or_predicate(core)
                    if where is None: return None
                    if note:
                        first = where if where.get('field') else where
                        first['quote'] = [first['quote'] if isinstance(first['quote'], str) else ' '.join(first['quote']), note.strip()]
                label = re.sub(r'\s*\([^)]*\)', '', item).strip()
                groups.append({'label': label, 'where': where, 'quote': item if item in whole else item})
        except PlanError:
            return None
        finally:
            self.base = base
        return groups

    def population_phrase_or_predicate(self, core):
        """A group of patients: a predicate ("women", "Medicare", "under 40", "high-SVI (75+)") or a population phrase ("CKD patients", "diabetics without CKD")."""
        from .request_language import PlanError
        try:
            value, tail = self.one(core)
            if value is not None and not tail.strip(' ,'): return value
        except PlanError:
            pass
        try:
            where = self.population_phrase(core)
        except PlanError:
            return None
        return where or None

    def attach_groups(self, plan, groups, dim_quote, alias='group', band=False):
        """Group the request's measures by the named groups: a column when every group is one value of it, else a cohort_group dimension."""
        from .request_language import atom, combine
        table = plan['source']['table']
        for g in groups:
            if band:
                label = self.band_label(g['label'])
                if not label: return None
                g['label'] = label
            else:
                g['label'] = snake(g['label'])
        if len({g['label'] for g in groups}) != len(groups): return None
        single = [g['where'] for g in groups]
        field_values = [(w.get('field'), w.get('op'), w.get('value')) for w in single]
        if (all(isinstance(w, dict) and w.get('field') and w.get('op') == '=' for w in single) and len({f for f, _, _ in field_values}) == 1
                and not band and not plan.get('analyses')):
            field = field_values[0][0]
            key = field.partition('.')[2]
            select = {'kind': 'field', 'field': field, 'alias': key, 'quote': dim_quote}
            restrict = atom(field, 'in', [v for _, _, v in field_values], [g['quote'] for g in groups])
            out = {**plan, 'select': [select, *plan['select']], 'group_by': [key],
                   'where': combine('and', [plan.get('where'), restrict], self.question)}
            if field.startswith('patient.') and table != 'patient':
                out['joins'] = [*plan.get('joins', []), {'table': 'patient', 'left': f'{table}.patient_id', 'right': 'patient.patient_id', 'kind': 'inner', 'quote': dim_quote}]
            return self.with_group_size(out)
        select = {'kind': 'cohort_group', 'alias': alias, 'quote': dim_quote, 'groups': groups}
        joins = list(plan.get('joins', []))
        if table != 'patient' and 'patient.' in repr([g['where'] for g in groups]):
            joins.append({'table': 'patient', 'left': f'{table}.patient_id', 'right': 'patient.patient_id', 'kind': 'inner', 'quote': dim_quote})
        out = {**plan, 'select': [select, *plan['select']], 'group_by': [alias]}
        if joins: out['joins'] = joins
        return self.with_group_size(out)

    def with_group_size(self, plan):
        """A rate per patient states how many patients each group has (its denominator), as a count column."""
        measures = [e for e in plan['select'] if e.get('kind') == 'ratio']
        if len(measures) == 1 and measures[0].get('numerator', {}).get('kind') == 'count_related':
            denominator = measures[0]['denominator']
            if denominator.get('kind') == 'count_distinct' and not any(e.get('alias') == 'patients' for e in plan['select']):
                plan = {**plan, 'select': [*plan['select'], {**denominator, 'alias': 'patients'}]}
        return plan

    # -- the contrast frames ------------------------------------------------------------------------------------------------
    def compare_frame(self, text):
        from .request_language import PlanError
        t = text.strip().rstrip('?.').strip()
        t = re.sub(r'^(?:(?:and|now|also)\s+)+', '', t, flags=re.I)
        two = SENTENCE_SPLIT.match(text.strip().rstrip('.'))
        for handler in (self.compare_on, self.compare_how, self.compare_between, self.compare_do, self.compare_likely, self.compare_which_group, self.compare_in_areas,
                        self.share_categories, self.count_categories, self.compare_contrast):
            try:
                plan = handler(t, two)
            except PlanError:
                plan = None
            if plan: return plan
        return None

    CONTRAST_WORDS = re.compile(r'\b(?:versus|vs\.?|compared\s+(?:with|to)|compare)\b|^how\s+(?:do|does)\s+.+\s+compare\b', re.I)

    def contrast_first(self, text):
        """A sentence that states a contrast between named groups is read by the contrast frame BEFORE the ordinary grammar: that grammar would
        read "out of network versus in network" as two conditions on one population (a claim cannot be both), never as two groups."""
        if not self.CONTRAST_WORDS.search(text or ''): return None
        from .request_language import PlanError
        try:
            plan = self.compare_frame(text)
        except PlanError:
            return None
        return plan if plan and self.plan_covers(text.strip().rstrip('?.').strip(), plan) else None

    def parse_measure(self, text):
        from .request_language import PlanError
        for governed in (False, True):
            try:
                plan = self.parse(text, governed=governed)
            except PlanError:
                continue
            if plan.get('analyses') or plan.get('group_by') or plan.get('order_by') or plan.get('having') or any(e.get('kind') == 'field' for e in plan['select']): continue
            return plan
        return None

    def contrast_with(self, measure_text, cats_text, dim_quote, extra_quote=(), tail=''):
        plan = self.parse_measure(measure_text)
        if plan and tail.strip(): plan = self.apply_tail(plan, tail)
        if not plan: return None
        table = plan['source']['table']
        groups = self.named_groups(cats_text, table)
        if not groups: return None
        out = self.attach_groups(plan, groups, [q for q in [*dim_quote, *extra_quote] if q])
        return out

    def compare_on(self, t, two):
        """'Compare <groups> [with <population>] on the <share ...>' -> the share per group."""
        m = re.match(r'^compare\s+(?P<cats>.+?)\s+(?P<pop>(?:with|without|who|aged?|over|under)\s+.+?\s+)?on\s+(?P<measure>(?:the\s+)?(?:share|percent(?:age)?|proportion|fraction)\s+.+)$', t, re.I)
        if not m: return None
        pop_text = (m['pop'] or '').strip()
        measure = re.sub(r'^the\s+', '', m['measure'], flags=re.I)
        sm = re.match(r'^(?P<head>(?:share|percent(?:age)?|proportion|fraction))\s+(?:of\s+\S+\s+)?(?P<pred>.+)$', measure, re.I)
        if not sm: return None
        self.base = 'patient'
        population = self.expression(pop_text)[0] if pop_text else None
        pred, tail = self.expression(sm['pred'])
        if pred is None or tail.strip(): return None
        head_quote = 'the ' + sm['head'] if re.match(r'^the\s+', m['measure'], re.I) else sm['head']
        plan, _, _ = self.share_plan(head_quote, population, pred, t)
        groups = self.named_groups(m['cats'], 'patient')
        if not groups: return None
        return self.attach_groups(plan, groups, ['Compare'])

    SHARE_HEAD = r'(?:share|percent(?:age)?|proportion|fraction)'
    PRED_VERBS = r'(?:have|has|had|are|is|were|was|live|lives|living|filled|got|received|completed|screened|missed|visited|went|used|came)'

    def compare_in_areas(self, t, two):
        """'Among <population>, compare the share <predicate> in <groups> areas' -> the share per group."""
        m = re.match(r'^(?:among|of|for)\s+(?P<pop>[^,]+),\s*compare\s+(?:the\s+)?(?P<head>' + self.SHARE_HEAD + r')\s+(?P<pred>.+?)\s+in\s+(?P<cats>.+?)(?P<areas>\s+areas?)?$', t, re.I)
        if not m: return None
        self.base = 'patient'
        population = self.population_phrase(m['pop'])
        if population is None: return None
        pred, tail = self.expression(m['pred'])
        if pred is None or tail.strip(): return None
        plan, _, _ = self.share_plan(re.search(r'compare\s+(?:the\s+)?' + m['head'], t, re.I).group(), population or None, pred, m['pop'])
        groups = self.named_groups(m['cats'], 'patient')
        if not groups: return None
        return self.attach_groups(plan, groups, [x for x in ['compare', m['areas'].strip() if m['areas'] else ''] if x])

    def share_categories(self, t, two):
        """'[For <population>,] what share of <group> and of <group> <predicate>' -> the share per named group."""
        m = re.match(r'^(?:(?:for|among|of)\s+(?P<pop>[^,]+),\s*)?(?:what|which)\s+(?P<head>' + self.SHARE_HEAD + r')\s+of\s+(?P<cats>.+?)(?:\s+(?:patients|members|people))?\s+'
                     r'(?P<pred>' + self.PRED_VERBS + r'\b.+)$', t, re.I)
        if not m: return None
        self.base = 'patient'
        population = None
        if m['pop']:
            population = self.population_phrase(m['pop'])
            if population is None: return None
        groups = self.named_groups(m['cats'], 'patient')
        if not groups: return None
        pred, tail = self.expression(m['pred'])
        if pred is None or tail.strip(): return None
        head_quote = t[t.lower().index('what') if re.search(r'\bwhat\b', t, re.I) else 0:m.start('cats')].strip()
        plan, _, _ = self.share_plan(head_quote, population or None, pred, m['pop'] or t)
        return self.attach_groups(plan, groups, [m['head']])

    def count_categories(self, t, two):
        """'How many <group> and <group> <predicate>?' -> the number of patients of each named group that meet the predicate."""
        m = re.match(r'^how\s+many\s+(?P<cats>.+?)(?:\s+(?:patients|members|people))?\s+(?P<pred>' + self.PRED_VERBS + r'\b.+)$', t, re.I)
        if not m: return None
        self.base = 'patient'
        groups = self.named_groups(m['cats'], 'patient')
        if not groups: return None
        pred, tail = self.expression(m['pred'])
        if pred is None or tail.strip(): return None
        lead = t[:m.start('cats')].strip()
        plan = {'source': {'table': 'patient', 'quote': lead}, 'select': [{'kind': 'count_distinct', 'field': 'patient.patient_id', 'alias': 'patients', 'quote': lead}],
                'where': pred, 'group_by': [], 'order_by': [], 'limit': None, 'unresolved': []}
        return self.attach_groups(plan, groups, [lead])

    def compare_between(self, t, two):
        """'Compare <measure> between <group> and <group> [window]' -> the measure per group."""
        m = re.match(r'^compare\s+(?:the\s+)?(?P<measure>.+?)\s+between\s+(?P<cats>.+?)(?P<win>(?:\s+' + _WINDOW + r')*)\s*$', t, re.I)
        if not m: return None
        framing = [re.match(r'^compare', t, re.I).group(), 'between']
        return self.contrast_with(m['measure'], m['cats'], framing, tail=(m['win'] or '').strip())

    def compare_how(self, t, two):
        """'How do <measure> compare between|across <groups>' -> the measure per group."""
        m = re.match(r'^how\s+(?:do|does)\s+(?P<measure>.+?)\s+compare\s+(?:between|across|for|among)\s+(?P<cats>.+)$', t, re.I)
        if not m: return None
        return self.contrast_with(m['measure'], m['cats'], ['How do', 'compare', 'between'] if re.match(r'^.*compare\s+between', t, re.I) else ['How do', 'compare'])

    def second_sentence(self, two):
        """The trailing output instruction ("Give the visits per 1,000 for each.") -> (text, scale or None)."""
        if not two: return None, None
        b = two['b'].strip()
        scale = re.search(r'\bper\s+(1,?000|100)\b', b, re.I)
        return b, ({'1000': 1000, '1,000': 1000, '100': 100}[scale[1]] if scale else None)

    def compare_do(self, t, two):
        """'Do <groups> have more <measure>?' (+ 'Give the <measure> per 1,000 for each.')."""
        first = re.sub(r'\?$', '', two['a']).strip() if two else t
        m = re.match(r'^(?:do|does)\s+(?P<cats>.+?)\s+have\s+(?:more|a\s+higher|higher|fewer|lower|less)\s+(?P<measure>.+)$', first, re.I)
        if not m: return None
        b, scale = self.second_sentence(two)
        plan = self.parse_measure(m['measure'])
        if not plan: return None
        if scale and plan['select'][0].get('kind') == 'ratio' and plan['select'][0].get('scale') in (1, 100, 1000):
            plan['select'][0] = {**plan['select'][0], 'scale': scale, 'quote': [*self.as_list(plan['select'][0]['quote']), b]}
            self.window_scale_note(plan, scale)
        groups = self.named_groups(m['cats'], plan['source']['table'])
        if not groups: return None
        pre = re.match(r'^(?:do|does)\b', first, re.I).group()
        if not scale and b: plan['select'][0]['quote'] = [*self.as_list(plan['select'][0]['quote']), b]
        return self.attach_groups(plan, groups, [pre, 'have more' if re.search(r'\bhave\s+more\b', first, re.I) else 'have'])

    def as_list(self, quote):
        return [quote] if isinstance(quote, str) else list(quote)

    def window_scale_note(self, plan, scale):
        plan.setdefault('notes', []).append(f'The rate is stated per {scale:,} patients as the question asks.')

    def compare_likely(self, t, two):
        """'[Among <population>,] is a woman or a man more likely to <predicate>?' (+ 'Give both shares.') -> the share per group."""
        first = re.sub(r'\?$', '', two['a']).strip() if two else t
        m = re.match(r'^(?:(?:among|of|for)\s+(?P<pop>[^,]+),\s*)?(?:is|are)\s+(?P<cats>.+?\s+or\s+.+?)\s+(?:more|less)\s+likely\s+to\s+(?P<pred>.+)$', first, re.I)
        if not m: return None
        b, _ = self.second_sentence(two)
        self.base = 'patient'
        population = None
        if m['pop']:
            population = self.population_phrase(m['pop'])
            if population is None: return None
        pred, tail = self.expression(m['pred'])
        if pred is None or tail.strip(): return None
        head = re.search(r'(?:is|are)\s+', first, re.I).group().strip()
        source_quote = re.search(r'\b(?:diabetics|patients|members|people)\b', first, re.I)
        plan, _, _ = self.share_plan(b or 'more likely', population or None, pred, source_quote.group() if source_quote else 'patients')
        groups = self.named_groups(m['cats'], 'patient')
        if not groups: return None
        out = self.attach_groups(plan, groups, [head, 'more likely to'] if re.search(r'more\s+likely\s+to', first) else [head])
        if out and b: out['select'][1]['quote'] = [*self.as_list(out['select'][1]['quote']), b]
        return out

    def compare_which_group(self, t, two):
        """'Which group is <verb> more often <window>: <groups>?' + 'Give <events> per 100 patients for each.' -> the event rate per group."""
        first = re.sub(r'\?$', '', two['a']).strip() if two else t
        m = re.match(r'^which\s+group\s+(?:is|are|has|have)\s+(?P<verb>\w+)\s+(?:more|less)\s+often(?P<win>\s+(?:in|during|over)\s+[^:]+?)?\s*:\s*(?P<cats>.+)$', first, re.I)
        if not m: return None
        b, scale = self.second_sentence(two)
        if not b or not scale: return None
        core = re.match(r'^(?:give|show|report)\s+(?:me\s+)?(?:the\s+)?(?P<what>.+?)(?:\s+for\s+each|\s+each)?$', b, re.I)
        if not core: return None
        measure_text = core['what'] + (m['win'] or '')
        plan = self.parse_measure(measure_text)
        if not plan: return None
        groups = self.named_groups(m['cats'], plan['source']['table'])
        if not groups: return None
        out = self.attach_groups(plan, groups, ['Which group', 'more often'])
        if out: out['select'][1]['quote'] = [*self.as_list(out['select'][1]['quote']), first[:m.start('verb') + len(m['verb'])].split()[-1], 'Give']
        return out

    def compare_contrast(self, t, two):
        """The generic contrast: '<measure>, A versus B', 'Compare the <rate> for A versus B in 2029', 'pay per fill on A versus B in 2029 on average'.
        The group phrases and the framing words come out; the rest is the measure sentence."""
        for m in CONTRAST_TOKEN.finditer(t):
            left_end, right_start = m.start(), m.end()
            tail_window = TRAILING_WINDOW.search(t[right_start:])
            right_end = right_start + (tail_window.start() if tail_window else len(t[right_start:]))
            right = t[right_start:right_end]
            # the left phrase starts after the nearest framing boundary
            starts = [mm.end() for mm in re.finditer(r'(?:,\s*|:\s*|\s+on\s+|\s+for\s+|\s+of\s+|\s+between\s+|^compare\s+|\s+across\s+)', t[:left_end], re.I)]
            for start in reversed(starts):
                left = t[start:left_end]
                before = t[:start]
                after = t[right_end:]
                framing = []
                lead = re.match(r'^compare\s+', before, re.I)
                if lead: framing.append(lead.group().strip())
                between = re.search(r'\bbetween\s*$', before, re.I)
                if between: framing.append(between.group().strip())
                framing += [re.search(r'versus|vs\.?|v\.|compared|against', m.group(), re.I).group()]
                measure = re.sub(r'^compare\s+(?:the\s+)?', '', before.strip(), flags=re.I)  # a literal slice of the question: the sentence before the groups
                measure = re.sub(r'(?:\s+(?:on|for|of|between|across|in))?[\s,:]*$', '', measure).strip(' ,:')
                if not measure: continue
                plan = self.contrast_with(measure, f'{left} versus {right}', framing, tail=after)
                if plan: return plan
        return None

    def apply_tail(self, plan, tail):
        """The words after the groups ("in 2029", "over the last 12 months", "on average") belong to the measure sentence: claims get the period, an
        average gets its "on average". -> plan, or None when the tail is anything else."""
        t = tail.strip(' ,.')
        if not t: return plan
        table = plan['source']['table']
        words = TRAILING_WINDOW.search(' ' + t)
        if not words or words.group().strip() != t: return None
        rate = plan['select'][-1] if plan['select'] and plan['select'][-1].get('kind') == 'ratio' else None
        if rate and rate.get('numerator', {}).get('kind') == 'count_related':  # a rate per patient: the period belongs to the events counted in the numerator
            from .request_language import CLOCKS, combine
            events = rate['numerator']
            clock = CLOCKS.get(events['table'])
            if not clock or clock not in self.catalog.tables.get(events['table'], []): return None
            atoms, rest = self.window(' ' + t, events['table'], clock)
            if not atoms or rest.strip(): return None
            plan = {**plan, 'select': [*plan['select'][:-1], {**rate, 'numerator': {**events, 'where': combine('and', [events.get('where'), *atoms], self.question)}}]}
            return plan
        average = re.search(r'\bon\s+average\b', t, re.I)
        period_text = re.sub(r'\bon\s+average\b', ' ', t, flags=re.I)
        if table in CLOCK:
            atoms, quotes, years = self.claim_period(table, period_text) if period_text.strip() else ([], [], [])
        else:  # any other dated relation: the grammar's own trailing window on that relation's clock
            from .request_language import CLOCKS
            if not CLOCKS.get(table) or CLOCKS[table] not in self.catalog.tables.get(table, []): return None
            atoms, rest = self.window(' ' + period_text.strip(), table, CLOCKS[table]) if period_text.strip() else ([], '')
            years = []
            if rest.strip(): return None
        if period_text.strip() and not atoms: return None
        if len(years) > 1: return None
        from .request_language import combine
        plan = dict(plan)
        if atoms: plan['where'] = combine('and', [plan.get('where'), *atoms], self.question)
        if average:
            measure = plan['select'][-1]
            if measure.get('kind') != 'avg': return None
            measure['quote'] = [*self.as_list(measure['quote']), average.group()]
        return plan
