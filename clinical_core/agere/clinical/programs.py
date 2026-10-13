"""Value-based-care program rulebook: cited, versioned program parameters the engines execute.

Each file in config/agere/programs/ describes one program/track (with optional variants such as MSSP BASIC
levels A-E) for the performance or payment years it lists. Every parameter is a leaf
{"value": ..., "citations": [<citation id>, ...]} and every citation records the public source URL, title,
section/page, the year it applies to and the retrieval date. Nothing here is a payer's private contract:
commercial and Medicaid files carry typical published ranges only and never resolve to settlement terms.

resolve("MSSP ENHANCED 2026") -> the program, variant and year; contract_terms(...) -> the terms vbc.settle
uses (sharing rate, MSR/MLR, caps, loss rate, two_sided ...) plus provenance the answer cites. MA helpers
(V24/V28 blend, normalization, coding-intensity adjustment, QBP and rebate percentages) serve a later
risk-adjustment engine.
"""
from functools import lru_cache
from pathlib import Path
import json, re

SCHEMA = 'agere-program/1'
ROOT = Path(__file__).resolve().parents[3] / 'config' / 'agere' / 'programs'
KINDS = ('shared_savings', 'capitation_model', 'episode_model', 'care_model', 'risk_adjustment', 'star_ratings', 'program_rules', 'typical_range')
STATUSES = ('active', 'announced', 'ended', 'terminated_early', 'ending', 'reference')
PRIMARY_HOSTS = ('cms.gov', 'ecfr.gov', 'federalregister.gov', 'govinfo.gov', 'ssa.gov', 'medpac.gov', 'congress.gov',
                 'medicaid.gov', 'hhs.gov', 'gao.gov')
CITATION_FIELDS = ('url', 'title', 'section', 'applies_to', 'retrieved')
# Settlement terms vbc.settle understands; programs map their parameters onto these names.
SETTLEMENT_TERMS = ('sharing_rate', 'msr', 'mlr', 'msr_schedule', 'loss_rate', 'loss_rate_min', 'loss_rate_max', 'savings_cap_pct',
                    'loss_cap_pct', 'loss_cap_revenue_pct', 'two_sided', 'quality_score', 'quality_mode', 'corridors', 'discount_pct',
                    'quality_withhold_pct')
REQUIRED_FROM_USER = ('benchmark_pmpm',)


def _host(url):
    m = re.match(r'https://([^/]+)/', url or '')
    return m.group(1).lower() if m else ''


def _is_primary(url):
    host = _host(url)
    return any(host == h or host.endswith('.' + h) for h in PRIMARY_HOSTS)


def _leaves(params, prefix=''):
    """(dotted name, leaf) for every parameter; a leaf is a dict with 'value'."""
    for name, node in (params or {}).items():
        path = f'{prefix}{name}'
        if isinstance(node, dict) and 'value' in node:
            yield path, node
        elif isinstance(node, dict):
            yield from _leaves(node, path + '.')
        else:
            yield path, node  # malformed: reported by validate


def validate_program(doc):
    """-> list of problems (empty when the file is valid)."""
    errs = []
    req = ('schema', 'id', 'program', 'label', 'kind', 'status', 'years', 'year_basis', 'aliases', 'parameters', 'citations', 'last_verified')
    for k in req:
        if k not in doc: errs.append(f'missing field {k}')
    if errs: return errs
    if doc['schema'] != SCHEMA: errs.append(f"schema must be {SCHEMA}")
    if doc['kind'] not in KINDS: errs.append(f"kind must be one of {KINDS}")
    if doc['status'] not in STATUSES: errs.append(f"status must be one of {STATUSES}")
    if doc['year_basis'] not in ('performance_year', 'payment_year', 'star_year', 'reference_year'): errs.append('bad year_basis')
    if not doc['years'] or not all(isinstance(y, int) and 2010 <= y <= 2040 for y in doc['years']): errs.append('years must be integers')
    if not re.match(r'^\d{4}-\d{2}-\d{2}$', str(doc['last_verified'])): errs.append('last_verified must be YYYY-MM-DD')
    for a in doc['aliases']:
        try: re.compile(a)
        except re.error as e: errs.append(f'bad alias regex {a}: {e}')
    cites = doc['citations']
    for cid, c in cites.items():
        for f in CITATION_FIELDS:
            if not c.get(f): errs.append(f'citation {cid} missing {f}')
        if not str(c.get('url', '')).startswith('https://'): errs.append(f'citation {cid} url must be https')
        if not re.match(r'^\d{4}-\d{2}-\d{2}$', str(c.get('retrieved', ''))): errs.append(f'citation {cid} retrieved must be YYYY-MM-DD')
        if c.get('excerpt') and len(str(c['excerpt']).split()) > 25: errs.append(f'citation {cid} excerpt over 25 words')
        if c.get('primary') is not _is_primary(c.get('url', '')) and doc['kind'] != 'typical_range':
            errs.append(f"citation {cid} primary flag disagrees with host {_host(c.get('url'))}")
    used = set()
    groups = [('', doc['parameters'])] + [(f'variant {v}: ', spec.get('parameters') or {}) for v, spec in (doc.get('variants') or {}).items()]
    for where, params in groups:
        for path, leaf in _leaves(params):
            if not isinstance(leaf, dict):
                errs.append(f'{where}{path} is not a parameter leaf'); continue
            ids = leaf.get('citations') or []
            if not ids: errs.append(f'{where}{path} has no citation')
            for cid in ids:
                if cid not in cites: errs.append(f'{where}{path} cites unknown {cid}')
                used.add(cid)
    for v, spec in (doc.get('variants') or {}).items():
        for a in spec.get('aliases') or []:
            try: re.compile(a)
            except re.error as e: errs.append(f'variant {v} bad alias {a}: {e}')
    for cid in set(cites) - used: errs.append(f'citation {cid} is not used by any parameter')
    if doc['kind'] != 'typical_range' and not any(_is_primary(c.get('url')) for c in cites.values()):
        errs.append('needs at least one primary (government) source')
    if doc['kind'] == 'typical_range':
        if doc.get('settlement'): errs.append('typical ranges never resolve to settlement terms')
        if not doc.get('disclaimer'): errs.append('typical_range files need a disclaimer')
    for term, ref in (doc.get('settlement') or {}).items():
        if term not in SETTLEMENT_TERMS: errs.append(f'settlement term {term} is not one vbc.settle understands')
        if isinstance(ref, str) and ref.startswith('$'):
            name = ref[1:]
            ok = _lookup(doc['parameters'], name) is not None or any(_lookup(s.get('parameters') or {}, name) is not None
                                                                     for s in (doc.get('variants') or {}).values())
            if not ok: errs.append(f'settlement {term} references missing parameter {name}')
    return errs


def _lookup(params, dotted):
    node = params
    for part in dotted.split('.'):
        if not isinstance(node, dict) or part not in node: return None
        node = node[part]
    return node if isinstance(node, dict) and 'value' in node else None


@lru_cache(maxsize=1)
def _load(root=str(ROOT)):
    out = {}
    for f in sorted(Path(root).glob('*.json')):
        if f.name.startswith('_'): continue
        doc = json.loads(f.read_text())
        doc['_file'] = f.name
        out[doc['id']] = doc
    return out


def load(root=None):
    return _load(str(root or ROOT))


def validate_all(root=None):
    """-> {file: [problems]} for every invalid file (empty dict when the rulebook is valid)."""
    bad = {}
    docs = load(root)
    for pid, doc in docs.items():
        errs = validate_program(doc)
        if errs: bad[doc['_file']] = errs
    return bad


# ---- naming a program in text ----------------------------------------------------------------
def detect(text):
    """Find a program named in free text -> {'id', 'variant', 'year', 'span': (start, end)} or None.

    Longest alias match wins (so 'ACO REACH Global' beats 'ACO REACH')."""
    low = (text or '').lower()
    best = None
    for pid, doc in load().items():
        for a in doc['aliases']:
            for m in re.finditer(a, low):
                if best is None or (m.end() - m.start()) > (best[2] - best[1]):
                    best = (pid, m.start(), m.end())
    if best is None: return None
    pid, s, e = best
    doc = load()[pid]
    variant, vs, ve = None, s, e
    window_end = min(len(low), e + 40)
    for v, spec in (doc.get('variants') or {}).items():
        for a in spec.get('aliases') or []:
            m = re.compile(a).search(low, max(0, s - 30), window_end)
            if m:
                variant, vs, ve = v, min(vs, m.start()), max(ve, m.end())
                break
        if variant: break
    s, e = min(s, vs), max(e, ve)
    year = None
    ym = re.compile(r'\b(?:for |in )?(?:py ?|performance year |payment year |plan year |py|cy ?|star year )?(20\d\d)\b').search(low, e, min(len(low), e + 30))
    if ym and ym.start() - e <= 3: year, e = int(ym.group(1)), ym.end()
    else:
        yb = re.compile(r'\b(?:py ?|performance year |payment year |cy ?)?(20\d\d) ?$').search(low[:s].rstrip() + ' ')
        if yb and s - yb.end() <= 2: year, s = int(yb.group(1)), yb.start()
    return {'id': pid, 'variant': variant, 'year': year, 'span': (s, e)}


def resolve(name_or_ref, year=None, variant=None):
    """'MSSP ENHANCED 2026' | {'id':..., 'variant':..., 'year':...} -> resolution with merged parameters.

    Raises ValueError for an unknown program, variant or a year the rulebook does not cover."""
    ref = name_or_ref
    if isinstance(ref, str):
        docs = load()
        ref = {'id': ref} if ref in docs else detect(ref)
        if ref is None: raise ValueError(f'Unknown program: {name_or_ref}. Known: ' + ', '.join(sorted(d['label'] for d in docs.values())))
    doc = load().get(ref['id'])
    if doc is None: raise ValueError(f"Unknown program id: {ref['id']}")
    variant = variant or ref.get('variant') or doc.get('default_variant')
    variants = doc.get('variants') or {}
    if variants and variant not in variants:
        raise ValueError(f"{doc['label']} needs supplied option: one of {', '.join(variants)}")
    year = int(year or ref.get('year') or max(doc['years']))
    if year not in doc['years']:
        raise ValueError(f"{doc['label']} rulebook covers {doc['year_basis'].replace('_', ' ')}s {', '.join(map(str, doc['years']))}, not {year}")
    params = dict(_flat(doc['parameters']))
    if variant: params.update(_flat(variants[variant].get('parameters') or {}))
    label = doc['label'] + (f' {variant}' if variant and not doc['label'].endswith(' ' + variant) else '')
    return {'id': doc['id'], 'program': doc['program'], 'label': label, 'variant': variant, 'year': year, 'year_basis': doc['year_basis'],
            'kind': doc['kind'], 'status': doc['status'], 'parameters': params, 'doc': doc}


def _flat(params):
    return {path: leaf for path, leaf in _leaves(params)}


def value(resolution, name, default=None):
    leaf = resolution['parameters'].get(name)
    if leaf is None: return default
    v = leaf['value']
    if isinstance(v, dict) and v and all(re.match(r'^20\d\d$', k) for k in v):  # year-keyed values
        if str(resolution['year']) not in v:
            raise ValueError(f"{resolution['label']}: {name} not in the rulebook for {resolution['year']} (covers {', '.join(sorted(v))})")
        return v[str(resolution['year'])]
    return v


def sources(resolution, names=None):
    """Citations behind the named parameters (all parameters when names is None), de-duplicated."""
    cites = resolution['doc']['citations']
    seen, out = set(), []
    for name, leaf in resolution['parameters'].items():
        if names is not None and name not in names: continue
        for cid in leaf['citations']:
            if cid in seen: continue
            seen.add(cid)
            c = cites[cid]
            out.append({'id': cid, 'title': c['title'], 'url': c['url'], 'section': c['section'], 'applies_to': c['applies_to'],
                        'retrieved': c['retrieved']})
    return out


def contract_terms(ref, stated=None, year=None, variant=None):
    """Program terms for vbc.settle, with stated terms overriding. The result carries 'program' provenance:
    label, year, the parameters used, their citations and the assumptions made (e.g. quality standard met)."""
    res = ref if isinstance(ref, dict) and 'parameters' in ref else resolve(ref, year=year, variant=variant)
    doc = res['doc']
    if not doc.get('settlement') and doc.get('choose'):
        raise ValueError(f"Shared savings needs supplied program track: {doc['choose']}")
    if not doc.get('settlement'):
        raise ValueError(f"{res['label']} is a {doc['kind'].replace('_', ' ')} entry with no shared-savings settlement terms in the rulebook")
    terms, used = {}, []
    for term, ref_ in doc['settlement'].items():
        if isinstance(ref_, str) and ref_.startswith('$'):
            name = ref_[1:]
            if name not in res['parameters']: continue
            terms[term] = value(res, name); used.append(name)
        else:
            terms[term] = ref_
    if terms.get('loss_rate') is None and terms.get('two_sided') and terms.get('sharing_rate') is not None:
        lo, hi = terms.get('loss_rate_min'), terms.get('loss_rate_max')
        rate = 1 - float(terms['sharing_rate']) * float((stated or {}).get('quality_score', terms.get('quality_score', 1.0)))
        if lo is not None: rate = max(rate, float(lo))
        if hi is not None: rate = min(rate, float(hi))
        terms['loss_rate'] = round(rate, 4)
    stated = {k: v for k, v in (stated or {}).items() if k != 'program'}
    overridden = sorted(k for k in stated if k in terms and stated[k] != terms[k])
    terms.update(stated)
    if 'msr' in stated and 'mlr' not in stated and terms.get('two_sided'): terms['mlr'] = stated['msr']
    if 'msr' in terms: terms.pop('msr_schedule', None)
    assumptions = list(doc.get('assumptions') or [])
    if 'quality_score' not in stated and terms.get('quality_mode', 'sharing_multiplier') == 'sharing_multiplier':
        terms.setdefault('quality_score', 1.0)
    terms['program'] = {'id': res['id'], 'label': res['label'], 'year': res['year'], 'year_basis': res['year_basis'],
                        'status': res['status'], 'parameters_used': used, 'overridden_by_question': overridden,
                        'assumptions': assumptions, 'sources': sources(res, set(used)), 'last_verified': doc['last_verified']}
    return terms


def missing_terms(terms):
    """User-only inputs still needed to settle (the program cannot know the benchmark)."""
    out = [k for k in REQUIRED_FROM_USER if not isinstance(terms.get(k), (int, float))]
    if not isinstance(terms.get('sharing_rate'), (int, float)) and not terms.get('corridors'): out.append('sharing_rate')
    return out


def variable_msr(assigned, schedule):
    """MSR (or MLR) from a population schedule [{'min','max','rate_at_min','rate_at_max'}], linearly interpolated
    within a band (42 CFR 425.604 style). Below the first band -> its rate_at_min; above the last -> its rate_at_max."""
    rows = sorted(schedule, key=lambda r: r['min'])
    n = float(assigned or 0)
    if n <= rows[0]['min']: return float(rows[0]['rate_at_min'])
    for r in rows:
        hi = r.get('max')
        if hi is None or n <= hi:
            if hi is None or hi == r['min']: return float(r['rate_at_max'])
            f = (n - r['min']) / (hi - r['min'])
            return round(float(r['rate_at_min']) + f * (float(r['rate_at_max']) - float(r['rate_at_min'])), 5)
    return float(rows[-1]['rate_at_max'])


def describe_terms(terms):
    """One sentence naming the program and its sources, for the answer's method line."""
    p = terms.get('program') if isinstance(terms, dict) else None
    if not p: return ''
    src = '; '.join(f"{s['title']} ({s['section']}) {s['url']}" for s in p['sources'][:4])
    extra = f" Question overrides: {', '.join(p['overridden_by_question'])}." if p['overridden_by_question'] else ''
    assume = f" Assumptions: {' '.join(p['assumptions'])}" if p['assumptions'] else ''
    return f"Terms from the Agere program rulebook: {p['label']} ({p['year_basis'].replace('_', ' ')} {p['year']}; last verified {p['last_verified']}). Sources: {src}.{extra}{assume}"


# ---- Medicare Advantage helpers (for a later risk-adjustment engine) --------------------------------
def _ma(pid='ma_risk_adjustment'):
    doc = load().get(pid)
    if doc is None: raise ValueError(f'{pid} not in the rulebook')
    return doc


def _year_value(doc, name, year):
    leaf = _lookup(doc['parameters'], name)
    if leaf is None: raise ValueError(f'{doc["id"]} has no parameter {name}')
    v = leaf['value']
    key = str(year)
    if key not in v: raise ValueError(f"{doc['label']}: {name} not in the rulebook for {year} (covers {', '.join(sorted(v))})")
    return v[key]


def hcc_blend(payment_year):
    """{'V24': w, 'V28': w} model weights for an MA payment year."""
    return dict(_year_value(_ma(), 'model_blend', payment_year))


def normalization_factor(payment_year, model):
    norms = _year_value(_ma(), 'normalization_factor', payment_year)
    if model not in norms: raise ValueError(f'No {model} normalization factor for {payment_year}')
    return float(norms[model])


def coding_intensity(payment_year):
    return float(_year_value(_ma(), 'coding_pattern_adjustment', payment_year))


def payment_risk_score(raw_scores, payment_year):
    """Raw CMS-HCC scores by model -> payment risk score: each model's score / its normalization factor, blended
    by the year's model weights, then reduced by the coding pattern adjustment."""
    blend = hcc_blend(payment_year)
    total = 0.0
    for model, w in blend.items():
        if w == 0: continue
        if model not in raw_scores: raise ValueError(f'{payment_year} blends {model}; supply its raw score')
        total += w * float(raw_scores[model]) / normalization_factor(payment_year, model)
    return round(total * (1 - coding_intensity(payment_year)), 4)


def qbp_bonus_pct(stars, double_bonus_county=False, star_year=None):
    doc = _ma('ma_star_ratings')
    v = _lookup(doc['parameters'], 'qbp_bonus')['value']
    if float(stars) < float(v['min_stars']): return 0.0
    return float(v['double_bonus_pct'] if double_bonus_county else v['bonus_pct'])


def rebate_pct(stars):
    doc = _ma('ma_star_ratings')
    bands = _lookup(doc['parameters'], 'rebate_percentage')['value']
    for b in sorted(bands, key=lambda b: -b['min_stars']):
        if float(stars) >= b['min_stars']: return float(b['rebate_pct'])
    return float(min(bands, key=lambda b: b['min_stars'])['rebate_pct'])


if __name__ == '__main__':  # python -m clinical_core.agere.clinical.programs  -> validate the rulebook
    import sys
    problems = validate_all()
    for f, errs in problems.items():
        for e in errs: print(f'{f}: {e}')
    docs = load()
    n = sum(len(_flat(d['parameters'])) + sum(len(_flat(v.get('parameters') or {})) for v in (d.get('variants') or {}).values()) for d in docs.values())
    print(f'{len(docs)} program files, {n} cited parameters, {sum(len(d["citations"]) for d in docs.values())} citations: '
          + ('INVALID' if problems else 'valid'))
    sys.exit(1 if problems else 0)
