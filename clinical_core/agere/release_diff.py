"""Release-diff guard: what a new warehouse release silently does to Agere's deterministic definitions.

A release that adds sources changes the scope of definitions that did not change in code. On v2.3 four such drifts
surfaced only when a benchmark disagreed: diabetes widened to uncoded free text, prediabetes R73.0 caught R73.09,
place_of_service's tail form 'service' consumed 'service_date'/'service_from', and an upper-case lab name
('HEMOGLOBIN A1C') escaped exact-name consumers. This module compares a candidate build with the prior active build
and reports, per concept (concepts.py CONDITIONS / MEASUREMENTS / ENCOUNTERS, with definitions.json rolled up onto
them), per governed metric (config/agere/metrics) and per semantic field binding:

  * patients and the evidence-source mix (source stream, diagnosis basis, coded vs free text), normalised by the
    patient growth of the source stream (and of the roster for the concept total) - widening / narrowing beyond a
    tolerance, a new evidence source, a lost one;
  * the codes each concept bound in each build and the codes inside each concept's ICD-10 prefix family - new codes,
    and codes on a small curated list of clinically distinct members (R73.09 under R73.0);
  * free-text channels the concept library does not read (medication_lists.indication ...) that mention a concept:
    any consumer reading them widens the concept (review only);
  * lab / vital name vocabularies grouped case-insensitively: new case or spelling variants of an existing name;
  * every natural form of every field of every received stream, resolved with the literal-stream reader's own
    take order (local_compiler.resolve_field_mentions): a form whose binding changed, or that no longer binds to its
    own field;
  * governed metric value sets rebuilt on both releases (metrics.build_value_set): unlisted label variants, case
    variants of adopted members, members that disappeared, and the concepts / definitions each metric relies on.

Everything is read-only: source-profile.json and semantic.sqlite3 of both builds are opened directly (never through
semantic_layer.ensure, which would rebuild a build made by older code). Synthetic data only; no model calls.
Output: a JSON report and a short markdown summary. The guard is informational by default; gate() returns the
'flag' findings not acknowledged in an acceptance file.
"""
from __future__ import annotations

import difflib, hashlib, json, math, re, sqlite3, time
from collections import defaultdict
from pathlib import Path

VERSION = 'release-diff-guard/1'
TOLERANCE = 0.25        # allowed relative departure from the growth-normalised expectation
MIN_PATIENTS = 5        # ...and at least this many patients of departure before a scope change is flagged
QUERY_SECONDS = 20

# ICD-10-CM codes that sit inside a concept's prefix family but are a different clinical entity. Small and curated:
# only the obvious ones; anything else new in a family whose labels do not read as the concept goes to review.
CLINICALLY_DISTINCT = {
    'R73.09': 'Other abnormal glucose / elevated A1c (R73.09) is not prediabetes (R73.01-R73.03)',
    'E78.6': 'Lipoprotein deficiency (E78.6) is hypolipidemia, not hyperlipidemia',
    'E78.7': 'Bile acid and cholesterol metabolism disorders (E78.7x: Barth, Smith-Lemli-Opitz) are not dyslipidemia',
    'D57.3': 'Sickle-cell trait (D57.3) is a carrier state, not anemia',
    'E66.3': 'Overweight (E66.3) is not obesity',
    'C44': 'Other skin cancers (C44, mostly non-melanoma) are usually excluded from cancer registries and measures',
}


# ---------------------------------------------------------------------------------------------------------- loading
def _stack(build):
    build = Path(build)
    return build / 'stack' if (build / 'stack' / 'run.json').exists() else build


def load_build(build):
    """{'build','stack','run','profile','semantic','as_of','digest','release'} - read only, no rebuilds."""
    stack = _stack(build)
    run = json.loads((stack / 'run.json').read_text())
    profile = json.loads((stack / 'source-profile.json').read_text())
    if profile.get('source_digest') != run.get('source_digest'):
        raise ValueError(f'{stack}: source-profile.json belongs to another source digest (rebuild the profile first)')
    semantic = stack / 'semantic.sqlite3'
    meta = {}
    if semantic.exists():
        with _ro(semantic) as db: meta = dict(db.execute('SELECT key, value FROM meta').fetchall())
    conn = stack.parent / 'connection.json'
    release = json.loads(conn.read_text()).get('release') if conn.exists() else None
    return {'build': str(stack.parent), 'stack': str(stack), 'run': run, 'profile': profile,
            'semantic': str(semantic) if semantic.exists() else None, 'semantic_version': meta.get('version'),
            'as_of': (run.get('snapshot') or {}).get('as_of') or meta.get('as_of'), 'digest': run.get('source_digest'),
            'release': release}


class _ro:
    def __init__(self, path, seconds=QUERY_SECONDS):
        self.path, self.seconds = path, seconds

    def __enter__(self):
        self.db = sqlite3.connect(f'file:{self.path}?mode=ro', uri=True)
        deadline = time.perf_counter() + self.seconds
        self.db.set_progress_handler(lambda: 1 if time.perf_counter() > deadline else 0, 20000)
        return self.db

    def __exit__(self, *exc):
        self.db.close()


def _columns(db, table):
    return {r[1] for r in db.execute(f'PRAGMA table_info({table})')}


def concepts_module(rev=None, root=None):
    """The concept library: the live module, or concepts.py as of a git revision (it only imports re), so a release
    pair can be checked with the definitions that were live when the candidate was built."""
    if rev is None:
        from .clinical import concepts
        return concepts
    import subprocess, types
    root = Path(root or Path(__file__).resolve().parents[2])
    src = subprocess.run(['git', '-C', str(root), 'show', f'{rev}:clinical_core/agere/clinical/concepts.py'],
                         capture_output=True, text=True, check=True).stdout
    mod = types.ModuleType(f'concepts_at_{rev}')
    exec(compile(src, f'concepts.py@{rev}', 'exec'), mod.__dict__)  # noqa: S102 - repository source at a named revision
    return mod


# ---------------------------------------------------------------------------------------------------------- population
def population(b, streams=None):
    """Roster ids, per-stream patient coverage (ids; only `streams` when given) and per-stream record counts."""
    p = b['profile']
    out = {'patients': int(p['patients']['records']), 'ids': set(), 'coverage': defaultdict(set),
           'records': {s: int(st.get('records') or 0) for s, st in p['streams'].items()}}
    if b['semantic']:
        with _ro(b['semantic'], 300) as db:
            out['ids'] = {r[0] for r in db.execute('SELECT patient_id FROM patient')}
            for s in sorted(streams) if streams is not None else [r[0] for r in db.execute('SELECT DISTINCT stream FROM record')]:
                out['coverage'][s] = {r[0] for r in db.execute('SELECT DISTINCT patient_id FROM record WHERE stream = ?', (s,))}
    return out


def segments(P0, P1, mode='auto'):
    """'identity': patients present in both releases (same ids) are compared with themselves, so data added for new
    patients does not read as widening. 'roster': the whole rosters are compared (a regenerated population, rebaseline).
    auto = identity when at least half of the prior roster is still present."""
    shared = P0['ids'] & P1['ids']
    if mode == 'auto': mode = 'identity' if P0['ids'] and len(shared) >= 0.5 * len(P0['ids']) else 'roster'
    return mode, (shared if mode == 'identity' else None)


def _restrict(ids, keep): return ids if keep is None else ids & keep


def coverage_growth(P0, P1, streams, keep):
    """Growth of the patients holding any record in `streams` (within the segment): the expected scale of a concept
    that reads those streams when nothing about its definition changed."""
    a = set().union(*[P0['coverage'].get(s, set()) for s in streams]) if streams else set()
    b = set().union(*[P1['coverage'].get(s, set()) for s in streams]) if streams else set()
    a, b = _restrict(a, keep), _restrict(b, keep)
    if a and b: return len(b) / len(a)
    r0, r1 = _restrict(P0['ids'], keep), _restrict(P1['ids'], keep)
    return len(r1) / len(r0) if r0 else 1.0


def scope_change(n0, n1, growth, tolerance=TOLERANCE, min_patients=MIN_PATIENTS):
    """'widened' | 'narrowed' | None for a count moving from n0 to n1 when the segment grew by `growth`."""
    expected = n0 * growth
    ratio = n1 / expected if expected else None
    if abs(n1 - expected) < min_patients: return None, expected, ratio
    if ratio is None: return 'widened', expected, None
    if n1 and abs(math.log(ratio)) <= math.log1p(tolerance): return None, expected, ratio
    return ('widened' if n1 > expected else 'narrowed'), expected, ratio


# ---------------------------------------------------------------------------------------------------------- concepts
def concept_snapshot(b):
    """As built: patient ids per concept; per concept and patient the evidence sources that reached it (coarse:
    stream + coded/free text; fine: + diagnosis basis); bound codes, names and encounter values with patient counts."""
    out = {'concepts': {}, 'patients': defaultdict(set), 'keys': defaultdict(lambda: defaultdict(set)),
           'fine': defaultdict(lambda: defaultdict(set)), 'codes': defaultdict(dict), 'code_labels': defaultdict(set),
           'names': defaultdict(dict), 'values': defaultdict(dict), 'unbound_code_labels': defaultdict(set)}
    if not b['semantic']: return out
    with _ro(b['semantic'], 300) as db:
        for c, kind, pid in db.execute('SELECT concept, kind, patient_id FROM concept_patient'):
            out['concepts'][c] = kind
            out['patients'][c].add(pid)
        basis = "coalesce(diagnosis_basis, '')" if 'diagnosis_basis' in _columns(db, 'condition') else "''"
        coded = "(coalesce(icd10, '') <> '' OR coalesce(snomed, '') <> '')"
        for c, st, ba, cd, pid in db.execute(f'SELECT DISTINCT concept, source_table, {basis}, {coded}, patient_id FROM condition '
                                             'WHERE concept IS NOT NULL'):
            coding = 'coded' if cd else 'free_text'
            out['keys'][c][pid].add(f'{st}/{coding}')
            out['fine'][c][f"{st}/{ba or '-'}/{coding}"].add(pid)
        for c, code, label, n in db.execute('SELECT concept, icd10, label, count(DISTINCT patient_id) FROM condition '
                                            "WHERE icd10 IS NOT NULL AND icd10 <> '' GROUP BY 1, 2, 3"):
            if c:
                out['codes'][c][code] = out['codes'][c].get(code, 0) + n
                out['code_labels'][(c, code)].add(label)
            out['unbound_code_labels'][code].add(label)
        for c, st, pid in db.execute('SELECT DISTINCT concept, source_table, patient_id FROM measurement WHERE concept IS NOT NULL'):
            out['keys'][c][pid].add(st)
        for c, st, name, n in db.execute('SELECT concept, source_table, name, count(DISTINCT patient_id) FROM measurement '
                                         'WHERE concept IS NOT NULL GROUP BY 1, 2, 3'):
            out['names'][c][f'{st}:{name}'] = n
        for c, st, pid in db.execute('SELECT DISTINCT type_concept, source_table, patient_id FROM encounter WHERE type_concept IS NOT NULL'):
            out['keys'][c][pid].add(st)
        for c, st, value, n in db.execute('SELECT type_concept, source_table, type, count(DISTINCT patient_id) FROM encounter '
                                          'WHERE type_concept IS NOT NULL GROUP BY 1, 2, 3'):
            out['values'][c][f'{st}:{value}'] = n
    return out


def _source_stream(key):
    return key.split('/')[0].split(':')[0]


def _by_key(per_patient, keep=None):
    out = defaultdict(set)
    for pid, keys in per_patient.items():
        if keep is not None and pid not in keep: continue
        for k in keys: out[k].add(pid)
    return out


def _code_labels_read_as(concept_def, labels):
    text = re.compile(concept_def['text'], re.I) if concept_def.get('text') else None
    exclude = re.compile(concept_def['exclude'], re.I) if concept_def.get('exclude') else None
    good = [l for l in labels if l and text and text.search(str(l).replace('_', ' ')) and not (exclude and exclude.search(str(l)))]
    return bool(good)


def _distinct(code):
    for k, why in CLINICALLY_DISTINCT.items():
        if code.upper().startswith(k): return k, why
    return None, None


def _profile_codes(b, C):
    """Every code observed in the condition streams' code fields (bound or not), with records."""
    out = defaultdict(int)
    for stream, spec in C.CONDITION_STREAMS.items():
        f = b['profile']['streams'].get(stream, {}).get('fields', {}).get(spec['code']) or {}
        for code, n in (f.get('values') or {}).items(): out[str(code).upper()] += int(n)
    return out


def mention_channels(b, C):
    """Free-text fields outside the condition streams that mention each condition concept: {concept: {stream.field: records}}."""
    out = defaultdict(dict)
    skip_fields = re.compile(r'(?:^|_)(?:id|code|date|time|status|system|basis|source|type|unit|identity\w*)$')
    rx = {c: (re.compile(d['text'], re.I), re.compile(d['exclude'], re.I) if d.get('exclude') else None) for c, d in C.CONDITIONS.items()}
    for s, st in b['profile']['streams'].items():
        if s in C.CONDITION_STREAMS: continue
        for f, spec in st.get('fields', {}).items():
            values = spec.get('values')
            if not values or skip_fields.search(f) or f in ('source_table', 'patient_id', 'record_id'): continue
            for c, (text, exclude) in rx.items():
                n = sum(int(k) for v, k in values.items() if isinstance(v, str) and text.search(v.replace('_', ' ')) and not (exclude and exclude.search(v)))
                if n: out[c][f'{s}.{f}'] = n
    return out


# ---------------------------------------------------------------------------------------------------------- vocabularies
def _norm(name): return re.sub(r'\s+', ' ', str(name).strip().lower())
def _squash(name): return re.sub(r'[^a-z0-9]', '', str(name).lower())


def measurement_names(b):
    """{(stream, name): records} for lab and vital names in the profile."""
    out = {}
    for stream, rows in (b['profile'].get('measurements') or {}).items():
        for m in rows:
            if m.get('name') is None: continue
            out[(stream, m['name'])] = out.get((stream, m['name']), 0) + int(m.get('records') or 0)
    return out


def name_variants(prior_names, new_names, C):
    """New case/spacing variants of an existing name (same lower-cased form) and close spelling variants of a name the
    prior release had, per stream. -> [{stream, name, records, variant_of, kind, concept}]"""
    out = []
    old = defaultdict(set)
    for (s, n) in prior_names: old[s].add(n)
    for (s, n), records in sorted(new_names.items()):
        if n in old[s]: continue
        same_case = sorted(o for o in old[s] if _norm(o) == _norm(n))
        same_squash = sorted(o for o in old[s] if _squash(o) == _squash(n) and o not in same_case)
        kind, of = ('case', same_case) if same_case else ('spelling', same_squash) if same_squash else (None, [])
        if not kind:
            close = difflib.get_close_matches(_norm(n), [_norm(o) for o in old[s]], n=2, cutoff=0.92)
            if close:
                kind, of = 'spelling', sorted(o for o in old[s] if _norm(o) in close)
        if not kind: continue
        concept = C.concept_for_label(n) if hasattr(C, 'concept_for_label') else None
        out.append({'stream': s, 'name': n, 'records': records, 'variant_of': of, 'kind': kind,
                    'concept': concept or next((C.concept_for_label(o) for o in of if C.concept_for_label(o)), None)})
    return out


# ---------------------------------------------------------------------------------------------------------- field bindings
def field_bindings(b, resolver=None):
    """{(stream, form): (own fields, resolved fields)} for every natural form of every populated field."""
    from .clinical import local_compiler as lc
    resolver = resolver or lc.resolve_field_mentions
    out = {}
    for s, st in b['profile']['streams'].items():
        forms = lc.stream_field_forms(st.get('fields') or {})
        for f, fs in forms.items():
            for form in fs:
                own = tuple(sorted(g for g, gs in forms.items() if form in gs))
                out[(s, form)] = (own, tuple(resolver(form, forms)))
    return out


def legacy_resolver_pre_c3c6f59(text, forms):
    """The literal-stream reader's take order before commit c3c6f59 (fields ordered by their longest form, a one-word
    tail form allowed inside snake_case names). Kept only to replay the guard on releases built with that code."""
    from .clinical.local_compiler import Reader, _alias_pattern
    r, out = Reader(text), []
    for f, fs in sorted(forms.items(), key=lambda kv: -max(map(len, kv[1]))):
        for form in fs:
            out.extend(f for _ in r.take(_alias_pattern(form), f'field {f}'))
    return out


RESOLVERS = {'live': None, 'pre-c3c6f59': legacy_resolver_pre_c3c6f59}


# ---------------------------------------------------------------------------------------------------------- metrics
METRIC_CONCEPT_RX = re.compile(r"\b(?:type_concept|concept)\s*(?:=|IN\s*\()\s*'([a-z0-9_]+)'|library concept '([a-z0-9_]+)'|concepts?\.\w+\s+([a-z0-9_]+)", re.I)


def metric_concepts(m, C):
    known = set(C.CONDITIONS) | set(C.MEASUREMENTS) | set(C.ENCOUNTERS)
    found = set()
    for vs in (m.get('value_sets') or {}).values():
        if vs.get('concept') in known: found.add(vs['concept'])
    text = ' '.join(str(m.get(k) or '') for k in ('sql_template', 'summary')) + json.dumps(m.get('population')) + json.dumps(m.get('numerator'))
    for g in METRIC_CONCEPT_RX.findall(text):
        found.update(x for x in g if x in known)
    for d in m.get('definitions') or []:
        if d in known: found.add(d)
    return sorted(found)


def _value_set_state(b, vs, cache):
    from .clinical import metrics as ML
    key = (b['semantic'], vs['dimension'])
    if key not in cache:
        try: cache[key] = ML.universe(b['semantic'], vs['dimension']) if b['semantic'] else []
        except sqlite3.Error as exc: cache[key] = exc
    rows = cache[key]
    if isinstance(rows, Exception): return {'unavailable': str(rows)[:200]}
    built = ML.build_value_set(vs, rows)
    keys = {str(r.get(ML.DIMENSIONS[vs['dimension']]['key'])): int(r.get('n') or 0) for r in rows}
    return {'members': {k: v['rows'] for k, v in built['members'].items()}, 'review': {k: v['rows'] for k, v in built['review'].items()},
            'observed': keys, 'concept_coverage': built.get('concept_coverage')}


def _profile_values(b):
    """Every observed low-cardinality string value and lab/vital name of a build (from its source profile)."""
    out = {n for (_, n) in measurement_names(b)}
    for st in b['profile']['streams'].values():
        for spec in (st.get('fields') or {}).values():
            out.update(v for v in (spec.get('values') or {}) if isinstance(v, str))
    return out


def _metric_literals(m):
    return set(re.findall(r"'([^'{}%]{2,80})'", re.sub(r'--[^\n]*', ' ', m.get('sql_template') or '')))


# ---------------------------------------------------------------------------------------------------------- the diff
def _fid(kind, subject):
    return kind + ':' + hashlib.sha256(json.dumps(subject, sort_keys=True, default=str).encode()).hexdigest()[:12]


def diff(prior, new, *, concepts_rev=None, resolver='live', tolerance=TOLERANCE, min_patients=MIN_PATIENTS,
         metrics_directory=None, root=None, segment_mode='auto'):
    """Compare two builds (paths or load_build() dicts). -> report dict (see module docstring)."""
    started = time.perf_counter()
    prior = load_build(prior) if not isinstance(prior, dict) else prior
    new = load_build(new) if not isinstance(new, dict) else new
    C = concepts_module(concepts_rev, root)
    findings = []

    def add(kind, severity, subject, message, **detail):
        findings.append({'id': _fid(kind, subject), 'kind': kind, 'severity': severity, 'subject': subject,
                         'message': message, **detail})

    # --- concepts: totals, evidence sources, codes, names, values
    S0, S1 = concept_snapshot(prior), concept_snapshot(new)
    used = {_source_stream(k) for S in (S0, S1) for per in S['keys'].values() for keys in per.values() for k in keys}
    P0, P1 = population(prior, used), population(new, used)
    mode, keep = segments(P0, P1, segment_mode)
    concepts_report = {}
    every = list(C.CONDITIONS) + list(C.MEASUREMENTS) + list(C.ENCOUNTERS)
    for c in sorted(set(every) | set(S0['concepts']) | set(S1['concepts'])):
        k0, k1 = _by_key(S0['keys'].get(c, {})), _by_key(S1['keys'].get(c, {}))
        streams0 = sorted({_source_stream(k) for k in k0})
        g = coverage_growth(P0, P1, streams0, keep)
        p0, p1 = _restrict(S0['patients'].get(c, set()), keep), _restrict(S1['patients'].get(c, set()), keep)
        n0, n1 = len(p0), len(p1)
        change, expected, ratio = scope_change(n0, n1, g, tolerance, min_patients)
        row = {'kind': S1['concepts'].get(c) or S0['concepts'].get(c), 'segment': mode,
               'prior_patients': n0, 'new_patients': n1, 'expected_patients': round(expected, 1), 'coverage_growth': round(g, 3),
               'ratio_to_expected': None if ratio is None else round(ratio, 3), 'sources': {}}
        if keep is not None:
            fresh = len(S1['patients'].get(c, set()) - keep)
            row['new_segment'] = {'patients': fresh, 'roster': len(P1['ids'] - keep)}
        if change and c not in S0['concepts'] and c not in every:
            add('concept_new', 'review', {'concept': c}, f"{c} is a concept the prior build did not have ({n1} patients): a code change, not a release drift")
        elif change:
            add(f'concept_{change}', 'flag', {'concept': c},
                f"{c}: {n0} -> {n1} patients ({'same patients' if mode == 'identity' else 'whole roster'}); coverage of its prior "
                f"source streams {streams0 or ['roster']} grew x{g:.2f}, expecting {expected:.0f} ({change} beyond {tolerance:.0%})",
                prior_patients=n0, new_patients=n1, expected=round(expected, 1))
        for key in sorted(set(k0) | set(k1)):
            a, b_ = _restrict(k0.get(key, set()), keep), _restrict(k1.get(key, set()), keep)
            gs = coverage_growth(P0, P1, [_source_stream(key)], keep)
            ch, exp, _ = scope_change(len(a), len(b_), gs, tolerance, min_patients)
            row['sources'][key] = {'prior': len(a), 'new': len(b_), 'expected': round(exp, 1), 'stream_growth': round(gs, 3)}
            if key not in k0 and key in k1:
                # Patients this source alone adds: not in the concept before (same patients) and reached by no source the
                # concept already used. A source that only re-reaches known patients (e.g. records that gained codes) is review.
                old_keys = set(k0)
                only_new = {pid for pid in k1[key] if not (S1['keys'][c][pid] & old_keys)}
                added = only_new - S0['patients'].get(c, set()) if mode == 'identity' else only_new
                sev = 'flag' if added and S0['patients'].get(c) else 'review'
                add('new_evidence_source' if sev == 'flag' else 'new_evidence_source_overlapping', sev, {'concept': c, 'source': key},
                    f"{c} gained an evidence source: {key} ({len(k1[key])} patients, {len(added)} reached by nothing {c} used before)",
                    new_patients=len(k1[key]), added_patients=len(added), examples=sorted(added)[:5])
            elif key in k0 and key not in k1 and S1['patients'].get(c):
                add('evidence_source_lost', 'review', {'concept': c, 'source': key},
                    f"{c} lost an evidence source: {key} ({len(k0[key])} patients in the prior release)", prior_patients=len(k0[key]))
            elif ch:
                add(f'source_{ch}', 'review', {'concept': c, 'source': key},
                    f"{c} via {key}: {len(a)} -> {len(b_)} patients; stream coverage x{gs:.2f} expects {exp:.0f}",
                    prior_patients=len(a), new_patients=len(b_))
        f0, f1 = S0['fine'].get(c, {}), S1['fine'].get(c, {})
        finer = sorted(k for k in set(f1) - set(f0) if f"{k.split('/')[0]}/{k.split('/')[-1]}" in k0)
        if finer:
            row['new_fine_sources'] = {k: len(f1[k]) for k in finer}
        # codes bound as built
        codes0, codes1 = S0['codes'].get(c, {}), S1['codes'].get(c, {})
        new_codes = sorted(set(codes1) - set(codes0))
        if new_codes: row['new_bound_codes'] = {k: codes1[k] for k in new_codes}
        for code in sorted(codes1):
            k, why = _distinct(code)
            labels = sorted(S1['code_labels'].get((c, code), set()), key=str)
            if k:
                add('clinically_distinct_code', 'flag' if code in new_codes else 'review', {'concept': c, 'code': code},
                    f"{c} binds {code} ({', '.join(map(str, labels[:3]))}; {codes1[code]} patients"
                    f"{', new in this release' if code in new_codes else ', also bound in the prior release'}): {why}",
                    patients=codes1[code], new_in_release=code in new_codes)
            elif code in new_codes and c in C.CONDITIONS and not _code_labels_read_as(C.CONDITIONS[c], labels):
                add('new_code_in_family', 'review', {'concept': c, 'code': code},
                    f"{c} now binds {code} ({', '.join(map(str, labels[:3]))}; {codes1[code]} patients), new in this release; its labels do not read as {c}",
                    patients=codes1[code])
        if row['kind'] == 'measurement' or c in C.MEASUREMENTS:
            n0s, n1s = S0['names'].get(c, {}), S1['names'].get(c, {})
            added = sorted(set(n1s) - set(n0s))
            if added: row['new_bound_names'] = {k: n1s[k] for k in added}
        if c in C.ENCOUNTERS:
            v0, v1 = S0['values'].get(c, {}), S1['values'].get(c, {})
            added = sorted(set(v1) - set(v0))
            if added:
                row['new_bound_values'] = {k: v1[k] for k in added}
                add('new_encounter_value', 'review', {'concept': c, 'values': added},
                    f"{c} binds encounter values new in this release: {', '.join(added[:6])}")
        concepts_report[c] = row

    # --- ICD-10 prefix families against every observed code (bound or not), with the guard's concept definitions
    pc0, pc1 = _profile_codes(prior, C), _profile_codes(new, C)
    families = {}
    for c, d in C.CONDITIONS.items():
        fam1 = {code: n for code, n in pc1.items() if any(code.startswith(p) for p in d['icd10'])}
        fam0 = {code: n for code, n in pc0.items() if any(code.startswith(p) for p in d['icd10'])}
        added = sorted(set(fam1) - set(fam0))
        families[c] = {'prefixes': d['icd10'], 'observed_codes': len(fam1), 'new_codes': added}
        for code in sorted(fam1):
            k, why = _distinct(code)
            if k and code not in S1['codes'].get(c, {}):  # bound codes were reported above
                add('clinically_distinct_code', 'flag' if code in added else 'review', {'concept': c, 'code': code, 'basis': 'prefix family'},
                    f"{c}'s prefix family {[p for p in d['icd10'] if code.startswith(p)]} includes {code} ({fam1[code]} records): {why}",
                    records=fam1[code], new_in_release=code in added)
        for code in added:
            if _distinct(code)[0] or code in S1['codes'].get(c, {}): continue
            labels = sorted(S1['unbound_code_labels'].get(code, set()), key=str)
            if not _code_labels_read_as(d, labels):
                add('new_code_in_family', 'review', {'concept': c, 'code': code, 'basis': 'prefix family'},
                    f"{code} is new in {c}'s prefix family and its labels ({', '.join(map(str, labels[:3])) or 'none'}) do not read as {c}",
                    records=fam1[code])

    # --- free-text channels the library does not read
    M0, M1 = mention_channels(prior, C), mention_channels(new, C)
    channels = {}
    for c in sorted(set(M0) | set(M1)):
        for ch in sorted(set(M0.get(c, {})) | set(M1.get(c, {}))):
            a, b_ = M0.get(c, {}).get(ch, 0), M1.get(c, {}).get(ch, 0)
            stream = ch.split('.')[0]
            g = (P1['records'].get(stream, 0) / P0['records'][stream]) if P0['records'].get(stream) else None
            channels.setdefault(c, {})[ch] = {'prior_records': a, 'new_records': b_}
            if a == 0 and b_ >= min_patients:
                add('free_text_channel_new', 'review', {'concept': c, 'channel': ch},
                    f"{ch} now mentions {c} in {b_} records; the concept library does not read it, so any consumer that does widens {c}")
            elif g and a and b_ and abs(math.log((b_ / a) / g)) > math.log1p(tolerance) and abs(b_ - a * g) >= min_patients:
                add('free_text_channel_grew', 'review', {'concept': c, 'channel': ch},
                    f"{ch} mentions {c} in {a} -> {b_} records (stream records x{g:.2f} expects {a * g:.0f}); the concept library does not read it")

    # --- lab / vital name vocabularies
    N0, N1 = measurement_names(prior), measurement_names(new)
    variants = name_variants(N0, N1, C)
    for v in variants:
        sev = 'flag' if v['concept'] else 'review'
        add('lab_name_variant', sev, {'stream': v['stream'], 'name': v['name']},
            f"{v['stream']} name '{v['name']}' ({v['records']} records) is a new {v['kind']} variant of {v['variant_of']}"
            + (f" (concept {v['concept']}): exact-name consumers (value-set members, verified SQL, references) miss it" if v['concept'] else ''),
            records=v['records'], variant_of=v['variant_of'], variant_kind=v['kind'], concept=v['concept'])

    # --- semantic field bindings
    resolve = RESOLVERS[resolver] if isinstance(resolver, str) else resolver
    B0, B1 = field_bindings(prior, resolve), field_bindings(new, resolve)
    changed, misbound_new, misbound_standing = [], [], []
    for key, (own, got) in sorted(B1.items()):
        if key in B0 and B0[key][1] != got:
            changed.append({'stream': key[0], 'form': key[1], 'prior': list(B0[key][1]), 'new': list(got)})
        if set(got) != set(own):
            (misbound_standing if key in B0 and set(B0[key][1]) != set(B0[key][0]) else misbound_new).append(
                {'stream': key[0], 'form': key[1], 'own': list(own), 'binds': list(got)})
    for x in changed:
        add('alias_binding_changed', 'flag', {'stream': x['stream'], 'form': x['form']},
            f"'{x['form']}' in {x['stream']} bound {x['prior']} and now binds {x['new']}")
    for x in misbound_new:
        add('alias_misbinding', 'flag', {'stream': x['stream'], 'form': x['form']},
            f"'{x['form']}' in {x['stream']} names {x['own']} but the reader binds {x['binds'] or 'nothing'} (new in this release)")
    by_stream = defaultdict(list)
    for x in misbound_standing: by_stream[x['stream']].append(x)
    for s, xs in sorted(by_stream.items()):
        add('alias_misbinding_standing', 'review', {'stream': s},
            f"{s}: {len(xs)} field forms already misbound in the prior release, e.g. '{xs[0]['form']}' -> {xs[0]['binds']}", forms=xs)

    # --- governed metrics and definitions
    from .clinical import metrics as ML
    library = ML.library(metrics_directory, include_measures=False)
    defs_file = Path(metrics_directory).parent / 'definitions.json' if metrics_directory else ML.DEFINITIONS_FILE
    definitions = json.loads(Path(defs_file).read_text()).get('definitions', {}) if Path(defs_file).exists() else {}
    cache, metrics_report = {}, {}
    prior_values, candidate_values = _profile_values(prior), _profile_values(new)
    from .clinical.drug_classes import CLASSES
    known_ids = set(C.CONDITIONS) | set(C.MEASUREMENTS) | set(C.ENCOUNTERS) | set(CLASSES)  # ids, not received labels
    for mid, m in sorted(library.items()):
        cs = metric_concepts(m, C)
        row = {'concepts': cs, 'definitions': m.get('definitions') or [], 'value_sets': {}}
        drifted = sorted({f['subject']['concept'] for f in findings if f['subject'].get('concept') in cs and f['severity'] == 'flag'})
        if drifted:
            row['concept_flags'] = drifted
            add('metric_scope_at_risk', 'review', {'metric': mid},
                f"metric {mid} relies on concept(s) with flagged scope changes: {', '.join(drifted)}", concepts=drifted)
        for name, vs in (m.get('value_sets') or {}).items():
            if vs.get('dimension') not in ML.DIMENSIONS: continue
            a, b_ = _value_set_state(prior, vs, cache), _value_set_state(new, vs, cache)
            row['value_sets'][name] = {'prior_members': len(a.get('members') or {}), 'new_members': len(b_.get('members') or {}),
                                       'new_review_labels': sorted(set(b_.get('review') or {}) - set(a.get('review') or {}))[:40]}
            if 'unavailable' in b_:
                add('metric_dimension_unavailable', 'flag', {'metric': mid, 'value_set': name},
                    f"metric {mid} value set {name}: {vs['dimension']} cannot be read in the candidate ({b_['unavailable']})")
                continue
            members = [str(x) for x in vs.get('members') or []]
            observed = b_.get('observed') or {}
            for mem in members:
                variants = sorted(o for o in observed if o != mem and _norm(o) == _norm(mem) and o not in members)
                if variants:
                    add('metric_member_case_variant', 'flag', {'metric': mid, 'value_set': name, 'member': mem},
                        f"metric {mid} value set {name} adopts '{mem}' but the candidate also spells it {variants} "
                        f"({sum(observed[v] for v in variants)} rows), which the member list misses", variants=variants)
                if mem not in observed and mem in (a.get('observed') or {}):
                    add('metric_member_missing', 'review', {'metric': mid, 'value_set': name, 'member': mem},
                        f"metric {mid} value set {name}: adopted member '{mem}' no longer occurs in the candidate")
            new_review = sorted(set(b_.get('review') or {}) - set(a.get('review') or {}) - set(members))
            if new_review:
                add('metric_unlisted_label', 'review', {'metric': mid, 'value_set': name},
                    f"metric {mid} value set {name}: {len(new_review)} new label(s) match its patterns or concept but are not adopted, "
                    f"e.g. {new_review[:4]}", labels=new_review[:40])
        for lit in sorted(_metric_literals(m) - known_ids):
            hits = sorted(v for v in candidate_values - prior_values if v != lit and _norm(v) == _norm(lit))
            if hits:
                add('metric_literal_case_variant', 'flag', {'metric': mid, 'literal': lit},
                    f"metric {mid} SQL matches '{lit}' exactly; the candidate also has {hits}", variants=hits)
        metrics_report[mid] = row
    definitions_report = {}
    for key, d in definitions.items():
        linked_concepts = [key] if key in C.CONDITIONS or key in C.MEASUREMENTS or key in C.ENCOUNTERS else []
        linked_metrics = sorted(mid for mid, m in library.items() if key in (m.get('definitions') or []))
        flagged = sorted({f['kind'] for f in findings if f['severity'] == 'flag' and (
            f['subject'].get('concept') in linked_concepts or f['subject'].get('metric') in linked_metrics)})
        definitions_report[key] = {'label': d.get('label'), 'concepts': linked_concepts, 'metrics': linked_metrics, 'flag_kinds': flagged}
        if flagged:
            add('definition_scope_at_risk', 'review', {'definition': key},
                f"definition {key} ({d.get('label')}) rests on concepts/metrics with flagged changes: {', '.join(flagged)}")

    counts = defaultdict(int)
    for f in findings: counts[f"{f['severity']}:{f['kind']}"] += 1
    return {'schema': VERSION, 'synthetic': True,
            'prior': {k: prior[k] for k in ('build', 'release', 'digest', 'as_of', 'semantic_version')} | {'patients': P0['patients']},
            'candidate': {k: new[k] for k in ('build', 'release', 'digest', 'as_of', 'semantic_version')} | {'patients': P1['patients']},
            'parameters': {'tolerance': tolerance, 'min_patients': min_patients, 'concepts': concepts_rev or 'live',
                           'resolver': resolver if isinstance(resolver, str) else getattr(resolver, '__name__', 'custom'),
                           'segments': segment_mode,
                           'normalisation': 'within the segment (identity: patients in both releases; roster: whole rosters), concept totals by the growth of patients covered by the concept\'s prior source streams, evidence sources by the growth of their stream'},
            'population': {'prior_patients': P0['patients'], 'new_patients': P1['patients'],
                           'roster_growth': round(P1['patients'] / P0['patients'], 4) if P0['patients'] else None,
                           'segment': mode, 'shared_patients': len(keep) if keep is not None else None,
                           'new_streams': sorted(set(P1['records']) - set(P0['records'])),
                           'removed_streams': sorted(set(P0['records']) - set(P1['records']))},
            'concepts': concepts_report, 'code_families': families, 'free_text_channels': channels,
            'lab_name_variants': variants,
            'field_bindings': {'forms_checked': len(B1), 'changed': changed, 'misbound_new': misbound_new,
                               'misbound_standing': len(misbound_standing)},
            'metrics': metrics_report, 'definitions': definitions_report,
            'findings': findings, 'summary': dict(sorted(counts.items())), 'elapsed_seconds': round(time.perf_counter() - started, 1)}


def gate(report, accepted=()):
    """Unacknowledged 'flag' findings (the gate passes when empty). accepted: finding ids or kinds a reviewer accepted."""
    accepted = set(accepted)
    open_ = [f for f in report['findings'] if f['severity'] == 'flag' and f['id'] not in accepted and f['kind'] not in accepted]
    return {'passed': not open_, 'open_flags': [f['id'] for f in open_], 'accepted': sorted(accepted)}


def markdown(report, gate_result=None, limit=40):
    p, c = report['prior'], report['candidate']
    flags = [f for f in report['findings'] if f['severity'] == 'flag']
    review = [f for f in report['findings'] if f['severity'] == 'review']
    lines = [f"# Release diff: {Path(str(p['release'] or p['build'])).name} -> {Path(str(c['release'] or c['build'])).name}", '',
             f"Prior {p['patients']} patients (as of {p['as_of']}, {p['semantic_version']}); candidate {c['patients']} patients "
             f"(as of {c['as_of']}, {c['semantic_version']}); roster growth x{report['population']['roster_growth'] or 0:.2f}; "
             f"segment: {report['population']['segment']}" + (f" ({report['population']['shared_patients']} patients in both releases)" if report['population']['shared_patients'] is not None else '') + '. '
             f"Concepts: {report['parameters']['concepts']}; field resolver: {report['parameters']['resolver']}; tolerance "
             f"{report['parameters']['tolerance']:.0%} and >= {report['parameters']['min_patients']} patients.", '',
             f"**{len(flags)} flags, {len(review)} for review.**" + (
                 f" Gate: {'passed' if gate_result['passed'] else 'BLOCKED (' + str(len(gate_result['open_flags'])) + ' unacknowledged flags)'}." if gate_result else
                 ' Informational (no gate).'), '']
    if report['population']['new_streams']: lines += [f"New streams: {', '.join(report['population']['new_streams'])}", '']
    order = ['new_evidence_source', 'concept_widened', 'concept_narrowed', 'clinically_distinct_code', 'alias_binding_changed',
             'alias_misbinding', 'lab_name_variant', 'metric_member_case_variant', 'metric_literal_case_variant']
    flags.sort(key=lambda f: (order.index(f['kind']) if f['kind'] in order else 99, f['message']))
    lines += ['## Flags', ''] + [f"- `{f['kind']}` {f['message']}" for f in flags[:limit]]
    if len(flags) > limit: lines.append(f"- ... {len(flags) - limit} more in the JSON report")
    lines += ['', '## Review (counts by kind)', ''] + [f"- {k.split(':', 1)[1]}: {n}" for k, n in report['summary'].items() if k.startswith('review:')]
    return '\n'.join(lines) + '\n'


def run(candidate, prior, out=None, *, accept_file=None, gate_on=False, **kw):
    """Diff, write <out>/release-diff.json and .md, return (report, gate_result)."""
    report = diff(prior, candidate, **kw)
    accepted = json.loads(Path(accept_file).read_text()).get('accepted', []) if accept_file and Path(accept_file).exists() else []
    g = gate(report, accepted)
    report['gate'] = {'enabled': bool(gate_on), **g}
    if out:
        out = Path(out); out.mkdir(parents=True, exist_ok=True)
        (out / 'release-diff.json').write_text(json.dumps(report, indent=1, default=str) + '\n')
        (out / 'release-diff.md').write_text(markdown(report, g if gate_on else None))
    return report, g
