"""Declarative compiler rules: phrasing patterns with typed slots -> validated plan templates ($0, no model).

The hand-written routes in local_compiler.py run first. When they decline, local_compiler.compile calls apply(): each
rule in config/agere/compiler_rules/*.json is a phrasing pattern whose {slots} are filled from the compiler's own
vocabularies (conditions, measurements, drug classes, encounters, periods, ages, thresholds, observed field values,
rule-local options) and a plan template (governed SQL over the semantic layer, or a typed operator node).

A rule fires only when
  * the release carries what the rule reads (``requires``: streams and patient fields in the source profile),
  * its pattern matches and every slot resolves against the vocabularies and this profile,
  * every word of the question is explained: words outside the matched pattern must be conversational filler
    (local_compiler.FILLER minus operator words) or listed in the rule's ``allow``; numbers and operator words never
    pass silently (the same unexplained-word guard the hand-written routes use), and
  * the produced plan passes analysis_graph.validate.

Only rules with an acceptance receipt whose digest matches the rule are loaded (tools/compiler_growth/accept.py writes
the receipt after the gate passes), so the gate is the only way a rule reaches the compiler. ``using(rules)`` overrides
the loaded set (the gate and tests evaluate candidates that way).
"""
import contextlib, hashlib, json, os, re
from pathlib import Path

VERSION = 'compiler-rules/1'
ROOT = Path(__file__).resolve().parents[3]
RULES_DIR = ROOT / 'config/agere/compiler_rules'
RECEIPTS_DIR = RULES_DIR / 'receipts'
SLOT = re.compile(r'\{([a-z][a-z0-9_]*)(\?)?\}')
PLACEHOLDER = re.compile(r'\{([a-z][a-z0-9_]*)(?:\.([a-z][a-z0-9_]*))?(?::([a-z][a-z0-9_.]*))?\}')
_OVERRIDE = []          # stack of rule lists set by using()
_LOADED = {}            # rules dir -> (signature, rules)
_PATTERNS = {}          # (rule digest, profile id) -> compiled regex
NUMBER_WORDS = {'one': 1, 'two': 2, 'three': 3, 'four': 4, 'five': 5, 'six': 6, 'seven': 7, 'eight': 8, 'nine': 9, 'ten': 10,
                'twelve': 12, 'a': 1, 'an': 1}
SQL_OPS = {'gt': '>', 'ge': '>=', 'lt': '<', 'le': '<=', 'eq': '='}
MARKS = ('cpt_lookup',)  # info flags a rule may set (cpt_lookup: the plan reads CPT/HCPCS codes, so CPT wording is consumed)


# ---------------------------------------------------------------- rule identity, loading
def digest(rule):
    """sha256 over the canonical rule (receipts and the gate key on it)."""
    body = {k: v for k, v in rule.items() if not k.startswith('_')}  # loader annotations (_file, _digest) are not the rule
    return hashlib.sha256(json.dumps(body, sort_keys=True, separators=(',', ':')).encode()).hexdigest()


def receipt_path(rule, directory=RECEIPTS_DIR):
    return Path(directory) / f"{rule['id']}@{rule['version']}.json"


def accepted_receipt(rule, directory=RECEIPTS_DIR):
    """The gate's receipt when it accepted exactly this rule (same id, version and digest), else None."""
    try:
        receipt = json.loads(receipt_path(rule, directory).read_text())
    except (OSError, ValueError):
        return None
    return receipt if receipt.get('status') == 'accepted' and receipt.get('rule_digest') == digest(rule) else None


def accepted(rule, directory=RECEIPTS_DIR):
    return accepted_receipt(rule, directory) is not None


def read_rule_files(directory=RULES_DIR):
    """Every rule in <directory>/*.json, in file then list order (no receipt check)."""
    rules = []
    for path in sorted(Path(directory).glob('*.json')):
        data = json.loads(path.read_text())
        for rule in data.get('rules', []):
            rules.append({**rule, '_file': path.name})
    return rules


def load(directory=RULES_DIR):
    """Accepted rules only, cached on the directory's file signature."""
    directory = Path(directory)
    files = sorted(list(directory.glob('*.json')) + list((directory / 'receipts').glob('*.json'))) if directory.exists() else []
    signature = tuple((str(p), p.stat().st_mtime_ns, p.stat().st_size) for p in files)
    cached = _LOADED.get(str(directory))
    if cached and cached[0] == signature:
        return cached[1]
    rules = []
    for r in (read_rule_files(directory) if directory.exists() else []):
        receipt = accepted_receipt(r, directory / 'receipts')
        if receipt: rules.append({**r, '_digest': digest(r), '_sequence': receipt.get('sequence') or 0})
    rules.sort(key=lambda r: r['_sequence'])  # the order the gate accepted them in (each was checked behind the earlier ones)
    _LOADED[str(directory)] = (signature, rules)
    return rules


@contextlib.contextmanager
def using(rules):
    """Evaluate with exactly these rules (the gate, propose and tests); receipts are not consulted."""
    _OVERRIDE.append(list(rules))
    try:
        yield
    finally:
        _OVERRIDE.pop()


def active_rules():
    if _OVERRIDE: return _OVERRIDE[-1]
    if os.environ.get('AGERE_COMPILER_RULES', '1') in ('0', 'off', 'false'): return []
    return load()


# ---------------------------------------------------------------- vocabularies (from the hand-written compiler)
def _lc():
    from . import local_compiler
    return local_compiler


def _alt(forms):
    forms = sorted({f for f in forms if f}, key=len, reverse=True)
    return '(?:' + '|'.join(forms) + ')'


def _alias_forms(table):
    out = {}
    for key, aliases in table.items():
        for a in ([aliases] if isinstance(aliases, str) else aliases):
            if len(a) >= 2: out[re.escape(a).replace('\\ ', ' ')] = key  # spaces stay spaces: patterns read them as \s+
    return out


def _lookup(text, table):
    """Phrase -> key via the alias table (longest alias that matches the whole phrase)."""
    for form, key in sorted(table.items(), key=lambda kv: -len(kv[0])):
        if re.fullmatch(form, text): return key
    return None


def _list_regex(item):
    return item + r'(?:(?:,\s*|\s+)(?:(?:and|or)\s+)?' + item + r')*'


def _split_list(text):
    parts = [p.strip() for p in re.split(r',\s*(?:and\s+|or\s+)?|\s+(?:and|or)\s+', text) if p.strip()]
    join = 'and' if re.search(r'\band\b', text) and not re.search(r'\bor\b', text) else 'or'
    return parts, join


AGE_RX = (r'(?:(?:aged?|ages) )?(?:\d{1,3}\s*(?:-|–|to|through)\s*\d{1,3}(?: years?(?: old)?)?|\d{1,3}\s*(?:\+|(?:years? (?:old )?)?(?:and|or) (?:older|over|above))'
          r'|(?:over|older than|at least) \d{1,3}|(?:under|younger than) \d{1,3})')
POP_NOUN = r'(?:patients|members|adults|children|kids|seniors|older adults|women|men|people|beneficiaries|adolescents|enrollees)'
THRESHOLD_RX = (r'(?:(?:of |at |is |was |level of )?between -?\d+(?:\.\d+)?\s*%? and -?\d+(?:\.\d+)?\s*%?|'
                r'(?:(?:of|at|is|was|level of) )?(?:(?:at or above|at or below|at least|at most|no less than|no more than|greater than|less than|'
                r'more than|higher than|lower than|over|above|under|below|exceeding|>=|<=|>|<)\s*)?-?\d+(?:\.\d+)?\s*%?'
                r'(?:\s*(?:or (?:more|higher|above|greater|over)|or (?:less|lower|below|under)|and (?:above|over|higher)|and (?:below|under)))?)')


ICD_RANGE_RX = r'(?:[a-z]\d{2}(?:\.\d{1,4})?\s*(?:-|–|to|through)\s*[a-z]\d{2}(?:\.\d{1,4})?|[a-z]\d{2}(?:\.\d{1,4})?)(?: codes?)?'


def _icd_range(text):
    """'z55-z65' -> category-level ICD-10 range predicate on a code column (same letter only); 'e11.9' -> prefix match."""
    m = re.fullmatch(r'([a-z])(\d{2})(?:\.\d{1,4})?\s*(?:-|–|to|through)\s*([a-z])(\d{2})(?:\.\d{1,4})?(?: codes?)?', text)
    if m:
        if m.group(1) != m.group(3) or int(m.group(2)) > int(m.group(4)): return None
        letter, lo, hi = m.group(1).upper(), int(m.group(2)), int(m.group(4))
        return {'text': text, 'low': f'{letter}{lo:02d}', 'high': f'{letter}{hi:02d}',
                'pred_fmt': f"(upper(substr({{col}}, 1, 1)) = '{letter}' AND cast(substr({{col}}, 2, 2) AS integer) BETWEEN {lo} AND {hi})"}
    m = re.fullmatch(r'([a-z]\d{2}(?:\.\d{1,4})?)(?: codes?)?', text)
    if not m: return None
    code = m.group(1).upper()
    return {'text': text, 'low': code, 'high': code, 'pred_fmt': f"(upper(replace({{col}}, '.', '')) LIKE '{code.replace('.', '')}%')"}


def _num(token):
    value = float(token)
    return int(value) if value == int(value) and '.' not in token else value


def _threshold(text):
    """Comparator phrase -> {op, value, sql_op, pred_fmt}; 'between a and b' is inclusive."""
    b = re.search(r'between (-?\d+(?:\.\d+)?)\s*%? and (-?\d+(?:\.\d+)?)', text)
    if b:
        lo, hi = sorted((_num(b.group(1)), _num(b.group(2))))
        return {'op': 'between', 'value': lo, 'high': hi, 'sql_op': 'BETWEEN', 'pred_fmt': f'{{col}} BETWEEN {lo} AND {hi}'}
    out = _threshold_one(text)
    if out: out['pred_fmt'] = f"{{col}} {out['sql_op']} {out['value']}"
    return out


def _threshold_one(text):
    m = re.search(r'-?\d+(?:\.\d+)?', text)
    if not m: return None
    value = float(m.group(0)); value = int(value) if value == int(value) and '.' not in m.group(0) else value
    words = text[:m.start()] + ' ' + text[m.end():]
    op = None
    for rx, o in [(r'at or above|at least|no less than|>=|or (?:more|higher|above|greater|over)|and (?:above|over|higher)', 'ge'),
                  (r'at or below|at most|no more than|<=|or (?:less|lower|below|under)|and (?:below|under)', 'le'),
                  (r'greater than|more than|higher than|over|above|exceeding|>', 'gt'), (r'less than|lower than|under|below|<', 'lt')]:
        if re.search(rx, words): op = o; break
    if op is None:
        if re.search(r'\b(?:of|at|is|was|level of)\b', words) or not words.strip(): op = 'eq'
        else: return None
    return {'op': op, 'value': value, 'sql_op': SQL_OPS[op]}


def _age(text, field='age_latest'):
    """Age phrase -> list of (op, value) on the patient's latest reported age."""
    low = text
    m = re.search(r'(\d{1,3})\s*(?:-|–|to|through)\s*(\d{1,3})', low)
    if m: return [('ge', int(m.group(1))), ('le', int(m.group(2)))]
    m = re.search(r'(\d{1,3})\s*(?:\+|(?:years? (?:old )?)?(?:and|or) (?:older|over|above))', low)
    if m: return [('ge', int(m.group(1)))]
    m = re.search(r'(?:over|older than) (\d{1,3})', low)
    if m: return [('gt', int(m.group(1)))]
    m = re.search(r'at least (\d{1,3})', low)
    if m: return [('ge', int(m.group(1)))]
    m = re.search(r'(?:under|younger than) (\d{1,3})', low)
    if m: return [('lt', int(m.group(1)))]
    return None


def _noun_constraints(noun):
    return {'adults': [('age', 'ge', 18)], 'children': [('age', 'lt', 18)], 'kids': [('age', 'lt', 18)],
            'adolescents': [('age', 'ge', 12), ('age', 'le', 17)], 'seniors': [('age', 'ge', 65)], 'older adults': [('age', 'ge', 65)],
            'women': [('sex', 'eq', 'F')], 'men': [('sex', 'eq', 'M')]}.get(noun, [])


# ---------------------------------------------------------------- periods
def _shift_months(d, months):
    y, m = int(d[:4]), int(d[5:7]) + months
    while m <= 0: y, m = y - 1, m + 12
    while m > 12: y, m = y + 1, m - 12
    import calendar
    return f'{y:04d}-{m:02d}-{min(int(d[8:10]), calendar.monthrange(y, m)[1]):02d}'


def _period(text, as_of):
    """Analyst period phrase (after local_compiler paraphrase) -> (start, end) or None."""
    from datetime import date, timedelta
    t = text.strip()
    y = int(as_of[:4]); q = (int(as_of[5:7]) - 1) // 3
    qstart = lambda yy, qq: f'{yy:04d}-{3 * qq + 1:02d}-01'
    qend = lambda yy, qq: [f'{yy}-03-31', f'{yy}-06-30', f'{yy}-09-30', f'{yy}-12-31'][qq]
    if re.fullmatch(r'ever|to date|all time|at any time', t): return ('0001-01-01', as_of)
    m = re.fullmatch(r'(?:in|within) (\d+) (days?|weeks?|months?|years?)', t)  # backward-looking: "checked in 12 months"
    if m: t = f'last {m.group(1)} {m.group(2)}'
    m = re.fullmatch(r'(?:in |during |for )?(\d{4}) ytd', t) or re.fullmatch(r'(?:in |during |for )?this (?:calendar )?year', t)
    if m: return (f'{m.group(1) if m.groups() else y}-01-01', as_of)
    m = re.fullmatch(r'(?:in |during |for )(\d{4})', t)
    if m: return (f'{m.group(1)}-01-01', min(f'{m.group(1)}-12-31', as_of))
    if re.fullmatch(r'(?:in |during )?last (?:calendar )?year|(?:in |during )?the (?:prior|previous) (?:calendar )?year', t): return (f'{y - 1}-01-01', f'{y - 1}-12-31')
    m = re.fullmatch(r'(?:in |within |during |over )?(?:the )?(?:last|past|prior|previous) (\d+|one|two|three|six|twelve|eighteen)? ?(days?|weeks?|months?|years?|quarters?)', t)
    calendar_last = re.fullmatch(r'(?:in |during )?(?:the )?(?:last|prior|previous) (?:quarter|month)', t)  # "last quarter" = the previous calendar quarter
    if m and not calendar_last:
        n = int(m.group(1)) if m.group(1) and m.group(1).isdigit() else NUMBER_WORDS.get(m.group(1) or 'one', 18 if m.group(1) == 'eighteen' else 1)
        unit = m.group(2)[0]
        d = date.fromisoformat(as_of)
        if unit == 'd': start = (d - timedelta(days=n)).isoformat()
        elif unit == 'w': start = (d - timedelta(days=7 * n)).isoformat()
        elif unit == 'm': start = _shift_months(as_of, -n)
        elif unit == 'q': start = _shift_months(as_of, -3 * n)
        else: start = _shift_months(as_of, -12 * n)
        # N units ending on the snapshot day: the window starts the day after the same date N units earlier
        # ("last 12 months" at 2029-08-15 = 2028-08-16..2029-08-15, the bench and CPT-route convention)
        return ((date.fromisoformat(start) + timedelta(days=1)).isoformat(), as_of)
    if re.fullmatch(r'(?:in |during )?(?:the )?last quarter|(?:in |during )?the (?:prior|previous) quarter', t):
        yy, qq = (y, q - 1) if q else (y - 1, 3)
        return (qstart(yy, qq), qend(yy, qq))
    if re.fullmatch(r'(?:in |during )?this quarter|(?:in |during )?the current quarter|quarter to date', t): return (qstart(y, q), as_of)
    if re.fullmatch(r'(?:in |during )?(?:the )?last month|(?:in |during )?the (?:prior|previous) month', t):
        first = as_of[:8] + '01'; prev = _shift_months(first, -1)
        return (prev, (date.fromisoformat(first) - timedelta(days=1)).isoformat())
    if re.fullmatch(r'(?:in |during )?this month|month to date', t): return (as_of[:8] + '01', as_of)
    m = re.fullmatch(r'(?:in |during )?(?:the )?(?:q([1-4])|(first|second|third|fourth) quarter(?: of)?) (\d{4})', t)
    if m:
        n = int(m.group(1)) - 1 if m.group(1) else ['first', 'second', 'third', 'fourth'].index(m.group(2))
        return (qstart(int(m.group(3)), n), min(qend(int(m.group(3)), n), as_of))
    m = re.fullmatch(r'since (\d{4})', t)
    if m: return (f'{m.group(1)}-01-01', as_of)
    return None


PERIOD_RX = (r'(?:ever|to date|(?:in|within) \d+ (?:days?|weeks?|months?|years?)|(?:in |during |for )?\d{4} ytd|(?:in |during |for )?this (?:calendar )?year|(?:in |during |for )\d{4}(?![\d-])|'
             r'(?:in |during )?last (?:calendar )?year|(?:in |during )?the (?:prior|previous) (?:calendar )?year|'
             r'(?:in |within |during |over )?(?:the )?(?:last|past|prior|previous) (?:(?:\d+|one|two|three|six|twelve|eighteen) )?(?:days?|weeks?|months?|years?|quarters?)|'
             r'(?:in |during )?(?:the )?last (?:quarter|month)|(?:in |during )?this (?:quarter|month)|(?:in |during )?the current quarter|'
             r'(?:in |during )?(?:the )?(?:q[1-4]|(?:first|second|third|fourth) quarter(?: of)?) \d{4}|since \d{4})')


# ---------------------------------------------------------------- slot types
class Slot:
    """regex(profile, spec) -> pattern text (no capture groups); resolve(text, profile, as_of, spec) -> value dict or None."""


def _profile_values(profile, stream, field):
    spec = (((profile or {}).get('streams') or {}).get(stream) or {}).get('fields', {}).get(field) or {}
    return [v for v in (spec.get('values') or {}) if isinstance(v, str) and 2 <= len(v) <= 60]


def _value_regex(value):
    parts = [t for t in re.split(r'[_\s/-]+', value.lower()) if t]
    return r'[\s_/-]*'.join(re.escape(t) for t in parts)


def slot_regex(kind, spec, profile):
    lc = _lc()
    if kind == 'period': return PERIOD_RX
    if kind == 'condition': return _list_regex(_alt(_alias_forms({**lc.CONDITION_ALIASES, **spec.get('aliases', {})})))
    if kind == 'measurement': return _list_regex(_alt(_alias_forms({**lc.MEASUREMENT_ALIASES, **spec.get('aliases', {})})))
    if kind == 'encounter': return _alt(_alias_forms(lc.ENCOUNTER_ALIASES))
    if kind == 'drug': return _list_regex(_alt(list({**lc.DRUG_ALIASES, **lc.DRUG_GROUP_ALIASES, **spec.get('drug_aliases', {})}.values())))
    if kind == 'age': return AGE_RX
    if kind == 'threshold': return THRESHOLD_RX
    if kind == 'icd_range': return ICD_RANGE_RX
    if kind == 'number': return r'(?:\d+(?:\.\d+)?|' + '|'.join(k for k in NUMBER_WORDS if len(k) > 2) + ')'
    if kind in ('option', 'group_by'):
        return _alt(list(spec['options']))
    if kind == 'value':
        return _list_regex(_alt([_value_regex(v) for v in _profile_values(profile, spec['stream'], spec['field'])] or ['(?!x)x']))
    if kind == 'population':
        return _population_regex(spec)
    raise ValueError(f'Unknown slot type: {kind}')


def resolve_slot(kind, text, spec, profile, as_of):
    lc = _lc()
    text = text.strip()
    if kind == 'period':
        p = _period(text, as_of)
        if not p or p[0] > p[1]: return None
        return {'start': p[0], 'end': p[1], 'text': text}
    if kind == 'condition':
        parts, join = _split_list(text)
        concepts = [_lookup(p, _alias_forms({**lc.CONDITION_ALIASES, **spec.get('aliases', {})})) for p in parts]
        if not concepts or None in concepts: return None
        return _concept_value(concepts, join)
    if kind == 'measurement':
        parts, join = _split_list(text)
        cs = [_lookup(p, _alias_forms({**lc.MEASUREMENT_ALIASES, **spec.get('aliases', {})})) for p in parts]
        if not cs or None in cs: return None
        quoted = ', '.join(f"'{c}'" for c in cs)
        return {'concept': cs[0], 'concepts': cs, 'join': join, 'need': len(cs) if join == 'and' else 1, 'quoted': f"'{cs[0]}'", 'in': f'({quoted})'}
    if kind == 'encounter':
        c = _lookup(text, _alias_forms(lc.ENCOUNTER_ALIASES))
        return {'concept': c, 'quoted': f"'{c}'"} if c else None
    if kind == 'drug':
        return _drug_value(text, spec)
    if kind == 'age':
        cons = _age(text)
        if not cons: return None
        where = ' AND '.join(f"age_latest {SQL_OPS[o]} {v}" for o, v in cons)
        return {'where': where, 'atoms': [{'kind': 'patient', 'field': 'age_latest', 'op': o, 'value': v} for o, v in cons]}
    if kind == 'threshold':
        return _threshold(text)
    if kind == 'icd_range':
        return _icd_range(text)
    if kind == 'number':
        n = NUMBER_WORDS.get(text)
        if n is None:
            try: n = float(text); n = int(n) if n == int(n) else n
            except ValueError: return None
        return {'value': n}
    if kind in ('option', 'group_by'):
        for rx, value in spec['options'].items():
            if re.fullmatch(rx, text):
                return value if isinstance(value, dict) else {'value': value}
        return None
    if kind == 'value':
        values = _profile_values(profile, spec['stream'], spec['field'])
        parts, _ = _split_list(text)
        found = []
        for p in parts:
            v = next((v for v in values if re.fullmatch(_value_regex(v), p)), None)
            if v is None: return None
            found.append(v)
        return {'values': found, 'in': '(' + ', '.join("'" + v.replace("'", "''") + "'" for v in found) + ')', 'text': text}
    if kind == 'population':
        return _population(text, spec, profile)
    raise ValueError(f'Unknown slot type: {kind}')


def _concept_value(concepts, join):
    quoted = ', '.join(f"'{c}'" for c in concepts)
    if join == 'and' and len(concepts) > 1:
        where = ' AND '.join(f"patient_id IN (SELECT patient_id FROM concept_patient WHERE concept = '{c}')" for c in concepts)
    else:
        where = f"patient_id IN (SELECT DISTINCT patient_id FROM concept_patient WHERE concept IN ({quoted}))"
    return {'concepts': concepts, 'join': join, 'in': f'({quoted})', 'where': where}


def _drug_value(text, spec=None):
    lc = _lc()
    from .drug_classes import GROUPS
    aliases = {**lc.DRUG_ALIASES, **lc.DRUG_GROUP_ALIASES, **(spec or {}).get('drug_aliases', {})}
    parts, join = _split_list(text)
    classes = []
    for p in parts:
        cls = next((c for c, rx in sorted(aliases.items(), key=lambda kv: -len(kv[1])) if re.fullmatch(rx, p)), None)
        if cls is None: return None
        classes += [c for c in GROUPS.get(cls, [cls]) if c not in classes]
    quoted = ', '.join(f"'{c}'" for c in classes)
    return {'classes': classes, 'join': join, 'in': f'({quoted})',
            'where': f"patient_id IN (SELECT DISTINCT patient_id FROM medication WHERE drug_class IN ({quoted}))"}


def _pop_parts(spec):
    lc = _lc()
    cond = _alt(_alias_forms({**lc.CONDITION_ALIASES, **(spec or {}).get('condition_aliases', {})}))
    meas = _alt(_alias_forms({**lc.MEASUREMENT_ALIASES, **(spec or {}).get('measurement_aliases', {})}))
    drug = _alt(list({**lc.DRUG_ALIASES, **lc.DRUG_GROUP_ALIASES, **(spec or {}).get('drug_aliases', {})}.values()))
    return {'age': r' (?:(?:who are |that are )?' + AGE_RX + r'|of age \d{1,3} and older)',
            'cond': r' (?:and )?(?:with|who have|having|diagnosed with|with diagnosed)(?: an?)? (' + _list_regex(cond) + r')(?: diagnos[ie]s)?',
            'meas': r' (?:(?:and )?(?:with|who have|having|have|has)|and)(?: an?)?(?: latest| most recent)? (' + meas + r') (' + THRESHOLD_RX + r')',
            'drug': r' (?:on|taking|prescribed|using|filling)(?: an?)? (' + _list_regex(drug) + r')(?: therapy)?'}


def _population_regex(spec):
    parts = _pop_parts(spec)
    clause = '(?:' + '|'.join(parts.values()) + ')'
    clause = re.sub(r'\((?!\?)', '(?:', clause)  # no capture groups inside a slot
    return r'(?:(?:our|all|the) )?(?:(?:attributed|assigned) )?(?:(?:female|male|adult) )?' + POP_NOUN + '(?:' + clause + '){0,4}'


def _population(text, spec, profile):
    """'women 65 and older with diabetes on a statin' -> patient WHERE clause (latest reported age, reported sex).
    Clauses may come in any order; each must be understood or the slot does not resolve."""
    lc = _lc()
    spec = spec or {}
    conds_table = _alias_forms({**lc.CONDITION_ALIASES, **spec.get('condition_aliases', {})})
    meas_table = _alias_forms({**lc.MEASUREMENT_ALIASES, **spec.get('measurement_aliases', {})})
    m = re.match(r'(?:(?:our|all|the) )?((?:attributed|assigned) )?(?:(female|male|adult) )?(' + POP_NOUN + r')', text)
    if not m: return None
    rest = text[m.end():]
    attributed = bool(m.group(1))
    cons = _noun_constraints(m.group(3)) + ({'female': [('sex', 'eq', 'F')], 'male': [('sex', 'eq', 'M')], 'adult': [('age', 'ge', 18)]}.get(m.group(2), []))
    parts = _pop_parts(spec)
    clauses = []
    W = {'basis': 'all_observed', 'start': '', 'end': ''}
    atoms_all, atoms_any, expressible = [], [], not attributed  # the same population as a typed cohort specification
    while rest.strip():
        for kind, rx in parts.items():
            mm = re.match(rx, rest)
            if mm: break
        else:
            return None
        if kind == 'age':
            ages = _age(mm.group(0)) or ([('ge', int(re.search(r'\d+', mm.group(0)).group(0)))] if 'of age' in mm.group(0) else None)
            if not ages: return None
            cons = [c for c in cons if c[0] != 'age'] + [('age', o, v) for o, v in ages]
        elif kind == 'cond':
            names, join = _split_list(mm.group(1))
            concepts = [_lookup(p, conds_table) for p in names]
            if None in concepts: return None
            clauses.append(_concept_value(concepts, join)['where'])
            atoms = [{'kind': 'concept', 'concept': c, 'window': dict(W)} for c in concepts]
            if join == 'or' and len(atoms) > 1:
                if atoms_any: expressible = False  # two either/or groups do not fit one cohort specification
                atoms_any += atoms
            else: atoms_all += atoms
        elif kind == 'meas':
            concept = _lookup(mm.group(1), meas_table); thr = _threshold(mm.group(2))
            if not concept or not thr: return None
            clauses.append(f"patient_id IN (SELECT patient_id FROM measurement_latest WHERE concept = '{concept}' "
                           f"AND {thr['pred_fmt'].format(col='value_canonical')})")
            bounds = [('ge', thr['value']), ('le', thr['high'])] if thr['op'] == 'between' else [(thr['op'], thr['value'])]
            if any(o == 'eq' for o, _ in bounds): expressible = False
            atoms_all += [{'kind': 'measurement', 'concept': concept, 'op': o, 'value': v, 'aggregate': 'latest', 'window': dict(W)} for o, v in bounds]
        else:
            expressible = False  # drug classes are not cohort predicates
            d = _drug_value(mm.group(1), spec)
            if not d: return None
            clauses.append(d['where'])
        rest = rest[mm.end():]
    pre = [f"age_latest {SQL_OPS[op]} {value}" if kind == 'age' else f"sex_reported = '{value}'" for kind, op, value in cons]
    if attributed: pre.append('pcp_clinician_id IS NOT NULL')  # prospective attribution: an assigned primary-care relationship
    where = ' AND '.join(pre + clauses) or '1 = 1'
    atoms_all = [{'kind': 'patient', 'field': 'age_latest' if k == 'age' else 'sex_reported', 'op': op, 'value': v} for k, op, v in cons] + atoms_all
    out = {'where': where, 'sql': f'SELECT patient_id FROM patient WHERE {where}', 'text': text}
    if expressible: out['spec'] = {'all': atoms_all, 'any': atoms_any, 'exclude': []}
    return out


# ---------------------------------------------------------------- pattern compilation and matching
def _compile_pattern(rule, profile):
    slots = rule.get('slots') or {}
    reads_profile = any(s.get('type') == 'value' for s in slots.values())  # observed-value slots differ per profile
    key = (rule.get('_digest') or digest(rule), id(profile) if reads_profile else None)
    cached = _PATTERNS.get(key)
    if cached and (not reads_profile or cached[0] is profile): return cached[1]
    out, pos, seen = [], 0, {}
    text = rule['pattern']
    for m in SLOT.finditer(text):
        out.append(text[pos:m.start()])
        name, optional = m.group(1), bool(m.group(2))
        if name not in slots: raise ValueError(f"{rule['id']}: slot {name} not declared")
        group = name if not seen.get(name) else f'{name}__{seen[name]}'  # a slot may appear in alternative branches
        seen[name] = seen.get(name, 0) + 1
        body = f"(?P<{group}>{slot_regex(slots[name]['type'], slots[name], profile)})"
        if optional:  # absorb the preceding space so an absent slot leaves no gap
            if out and out[-1].endswith(' '): out[-1] = out[-1][:-1]; body = r'(?:\s+' + body + ')?'
            else: body = '(?:' + body + ')?'
        out.append(body)
        pos = m.end()
    out.append(text[pos:])
    rx = ''.join(out).replace(' ', r'\s+')
    compiled = re.compile(r'(?<![a-z0-9])' + rx + r'(?![a-z0-9])')
    _PATTERNS[key] = (profile, compiled)
    return compiled


def _requires_met(rule, profile):
    req = rule.get('requires') or {}
    streams = (profile or {}).get('streams') or {}
    for s in req.get('streams', []):
        if not (streams.get(s) or {}).get('records'): return False
    for s, fields in (req.get('fields') or {}).items():
        have = (streams.get(s) or {}).get('fields') or {}
        if any(not (have.get(f) or {}).get('populated') for f in fields): return False
    pf = ((profile or {}).get('patients') or {}).get('fields') or {}
    if any(not (pf.get(f) or {}).get('populated') for f in req.get('patient_fields', [])): return False
    for s, field, values in req.get('values', []):  # observed values that must be present (e.g. CPT coding)
        if not set(values) & set(_profile_values(profile, s, field)): return False
    return True


def normalize(question, as_of):
    """The compiler's own normalisation (snapshot phrasing, numbers, framing, paraphrases), lower case, end punctuation off."""
    lc = _lc()
    q = lc._norm(question, as_of)
    low, _ = lc._paraphrase(q, as_of)
    return re.sub(r'\s+', ' ', re.sub(r'[?!.]+\s*$', '', low.replace('’', "'"))).strip(' ,')


ALLOWED_EXTRA = {'our', 'we', 'us', 'currently', 'overall', 'so', 'far', 'right', 'now', 'today', 'please', 'the', 'what',
                 'many', 'how', 'much', 'show', 'give', 'me', 'list', 'which', 'who', 'are', 'is', 'were', 'was', 'did', 'do', 'does'}


def allowed_words(rule):
    lc = _lc()
    return (lc.FILLER - lc.OPERATOR_WORDS) | ALLOWED_EXTRA | set(rule.get('allow') or [])


def unexplained(low, span, rule, profile):
    """Words of the question outside the matched pattern that carry meaning: anything not filler / rule-allowed
    (numbers and operator words always count)."""
    lc = _lc()
    r = lc.Reader(low)
    r.profile = profile
    for i in range(span[0], span[1]): r.used[i] = True
    for rx in rule.get('consume') or []:
        r.take(rx, None)
    ok = allowed_words(rule)
    return [w for w in r.leftover() if w not in ok]


def match(rule, question, profile, as_of, low=None):
    """-> (graph, detail) when the rule fires, else (None, detail with the reason)."""
    if not _requires_met(rule, profile): return None, {'reason': 'requires not met'}
    low = low if low is not None else normalize(question, as_of)
    m = _compile_pattern(rule, profile).search(low)
    if not m: return None, {'reason': 'pattern'}
    values = {}
    groups = m.groupdict()
    for name, spec in (rule.get('slots') or {}).items():
        text = next((v for k, v in groups.items() if v is not None and (k == name or k.startswith(name + '__'))), None)
        if text is None:
            if 'default' in spec:
                values[name] = _default(spec, as_of)
                continue
            return None, {'reason': f'slot {name} missing'}
        v = resolve_slot(spec['type'], text, spec, profile, as_of)
        if v is None: return None, {'reason': f'slot {name} unresolved: {text}'}
        values[name] = v
    values['as_of'] = {'value': as_of}  # reserved: the snapshot date, for templates that compare periods to it
    for name, variant in (rule.get('variants') or {}).items():  # named alternatives in the pattern pick a plan variant
        if groups.get(name) is not None: values['variant'] = variant; break
    left = unexplained(low, m.span(), rule, profile)
    if left: return None, {'reason': 'unexplained: ' + ', '.join(left[:8]), 'unexplained': left}
    for guard in rule.get('reject_if') or []:  # phrasings the rule must leave to other engines
        if re.search(guard, low): return None, {'reason': f'reject_if {guard}'}
    graph = render(rule, values, question)
    from .analysis_graph import validate
    try:
        graph = validate(graph, as_of)
    except (ValueError, KeyError, TypeError) as exc:
        return None, {'reason': f'invalid plan: {exc}'[:300]}
    return graph, {'slots': {k: v.get('text', v) if isinstance(v, dict) else v for k, v in values.items()}, 'span': m.span()}


def _default(spec, as_of):
    d = spec['default']
    if spec['type'] == 'period':
        if d == 'all': return {'start': '0001-01-01', 'end': as_of, 'text': '', 'all': True}
        if d == 'open': return {'start': '', 'end': '', 'text': ''}  # operator parameters: no period named, the engine's default window
        p = _period(d, as_of)
        return {'start': p[0], 'end': p[1], 'text': d}
    return d if isinstance(d, dict) else {'value': d}


def _fill(value, values):
    """Replace {slot} / {slot.attr} placeholders. A string that is exactly one placeholder keeps the slot's type."""
    if isinstance(value, dict): return {k: _fill(v, values) for k, v in value.items()}
    if isinstance(value, list): return [_fill(v, values) for v in value]
    if not isinstance(value, str): return value
    whole = PLACEHOLDER.fullmatch(value)
    def get(name, attr, column=None):
        if name not in values: raise ValueError(f'Unknown slot {name}')
        v = values[name]
        if column and isinstance(v, dict) and f'{attr}_fmt' in v:  # {threshold.pred:value_canonical}, {code.pred:j.value}
            return v[f'{attr}_fmt'].format(col=column)
        if column:  # {period.where:column} -> a date predicate on that column (all time = up to the snapshot)
            if not (isinstance(v, dict) and 'start' in v and attr == 'where'): raise ValueError(f'{name}.{attr}:{column} is only for periods')
            if v.get('all'): return f"({column} IS NULL OR {column} <= '{v['end']}')"
            return f"{column} BETWEEN '{v['start']}' AND '{v['end']}'"
        if attr is None: attr = 'value' if isinstance(v, dict) and 'value' in v else 'sql' if isinstance(v, dict) and 'sql' in v else None
        out = v.get(attr) if isinstance(v, dict) and attr else v
        if out is None: raise ValueError(f'Slot {name} has no {attr}')
        return out
    if whole: return get(whole.group(1), whole.group(2), whole.group(3))
    return PLACEHOLDER.sub(lambda mm: str(get(mm.group(1), mm.group(2), mm.group(3))), value)


def render(rule, values, question):
    plan = rule['plan']
    interp = ' '.join(str(question).split())[:1900]
    if plan['kind'] == 'sql':
        sql = _fill(plan['query'], values)
        return {'interpretation': interp, 'coverage': 'supported_observation', 'unmet_requirements': [],
                'nodes': [{'id': 'answer', 'operation': 'sql', 'inputs': [],
                           'parameters': {'query': re.sub(r'\s+', ' ', sql).strip(), 'returns': _fill(plan['returns'], values)},
                           'purpose': (_fill(plan.get('purpose') or rule.get('description') or rule['id'], values))[:900]}]}
    if plan['kind'] == 'operator':
        params = _fill(plan.get('parameters') or {}, values)
        nodes = []
        if plan.get('population'):
            spec = _fill(plan['population'], values)
            nodes.append({'id': 'population', 'operation': 'cohort', 'inputs': [], 'parameters': {'specification': spec},
                          'purpose': 'Population named in the question'})
        nodes.append({'id': 'answer', 'operation': plan['operation'], 'inputs': ['population'] if nodes else [], 'parameters': params,
                      'purpose': (_fill(plan.get('purpose') or rule.get('description') or rule['id'], values))[:900]})
        return {'interpretation': interp, 'coverage': 'supported_observation', 'unmet_requirements': [], 'nodes': nodes}
    raise ValueError(f"Unknown plan kind {plan['kind']}")


def apply(question, profile, as_of, info=None, rules=None):
    """Called by local_compiler.compile when the hand-written routes declined. -> (graph, info); graph None when no rule fires."""
    info = dict(info or {})
    rules = active_rules() if rules is None else rules
    if not rules: return None, info
    try:
        low = normalize(question, as_of)
    except Exception:  # normalisation is the compiler's own; never let a rule break the decline path
        return None, info
    near = []
    for rule in rules:
        try:
            graph, detail = match(rule, question, profile, as_of, low)
        except (ValueError, KeyError, re.error) as exc:
            graph, detail = None, {'reason': f'rule error: {exc}'[:200]}
        if graph is not None:
            out = {**info, 'route': 'local_compiler', 'compiler_rule': {'id': rule['id'], 'version': rule['version'], 'engine': rule['engine'],
                                                                         'interpreter': VERSION, **detail},
                   'unexplained': [], 'reason': f"compiler rule {rule['id']}@{rule['version']}", **{k: v for k, v in (rule.get('marks') or {}).items() if k in MARKS}}
            out.pop('requires_input', None)
            return graph, out
        if detail.get('unexplained'): near.append({'rule': rule['id'], 'unexplained': detail['unexplained'][:6]})
    if near: info['compiler_rule_near_misses'] = near[:5]
    return None, info
