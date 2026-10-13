"""External reference values (national/peer benchmarks) attached to answers whose metric matches.

The library is config/agere/benchmarks.json, researched offline by tools/benchmarks/build_benchmarks.py through the
Perplexity Agent API with cited sources. At answer time matching is deterministic and local ($0, no model call).
A benchmark is context next to the answer: it never alters the computed value, and it is not a target, a risk
adjustment or a statement that the populations are comparable.
"""
import json, re
from pathlib import Path

PATH = Path(__file__).resolve().parents[3] / 'config' / 'agere' / 'benchmarks.json'
PER_1000 = r'per[_ ]?1,?000|per thousand|member[- _]?years?|per_k\b'
# family -> (all of these patterns must match the answer's metric text, none of these may)
FAMILIES = {
    'ed_per_1000': ([r'\bed\b|emergency', PER_1000], []),
    'admits_per_1000': ([r'\badmi(?:t|ts|ssion|ssions)\b|\binpatient\b', PER_1000], []),
    'readmission_30d': ([r'readmi'], []),
    'pmpm': ([r'\bpmpm\b|per member per month|_pmpm'], []),
    'a1c_poor_control': ([r'a1c|hba1c|glycemic|hemoglobin a1c', r'poor[ _]control|>\s*9|over 9|above 9|greater than 9|a1c_poor_control|\b9(?:\.0)?\s*%'], []),
    'bp_control': ([r'blood pressure|\bbp\b|hypertension|\bcbp\b', r'control'], []),
    'statin_diabetes': ([r'statin', r'diabet'], []),
    'no_show': ([r'no[- _]?shows?'], []),
    'denial_rate': ([r'denial|denied'], []),
    'days_to_payment': ([r'days? to (?:payment|pay|paid)|days in (?:a/?r|accounts receivable)|payment lag|claim lag|time to payment|days_to_pa'], []),
}
PAYERS = {'medicare': r'medicare', 'medicaid': r'medicaid', 'commercial': r'commercial|employer|private insur'}
_CACHE = {}


def library(path=PATH):
    """Benchmark entries keyed by id (empty when the library has not been built)."""
    key = str(path)
    if key not in _CACHE:
        _CACHE[key] = json.loads(Path(path).read_text())['benchmarks'] if Path(path).exists() else {}
    return _CACHE[key]


def metric_text(answer, question=None):
    """(primary, secondary) words that say WHICH metric the answer reports.

    Primary: question, column names, stated definition, concepts. Secondary (used only when the primary text names
    no metric): method and query text, which also mention neighbouring measures."""
    table = answer.get('table') or []
    columns = sorted({k for r in table[:50] if isinstance(r, dict) for k in r})
    concepts = [c.get('concept') or c.get('label') for c in (answer.get('concepts') or []) if isinstance(c, dict)]
    primary = ' '.join([question or '', ' '.join(columns), str(answer.get('computed') or ''), ' '.join(str(c) for c in concepts if c),
                        ' '.join(d.get('definition', '') for d in answer.get('standards') or [] if isinstance(d, dict))])
    secondary = ' '.join([str(answer.get('method') or '')[:1500], str(answer.get('query') or '')[:1500]])
    return primary.lower().replace('_', ' '), secondary.lower().replace('_', ' ')


def families(text):
    return [f for f, (need, avoid) in FAMILIES.items()
            if all(re.search(p, text, re.I) for p in need) and not any(re.search(p, text, re.I) for p in avoid)]


def payers(answer, question=None):
    """Payer segments the answer is about: named in the question or appearing as table group values."""
    values = ' '.join(str(v) for r in (answer.get('table') or [])[:50] if isinstance(r, dict) for v in r.values()
                      if isinstance(v, str))
    text = f'{question or ""} {values}'.lower()
    return [p for p, rx in PAYERS.items() if re.search(rx, text)]


def display(entry):
    """'national/peer reference: X (source, year)' for one library entry."""
    unit = entry.get('unit') or ''
    def num(v): return f'{v:,.1f}'.rstrip('0').rstrip('.') if isinstance(v, float) else f'{v:,}'
    if entry.get('low') is not None and entry.get('high') is not None and entry['low'] != entry['high']:
        value = f"{num(entry['low'])}–{num(entry['high'])}"
        if entry.get('value') is not None: value = f"{num(entry['value'])} (range {value})"
    elif entry.get('value') is not None: value = num(entry['value'])
    else: return None
    sep = '' if unit.startswith('%') else ' '
    source = (entry.get('sources') or [{}])[0]
    name = source.get('publisher') or source.get('title') or 'source'
    return f"national/peer reference: {value}{sep}{unit}".rstrip() + f" ({name}, {entry.get('year') or 'year n/a'})"


def benchmarks(answer, question=None, *, path=PATH, limit=6):
    """Reference values whose metric matches the answer. Read-only: the answer's numbers are never changed."""
    if answer.get('status') in ('unsupported', 'grounding_failed'): return []
    if answer.get('answer_type') not in ('value', 'table'): return []  # counts and patient lists are not rates or PMPMs
    lib = library(path)
    if not lib: return []
    primary, secondary = metric_text(answer, question)
    found = families(primary) or families(secondary)
    if not found: return []
    wanted = payers(answer, question)
    out = []
    for family in found:
        entries = [e for e in lib.values() if e.get('family') == family and e.get('status') == 'cited']
        payer_specific = [e for e in entries if e.get('payer') in wanted] if wanted else []
        chosen = payer_specific or [e for e in entries if not e.get('payer')] or entries
        for e in chosen:
            text = display(e)
            if text:
                out.append({'id': e['id'], 'family': family, 'payer': e.get('payer'), 'label': e.get('label'), 'value': e.get('value'),
                            'low': e.get('low'), 'high': e.get('high'), 'unit': e.get('unit'), 'population': e.get('population'),
                            'year': e.get('year'), 'sources': e.get('sources', [])[:3], 'display': text,
                            'role': 'reference only; computed answer unchanged; populations and definitions may differ'})
    return out[:limit]
