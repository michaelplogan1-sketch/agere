"""All-of-healthcare question coverage: both libraries x both stack profiles -> coverage report, acceptance cases, backlog.

Reads config/agere/vbc_questions.json (the value-based-care domain group) and config/agere/healthcare_questions.json
(every other buyer), checks each question's required data elements against each stack's read-only source profile,
compiles it with the local compiler, and writes
  docs/agere/HEALTHCARE_QUESTION_COVERAGE.md   per profile and domain: answered locally / needs router / wrong engine /
                                               unsupported (missing elements tallied) / needs a new engine / knowledge
  docs/agere/HEALTHCARE_BACKLOG.md             ranked (a) new engines and (b) data elements the warehouse lacks
  bench/acceptance/healthcare_library.json     operator-answerable questions (expected engine is a PRODUCT operator)

Usage: .venv/bin/python tools/question_coverage.py [--stack NAME=PATH ...] [--as-of 2029-08-15]
Stacks are opened read-only (cached source-profile.json, else an in-memory profile). No model or paid calls.
"""
from collections import Counter, defaultdict
from pathlib import Path
import argparse, json, re, sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

ARTIFACTS = Path('/Users/mikel/clinical-insights-local/artifacts')
DEFAULT_STACKS = {'v1.23': ARTIFACTS / 'agere-healthcare-v123' / 'stack', 'v2.0': ARTIFACTS / 'agere-healthcare-v20' / 'stack'}
VBC_GROUP = 'value_based_care'
BUCKETS = ['answered locally', 'needs router', 'wrong engine', 'declined locally', 'needs new engine', 'knowledge',
           'unsupported (data missing)']
STOP = set('the a an of and or in for by to our we what which is are how with on at do does this that from per each last '
           'than more most who have has be as it its share percent did any into'.split())


# ---------------------------------------------------------------- library helpers (no compiler needed)
def load_libraries(root=ROOT):
    vbc = json.loads((root / 'config/agere/vbc_questions.json').read_text())
    hc = json.loads((root / 'config/agere/healthcare_questions.json').read_text())
    return vbc, hc


def tokens(text):
    return {w for w in re.findall(r'[a-z0-9]+', text.lower()) if w not in STOP}


def normalize(text):
    return ' '.join(re.findall(r'[a-z0-9]+', text.lower()))


def near_duplicates(questions, others, threshold=0.6):
    """(id, other id, jaccard) pairs whose content-word overlap reaches the threshold, or whose normalized text is equal."""
    out = []
    idx = [(o['id'], normalize(o['question']), tokens(o['question'])) for o in others]
    for q in questions:
        n, t = normalize(q['question']), tokens(q['question'])
        for oid, on, ot in idx:
            if oid == q['id']: continue
            j = len(t & ot) / max(1, len(t | ot))
            if n == on or j >= threshold:
                out.append((q['id'], oid, round(j, 2)))
    return out


def element_available(element, profile):
    """True when any of the element's checks finds the stream (or roster) with all listed fields in the profile."""
    for stream, fields in element.get('checks') or []:
        if stream == '__patients__':
            have = set((profile.get('patients') or {}).get('fields') or {})
        else:
            s = (profile.get('streams') or {}).get(stream)
            if not s or not s.get('records'): continue
            have = set(s.get('fields') or {})
        if set(fields) <= have:
            return True
    return False


def missing_elements(item, elements, profile):
    return [k for k in item.get('required_data') or [] if not element_available(elements[k], profile)]


def engine_of(item):
    """The engine that answers once data is present (unsupported questions carry engine_when_data_arrives)."""
    return item.get('engine_when_data_arrives') or item['expected_engine']


# ---------------------------------------------------------------- classification
def classify(item, elements, profile, compile_fn, as_of, product):
    """One question on one profile -> row with bucket, compiled operation and missing elements."""
    try:
        graph, info = compile_fn(item['question'], profile, as_of)
    except Exception as e:  # a compiler crash is itself a backlog item
        graph, info = None, {'route': 'error', 'reason': repr(e)}
    route = (info or {}).get('route')
    nodes = (graph or {}).get('nodes') or []
    declined = bool(graph and graph.get('coverage') == 'unsupported') or route == 'local_unsupported'
    op = None if declined or not nodes else nodes[-1]['operation']
    if 'required_data' in item:
        missing = missing_elements(item, elements, profile)
    else:  # VBC library: unsupported is hand-assigned against the received data
        missing = ['vbc: ' + item.get('missing_data', 'data not received')] if item['expected_engine'] == 'unsupported' else []
    engine = engine_of(item)
    if missing:
        bucket = 'unsupported (data missing)'
    elif engine.startswith('new_engine:'):
        bucket = 'needs new engine'
    elif engine == 'knowledge':
        bucket = 'knowledge'
    elif engine == 'unsupported':  # hand-assigned unsupported without element list
        bucket = 'unsupported (data missing)'
    elif declined:
        bucket = 'declined locally'
    elif op is None:
        bucket = 'needs router'
    else:
        if engine in product:
            ok = op == engine and (not item.get('expected_measure') or nodes[-1]['parameters'].get('measure') == item['expected_measure'])
        else:  # literal_lookup: any non-operator (SQL/count) plan
            ok = op not in product
        bucket = 'answered locally' if ok else 'wrong engine'
    risk = bucket in ('unsupported (data missing)', 'knowledge', 'needs new engine') and op is not None
    return {'id': item['id'], 'domain': item['domain'], 'persona': item['persona'], 'question': item['question'], 'engine': engine,
            'expected_engine': item['expected_engine'], 'bucket': bucket, 'compiled_operation': op, 'missing': missing,
            'correctness_risk': risk, 'reason': ((info or {}).get('reason') or '')[:160]}


def run(libraries, profiles, compile_fn, as_of, product):
    """{profile name: [row]} over every question of every library."""
    out = {}
    for name, (profile, elements) in profiles.items():
        rows = []
        for lib in libraries:
            for item in lib['questions']:
                rows.append({**classify(item, elements, profile, compile_fn, as_of, product), 'library': lib['schema'].split('/')[0]})
        out[name] = rows
    return out


def acceptance_cases(hc, product):
    return [{'question': q['question'], 'expect_operation': q['expected_engine'], 'id': q['id'], 'domain': q['domain'],
             **({'expect_measure': q['expected_measure']} if q.get('expected_measure') else {})}
            for q in hc['questions'] if q['expected_engine'] in product]


# ---------------------------------------------------------------- reports
def domain_order(vbc, hc):
    return [(VBC_GROUP, d) for d in vbc['domains']] + [(d, d) for d in hc['domains']]


def coverage_report(results, vbc, hc, stacks, as_of):
    names = list(results)
    L = ['# Healthcare question library: coverage on both stack profiles', '',
         f'Generated by `tools/question_coverage.py` from `config/agere/vbc_questions.json` ({vbc["question_count"]} questions, the '
         f'value-based-care group) and `config/agere/healthcare_questions.json` ({hc["question_count"]} questions, {len(hc["domains"])} domains). '
         f'Profiles (read-only): ' + '; '.join(f'`{n}` = `{p}`' for n, p in stacks.items()) + f'. Compiler: `local_compiler.compile`, as of {as_of}. '
         'No model or paid calls. Synthetic data only.', '',
         'Buckets: **answered locally** = compiles to the expected engine; **needs router** = the local compiler declines and the operator '
         'router / hosted planner must choose; **wrong engine** = compiles to a different engine (correctness bug); **declined locally** = the '
         'compiler\'s unsupported rules fire although the data and engine exist; **needs new engine** = data present but no Agere method; '
         '**knowledge** = explanation question; **unsupported** = a required data element is not in this profile.', '']
    L += ['## Summary', '', '| Bucket | ' + ' | '.join(names) + ' |', '|---|' + '---:|' * len(names)]
    totals = {n: Counter(r['bucket'] for r in rows) for n, rows in results.items()}
    for b in BUCKETS:
        L.append(f'| {b} | ' + ' | '.join(f'{totals[n][b]} ({totals[n][b] / len(results[n]):.0%})' for n in names) + ' |')
    L.append('| total | ' + ' | '.join(str(len(results[n])) for n in names) + ' |')
    for lib, label in (('agere-vbc-questions', 'VBC library'), ('agere-healthcare-questions', 'healthcare library')):
        L += ['', f'**{label} only:** ' + '; '.join(
            f'`{n}`: ' + ', '.join(f'{b} {c}' for b, c in Counter(r['bucket'] for r in results[n] if r['library'] == lib).most_common())
            for n in names)]
    for n in names:
        L += ['', f'## By domain: `{n}`', '', '| Group | Domain | Total | Local | Router | Wrong engine | Declined locally | New engine | Knowledge | Unsupported | Top missing elements |',
              '|---|---|---:|---:|---:|---:|---:|---:|---:|---:|---|']
        by = defaultdict(list)
        for r in results[n]: by[r['domain']].append(r)
        for group, d in domain_order(vbc, hc):
            rows = by.get(d, [])
            c = Counter(r['bucket'] for r in rows)
            miss = Counter(m for r in rows for m in r['missing'] if not m.startswith('vbc: '))
            top = ', '.join(f'{k} ({v})' for k, v in miss.most_common(4)) or ('see VBC_QUESTION_COVERAGE.md' if group == VBC_GROUP and c['unsupported (data missing)'] else '')
            L.append(f'| {group} | {d} | {len(rows)} | {c["answered locally"]} | {c["needs router"]} | {c["wrong engine"]} | {c["declined locally"]} | '
                     f'{c["needs new engine"]} | {c["knowledge"]} | {c["unsupported (data missing)"]} | {top} |')
    if len(names) == 2:
        a, b = names
        ia = {r['id']: r for r in results[a]}
        changed = [(ia[r['id']], r) for r in results[b] if ia[r['id']]['bucket'] != r['bucket']]
        L += ['', f'## Questions whose bucket differs between `{a}` and `{b}` ({len(changed)})', '', f'| ID | {a} | {b} | Question |', '|---|---|---|---|']
        L += [f"| {x['id']} | {x['bucket']}{' (' + ', '.join(x['missing']) + ')' if x['missing'] and not x['missing'][0].startswith('vbc') else ''} | {y['bucket']} | {y['question']} |"
              for x, y in changed]
    for n in names:
        rows = results[n]
        wrong = [r for r in rows if r['bucket'] in ('wrong engine', 'declined locally')]
        L += ['', f'## Correctness backlog on `{n}`: wrong engine or declined locally ({len(wrong)})', '',
              '| ID | Bucket | Expected | Compiled | Question | Compiler note |', '|---|---|---|---|---|---|']
        L += [f"| {r['id']} | {r['bucket']} | {r['engine']} | {r['compiled_operation'] or '-'} | {r['question'].replace('|', '/')} | {r['reason'].replace('|', '/')[:100]} |" for r in wrong]
        risky = [r for r in rows if r['correctness_risk']]
        L += ['', f'### Compiled into a data graph although unsupported, knowledge or needing a new engine on `{n}` ({len(risky)})', '',
              '| ID | Bucket | Expected | Compiled | Question |', '|---|---|---|---|---|']
        L += [f"| {r['id']} | {r['bucket']} | {r['engine']} | {r['compiled_operation']} | {r['question'].replace('|', '/')} |" for r in risky]
    for n in names:
        local = [r for r in results[n] if r['bucket'] == 'answered locally' and r['library'] == 'agere-healthcare-questions']
        L += ['', f'## Healthcare-library questions answered locally on `{n}` ({len(local)})', '', '| ID | Engine | Question |', '|---|---|---|']
        L += [f"| {r['id']} | {r['compiled_operation']} | {r['question'].replace('|', '/')} |" for r in local]
    personas = Counter(q['persona'] for q in hc['questions'])
    L += ['', '## Healthcare library by persona', '', '| Persona | Questions |', '|---|---:|'] + [f'| {p} | {c} |' for p, c in personas.most_common()]
    engines = Counter(engine_of(q) for q in hc['questions'])
    L += ['', '## Healthcare library by engine (engine once data arrives)', '', '| Engine | Questions |', '|---|---:|'] + [f'| {e} | {c} |' for e, c in engines.most_common()]
    return '\n'.join(L) + '\n'


def backlog(hc, profiles):
    """(a) new engines and (b) missing data elements, ranked by question count. profiles: {name: source profile}."""
    E, NE = hc['data_elements'], hc['new_engines']
    qs = hc['questions']
    names = list(profiles)
    L = ['# Healthcare analytics backlog: new engines and missing warehouse data', '',
         f'Generated by `tools/question_coverage.py` from `config/agere/healthcare_questions.json` ({len(qs)} questions beyond value-based care). '
         'Counts are questions; a question can need both a new engine and missing data. Availability is checked against the read-only '
         'profiles ' + ', '.join(f'`{n}`' for n in names) + '. VBC-library data gaps are in `docs/agere/VBC_QUESTION_COVERAGE.md`.', '',
         '## (a) New engines needed', '',
         '| Rank | Engine | Method | Questions | Of which also blocked by data | Domains served | Data needed (missing in warehouse marked *) |',
         '|---:|---|---|---:|---:|---|---|']
    groups = defaultdict(list)
    for q in qs:
        e = engine_of(q)
        if e.startswith('new_engine:'): groups[e.split(':', 1)[1]].append(q)
    for i, (name, items) in enumerate(sorted(groups.items(), key=lambda kv: (-len(kv[1]), kv[0])), 1):
        blocked = sum(1 for q in items if q.get('missing_elements'))
        doms = Counter(q['domain'] for q in items)
        data = Counter(k for q in items for k in q['required_data'])
        L.append(f"| {i} | `{name}` | {NE.get(name, '')} | {len(items)} | {blocked} | " + ', '.join(f'{d} ({c})' for d, c in doms.most_common()) + ' | '
                 + ', '.join(f"{k}{'*' if not E[k]['checks'] else ''}" for k, _ in data.most_common()) + ' |')
    L += ['', 'Existing-engine gaps found while assigning engines (not new methods): the VBC measure library lacks FUH/FUM/IET-style behavioural '
          'follow-up measures (assigned `vbc`); the knowledge base is VBC-only, so hospital-program, pharmacy and RWE explanation questions '
          '(assigned `knowledge`) need curated passages.', '']
    L += ['## (b) Data the warehouse lacks (request to the synthetic-warehouse generator)', '',
          'Ranked by questions blocked. "Sole blocker" = questions that become answerable (by an existing engine or SQL) once only this element arrives. '
          'Reference elements are public files (CMS, CDC, FDA) the warehouse could ship as lookup tables.', '',
          '| Rank | Element | Kind | What to generate | Questions blocked | Sole blocker | Domains blocked |', '|---:|---|---|---|---:|---:|---|']
    blocked = defaultdict(list)
    for q in qs:
        for k in q.get('missing_elements') or []: blocked[k].append(q)
    for i, (k, items) in enumerate(sorted(blocked.items(), key=lambda kv: (-len(kv[1]), kv[0])), 1):
        sole = sum(1 for q in items if q['missing_elements'] == [k] and not engine_of(q).startswith('new_engine:'))
        doms = Counter(q['domain'] for q in items)
        L.append(f"| {i} | `{k}` | {E[k]['kind']} | {E[k]['description']} | {len(items)} | {sole} | " + ', '.join(f'{d} ({c})' for d, c in doms.most_common()) + ' |')
    partial = []
    for k, e in E.items():
        if not e['checks']: continue
        have = [n for n in names if element_available(e, profiles[n])]
        if have and len(have) < len(names):
            partial.append((k, have, sum(1 for q in qs if k in q['required_data'])))
    if partial:
        L += ['', '### Elements present in only some profiles', '', '| Element | Present in | Questions using it |', '|---|---|---:|']
        L += [f"| `{k}` | {', '.join(h)} | {c} |" for k, h, c in sorted(partial, key=lambda x: -x[2])]
    return '\n'.join(L) + '\n'


# ---------------------------------------------------------------- entry point
def load_profile(stack):
    """Read-only profile: never writes into the stack directory."""
    from clinical_core.agere.clinical import source_profile
    stack = Path(stack)
    digest = json.loads((stack / 'run.json').read_text())['source_digest']
    cached = stack / 'source-profile.json'
    if cached.exists():
        saved = json.loads(cached.read_text())
        if saved.get('schema') == source_profile.VERSION and saved.get('source_digest') == digest:
            return saved
    return {**source_profile.build(stack / 'stack.sqlite3'), 'source_digest': digest}


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument('--stack', action='append', default=[], help='NAME=PATH (repeatable); default v1.23 and v2.0')
    ap.add_argument('--as-of', default='2029-08-15')
    a = ap.parse_args(argv)
    stacks = dict(s.split('=', 1) for s in a.stack) if a.stack else {k: str(v) for k, v in DEFAULT_STACKS.items()}
    from clinical_core.agere.clinical import capability_registry, local_compiler
    product = set(capability_registry.PRODUCT)
    vbc, hc = load_libraries()
    profiles = {n: (load_profile(p), hc['data_elements']) for n, p in stacks.items()}
    results = run([vbc, hc], profiles, local_compiler.compile, a.as_of, product)
    (ROOT / 'docs/agere/HEALTHCARE_QUESTION_COVERAGE.md').write_text(coverage_report(results, vbc, hc, stacks, a.as_of))
    (ROOT / 'docs/agere/HEALTHCARE_BACKLOG.md').write_text(backlog(hc, {n: p for n, (p, _) in profiles.items()}))
    accept = acceptance_cases(hc, product)
    (ROOT / 'bench/acceptance/healthcare_library.json').write_text(json.dumps(accept, indent=1) + '\n')
    print(json.dumps({n: dict(Counter(r['bucket'] for r in rows)) for n, rows in results.items()} | {'acceptance_cases': len(accept)}, indent=1))


if __name__ == '__main__':
    main()
