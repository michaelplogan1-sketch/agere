"""The clinical knowledge layer: cited clinical concepts, retrieved at question time, enforced only after clinical review.

Why. Independent tests showed Agere's answers diverge from a hospital's analysts mainly on definitions. Customer onboarding
(customer.py) settles the customer's own definitions. Many questions also turn on medical science no customer writes down:
what stage 3 CKD is, that anaemia has a different haemoglobin cutoff for men and women, that a CMS165 patient with no
reading counts as uncontrolled, that SOFA cannot be computed from claims. This layer states that science once, with its
source, and gives it to every part of the stack that interprets a question.

What it holds (config/agere/clinical_knowledge/):
  sources.json   the curated source store: each guideline, measure specification, code system or paper with its citation,
                 licence and what Agere keeps from it (summaries in our own words and public code lists, never paper text);
  concepts.json  clinical concepts proposed from those sources: plain-words summary, codes (LOINC, ICD-10-CM), drug classes,
                 thresholds, readings where the sources differ, what cannot be computed, and test cases;
  reviews.json   the clinical reviewer's decisions, each bound to the exact content it approved (a hash).

The lifecycle, like saved recipes. A concept starts as a candidate. It becomes enforced only when its test cases pass at its
current content AND the clinical reviewer approved that exact content. Editing an enforced concept returns it to candidate
until it is reviewed again; a rejected concept is never served. Nothing is learned without review; a model may propose a
concept (validate()), never approve one. Candidates are served only in an evaluation run that asks for them by name
(AGERE_TIERED_KNOWLEDGE=candidates, or the proof arms *_knowledge), so their effect can be measured before review.

Where it is used:
  block(question)        the frontier SQL writer's prompt (models.llm_recipe), after the governed definitions;
  context(question)      the clarifying conversation (clarify.py): a reading the sources disagree on is worth asking about;
  reader_note(statement) the document readers' model prompts (read.py);
  check(question, sql)   an enforced concept's code check, beside the governed checks (router.py): a query that takes a reading
                         the concept rules out (a wrong threshold, a missing half of a two-part numerator) does not answer;
  not_computable(q)      a concept that says what this warehouse lacks declines the question before any rung;
  cited(question)        the concepts and sources an answer relied on, shown with the answer (app.py).

Modes (AGERE_TIERED_KNOWLEDGE): 'reviewed' (default: enforced concepts only), 'candidates' (evaluation only: enforced and
candidate concepts), 'off'. ENABLED overrides the environment for one thread (proof arms).

    python -m clinical_core.agere.tiered.knowledge status            every concept, its status and why
    python -m clinical_core.agere.tiered.knowledge test [ID]         run the test cases and show the warehouse binding
    python -m clinical_core.agere.tiered.knowledge show ID           one concept as the reviewer sees it
    python -m clinical_core.agere.tiered.knowledge review ID --decision approve|reject --by NAME [--notes TEXT]
"""
import contextvars
import hashlib
import json
import os
import re
import sys
import time
from pathlib import Path

DIRECTORY = Path(os.environ.get('AGERE_TIERED_KNOWLEDGE_DIR', 'config/agere/clinical_knowledge'))
ENABLED = contextvars.ContextVar('agere_knowledge', default=None)   # None: follow AGERE_TIERED_KNOWLEDGE
MODES = ('off', 'reviewed', 'candidates')
MAX_SERVED = 4   # concepts given to one prompt: the closest few, so a broad question does not carry the whole store


def mode():
    flag = ENABLED.get()
    m = flag if flag is not None else os.environ.get('AGERE_TIERED_KNOWLEDGE', 'reviewed')
    return m if m in MODES else 'reviewed'


# ---- the store -------------------------------------------------------------------------------------------------------

_cache = {}


def _read(name):
    p = DIRECTORY / name
    return json.loads(p.read_text()) if p.exists() else {}


def _load():
    key = tuple((DIRECTORY / n).stat().st_mtime if (DIRECTORY / n).exists() else 0 for n in ('sources.json', 'concepts.json', 'reviews.json'))
    if _cache.get('key') != (str(DIRECTORY), key):
        _cache.update(key=(str(DIRECTORY), key), sources={s['id']: s for s in _read('sources.json').get('sources', [])},
                      concepts=_read('concepts.json').get('concepts', []), reviews=_read('reviews.json').get('reviews', []))
    return _cache


def sources():
    return _load()['sources']


def concepts():
    return _load()['concepts']


def concept(cid):
    return next((c for c in concepts() if c['id'] == cid), None)


def content_hash(c):
    """The hash a review approves: everything in the concept and the text of every source it cites."""
    cited = {s['id']: sources().get(s['id']) for s in c.get('sources', [])}
    return hashlib.sha256(json.dumps({'concept': c, 'sources': cited}, sort_keys=True).encode()).hexdigest()[:16]


def validate(c):
    """-> problems with a proposed concept ([] when it is well formed). A proposal from the literature, by a person or a model,
    enters concepts.json only through this and code review, and always as a candidate."""
    problems = []
    for field in ('id', 'name', 'domain', 'triggers', 'summary', 'sources', 'tests'):
        if not c.get(field): problems.append(f'missing {field}')
    for field in ('triggers',):
        try: re.compile(c.get(field) or '')
        except re.error as exc: problems.append(f'{field} is not a valid pattern: {exc}')
    for part in ('check', 'not_computable'):
        if c.get(part):
            try: re.compile(c[part].get('when_question') or ''); re.compile(c[part].get('when_sql') or '')
            except re.error as exc: problems.append(f'{part} pattern: {exc}')
    for s in c.get('sources') or []:
        if s.get('id') not in sources(): problems.append(f"cites {s.get('id')}, which is not in the source store")
    tests = c.get('tests') or {}
    if not tests.get('retrieves'): problems.append('needs at least one question it must be retrieved for')
    if not tests.get('ignores'): problems.append('needs at least one question it must not be retrieved for')
    if c.get('check') and not (tests.get('check_passes') and tests.get('check_fails') and tests.get('check_question')):
        problems.append('a concept with a code check needs a passing and a failing SQL example and a check question')
    if c.get('not_computable') and not tests.get('not_computable_question'): problems.append('a not-computable concept needs a question it declines')
    return problems


# ---- matching and checks (code only) ---------------------------------------------------------------------------------

def _q(text):
    return ' ' + ' '.join(str(text).lower().split()) + ' '


def matches(c, question):
    return re.search(c['triggers'], _q(question)) is not None


def _numbers_outside_strings(sql):
    bare = re.sub(r"'(?:[^']|'')*'", ' ', sql)
    return {float(n) for n in re.findall(r'(?<![\w.])(\d+(?:\.\d+)?)(?![\w.])', bare.replace(',', ''))}


def _has_literal(sql, literal):
    try:
        value = float(literal.replace(',', ''))
    except ValueError:
        return literal.lower() in sql.lower()
    return value in _numbers_outside_strings(sql) or (literal in sql and not re.fullmatch(r'[\d.]+', literal))


def _check_one(c, question, sql):
    """-> the message when this concept's code check rules the SQL out for this question, else None."""
    ck = c.get('check')
    if not ck or not re.search(ck['when_question'], _q(question)) or not re.search(ck['when_sql'], sql, re.I): return None
    missing = [group for group in ck['needs'] if not any(_has_literal(sql, lit) for lit in group)]
    if not missing: return None
    want = '; '.join(' or '.join(g) for g in missing)
    return (f"Clinical definition ({c['name']}; {short_cite(c)}): {c['summary']} The query does not use what this definition "
            f'needs ({want}). Rewrite it to follow the definition.')


def _not_computable_one(c, question):
    nc = c.get('not_computable')
    if nc and re.search(nc['when_question'], _q(question)):
        return f"{c['name']} ({short_cite(c)}) needs {nc['needs']}"
    return None


def run_tests(c):
    """The concept's own test cases, code only. -> [failure, ...] ([] when every case passes)."""
    failures = list(validate(c))
    t = c.get('tests') or {}
    failures += [f'not retrieved for: {q}' for q in t.get('retrieves', []) if not matches(c, q)]
    failures += [f'retrieved for an unrelated question: {q}' for q in t.get('ignores', []) if matches(c, q)]
    if c.get('check'):
        qq = t.get('check_question', '')
        if not re.search(c['check']['when_question'], _q(qq)): failures.append(f'the check does not apply to its own check question: {qq}')
        failures += [f'check rejects its correct example: {s[:80]}' for s in t.get('check_passes', []) if _check_one(c, qq, s)]
        failures += [f'check accepts its wrong example: {s[:80]}' for s in t.get('check_fails', []) if not _check_one(c, qq, s)]
    if c.get('not_computable') and not _not_computable_one(c, t.get('not_computable_question', '')):
        failures.append('does not decline its own not-computable question')
    return failures


def review_of(c):
    """The latest review of this concept, or None."""
    mine = [r for r in _load()['reviews'] if r.get('concept') == c['id']]
    return mine[-1] if mine else None


def status(c):
    """-> (status, why). 'enforced': tests pass and the reviewer approved this exact content. 'rejected': the reviewer rejected
    this exact content. 'candidate' otherwise (never reviewed, reviewed content since changed, or tests failing)."""
    failures = run_tests(c)
    r, h = review_of(c), content_hash(c)
    if r and r.get('hash') == h and r.get('decision') == 'reject':
        return 'rejected', f"rejected by {r.get('by')} on {r.get('at', '')[:10]}: {r.get('notes') or 'no notes'}"
    if failures:
        return 'candidate', f'{len(failures)} test case(s) failing: ' + '; '.join(failures[:3])
    if r and r.get('decision') == 'approve' and r.get('hash') == h:
        return 'enforced', f"approved by {r.get('by')} on {r.get('at', '')[:10]}"
    if r and r.get('hash') != h:
        return 'candidate', f"changed since {r.get('by')}'s review on {r.get('at', '')[:10]}: needs review again"
    return 'candidate', 'tests pass; awaiting clinical review'


def served():
    """The concepts this mode may show to a question: enforced ones, and candidates in an evaluation run."""
    m = mode()
    if m == 'off': return []
    allowed = ('enforced', 'candidate') if m == 'candidates' else ('enforced',)
    key = ('served', m, _load()['key'])
    if key not in _cache:
        _cache[key] = [c for c in concepts() if status(c)[0] in allowed]
    return _cache[key]


def relevant(question, k=MAX_SERVED):
    """-> the served concepts this question calls for, most specific first (longest trigger match)."""
    q = _q(question)
    hits = []
    for c in served():
        m = re.search(c['triggers'], q)
        if m: hits.append((-(m.end() - m.start()), c['id'], c))
    return [c for _, _, c in sorted(hits)[:k]]


def short_cite(c):
    out = []
    for s in c.get('sources', []):
        src = sources().get(s['id'])
        if src and src['kind'] != 'code system': out.append(src.get('short') or f"{src['id']} {src['year']}")
    return ', '.join(dict.fromkeys(out)) or 'code systems'


def check(question, sql, params=None):
    """-> messages for every served concept whose code check rules this SQL out ([] when none)."""
    text = str(sql)
    for name, value in (params or {}).items():
        if isinstance(value, dict): value = value.get('value')
        if isinstance(value, (int, float)): text = re.sub(rf':{re.escape(name)}\b', str(value), text)
        elif isinstance(value, str): text = re.sub(rf':{re.escape(name)}\b', "'" + value.replace("'", "''") + "'", text)
    out = []
    for c in relevant(question, k=len(concepts())):
        m = _check_one(c, question, ' '.join(text.split()))
        if m: out.append(m)
    return out


def not_computable(question):
    """-> [reason, ...] for served concepts that say this question needs data the warehouse does not hold."""
    return [m for m in (_not_computable_one(c, question) for c in relevant(question, k=len(concepts()))) if m]


# ---- binding: where a concept's codes are in this warehouse ----------------------------------------------------------

_ICD_COLUMNS = (('encounter_diagnosis', 'icd10'), ('condition', 'icd10'), ('payer_claim_diagnosis', 'icd10'), ('hospital_visit', 'principal_icd10'))


def binding(c):
    """-> lines saying where the concept's LOINC codes, diagnosis codes and drug classes appear in the warehouse in use.
    Computed by code from the codes, so the literature stays warehouse-independent. Cached per warehouse."""
    from . import store
    cache = store.scoped('knowledge_binding')
    if c['id'] in cache: return cache[c['id']]
    lines = []
    try:
        db = store.connect()
    except Exception:
        return []
    try:
        tables = {r[0] for r in db.execute("SELECT name FROM sqlite_master WHERE type IN ('table','view')")}
        if 'measurement' in tables:
            cols = {r[1] for r in db.execute('PRAGMA table_info(measurement)')}
            for m in c.get('measures', []):
                found = []
                if 'loinc' in cols and m.get('loinc'):
                    marks = ','.join('?' * len(m['loinc']))
                    name_col = 'COALESCE(concept, name)' if 'name' in cols else 'concept'
                    for code, label, unit, n in db.execute(f'SELECT loinc, {name_col}, unit_canonical, COUNT(*) FROM measurement WHERE loinc IN ({marks}) GROUP BY 1, 2, 3 ORDER BY 4 DESC', m['loinc']):
                        is_concept = db.execute('SELECT 1 FROM measurement WHERE concept = ? LIMIT 1', (label,)).fetchone() is not None
                        where = f"concept = '{label}'" if is_concept else f"loinc = '{code}' (concept is empty; name '{label}')"
                        found.append(f"measurement {where}, LOINC {code}, {n} results" + (f', unit {unit}' if unit else ' (unit_canonical empty: use value and unit)'))
                if not found and m.get('concept'):
                    n = db.execute('SELECT COUNT(*) FROM measurement WHERE concept = ?', (m['concept'],)).fetchone()[0]
                    if n: found.append(f"measurement concept = '{m['concept']}', {n} results")
                lines += found or [f"{m.get('concept')}: not found in this warehouse (LOINC {', '.join(m.get('loinc', [])) or 'none'})"]
        if c.get('icd10'):
            likes = ' OR '.join(f"{{col}} LIKE '{p}%'" for p in c['icd10'])
            held = []
            for table, col in _ICD_COLUMNS:
                if table in tables:
                    try:
                        n = db.execute(f'SELECT COUNT(*) FROM {table} WHERE ' + likes.format(col=col)).fetchone()[0]
                    except Exception:
                        continue
                    if n: held.append(f'{table}.{col} ({n} rows)')
            codes = ', '.join(c['icd10'])
            lines.append(f'ICD-10-CM {codes} (prefixes; dotted codes as stored): ' + ('; '.join(held) if held else 'none in this warehouse'))
        if c.get('drug_classes') and 'fill' in tables:
            have = {r[0] for r in db.execute('SELECT DISTINCT drug_class FROM fill')}
            got = [d for d in c['drug_classes'] if d in have]
            gone = [d for d in c['drug_classes'] if d not in have]
            lines.append('fill.drug_class ' + ', '.join(f"'{d}'" for d in got) + (f' (not held: {", ".join(gone)})' if gone else '')
                         + '; supply runs fill_date to supply_end')
    finally:
        db.close()
    cache[c['id']] = lines
    return lines


# ---- what each part of the stack is given ----------------------------------------------------------------------------

def _label(c):
    return '' if status(c)[0] == 'enforced' else ' [candidate, not yet clinically reviewed]'


def block(question):
    """The text the frontier SQL writer is given. Empty when no served concept applies."""
    found = relevant(question)
    if not found: return ''
    lines = ['Clinical definitions from cited sources (follow them where the customer and governed definitions above are silent; '
             'a customer definition wins where they differ):']
    for c in found:
        lines.append(f"- {c['name']} ({short_cite(c)}){_label(c)}: {c['summary']}")
        for r in c.get('readings') or []:
            lines.append(f"    reading where sources differ: {r['label']} ({r['rule']})")
        if c.get('not_computable'): lines.append(f"    cannot be computed without: {c['not_computable']['needs']}")
        lines += [f'    in this warehouse: {b}' for b in binding(c)]
    return '\n'.join(lines)


def context(question):
    """The text the clarifying conversation is given: what the sources settle, and where they disagree (worth asking)."""
    found = relevant(question)
    if not found: return ''
    lines = ['Clinical definitions from cited sources (they settle what they cover unless the customer defines it otherwise; '
             'where the sources differ, that is worth asking about):']
    for c in found:
        lines.append(f"- {c['name']} ({short_cite(c)}): {c['summary']}")
        if c.get('readings'):
            lines.append('    the sources differ: ' + ' | '.join(r['label'] for r in c['readings']))
    return '\n'.join(lines)


def reader_note(statement):
    """One short paragraph for a document reader: the clinical definition behind the statement it judges, or ''."""
    found = relevant(statement, k=2)
    if not found: return ''
    return 'Clinical definition (for judging meaning only; the document must still state it): ' + ' '.join(
        f"{c['name']} ({short_cite(c)}): {c['summary']}" for c in found)


def cited(question):
    """-> [{'concept','name','status','sources':[{'title','citation','url'}]}] for the concepts this question used."""
    out = []
    for c in relevant(question):
        srcs = [sources()[s['id']] for s in c.get('sources', []) if s['id'] in sources()]
        out.append({'concept': c['id'], 'name': c['name'], 'status': status(c)[0],
                    'sources': [{'title': s['title'], 'citation': s['citation'], 'url': s['url'], **({'where': x.get('where')} if x.get('where') else {})}
                                for s, x in zip(srcs, [x for x in c.get('sources', []) if x['id'] in sources()])]})
    return out


def stated(question):
    """One line for an answer's facts: the clinical definitions it relied on and their sources."""
    found = cited(question)
    if not found: return None
    return 'Clinical definitions used: ' + '; '.join(
        f"{f['name']}" + ('' if f['status'] == 'enforced' else ' (candidate, not yet reviewed)') + ' - ' + '; '.join(s['citation'] for s in f['sources'] if s['citation']) for f in found)


# ---- review ----------------------------------------------------------------------------------------------------------

def review(cid, decision, by, notes=None):
    """Record the clinical reviewer's decision on the concept's current content. An approval needs every test case passing.
    The decision is bound to the content hash: a later edit needs a new review."""
    c = concept(cid)
    if c is None: raise SystemExit(f'No concept {cid}')
    if decision not in ('approve', 'reject'): raise SystemExit('decision is approve or reject')
    if not str(by or '').strip(): raise SystemExit('--by names the clinical reviewer')
    if decision == 'approve' and (os.environ.get('CLAUDECODE') or os.environ.get('CODEX_SANDBOX') or os.environ.get('AGERE_AGENT')):
        raise SystemExit('An approval is the clinical reviewer\'s own act: run this in your own terminal, not through an agent.')
    failures = run_tests(c)
    if decision == 'approve' and failures:
        raise SystemExit('Cannot approve: test cases fail:\n  ' + '\n  '.join(failures))
    path = DIRECTORY / 'reviews.json'
    data = json.loads(path.read_text()) if path.exists() else {'schema': 'agere-clinical-reviews/1', 'reviews': []}
    data['reviews'].append({'concept': cid, 'hash': content_hash(c), 'decision': decision, 'by': by.strip(),
                            'at': time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime()), 'notes': notes})
    path.write_text(json.dumps(data, indent=1) + '\n')
    _cache.clear()
    return status(concept(cid))


def _show(c):
    st, why = status(c)
    lines = [f"{c['id']}  {c['name']}  [{st}] {why}", f"  domain: {c['domain']}   content hash: {content_hash(c)}", '  ' + c['summary']]
    for r in c.get('readings') or []: lines.append(f"  reading: {r['label']} ({r['rule']})")
    if c.get('check'): lines.append(f"  code check: when the question matches /{c['check']['when_question']}/ and the SQL reads /{c['check']['when_sql']}/, it must use " + '; '.join(' or '.join(g) for g in c['check']['needs']))
    if c.get('not_computable'): lines.append(f"  declines: /{c['not_computable']['when_question']}/ - needs {c['not_computable']['needs']}")
    for s in c.get('sources', []):
        src = sources().get(s['id'], {})
        lines.append(f"  source: {src.get('title')} - {src.get('citation')} {src.get('url')}" + (f" ({s['where']})" if s.get('where') else '')
                     + ('' if src.get('verified_against_source') else '  [not yet checked against the published text]'))
        lines.append(f"    licence: {src.get('licence')}")
    lines += [f'  in this warehouse: {b}' for b in binding(c)]
    return '\n'.join(lines)


def main(argv):
    if not argv or argv[0] == 'status':
        for c in concepts():
            st, why = status(c)
            print(f"{st:10} {c['id']:30} {why}")
        return 0
    if argv[0] == 'test':
        bad = 0
        for c in [concept(argv[1])] if len(argv) > 1 else concepts():
            f = run_tests(c)
            bad += bool(f)
            print(f"{'PASS' if not f else 'FAIL'} {c['id']}" + ''.join(f'\n   - {x}' for x in f))
            for b in binding(c): print(f'     binds: {b}')
        return 1 if bad else 0
    if argv[0] == 'show' and len(argv) > 1:
        print(_show(concept(argv[1]))); return 0
    if argv[0] == 'review' and len(argv) > 1:
        import argparse
        p = argparse.ArgumentParser(prog='knowledge review')
        p.add_argument('id'); p.add_argument('--decision', required=True); p.add_argument('--by', required=True); p.add_argument('--notes')
        a = p.parse_args(argv[1:])
        print(_show(concept(a.id))); print(review(a.id, a.decision, a.by, a.notes)); return 0
    print(__doc__); return 2


if __name__ == '__main__':
    sys.exit(main(sys.argv[1:]))
