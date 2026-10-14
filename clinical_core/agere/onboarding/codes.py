"""Value-pattern recognisers for healthcare code systems, dates and money.

Each recogniser answers "does this literal look like a value of system X" and, where
the system defines one, "is it valid" (LOINC and NPI check digits). Column-level
detection lives in profile.py; these functions are pure and dependency-free so a
domain pack can add systems without touching the profiler.
"""
from datetime import date, datetime
import re

ICD10CM = re.compile(r'^[A-TV-Z][0-9][0-9AB](?:\.?[0-9A-TV-Z]{1,4})?$')
ICD10_DOTTED = re.compile(r'^[A-TV-Z][0-9][0-9AB]\.[0-9A-TV-Z]{1,4}$')
LOINC = re.compile(r'^\d{1,5}-\d$')
NPI = re.compile(r'^\d{10}$')
NDC = re.compile(r'^(?:\d{11}|\d{4}-\d{4}-\d{2}|\d{5}-\d{3}-\d{2}|\d{5}-\d{4}-\d{1}|\d{5}-\d{4}-\d{2})$')
CPT = re.compile(r'^\d{4}[0-9FTU]$')
HCPCS = re.compile(r'^[A-V]\d{4}$')
RXNORM = re.compile(r'^\d{2,8}$')
SNOMED = re.compile(r'^\d{6,18}$')
CVX = re.compile(r'^\d{1,3}$')
POS = re.compile(r'^\d{2}$')
TIN = re.compile(r'^\d{2}-?\d{7}$')
ZIP = re.compile(r'^\d{5}(?:-\d{4})?$')
ISO_DATE = re.compile(r'^\d{4}-\d{2}-\d{2}$')
ISO_DATETIME = re.compile(r'^\d{4}-\d{2}-\d{2}[T ]\d{2}:\d{2}(?::\d{2}(?:\.\d+)?)?(?:Z|[+-]\d{2}:?\d{2})?$')
US_DATE = re.compile(r'^\d{1,2}/\d{1,2}/\d{4}$')
COMPACT_DATE = re.compile(r'^(?:19|20)\d{2}(?:0[1-9]|1[0-2])(?:0[1-9]|[12]\d|3[01])$')


def luhn_ok(digits):
    total = 0
    for i, ch in enumerate(reversed(digits)):
        d = int(ch)
        if i % 2 == 1:
            d *= 2
            if d > 9: d -= 9
        total += d
    return total % 10 == 0


def loinc_valid(code):
    """LOINC: mod-10 check digit over the code part (the published LOINC algorithm is Luhn on code+check)."""
    code = str(code).strip()
    return bool(LOINC.match(code)) and luhn_ok(code.replace('-', ''))


def npi_valid(code):
    """NPI: Luhn over the card-issuer prefix 80840 + the 10 digits."""
    code = str(code).strip()
    return bool(NPI.match(code)) and luhn_ok('80840' + code)


def icd10_valid(code):
    return bool(ICD10CM.match(str(code).strip().upper()))


def icd10_normalize(code):
    """Canonical ICD-10-CM spelling: upper case with the dot after the category (E11319 -> E11.319)."""
    if code is None: return None
    c = str(code).strip().upper().replace(' ', '')
    if not c: return None
    if '.' not in c and len(c) > 3 and ICD10CM.match(c): c = c[:3] + '.' + c[3:]
    return c


def ndc_normalize(code):
    """NDC to the 11-digit HIPAA form (5-4-2) without hyphens."""
    if code is None: return None
    c = str(code).strip()
    if '-' in c:
        a, b, d = c.split('-')
        return a.zfill(5) + b.zfill(4) + d.zfill(2)
    return c if len(c) == 11 else None


def parse_date(value):
    """ISO date (YYYY-MM-DD) from the common source spellings; None when the literal is not a date."""
    if value is None: return None
    if isinstance(value, (date, datetime)): return value.strftime('%Y-%m-%d')
    s = str(value).strip()
    if not s: return None
    try:
        if ISO_DATE.match(s): return date.fromisoformat(s).isoformat()
        if ISO_DATETIME.match(s): return date.fromisoformat(s[:10]).isoformat()
        if US_DATE.match(s): return datetime.strptime(s, '%m/%d/%Y').date().isoformat()
        if COMPACT_DATE.match(s): return datetime.strptime(s, '%Y%m%d').date().isoformat()
    except ValueError:
        return None
    return None


def is_number(v):
    if isinstance(v, bool): return False
    if isinstance(v, (int, float)): return True
    try:
        float(str(v)); return True
    except (TypeError, ValueError):
        return False


def decimals(v):
    s = str(v)
    if 'e' in s.lower(): return 0
    return len(s.split('.', 1)[1].rstrip('0')) if '.' in s else 0


PROCEDURE_HINTS = {'cpt', 'hcpcs', 'procedure', 'service', 'code', 'px', 'billing', 'proc'}
DIAGNOSIS_HINTS = {'icd', 'icd10', 'diagnosis', 'dx', 'diag', 'condition', 'code', 'problem'}
SURROGATE_HINTS = {'id', 'key', 'number', 'sk'}

# Ordered recognisers: (system, matcher, validator). A column is assigned the system with the highest
# match share among its non-null sample, subject to the disambiguation rules in classify_values().
SYSTEMS = {
    'ICD-10-CM': (lambda s: bool(ICD10CM.match(s.upper())), icd10_valid),
    'LOINC': (lambda s: bool(LOINC.match(s)), loinc_valid),
    'NPI': (lambda s: bool(NPI.match(s)), npi_valid),
    'NDC': (lambda s: bool(NDC.match(s)), lambda s: bool(NDC.match(s))),
    'CPT': (lambda s: bool(CPT.match(s)), lambda s: bool(CPT.match(s))),
    'HCPCS': (lambda s: bool(HCPCS.match(s)), lambda s: bool(HCPCS.match(s))),
    'RxNorm': (lambda s: bool(RXNORM.match(s)), lambda s: bool(RXNORM.match(s))),
}


def classify_values(values, name_tokens=()):
    """Code-system evidence for a column sample: {system: share_matching} plus the chosen system.

    Disambiguation rules (all value-level, name hints only break ties between numeric systems):
    * dotless ICD-10 vs HCPCS: HCPCS codes are always 5 characters; a column whose lengths vary is ICD-10.
    * CPT vs HCPCS: a claim-line column mixes both; reported as 'CPT/HCPCS' when together they cover >= 90%.
    * NPI (10 digits) needs the Luhn check on >= 90% of values, else it is a generic identifier.
    * RxNorm vs NDC vs plain integers: RxNorm requires a drug-like name hint (rx, cui, drug, ingredient,
      medication) because RxCUIs are bare integers.
    """
    strs = [str(v).strip() for v in values if v is not None and str(v).strip() != '']
    if not strs: return {'system': None, 'shares': {}, 'valid_share': None}
    n = len(strs)
    shares = {s: sum(1 for x in strs if m(x)) / n for s, (m, _) in SYSTEMS.items()}
    tokens = set(name_tokens)
    lengths = {len(x.replace('.', '')) for x in strs}
    choice = None
    if shares['NPI'] >= 0.9 and sum(1 for x in strs if npi_valid(x)) / n >= 0.9:
        choice = 'NPI'
    elif shares['LOINC'] >= 0.9:
        choice = 'LOINC'
    elif shares['NDC'] >= 0.9 and any(len(x.replace('-', '')) == 11 for x in strs) and not (tokens & {'npi', 'phone'}):
        choice = 'NDC'
    elif shares['CPT'] + shares['HCPCS'] >= 0.9 and lengths == {5} and (tokens & PROCEDURE_HINTS or (shares['HCPCS'] > 0 and shares['CPT'] > 0)):
        choice = 'HCPCS' if shares['CPT'] == 0 else ('CPT/HCPCS' if shares['HCPCS'] > 0 else 'CPT')
    elif shares['ICD-10-CM'] >= 0.9 and sum(1 for x in strs if x[:1].isalpha()) / n >= 0.9 and (
            tokens & DIAGNOSIS_HINTS or len({x[:1].upper() for x in strs}) >= 2):
        # One shared leading letter with no diagnosis hint is an identifier scheme (Z100200), not ICD-10.
        choice = 'ICD-10-CM'
    elif tokens & {'tin', 'tax', 'ein'} and sum(1 for x in strs if TIN.match(x)) / n >= 0.9:
        choice = 'TIN'
    elif shares['RxNorm'] >= 0.9 and (tokens & {'rx', 'rxcui', 'rxnorm', 'cui'} or (
            tokens & {'drug', 'ingredient', 'medication', 'med'} and tokens & {'code', 'concept'})):
        choice = 'RxNorm'
    valid = None
    if choice in SYSTEMS:
        valid = sum(1 for x in strs if SYSTEMS[choice][1](x.upper() if choice == 'ICD-10-CM' else x)) / n
    elif choice == 'CPT/HCPCS':
        valid = sum(1 for x in strs if CPT.match(x) or HCPCS.match(x)) / n
    elif choice == 'TIN':
        valid = sum(1 for x in strs if TIN.match(x)) / n
    return {'system': choice, 'shares': {k: round(v, 3) for k, v in shares.items() if v}, 'valid_share': valid}


def classify_semantic(values, storage, name_tokens=()):
    """Semantic value type of a column sample: date, datetime, money_cents, money_dollars, integer, decimal,
    boolean, categorical, identifier or text. Money needs both a numeric shape and a monetary name hint;
    cents vs dollars comes from the values (fractional parts) and the name (cents/amount/charge)."""
    strs = [v for v in values if v is not None and str(v).strip() != '']
    if not strs: return 'empty'
    n = len(strs)
    tokens = set(name_tokens)
    if sum(1 for v in strs if isinstance(v, str) and ISO_DATETIME.match(v.strip())) / n >= 0.9: return 'datetime'
    if sum(1 for v in strs if parse_date(v) is not None and not is_number(v)) / n >= 0.9: return 'date'
    if sum(1 for v in strs if isinstance(v, str) and COMPACT_DATE.match(v.strip()) and parse_date(v)) / n >= 0.95 and \
            tokens & {'date', 'dt', 'dos', 'day', 'birth', 'dob', 'from', 'thru', 'through', 'effective'}:
        return 'date'  # YYYYMMDD stored as text
    if storage == 'text' and all(isinstance(v, str) and v.strip().isdigit() for v in strs):
        lengths = {len(v.strip()) for v in strs}
        if len(lengths) == 1 or any(v.strip().startswith('0') for v in strs):  # ZIP, FIPS, revenue codes: digit labels, not quantities
            return 'categorical' if len(set(strs)) <= max(12, n * 0.05) else 'identifier'
    if all(is_number(v) for v in strs):
        nums = [float(v) for v in strs]
        money_hint = tokens & {'cents', 'amount', 'amt', 'charge', 'charges', 'paid', 'cost', 'price', 'payment', 'allowed',
                               'billed', 'balance', 'dollars', 'usd', 'responsibility', 'copay', 'coinsurance', 'deductible'}
        frac = sum(1 for x in nums if x != int(x)) / n
        if money_hint and not tokens & SURROGATE_HINTS and max(decimals(v) for v in strs) <= 2:
            if 'cents' in tokens: return 'money_cents'
            if 'dollars' in tokens or 'usd' in tokens or frac > 0.05: return 'money_dollars'
            return 'money_cents' if storage == 'integer' else 'money_dollars'
        if set(nums) <= {0.0, 1.0} and len(set(nums)) <= 2: return 'boolean'
        return 'integer' if frac == 0 else 'decimal'
    distinct = len(set(map(str, strs)))
    if distinct <= max(12, n * 0.05) and not (distinct == n and n > 3): return 'categorical'
    avg = sum(len(str(v)) for v in strs) / n
    if avg <= 40 and ' ' not in ''.join(str(v) for v in strs[:50]): return 'identifier'
    return 'text'
