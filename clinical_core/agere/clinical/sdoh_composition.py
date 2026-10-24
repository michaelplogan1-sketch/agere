"""Social-needs predicates for the composition grammar (2026-10-24): area vulnerability, vendor risk scores, missed appointments, several needs.

  area vulnerability   "live in high-vulnerability areas (SVI 75 or above)", "high-SVI (75+) areas", "low-SVI (under 25) areas" -> patient_svi.svi_overall
                       compared with the stated number (the number is required: "high" alone is not a definition)
  vendor scores        "a transportation-risk score of 7 or higher (in the purchased SDOH data)" -> sdoh_latest score >= 7
  missed visits        "missed at least one appointment in the last year" -> a no-show appointment in the window (count_at_least for more than one)
  several needs        "screened positive for two or more different social needs" -> count_at_least with distinct needs
  needs breakdown      "how many patients screened positive for each social need" -> distinct positive patients per need
"""
import re

from .claims_language import SDOH_SOURCE

NUM = r'(?:\d+|one|two|three|four|five|six|seven|eight|nine|ten)'
SVI = re.compile(
    r'^(?:(?:who\s+)?(?:live|lives|living|reside|resides|residing)\s+in\s+|(?:are|is)\s+in\s+|in\s+)?(?:an?\s+)?(?:(?:areas?|neighborhoods?|tracts?|counties|zip\s+codes?)\s+(?:with|where|having)\s+(?:an?\s+)?)?(?:(?P<lvl>high|low)[- ])?'
    r'(?:svi|social\s+vulnerability(?:\s+index)?|vulnerability)\b(?:\s+(?:areas?|neighborhoods?|tracts?|counties|zip\s+codes?))?'
    r'\s*(?:\(\s*|\s+)(?:(?:svi|score)\s+)?(?:of\s+)?(?:(?P<op>under|below|less\s+than|over|above|at\s+least|<=|>=|<|>)\s*)?(?P<n>\d{1,3})\s*'
    r'(?P<suf>\+|or\s+(?:above|higher|more|over|greater)|or\s+(?:below|lower|less|under))?\s*\)?(?:\s+(?:areas?|neighborhoods?|tracts?))?', re.I)
VENDOR = re.compile(
    r'^(?:(?:also\s+)?(?:have|has|with)\s+)?(?:an?\s+)?(?P<dim>transportation|financial|housing|social)[- ](?P<what>risk|stress|instability|isolation)(?:[- ]score)?'
    r'\s+(?:score\s+)?(?:of\s+)?(?P<n>\d+(?:\.\d+)?)\s*(?:\+|or\s+(?P<dir>higher|more|above|greater|lower|less|below))', re.I)
VENDOR_COLUMN = {('transportation', 'risk'): 'transportation_risk_score', ('financial', 'stress'): 'financial_stress_score',
                 ('housing', 'instability'): 'housing_instability_score', ('social', 'isolation'): 'social_isolation_score'}
NUMBER_WORDS = {w: str(i) for i, w in enumerate('zero one two three four five six seven eight nine ten'.split()) if i}


class SdohComposition:
    """Mixed into request_language.Parser."""

    def sdoh_composition(self, text):
        return self.svi_predicate(text) or self.vendor_score_predicate(text) or self.missed_appointments(text) or self.distinct_needs(text)

    def svi_predicate(self, text):
        from .request_language import atom, related
        if self.base != 'patient' or 'patient_svi' not in self.catalog.tables: return None
        m = SVI.match(text)
        if not m: return None
        n = float(m['n']) if '.' in m['n'] else int(m['n'])
        op_word = (m['op'] or '').lower().replace('  ', ' ')
        suffix = (m['suf'] or '').lower()
        if op_word in ('under', 'below', 'less than', '<'): op = '<'
        elif op_word in ('over', 'above', '>'): op = '>'
        elif op_word in ('at least', '>='): op = '>='
        elif op_word == '<=': op = '<='
        elif suffix.startswith('or below') or re.match(r'or\s+(?:lower|less|under)', suffix): op = '<='
        elif suffix == '+' or suffix.startswith('or'): op = '>='
        elif (m['lvl'] or '').lower() == 'high': op = '>='
        else: return None  # "low SVI (25)" does not say which side of 25
        quote = text[:m.end()].strip()
        leaf = related('patient_svi', atom('patient_svi.svi_overall', op, n, quote), quote)
        return leaf, text[m.end():]

    def vendor_score_predicate(self, text):
        from .request_language import atom, related
        if self.base != 'patient' or 'sdoh_latest' not in self.catalog.tables: return None
        m = VENDOR.match(text)
        if not m: return None
        column = VENDOR_COLUMN.get((m['dim'].lower(), m['what'].lower()))
        if not column or column not in self.catalog.tables['sdoh_latest']: return None
        n = float(m['n']) if '.' in m['n'] else int(m['n'])
        low = (m['dir'] or '').lower() in ('lower', 'less', 'below')
        tail = text[m.end():]
        source = SDOH_SOURCE.match(tail)
        if source: tail = tail[source.end():]
        quote = text[:len(text) - len(tail)].strip()
        leaf = related('sdoh_latest', atom(f'sdoh_latest.{column}', '<=' if low else '>=', n, quote), quote)
        self.extra_notes.append("Purchased SDOH: each patient's latest matched delivery (exact or partial address match); the vendor score is on a 1-10 scale.")
        return leaf, tail

    def missed_appointments(self, text):
        from .request_language import atom, related, NUMBER_WORDS as WORDS
        if self.base != 'patient' or 'appointment' not in self.catalog.tables: return None
        m = re.match(r'^(?:(?:have|has|had)\s+)?(?:missed|no[- ]showed)\s+(?:(?:at\s+least\s+(?P<n>' + NUM + r'))|an?|any)\s+(?:scheduled\s+)?appointments?\b', text, re.I)
        if not m or 'final_status' not in self.catalog.tables['appointment']: return None
        n = 1
        if m['n']: n = int(m['n']) if m['n'].isdigit() else int(NUMBER_WORDS[m['n'].lower()])
        win, tail = self.window(text[m.end():], 'appointment', 'scheduled_date')
        quote = text[:len(text) - len(tail)].strip()
        status = atom('appointment.final_status', '=', 'no_show', quote)
        from .request_language import combine
        where = combine('and', [status, *win], quote)
        if n == 1: return related('appointment', where, quote), tail
        return {'op': 'count_at_least', 'table': 'appointment', 'where': where, 'n': n, 'quote': quote}, tail

    def distinct_needs(self, text):
        from .request_language import atom, combine
        if self.base != 'patient' or 'social_need' not in self.catalog.tables: return None
        m = re.match(r'^(?:(?:have|has|had)\s+)?(?:screened|tested)\s+positive\s+for\s+(?P<n>' + NUM + r')\s+or\s+more\s+(?:(?:different|distinct|separate)\s+)?(?:social\s+)?(?:needs|domains|barriers)\b', text, re.I)
        if not m: return None
        n = int(m['n']) if m['n'].isdigit() else int(NUMBER_WORDS[m['n'].lower()])
        tail = text[m.end():]
        win, tail = self.window(tail, 'social_need', 'event_date')
        quote = text[:len(text) - len(tail)].strip()
        where = combine('and', [atom('social_need.positive', '=', 1, quote), *win], quote)
        return {'op': 'count_at_least', 'table': 'social_need', 'where': where, 'n': n, 'distinct': 'need', 'quote': quote}, tail

    def needs_breakdown(self, text):
        """'How many patients screened positive for each social need?' -> distinct positive patients per need."""
        from .request_language import atom, combine
        if 'social_need' not in self.catalog.tables: return None
        m = re.match(r'^how\s+many\s+(?P<who>patients|members|people)\s+(?:have\s+|had\s+)?(?:screened|tested)\s+positive\s+for\s+(?P<each>each\s+(?:social\s+)?(?:need|domain|barrier))\s*$', text.strip().rstrip('?.'), re.I)
        if not m: return None
        positive = re.search(r'(?:screened|tested)\s+positive', text, re.I).group()
        self.base = 'social_need'
        return {'source': {'table': 'social_need', 'quote': m['each'].split()[-1]},
                'select': [{'kind': 'field', 'field': 'social_need.need', 'alias': 'need', 'quote': [m['each']]},
                           {'kind': 'count_distinct', 'field': 'social_need.patient_id', 'alias': 'patients', 'quote': ['How many', m['who']]}],
                'where': atom('social_need.positive', '=', 1, positive), 'group_by': ['need'], 'order_by': [], 'limit': None, 'unresolved': [],
                'notes': ['Living patients with at least one positive screen of the need, on any date.']}
