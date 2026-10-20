"""Reviewed healthcare facts and question-driven, scoped correction memory.

The existing knowledge corpus supplies explicitly opted-in facts. Exact matching
checks the stored reviewed assertion, not a new live source fetch, the user's
note, certified measure scoring, patient eligibility or model weights.
"""
import hashlib
import json
import re
import sqlite3
import time
from collections import Counter
from contextlib import closing
from pathlib import Path
from urllib.parse import urlparse

from . import vbc_knowledge

VERSION = 'healthcare-learning/1'
DOMAINS = ('cms_models', 'ma_measures', 'population_health')
ROOT = Path(__file__).resolve().parents[3]
PRIMARY_HOSTS = ('cms.gov', 'ahrq.gov', 'cdc.gov', 'healthit.gov')
TOPICS = {
    'cms_models': r'\bcms\b|\bcmmi\b|\baco reach\b|\b(?:guide|access|team|lead) model\b',
    'ma_measures': r'\bmedicare advantage\b|\b(?:ma|part [cd]) measures?\b|\bstar ratings?\b|\bhedis\b|\b(?:cbp|pdc|ked)\b',
    'population_health': r'\bpopulation health\b|\bprevalence\b|\bincidence\b|\bperson[- ]time\b|\bpqi\b|\bcare gaps?\b|\brisk stratification\b|\bhealth equit',
}


def _json(value):
    return json.dumps(value, sort_keys=True, separators=(',', ':'), allow_nan=False)


def _digest(value):
    return hashlib.sha256(_json(value).encode()).hexdigest()


def catalog(directory=None):
    """No implicit promotion of the old corpus or approximate engine definitions."""
    out = {}
    for entry in vbc_knowledge.load(directory):
        meta = entry.get('learning')
        if not meta: continue
        scope = meta['scope']
        if meta.get('review_status') != 'primary_source_reviewed' or scope['domain'] not in DOMAINS:
            raise ValueError('Unsupported healthcare learning review or domain')
        if not scope.get('entity') or not scope.get('version') or scope['year_basis'] not in ('performance_year', 'star_year', 'reference_version'):
            raise ValueError('Healthcare fact requires an entity, version and typed year basis')
        if scope['year_basis'] != 'reference_version' and type(scope.get('year')) is not int:
            raise ValueError('Policy facts require an explicit year')
        if not meta.get('aliases') or not meta.get('fields'): raise ValueError('Missing fact aliases or fields')
        for pattern in meta['aliases']: re.compile(pattern)
        for name, field in meta['fields'].items():
            if name not in entry['facts'] or not field.get('label') or not field.get('unit') or not field.get('sources'):
                raise ValueError('Incomplete reviewed fact')
            for index in field['sources']:
                if type(index) is not int or not 0 <= index < len(entry['citations']): raise ValueError('Invalid source reference')
                source = entry['citations'][index]
                url = urlparse(source['url']); host = url.hostname or ''
                if url.scheme != 'https' or not any(host == h or host.endswith('.' + h) for h in PRIMARY_HOSTS):
                    raise ValueError('Learning facts require an official primary source')
                if source.get('source_type') != 'government' or not source.get('section'):
                    raise ValueError('Learning sources require an inspected section')
        # Bind source assertions AND linked operational definitions. An engine
        # definition edit invalidates reuse even when the external fact is stable.
        dependencies = {}
        for kind, ident in meta.get('catalog_links', {}).items():
            if kind not in ('programs', 'measures') or not re.fullmatch(r'[a-z0-9_]+', ident): raise ValueError('Invalid catalog link')
            dependencies[kind + '/' + ident] = json.loads((ROOT/'config/agere'/kind/(ident+'.json')).read_text())
        clean = {k: v for k, v in entry.items() if not k.startswith('_')}
        out[entry['id']] = {**clean, 'source_digest': _digest([clean, dependencies])}
    return out


def _named(entry, question):
    return any(re.search(p, question, re.I) for p in entry['learning']['aliases'])


def applicability(entry, question):
    """Fail closed on mixed/unlabelled MA dates; reference versions are not data years."""
    scope = entry['learning']['scope']
    if not _named(entry, question): return 'unrelated'
    years = set(map(int, re.findall(r'\b(?:19|20)\d{2}\b', question)))
    basis = scope['year_basis']
    if basis == 'reference_version':
        versions = re.findall(r'\b(?:version\s*|v)(20\d{2})\b', question, re.I)
        return 'exact' if not versions or all(v in scope['version'] for v in versions) else 'mismatch'
    if not years: return 'unspecified'
    if basis == 'performance_year':
        if re.search(r'\b(?:measurement|rating|payment) year\b|\bstar ratings?\b', question, re.I): return 'ambiguous'
        return 'exact' if years == {scope['year']} else 'mismatch'
    # A date alone does not establish whether it is a measurement or rating year.
    labels = {
        'star_year': r'(?:star(?:s| ratings?)?(?: year)?|rating year)\s*(20\d{2})|(20\d{2})\s*(?:star(?:s| ratings?)?|ratings?)\b',
        'measurement_year': r'(?:measurement year|my)\s*(20\d{2})|(20\d{2})\s*measurement year',
    }
    found = {key: {int(a or b) for a,b in re.findall(pattern, question, re.I)} for key,pattern in labels.items()}
    consumed = set().union(*found.values())
    if years - consumed or not consumed: return 'ambiguous'
    for key, values in found.items():
        expected = scope['year'] if key == 'star_year' else scope.get(key)
        if values and values != {expected}: return 'mismatch'
    return 'exact'


def candidates(question, entries):
    named = [e for e in entries.values() if _named(e, question)]
    specific = {e['learning']['scope']['domain'] for e in named if not e['learning'].get('generic')}
    return [e for e in named if not e['learning'].get('generic') or e['learning']['scope']['domain'] not in specific]


def matching(question, *, exact=True, entries=None):
    entries = catalog() if entries is None else entries
    return [e for e in candidates(question, entries) if applicability(e, question) in (('exact',) if exact else ('exact', 'unspecified'))]


def rule_options(question):
    out = []
    for e in matching(question):
        meta = e['learning']; scope = meta['scope']
        for field, spec in meta['fields'].items():
            value = e['facts'][field]
            display = (f'{value:.0%}' if spec['unit'] == 'fraction' and type(value) in (int,float)
                       else _json(value) if not isinstance(value, str) else value)
            out.append({'label': e['title'] + ' · ' + spec['label'], 'scope': scope,
                'entry_id': e['id'], 'field': field, 'value': value, 'unit': spec['unit'],
                'display_value': display, 'source_digest': e['source_digest'],
                'sources': [e['citations'][i] for i in spec['sources']]})
    return out


def verify_rule(path, correction_id, rule):
    if type(correction_id) is not int or correction_id < 1: raise ValueError('A saved correction is required')
    if not isinstance(rule, dict) or set(rule) != {'scope', 'entry_id', 'field', 'value', 'source_digest'}:
        raise ValueError('Fact verification needs scope, entry_id, field, value and source_digest')
    entries = catalog()
    e = entries.get(rule['entry_id'])
    if not e: raise ValueError('No reviewed source fact with that identifier')
    if _json(rule['scope']) != _json(e['learning']['scope']): raise ValueError('Source fact scope or version differs')
    if rule['source_digest'] != e['source_digest']: raise ValueError('Source or operational specification changed; reload before verification')
    field = rule['field']
    if field not in e['learning']['fields'] or _json(rule['value']) != _json(e['facts'][field]):
        raise ValueError('Correction value does not match the reviewed source fact')
    from . import learning
    with closing(learning._db(path)) as db, db:
        row = db.execute('SELECT question FROM corrections WHERE rowid=?', (correction_id,)).fetchone()
        if not row or e['id'] not in {r['id'] for r in matching(row[0], entries=entries)}:
            raise ValueError('Correction needs the same entity and unambiguous applicable year or reference version')
        spec = e['learning']['fields'][field]
        payload = {'schema': VERSION, **rule, 'correction_id': correction_id, 'question': row[0],
            'scope_aliases': e['learning']['aliases'],
            'scope_generic': e['learning'].get('generic', False),
            'sources': [e['citations'][i] for i in spec['sources']], 'last_verified': e['last_verified'],
            'authority': 'exact_match_to_reviewed_stored_fact; free_text_feedback_not_verified',
            'verification': 'deterministic_fact_scope_and_version_check', 'created': time.time()}
        ident = _digest([correction_id, rule])
        db.executescript('''CREATE TABLE IF NOT EXISTS healthcare_lessons(id TEXT PRIMARY KEY,payload TEXT NOT NULL);
            CREATE TRIGGER IF NOT EXISTS healthcare_lessons_no_update BEFORE UPDATE ON healthcare_lessons
            BEGIN SELECT RAISE(ABORT,'Fact lessons are append-only'); END;
            CREATE TRIGGER IF NOT EXISTS healthcare_lessons_no_delete BEFORE DELETE ON healthcare_lessons
            BEGIN SELECT RAISE(ABORT,'Fact lessons are append-only'); END;''')
        db.execute('INSERT OR IGNORE INTO healthcare_lessons VALUES(?,?)', (ident, _json(payload)))
    return {'id': ident, 'status': 'verified_rule', 'field': field,
        'effect': 'Fact checked against its reviewed source, scope and version. Later matching questions reuse it; your explanation remains feedback.'}


def _rows(path):
    target = Path(path)/'learning.sqlite3'
    if not target.exists(): return [], []
    with closing(sqlite3.connect(target.resolve().as_uri()+'?mode=ro', uri=True)) as db:
        names = {r[0] for r in db.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        lessons = db.execute('SELECT id,payload FROM healthcare_lessons').fetchall() if 'healthcare_lessons' in names else []
        corrections = db.execute('SELECT rowid,question,note,created FROM corrections ORDER BY rowid DESC').fetchall() if 'corrections' in names else []
    return lessons, corrections


def lessons(path):
    rows, corrections = _rows(path); entries = catalog(); out = []
    for ident, raw in rows:
        lesson = json.loads(raw); entry = entries.get(lesson['entry_id']); status = 'active'
        if not entry: status = 'source_unavailable'
        elif entry['source_digest'] != lesson['source_digest']: status = 'source_changed'
        elif any(cid > lesson['correction_id'] and applicability(entry, q) in ('exact', 'unspecified', 'ambiguous') for cid,q,_,_ in corrections):
            status = 'suspended_by_new_correction'
        out.append({'id': ident, **lesson, 'status': status})
    return sorted(out, key=lambda r: r['created'], reverse=True)


def context(path, question):
    ids = {e['id'] for e in matching(question)}
    return [r for r in lessons(path) if r['status'] == 'active' and r['entry_id'] in ids][:8]


def feedback_context(path, question):
    # Recompute matching against current catalog, independent of active lessons.
    # Even unqualified/undated feedback retains a recheck obligation for that entity.
    rows, corrections = _rows(path); out = []
    known = catalog()
    for _,raw in rows:
        lesson = json.loads(raw)
        if lesson.get('scope_aliases'):
            known.setdefault(lesson['entry_id'] + ':' + _digest(lesson['scope']), {
                'id': lesson['entry_id'], 'learning': {'scope': lesson['scope'], 'aliases': lesson['scope_aliases'],
                                                    'generic': lesson.get('scope_generic', False)}})
    entries = matching(question, exact=False, entries=known)
    seen = set()
    for e in entries:
        key = (e['id'], _json(e['learning']['scope']))
        if key in seen: continue
        seen.add(key)
        for cid,q,note,created in corrections:
            if applicability(e, q) in ('exact', 'unspecified', 'ambiguous'):
                out.append({'status': 'needs_revalidation', 'question': q, 'note': note, 'created': created,
                    'healthcare_feedback': {'correction_id': cid, 'entry_id': e['id'], 'scope': e['learning']['scope']},
                    'authority': 'reported_feedback; recheck source, not verified truth'})
                break
    return out


def knowledge_context(question):
    entries = catalog()
    return {'facts': [{'id': e['id'], 'scope': e['learning']['scope'], 'applicability': applicability(e, question),
                      'facts': e['facts'], 'citations': e['citations'], 'source_digest': e['source_digest'],
                      'limitations': e['learning']['limitations']} for e in matching(question, exact=False, entries=entries)],
            'unresolved_scope': [e['id'] for e in candidates(question, entries) if applicability(e, question) in ('mismatch', 'ambiguous')],
            'policy': 'Use exact scoped facts only. Unspecified year requires disclosure and clarification before applying policy. '
                      'Reference versions are methods, not patient measurement years. Approximate measures do not establish certified HEDIS or contract Stars. '
                      'Use existing knowledge for explanations and measure/vbc engines for calculations; name missing eligibility, exclusions and data.'}


def topics(question):
    return [k for k,pattern in TOPICS.items() if re.search(pattern, question, re.I)]


def summary(path):
    counts, gaps, questions = Counter(), Counter(), 0
    target = Path(path)/'learning.sqlite3'
    if target.exists():
        with closing(sqlite3.connect(target.resolve().as_uri()+'?mode=ro', uri=True)) as db:
            if db.execute("SELECT 1 FROM sqlite_master WHERE name='outcomes'").fetchone():
                for q,status,reason,n in db.execute('SELECT question,status,reason,COUNT(*) FROM outcomes GROUP BY question,status,reason'):
                    tags = topics(q)
                    if not tags: continue
                    questions += n
                    for tag in tags: counts[tag] += n
                    if status != 'answered': gaps[reason or status] += n
    learned = lessons(path)
    return {'schema': VERSION, 'questions': questions, 'topics': dict(counts),
            'gaps': [{'reason': r, 'count': n} for r,n in gaps.most_common(10)],
            'verified_rules': sum(r['status']=='active' for r in learned),
            'inactive_rules': sum(r['status']!='active' for r in learned), 'lessons': learned[:20],
            'reviewed_entries': len(catalog()), 'model_weights_trained': False,
            'mode': 'Questions identify gaps automatically. Only reviewed, scoped facts are reusable; source updates and later feedback require revalidation.'}
