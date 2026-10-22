"""Release-diff guard on the question path: answers that rest on a concept / field / metric whose definition drifted in
this warehouse release carry a drift warning, and saved work that predates the drift is re-validated before reuse.

At activation (warehouse_activation.activate -> install) the release-diff report (release_diff.py; computed against the
prior active build when the candidate has none) is condensed into <stack>/release-drift.json: one entry per 'flag' or
'review' finding with the terms that name its subject in executable plans (concept key, its library label/loinc/name
pattern, the new evidence source, the stream field form, the governed metric id). On every executed proposal
(answer_checks.check -> annotate) the plan's executable text and the question are matched against the index: each hit
adds a QC-DRIFT warning ("hba1c gained an evidence source in this release: payer_claim_line ...") to the answer and a
receipt entry; nothing is changed or blocked. plan_fence reads touching() to retire saved pairs that predate a drift on
a subject they read. AGERE_RELEASE_DRIFT points at another index file (tests, or a report not yet installed).
"""
import json
import os
import re
import time
from pathlib import Path

VERSION = 'release-drift/1'
FILE = 'release-drift.json'
KINDS_REVIEWED = {'source_widened', 'source_narrowed', 'new_evidence_source_overlapping'}  # review findings that change scope
_CACHE = {}


def _concept_terms(key):
    terms = [re.escape(key), re.escape(key.replace('_', ' '))]
    try:
        from . import concepts as C
        for table in (C.CONDITIONS, C.MEASUREMENTS, C.ENCOUNTERS):
            d = table.get(key)
            if not d: continue
            terms += [re.escape(c) for c in d.get('loinc') or []]
            for k in ('names', 'text'):
                if isinstance(d.get(k), str): terms.append(d[k])
            terms += [re.escape(c) + r'(?![\d])' for c in d.get('icd10') or []]
    except Exception:  # noqa: BLE001 - the key alone still matches
        pass
    try:
        from .literal_check import ALIASES
        if key in ALIASES: terms.append(ALIASES[key])
        if key == 'hba1c': terms.append(ALIASES['a1c'])
    except Exception:  # noqa: BLE001
        pass
    return terms


def entry(f):
    """One index entry from a release-diff finding, or None when it names no subject a plan can read."""
    s = f.get('subject') or {}
    terms = []
    if isinstance(s, dict):
        if s.get('concept'): terms += _concept_terms(str(s['concept']))
        if s.get('metric'): terms.append(re.escape(str(s['metric'])))
        if s.get('stream') and s.get('form'):
            terms.append(re.escape(str(s['stream'])) + r'.{0,80}\b' + re.escape(str(s['form'])) + r'\b')
        if s.get('stream') and s.get('field'):
            terms.append(re.escape(str(s['stream'])) + r'.{0,80}\b' + re.escape(str(s['field'])) + r'\b')
    elif isinstance(s, str):
        terms.append(re.escape(s))
    if not terms: return None
    return {'id': f['id'], 'kind': f['kind'], 'severity': f['severity'], 'subject': s, 'message': f['message'][:300], 'terms': terms}


def index_from_report(report):
    keep = [f for f in report.get('findings') or [] if f.get('severity') == 'flag' or f.get('kind') in KINDS_REVIEWED]
    entries = [e for e in (entry(f) for f in keep) if e]
    return {'schema': VERSION, 'generated_at': time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime()),
            'candidate': {k: (report.get('candidate') or {}).get(k) for k in ('build', 'release', 'digest', 'as_of')},
            'prior': {k: (report.get('prior') or {}).get(k) for k in ('build', 'release', 'digest', 'as_of')},
            'entries': entries, 'counts': {'flag': sum(e['severity'] == 'flag' for e in entries),
                                           'review': sum(e['severity'] != 'flag' for e in entries)}}


def install(build, *, prior=None, report_path=None):
    """Activation hook: write <build>/stack/release-drift.json from <build>/release-diff.json, computing the report against
    `prior` first when it is missing. -> {'installed', 'entries', 'path'} (never raises into activation; errors returned)."""
    build = Path(build)
    stack = build / 'stack' if (build / 'stack').is_dir() else build
    report_path = Path(report_path) if report_path else build / 'release-diff.json'
    try:
        if not report_path.exists():
            if not prior: return {'installed': False, 'reason': 'no release-diff report and no prior build'}
            from .. import release_diff
            report, _ = release_diff.run(build, prior, build)
        else:
            report = json.loads(report_path.read_text())
        index = index_from_report(report)
        (stack / FILE).write_text(json.dumps(index, indent=1, default=str) + '\n')
        _CACHE.clear()
        return {'installed': True, 'entries': len(index['entries']), 'counts': index['counts'], 'path': str(stack / FILE)}
    except Exception as exc:  # noqa: BLE001 - activation stands; answers simply carry no drift warning
        return {'installed': False, 'error': f'{type(exc).__name__}: {exc}'[:300]}


def load(path):
    target = Path(os.environ.get('AGERE_RELEASE_DRIFT') or Path(path or '.') / FILE)
    try: key = (str(target), target.stat().st_mtime_ns)
    except OSError: return None
    if key not in _CACHE:
        try: doc = json.loads(target.read_text())
        except (OSError, ValueError): doc = None
        _CACHE.clear(); _CACHE[key] = doc if isinstance(doc, dict) and doc.get('schema') == VERSION else None
    return _CACHE[key]


def touching(path, text, question=None):
    """Index entries whose subject the executable text (or, for concept subjects, the question) names."""
    doc = load(path)
    if not doc: return []
    hits = []
    for e in doc['entries']:
        for t in e['terms']:
            try:
                if re.search(t, text or '', re.I) or (question and isinstance(e['subject'], dict) and e['subject'].get('concept')
                                                      and re.search(t, question, re.I)):
                    hits.append(e); break
            except re.error:
                continue
    return hits


def annotate(stack, question, graph, answer):
    """answer_checks hook: QC-DRIFT warnings on an executed answer. -> [entry ids] (empty when nothing drifted)."""
    from .literal_check import executable_text
    from .answer_checks import _add
    path = getattr(stack, 'path', None)
    text = executable_text([n.get('parameters') for n in (graph or {}).get('nodes') or []]) + '\n' + str(answer.get('query') or '')
    hits = touching(path, text, question)
    doc = load(path) or {}
    release = Path(str((doc.get('candidate') or {}).get('release') or (doc.get('candidate') or {}).get('build') or 'this release')).name
    for e in hits:
        _add(answer, 'QC-DRIFT', 'FM-06', f"Definition drift in {release}: {e['message']}", 'warning' if e['severity'] == 'flag' else 'info',
             drift_id=e['id'], drift_kind=e['kind'])
    if hits:
        answer['release_drift'] = {'version': VERSION, 'release': release, 'findings': [e['id'] for e in hits]}
    return [e['id'] for e in hits]
