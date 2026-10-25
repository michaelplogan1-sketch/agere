"""Rewordings of a question onto the wording the free grammar reads (phrasing_tables.py holds the tables).

The grammar (request_language, population_language, claims_language) and the engine forms (population_engines) were written against the
way the development questions are worded. People word the same question many other ways: "pts", "number of ... ", "among X, how many",
"In 2029, ...", "didn't get", "ACEi". This module closes that gap with GENERAL means only:

  * word tables (abbreviations, synonyms) and construction tables (alternative frames) in phrasing_tables.py -- data, not code paths;
  * candidate rewordings are tried only AFTER the question as typed has been declined, so a question the grammar already reads is never
    changed;
  * a rewording must keep the question's meaning exactly: every number, year and patient id of the original is still there, and a
    negation is neither added nor removed (`safe`). A rewording that fails the check is discarded.

The planner compiles the rewording (its quotes are spans of the rewording); the original question is still what the literal check, the
decision context and the answer see, and the answer says how the question was read.
"""
from __future__ import annotations

import re

from . import phrasing_tables as T

VERSION = 'phrasing/1'

_ID = re.compile(r'\b(?:SYN|WHP)-[0-9A-Z]+(?:-[0-9A-Z]+)*\b|\bP-[A-Z0-9]+\b')
_WORDS = [(re.compile(r'(?<![\w-])(?:' + p + r')(?![\w-])', re.I), r) for p, r in T.WORDS]
_FRAMES = []  # compiled on first use: the <DRUG> / <LAB> / <COND> tokens read the drug, lab and condition libraries
_NUM = re.compile(r'(?<![\w.])\d[\d,]*(?:\.\d+)?')
_NOSHOW = re.compile(r'\bno[- ]show\w*', re.I)
_NEG = re.compile(r"\b(?:no|not|never|none|without|neither|nor|nobody|lack(?:s|ing)?|missing|absent|cannot|can't|zero)\b|n't\b", re.I)
_DIGITS = {'one': '1', 'two': '2', 'three': '3', 'four': '4', 'five': '5', 'six': '6', 'seven': '7', 'eight': '8', 'nine': '9',
           'ten': '10', 'eleven': '11', 'twelve': '12'}


def _tokens():
    """Alternations for the closed lists the tables name: <DRUG> (drug classes, groups and their everyday names), <LAB>, <COND>."""
    from .drug_classes import CLASSES, GROUPS
    from .request_language import DRUG_ALIASES, LAB_NAMES, CONDITION_ALIASES
    drugs = {k.replace('_', ' ') for k in (*CLASSES, *GROUPS)} | {k for k in DRUG_ALIASES if not k.startswith(('a ', 'an '))}
    drugs |= {d + 's' for d in list(drugs) if not d.endswith('s')}
    drugs |= {'ace inhibitor', 'ace inhibitors', 'arb', 'arbs', 'gdmt'}

    def alt(items):
        return '(?:' + '|'.join(re.escape(x).replace(r'\ ', r'\s+') for x in sorted(items, key=len, reverse=True)) + ')'
    conds = set(CONDITION_ALIASES) | {'diabetes', 'hypertension', 'heart failure', 'ckd', 'copd', 'asthma', 'atrial fibrillation'}
    return {'<DRUG>': alt(drugs), '<LAB>': alt(LAB_NAMES), '<COND>': alt(conds)}


_VOCAB = []   # [set of domain words]; built on first use
_ENGLISH = []  # [set of dictionary words, or None when the system has no word list]
DOMAIN_NOUNS = ('patients members admissions appointments claims medications prescriptions visits discharges readmissions screening vaccination immunization colonoscopy mammography '
                'pharmacy diabetic hypertension hypertensive hospital hospitals practices payers providers clinicians encounters conditions diagnosis diagnoses laboratory '
                'cholesterol medication adherence enrollment coverage utilization emergency inpatient outpatient telehealth surgery specialist pediatric transportation '
                'insurance spending pregnant pregnancy diabetics asthma statin statins insulin').split()


def _vocabulary():
    """The words the grammar knows (source nouns, labs, conditions, drug names, the phrasing tables' own words, the domain nouns)."""
    if not _VOCAB:
        words = set(DOMAIN_NOUNS)
        try:
            from .request_language import SOURCES, LAB_NAMES, CONDITION_ALIASES, DRUG_ALIASES
            from .drug_classes import CLASSES, GROUPS
            for source in (*SOURCES, *LAB_NAMES, *CONDITION_ALIASES, *DRUG_ALIASES, *CLASSES, *GROUPS):
                words.update(re.findall(r'[a-z]+', source.lower().replace('_', ' ')))
            for _, replacement in T.WORDS:
                words.update(re.findall(r'[a-z]+', str(replacement).lower()))
        except Exception:  # noqa: BLE001 - the vocabulary only widens what is corrected; the grammar never depends on it
            pass
        _VOCAB.append({w for w in words if len(w) >= 5})
    return _VOCAB[0]


def _english():
    if not _ENGLISH:
        try:
            with open('/usr/share/dict/words', encoding='utf-8', errors='ignore') as handle:
                _ENGLISH.append({line.strip().lower() for line in handle})
        except OSError:
            _ENGLISH.append(None)
    return _ENGLISH[0]


def _one_edit(a, b):
    """True when the words differ by one insertion, deletion, substitution or adjacent transposition."""
    if a == b or abs(len(a) - len(b)) > 1: return False
    if len(a) == len(b):
        diff = [i for i in range(len(a)) if a[i] != b[i]]
        return len(diff) == 1 or (len(diff) == 2 and diff[1] == diff[0] + 1 and a[diff[0]] == b[diff[1]] and a[diff[1]] == b[diff[0]])
    short, long_ = (a, b) if len(a) < len(b) else (b, a)
    i = 0
    while i < len(short) and short[i] == long_[i]: i += 1
    return short[i:] == long_[i + 1:]


def _common_suffix(a, b):
    n = 0
    while n < min(len(a), len(b)) and a[-1 - n] == b[-1 - n]: n += 1
    return n


def fix_spelling(text):
    """A word that is neither a dictionary word nor a domain word, one edit from exactly one domain word, is that word ("admisions" -> "admissions",
    "pateints" -> "patients"). Names, ids, numbers and anything ambiguous stay as typed; `safe` still checks the rewording and the answer says how the question was read."""
    vocab, english = _vocabulary(), _english()
    if not vocab: return text

    def fix(m):
        word = m.group()
        w = word.lower()
        if len(w) < 6 or w in vocab or (english is not None and w in english): return word
        if english is None and len(w) < 8: return word
        # an inflection of a known word (denies / denied, filled / fills) is a word, not a typo: the edit must leave a common ending of three letters
        near = [v for v in vocab if abs(len(v) - len(w)) <= 1 and v[0] == w[0] and _one_edit(w, v) and _common_suffix(w, v) >= 3]
        if len(near) != 1: return word
        return near[0].capitalize() if word[:1].isupper() else near[0]
    return re.sub(r'(?<![\w-])[A-Za-z]{6,}(?![\w-])', fix, text)


def _frames():
    if not _FRAMES:
        tokens = _tokens()
        for name, pattern, template in T.FRAMES:
            for tok, value in tokens.items():
                pattern = pattern.replace(tok, value)
            _FRAMES.append((name, re.compile(pattern, re.I | re.S), template))
    return _FRAMES


def _special(name, parts, text, m):
    """Frames whose replacement depends on the matched words (a table template cannot say it)."""
    if name == 'plus_number':  # "65+" is an age floor after a person word, a count floor otherwise
        before = text[:m.start('n')].lower().split()[-1:]
        person = any(w.rstrip(',') in ('aged', 'age', 'patients', 'members', 'adults', 'women', 'men', 'people', 'over') for w in before)
        return f"{parts['n']} and older" if person else f"{parts['n']} or more"
    if name == 'among_how_many':  # "Among asthmatics, how many ..." -> "How many asthma patients ..."; "Among our diabetics" -> "How many of our diabetics"
        pop = parts['pop']
        of = 'of ' if re.match(r'(?:our|the|these|those|all|my|their)\b', pop, re.I) else ''
        return f"How many {of}{pop} {parts['rest']}"
    if name == 'high_cost_list':  # a list of the patients whose paid dollars in a year reach an amount -> the form the claims grammar reads
        if re.search(r'\b(?:more\s+than|over|above|exceed\w*|greater\s+than|under|below|less\s+than)\s+\$', text, re.I):
            return None
        year, amount = re.search(r'\b(20\d\d)\b', text), re.search(r'\$\s?([\d,]+(?:\.\d+)?)', text)
        basis = 'allowed' if re.search(r'\ballowed\b', text, re.I) else 'billed' if re.search(r'\bbilled\b', text, re.I) else 'paid'
        return f"Who are the high-cost claimants for {year[1]}, meaning ${amount[1]} or more {basis}"
    if name == 'why_change_noun':
        verb = {'jump': 'jump', 'rise': 'rise', 'increase': 'increase', 'spike': 'spike', 'growth': 'grow', 'climb': 'climb', 'drop': 'drop',
                'decline': 'fall', 'fall': 'fall', 'change': 'change', 'surge': 'jump'}[parts['n'].lower()]
        year = parts.get('y2') or parts.get('y1')
        return f"Why did {parts['x']} {verb}" + (f' in {year}' if year else '')
    if name == 'why_change_verb':
        base = {'went up': 'go up', 'jumped': 'jump', 'rose': 'rise', 'increased': 'increase', 'spiked': 'spike', 'climbed': 'climb', 'grew': 'grow',
                'dropped': 'drop', 'fell': 'fall', 'declined': 'fall', 'surged': 'jump'}[re.sub(r'\s+', ' ', parts['v'].lower())]
        return f"Why did {parts['x']} {base} {parts['t']}"
    if name == 'rate_rank':  # "which payer has the highest denial rate in 2029 (payers with at least 500 claims)"
        sup = re.search(r'\b(highest|lowest|largest|worst|best|most)\b', text, re.I)[1].lower()
        sup = {'largest': 'highest', 'most': 'highest'}.get(sup, sup)
        group = re.search(r'\b(payer|practice)s?\b', text, re.I)[1].lower()
        year = re.search(r'\b(20\d\d)\b', text)[1]
        floor = re.search(r'\b(\d[\d,]*)(?:\s+or\s+more|\+)?\s+claims\b', text, re.I)[1]
        return f"Which {group} has the {sup} denial rate in {year} ({group}s with at least {floor} claims)"
    if name == 'noshow_times':  # "had 2 or more no-shows" -> "also no-showed 2 or more times"
        n = parts.get('n1') or parts.get('n2')
        lead = (parts.get('lead') or '').lower()
        return f"{'and ' if lead.startswith('and') else ''}{'who ' if lead.startswith('who') else ''}also no-showed {n} or more times"
    if name == 'who_among':  # "Who among diabetics with an A1c over 9 lives in ..." -> "Which diabetics with an A1c over 9 live in ..."
        verb = {'lives': 'live', 'has': 'have', 'is': 'are', 'was': 'were'}.get(parts['v'].lower(), parts['v'])
        return f"Which {parts['pop']} {verb} {parts['rest']}"
    if name == 'years_in_words':  # "last 2 years" -> "last two years" (the grammar reads number words as whole-year windows)
        return f"{parts['w']} {['', '', 'two', 'three', 'four', 'five', 'six', 'seven', 'eight', 'nine', 'ten'][int(parts['n'])]} {parts['u']}"
    return None


def _mask(text):
    ids = []

    def keep(m):
        ids.append(m.group())
        return f'ZZID{len(ids) - 1}ZZ'
    return _ID.sub(keep, text), ids


def _unmask(text, ids):
    return re.sub(r'ZZID(\d+)ZZ', lambda m: ids[int(m.group(1))], text)


def _numbers(text):
    text = re.sub(r'(?<![\w.])(\d+)\s?[kK]\b', lambda m: str(int(m.group(1)) * 1000), text)  # 50K is 50000
    out = [n.replace(',', '') for n in _NUM.findall(text)]
    out += [_DIGITS[w.lower()] for w in re.findall(r'\b(?:' + '|'.join(_DIGITS) + r')\b', text, re.I)]
    return out


def safe(original, reworded):
    """The rewording asks the same thing: same numbers (a number word counts as its digit), same patient ids, negation in both or neither."""
    if _ID.findall(original) != _ID.findall(reworded):
        return False
    a, b = _numbers(original), _numbers(reworded)
    if any(n not in b for n in a):
        return False
    if bool(_NEG.search(_NOSHOW.sub('', original))) != bool(_NEG.search(_NOSHOW.sub('', reworded))):
        return False
    return True


def reword(question):
    """-> the question in canonical wording (the tables applied), or the question unchanged."""
    text = ' '.join(str(question or '').split())
    text, ids = _mask(text)
    tail = re.search(r'[?.!]+\s*$', text)
    punct = tail.group().strip() if tail else ''
    text = re.sub(r'[?.!]+\s*$', '', text).strip()
    text = fix_spelling(text)
    text = re.sub(T.FILLER_LEAD, '', text, flags=re.I)
    text = re.sub(T.FILLER_TAIL, '', text, flags=re.I)
    text = re.sub(r'(?<![<>=])>\s*(?=\d)', ' above ', text)
    text = re.sub(r'(?<![<>=])<\s*(?=\d)', ' under ', text)
    text = text.replace('≥', ' at least ').replace('≤', ' at most ').replace('>=', ' at least ').replace('<=', ' at most ')
    text = re.sub(r'\$\s?(\d+(?:\.\d+)?)\s?[kK]\b', lambda m: '$' + format(int(float(m.group(1)) * 1000), ','), text)
    text = re.sub(r'(?<![\w.])(\d+)\s?[kK]\b', lambda m: format(int(m.group(1)) * 1000, ','), text)
    for rx, repl in _WORDS:
        text = rx.sub(repl, text)
    for name, rx, template in _frames():
        m = rx.search(text)
        if not m:
            continue
        parts = {k: (v or '').strip() for k, v in m.groupdict().items()}
        if 'sup' in parts:
            parts['sup'] = T.SUPERLATIVES.get(parts['sup'].lower(), parts['sup'].lower())
        for key in ('time', 't', 'x'):
            if parts.get(key, '')[:1].isupper():
                parts[key] = parts[key][:1].lower() + parts[key][1:]
        if template.startswith('@'):
            new = _special(name, parts, text, m)
            if new is None:
                continue
        else:
            new = template.format(**parts)
        text = ' '.join((text[:m.start()] + ' ' + new + ' ' + text[m.end():]).split())
    text = re.sub(r'\s+([,;?])', r'\1', text)
    text = text[:1].upper() + text[1:] if text[:1].islower() and not text.startswith('ZZID') else text
    return _unmask(text + punct, ids)


def variants(question):
    """Candidate rewordings of a question, best first (usually one). Empty when nothing changes or no rewording keeps the meaning."""
    original = ' '.join(str(question or '').split())
    out = []
    candidate = reword(original)
    if candidate and candidate != original and safe(original, candidate):
        out.append(candidate)
    return out


def disclose(result, original, reworded):
    """Say on the answer how the question was read: the user's own wording stays the question, the reading is stated beside it."""
    if not isinstance(result, dict):
        return result
    result['question'] = original
    result['reworded_as'] = reworded
    receipt = result.get('planner_receipt')
    if isinstance(receipt, dict) and isinstance(receipt.get('interpretation'), dict):
        receipt['interpretation']['reworded_as'] = reworded
    answer = result.get('answer')
    if isinstance(answer, dict):
        answer['read_as'] = reworded
        answer['limitations'] = [*(answer.get('limitations') or []), f'Read as: "{reworded}" (same numbers, ids and negations as the question asked).']
    return result
