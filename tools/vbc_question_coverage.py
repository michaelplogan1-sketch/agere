"""VBC question library -> acceptance cases and a coverage report (the build backlog).

Reads config/agere/vbc_questions.json, compiles every question with the local compiler against a stack's source
profile (read-only: the cached source-profile.json, or an in-memory profile from a read-only connection), and writes
  bench/acceptance/vbc_library.json      operator-answerable questions with expect_operation (and expect_measure)
  docs/agere/VBC_QUESTION_COVERAGE.md     local / router / unsupported / knowledge counts, mismatches and top gaps

Usage: .venv/bin/python tools/vbc_question_coverage.py [--stack artifacts/agere-healthcare-v123/stack] [--as-of 2029-08-15]
No model or paid calls.
"""
from collections import Counter, defaultdict
from pathlib import Path
import argparse, json, sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from clinical_core.agere.clinical import capability_registry, local_compiler, source_profile  # noqa: E402

PRODUCT = set(capability_registry.PRODUCT)


def load_profile(stack):
    """Read-only profile: never writes into the stack directory."""
    stack = Path(stack)
    digest = json.loads((stack / 'run.json').read_text())['source_digest']
    cached = stack / 'source-profile.json'
    if cached.exists():
        saved = json.loads(cached.read_text())
        if saved.get('schema') == source_profile.VERSION and saved.get('source_digest') == digest:
            return saved
    return {**source_profile.build(stack / 'stack.sqlite3'), 'source_digest': digest}


def classify(item, graph, info):
    """coverage class + the operation the compiler produced."""
    route = info.get('route')
    expected = item['expected_engine']
    if route == 'local_requires_input':  # the engine can answer once the user supplies a program/terms: it asks for them
        return 'requires_input', None
    if graph and graph.get('coverage') == 'unsupported' or route == 'local_unsupported':
        return 'unsupported_local', None
    if graph and graph.get('nodes'):
        op = graph['nodes'][-1]['operation']
        if expected in PRODUCT:
            ok = op == expected and (not item.get('expected_measure') or graph['nodes'][-1]['parameters'].get('measure') == item['expected_measure'])
        elif expected == 'literal_lookup':
            ok = op not in PRODUCT
        elif expected == 'knowledge':
            ok = op == 'knowledge'  # an explanation answered from the cited knowledge base
        else:
            ok = False  # compiled something for an unsupported question: a wrong answer, not coverage
        return ('local' if ok else 'local_mismatch'), op
    return 'router', None


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--stack', default=str(Path('/Users/mikel/clinical-insights-local/artifacts/agere-healthcare-v123/stack')))
    ap.add_argument('--as-of', default='2029-08-15')
    ap.add_argument('--doc', default='docs/agere/VBC_QUESTION_COVERAGE.md', help='report path (relative to the repo)')
    ap.add_argument('--no-acceptance', action='store_true', help='do not rewrite bench/acceptance/vbc_library.json')
    a = ap.parse_args()
    lib = json.loads((ROOT / 'config/agere/vbc_questions.json').read_text())
    profile = load_profile(a.stack)
    rows = []
    for item in lib['questions']:
        try:
            graph, info = local_compiler.compile(item['question'], profile, a.as_of)
        except Exception as e:  # a compiler crash is itself a backlog item
            graph, info = None, {'route': 'error', 'reason': repr(e)}
        cls, op = classify(item, graph, info)
        rows.append({**item, 'coverage': cls, 'compiled_operation': op, 'route': info.get('route'),
                     'compiled_parameters': graph['nodes'][-1]['parameters'] if graph and graph.get('nodes') else None,
                     'reason': info.get('reason') or '; '.join((graph or {}).get('unmet_requirements') or [])})

    accept = [{'question': r['question'], 'expect_operation': r['expected_engine'], 'id': r['id'],
               **({'expect_measure': r['expected_measure']} if r.get('expected_measure') else {}), 'domain': r['domain']}
              for r in rows if r['expected_engine'] in PRODUCT]
    if not a.no_acceptance:
        (ROOT / 'bench/acceptance/vbc_library.json').write_text(json.dumps(accept, indent=1) + '\n')

    def bucket(r):
        e = r['expected_engine']
        if e == 'unsupported': return 'unsupported (data not received)'
        if e == 'knowledge': return 'knowledge (explanation)'
        if r['coverage'] == 'local': return 'answered locally'
        if r['coverage'] == 'requires_input': return 'asks for missing inputs (program / contract terms)'
        if r['coverage'] == 'router': return 'needs router / hosted planner'
        if r['coverage'] == 'unsupported_local': return 'declined locally (compiler says unsupported)'
        return 'compiled to the wrong engine'
    for r in rows: r['bucket'] = bucket(r)
    order = ['answered locally', 'asks for missing inputs (program / contract terms)', 'needs router / hosted planner', 'compiled to the wrong engine',
             'declined locally (compiler says unsupported)', 'knowledge (explanation)', 'unsupported (data not received)']
    counts = Counter(r['bucket'] for r in rows)
    by_domain = defaultdict(Counter)
    for r in rows: by_domain[r['domain']][r['bucket']] += 1
    engines = Counter(r['expected_engine'] for r in rows)
    personas = Counter(r['persona'] for r in rows)
    families = [('CMS-HCC / RAF / RADV risk adjustment', r'hcc|raf\b|radv|risk score|meat'),
                ('CMS ACO files: assignment lists, CCLF, participant lists, alignment, CMS reports', r'assignment|cclf|participant|alignment|cms (?:quarterly|settlement|assignment)|data-sharing|eligibility files|mbi|benefit enhancement|public use file|evaluation results'),
                ('Contract terms, benchmarks, premiums and payments', r'contract|benchmark|premium|capitation|withhold|revenue|payments|cost targets|apm|savings rates|trend'),
                ('Care-management enrollment, assignment and program exposure', r'care-manag|care manager|care plan|enrollment|program|outreach target|monitoring|workflow adoption|join dates|identification dates'),
                ('Enrollment status: dual/LIS, Medicare entitlement, MA plan, rosters, death', r'dual|lis\b|entitlement|ma plan|roster|death|out-of-area|churn'),
                ('Network lists: preferred SNF/specialist, network status, affiliations', r'preferred|network|affiliation|imaging-center|participant'),
                ('Survey, Star cut points and measure results (CAHPS, Stars, eCQM submissions)', r'cahps|star|ecqm|survey'),
                ('Claim types not received (Part B drugs, IRF, hospice, episodes)', r'part b|rehabilitation|hospice|episode|j-code|regional drg'),
                ('Area-level context (deprivation index, drive time, regional rates)', r'deprivation|drive-time|regional|national ffs')]
    import re as _re

    def family(text):
        return next((name for name, rx in families if _re.search(rx, text.lower())), 'Other: ' + text)
    missing = Counter(family(r['missing_data']) if r['origin'].startswith('public') else
                      'Research-supplement inputs (patient-reported function/goals, caregiver, rule versions, provenance; see each question)'
                      for r in rows if r['expected_engine'] == 'unsupported')
    answerable = [r for r in rows if r['expected_engine'] not in ('unsupported', 'knowledge')]
    eng_cov = defaultdict(Counter)
    for r in answerable: eng_cov[r['expected_engine']][r['bucket']] += 1

    L = ['# VBC question library: coverage and build backlog', '',
         f'Generated by `tools/vbc_question_coverage.py` from `config/agere/vbc_questions.json` ({len(rows)} questions). '
         f'Compiler: `local_compiler.compile` against the read-only source profile of `{a.stack}` (as of {a.as_of}); no model or paid calls. '
         'Synthetic data only. Expected engines are hand-assigned against `capability_registry.PRODUCT` and the received data.', '',
         '## Summary', '', '| Bucket | Questions | Share |', '|---|---:|---:|']
    L += [f'| {b} | {counts.get(b, 0)} | {counts.get(b, 0) / len(rows):.0%} |' for b in order]
    L += ['', f'Operator- or SQL-answerable questions (expected engine is a PRODUCT operator or literal lookup): {len(answerable)}. '
          f'Of these, {sum(1 for r in answerable if r["bucket"] == "answered locally")} compile locally to the expected engine today. '
          f'`bench/acceptance/vbc_library.json` holds the {len(accept)} questions whose expected engine is a PRODUCT operator.']
    for kind in ('unsupported', 'knowledge'):
        sub = [r for r in rows if r['expected_engine'] == kind]
        c = Counter(r['coverage'] for r in sub)
        graphs = Counter(r['compiled_operation'] for r in sub if r['coverage'] == 'local_mismatch')
        L += ['', f'Of the {len(sub)} {kind} questions, the compiler declines {c["unsupported_local"]} locally, asks for missing inputs on '
              f'{c["requires_input"]}, ' + (f'answers {c["local"]} from the cited knowledge base, ' if kind == 'knowledge' else '') +
              f'hands {c["router"]} to the router, and compiles {c["local_mismatch"]} into a data graph'
              + (f' ({", ".join(f"{k}: {v}" for k, v in sorted(graphs.items()))})' if graphs else '')
              + (' (an answer the data cannot support: a correctness risk, unless this release now carries the input; see below).' if kind == 'unsupported'
                 else ' (a number where an explanation was asked).')]
    L += ['', '## By expected engine (answerable questions)', '', '| Engine | Total | Local | Asks for inputs | Router | Wrong engine | Declined locally |',
          '|---|---:|---:|---:|---:|---:|---:|']
    for e, c in sorted(eng_cov.items(), key=lambda kv: -sum(kv[1].values())):
        L.append(f'| {e} | {sum(c.values())} | {c["answered locally"]} | {c["asks for missing inputs (program / contract terms)"]} | '
                 f'{c["needs router / hosted planner"]} | {c["compiled to the wrong engine"]} | {c["declined locally (compiler says unsupported)"]} |')
    L += ['', '## By domain', '', '| Domain | Total | Local | Asks for inputs | Router | Wrong engine | Declined locally | Knowledge | Unsupported |',
          '|---|---:|---:|---:|---:|---:|---:|---:|---:|']
    for d in lib['domains']:
        c = by_domain[d]
        L.append(f'| {d} | {sum(c.values())} | {c["answered locally"]} | {c["asks for missing inputs (program / contract terms)"]} | '
                 f'{c["needs router / hosted planner"]} | {c["compiled to the wrong engine"]} | '
                 f'{c["declined locally (compiler says unsupported)"]} | {c["knowledge (explanation)"]} | {c["unsupported (data not received)"]} |')
    L += ['', '## By persona', '', '| Persona | Questions |', '|---|---:|'] + [f'| {p} | {n} |' for p, n in personas.most_common()]
    L += ['', '## Expected engines', '', '| Engine | Questions |', '|---|---:|'] + [f'| {e} | {n} |' for e, n in engines.most_common()]
    L += ['', '## Missing inputs behind unsupported questions (grouped)', '', '| Missing input | Questions |', '|---|---:|']
    L += [f'| {m} | {n} |' for m, n in missing.most_common(20)]
    L += ['', '## Backlog: answerable questions not answered locally', '',
          'Router = the local compiler declined (words it could not interpret); the operator router / hosted planner must pick the engine. '
          'Wrong engine = the compiler produced a graph whose final operation differs from the expected engine (a correctness bug, highest priority). '
          'Declined locally = the compiler\'s UNSUPPORTED rules fired although the expected engine could answer (rules stale for this release).', '']
    for b in ('compiled to the wrong engine', 'declined locally (compiler says unsupported)', 'asks for missing inputs (program / contract terms)',
              'needs router / hosted planner'):
        items = [r for r in answerable if r['bucket'] == b]
        L += [f'### {b} ({len(items)})', '', '| ID | Domain | Expected | Compiled | Question | Compiler note |', '|---|---|---|---|---|---|']
        for r in items:
            note = (r['reason'] or '').replace('|', '/')[:110]
            L.append(f"| {r['id']} | {r['domain']} | {r['expected_engine']}{' / ' + r['expected_measure'] if r.get('expected_measure') else ''} | "
                     f"{r['compiled_operation'] or '-'} | {r['question'].replace('|', '/')} | {note} |")
        L.append('')
    risky = [r for r in rows if r['expected_engine'] in ('unsupported', 'knowledge') and r['coverage'] == 'local_mismatch']
    L += [f'### unsupported or knowledge questions compiled into a data graph ({len(risky)})', '', '| ID | Expected | Compiled | Question |', '|---|---|---|---|']
    L += [f"| {r['id']} | {r['expected_engine']}{': ' + r['missing_data'] if r.get('missing_data') else ''} | {r['compiled_operation']} | {r['question']} |" for r in risky]
    if any(r['compiled_operation'] == 'risk_adjustment' for r in risky):
        L += ['', 'Expected engines are assigned against the v1.23 release. A release that carries ICD-10 diagnoses on claims (v2.0+) makes '
              'CMS-HCC questions answerable by risk_adjustment, so a risk_adjustment graph here is the data-aware answer, not a wrong one.']
    asked = [r for r in rows if r['coverage'] == 'requires_input']
    L += ['', f'### questions answered by asking for missing inputs ({len(asked)})', '', '| ID | Expected | Question | Asks for |', '|---|---|---|---|']
    L += [f"| {r['id']} | {r['expected_engine']} | {r['question'].replace('|', '/')} | {(r['reason'] or '').replace('|', '/')[:140]} |" for r in asked]
    L += ['', '## Answered locally', '', '| ID | Engine | Parameters | Question |', '|---|---|---|---|']
    for r in rows:
        if r['bucket'] == 'answered locally':
            L.append(f"| {r['id']} | {r['compiled_operation']} | `{json.dumps(r['compiled_parameters'], sort_keys=True)[:90]}` | {r['question'].replace('|', '/')} |")
    (ROOT / a.doc).write_text('\n'.join(L) + '\n')
    detail = {k: dict(Counter(r['coverage'] for r in rows if r['expected_engine'] == k)) for k in ('unsupported', 'knowledge')}
    print(json.dumps({'questions': len(rows), **{b: counts.get(b, 0) for b in order}, 'acceptance_cases': len(accept), 'detail': detail}, indent=1))


if __name__ == '__main__':
    main()
