"""Acceptance gate for declarative compiler rules: the only way a rule reaches the local compiler.

A candidate rule (config/agere/compiler_rules/*.json, or a draft from propose.py) is accepted only when, on BOTH stack
profiles (read-only):
  (a) its own positives match it with the expected operation/returns and its negatives do not match it;
  (b) every question it newly captures in bench/questions/*.json, bench/acceptance/*.json and both question libraries is
      a question it answers with the labelled engine: never a question labelled for another engine, unsupported on that
      profile (missing data), knowledge or a new engine; never an acceptance case expecting another operation. A rule only
      runs where the hand-written routes and previously accepted rules declined, so it cannot change an existing route
      (re-checked: the questions it captures were declined before it);
  (c) where a reference exists it reproduces it: a captured bench question must match the bench reference answer
      (bench.common.Warehouse over the active v1.23 warehouse, scored like bench/score.py); a captured library question
      must compile to its labelled engine; every SQL plan the rule produces must execute read-only on that profile's
      semantic layer.
The gate writes a receipt (config/agere/compiler_rules/receipts/<id>@<version>.json when accepted with --write;
tmp/compiler-growth/receipts/ otherwise). compiler_rules.load() honours a rule only with an accepted receipt whose digest
matches the rule, ordered by the receipt's acceptance sequence.

Usage: .venv/bin/python tools/compiler_growth/accept.py config/agere/compiler_rules/population_lookups.json [--rule ID] [--write]
No model or paid calls. Synthetic data only. Baselines are cached in tmp/compiler-growth/ (keyed on code, rules and corpus).
"""
import argparse, hashlib, json, re, sqlite3, sys, time
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT)); sys.path.insert(0, str(ROOT / 'tools'))

from clinical_core.agere.clinical import capability_registry, compiler_rules, local_compiler  # noqa: E402

VERSION = 'compiler-growth-gate/1'
ARTIFACTS = Path('/Users/mikel/clinical-insights-local/artifacts')
STACKS = {'v1.23': ARTIFACTS / 'agere-healthcare-v123' / 'stack', 'v2.0': ARTIFACTS / 'agere-healthcare-v20' / 'stack'}
REFERENCE_PROFILE = 'v1.23'           # bench reference answers are computed on the active v1.23 warehouse
LIBRARY_AS_OF = '2029-08-15'          # the as-of tools/question_coverage.py uses on both profiles
CACHE = ROOT / 'tmp' / 'compiler-growth'
PRODUCT = set(capability_registry.PRODUCT)
SQL_SECONDS = 20


# ---------------------------------------------------------------- corpus
def corpus(root=ROOT):
    """[{kind, id, question, as_of, ...}] over bench questions, acceptance sets and both libraries."""
    import question_coverage as qc
    out = []
    for path in sorted((root / 'bench/questions').glob('*.json')):
        for q in json.loads(path.read_text()):
            out.append({'kind': 'bench', 'id': q['id'], 'question': q['question'], 'as_of': q['as_of'], 'item': q})
    for path in sorted((root / 'bench/acceptance').glob('*.json')):
        for i, c in enumerate(json.loads(path.read_text())):
            out.append({'kind': 'acceptance', 'id': f'{path.stem}#{i}', 'question': c['question'], 'as_of': LIBRARY_AS_OF, 'item': c})
    vbc, hc = qc.load_libraries(root)
    for lib in (vbc, hc):
        for q in lib['questions']:
            out.append({'kind': 'library', 'id': q['id'], 'question': q['question'], 'as_of': LIBRARY_AS_OF, 'item': q})
    return out, hc['data_elements']


def _sha(*parts):
    h = hashlib.sha256()
    for p in parts: h.update(p if isinstance(p, bytes) else str(p).encode()); h.update(b'\x1f')
    return h.hexdigest()


def code_identity():
    files = [Path(local_compiler.__file__), Path(compiler_rules.__file__), Path(__file__)]
    return _sha(*[p.read_bytes() for p in files])


def load_profiles(names=None):
    import question_coverage as qc
    return {n: (qc.load_profile(p), p) for n, p in STACKS.items() if (names is None or n in names) and (p / 'run.json').exists()}


# ---------------------------------------------------------------- compilation
def _compile(question, profile, as_of):
    try:
        return local_compiler.compile(question, profile, as_of)
    except Exception as e:  # a crash is a decline for the gate's purposes, recorded
        return None, {'route': 'error', 'reason': repr(e)[:200]}


def baseline(items, profiles, base_rules, refresh=False):
    """{profile: {item key: {'declined': bool, 'op': op}}} with the accepted rules; cached on code+rules+corpus+profile."""
    key = _sha(code_identity(), *sorted(compiler_rules.digest(r) for r in base_rules),
               *[i['kind'] + i['id'] + i['question'] + i['as_of'] for i in items],
               *[f"{n}:{p.get('source_digest')}" for n, (p, _) in sorted(profiles.items())])
    path = CACHE / f'baseline-{key[:20]}.json'
    if path.exists() and not refresh:
        return json.loads(path.read_text())
    out = {}
    with compiler_rules.using(base_rules):
        for name, (profile, _) in profiles.items():
            rows = {}
            for it in items:
                g, info = _compile(it['question'], profile, it['as_of'])
                op = g['nodes'][-1]['operation'] if g and g.get('nodes') else None
                rows[it['kind'] + ':' + it['id']] = {'declined': g is None, 'op': op, 'rule': (info.get('compiler_rule') or {}).get('id')}
            out[name] = rows
    CACHE.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(out))
    return out


# ---------------------------------------------------------------- execution and references
def _authorizer(action, *_):
    ok = {sqlite3.SQLITE_SELECT, sqlite3.SQLITE_READ, sqlite3.SQLITE_FUNCTION, getattr(sqlite3, 'SQLITE_RECURSIVE', -1)}
    return sqlite3.SQLITE_OK if action in ok else sqlite3.SQLITE_DENY


def run_sql(stack, params):
    """Execute a plan's SQL read-only on the stack's semantic layer (never builds or writes it)."""
    target = Path(stack) / 'semantic.sqlite3'
    db = sqlite3.connect(f'file:{target}?mode=ro', uri=True)
    deadline = time.perf_counter() + SQL_SECONDS
    db.set_progress_handler(lambda: 1 if time.perf_counter() > deadline else 0, 10000)
    db.set_authorizer(_authorizer)
    try:
        cur = db.execute(params['query'])
        cols = [c[0] for c in cur.description or []]
        rows = cur.fetchmany(5001)
    finally:
        db.close()
    kind = params['returns']
    if kind in ('count', 'value'):
        if not rows: raise ValueError('scalar query returned no rows')
        value = rows[0][0]
        if kind == 'count' and not isinstance(value, int): raise ValueError('count query must return an integer')
        return {'answer_type': kind, kind: value}
    if kind == 'patient_set':
        ids = sorted({r[0] for r in rows if r and r[0] is not None})
        return {'answer_type': kind, 'patient_ids': ids, 'count': len(ids)}
    return {'answer_type': 'table', 'table': [dict(zip(cols, r)) for r in rows]}


_REFERENCES = {}


def reference(item):
    """Bench reference answer from the read-only active warehouse (bench/common.py), cached per question."""
    if item['id'] not in _REFERENCES:
        from bench.common import Warehouse, reference_answer
        wh = Warehouse(as_of=item['as_of'], timeout_s=240)  # some references scan the whole warehouse
        try:
            _REFERENCES[item['id']] = reference_answer(item, wh)[0]
        finally:
            wh.close()
    return _REFERENCES[item['id']]


def reproduces(item, actual):
    """bench/score.py semantics: same answer type and value (tables: same rows by value, column names aside)."""
    expected = reference(item)
    kind = item['answer_type']
    if kind == 'unsupported' or actual['answer_type'] != kind: return False
    tol = item.get('tolerance', 0)
    if kind == 'patient_set': return set(actual['patient_ids']) == set(expected['patient_ids'])
    if kind in ('count', 'value'):
        a, b = actual[kind], expected[kind]
        return isinstance(a, (int, float)) and isinstance(b, (int, float)) and abs(a - b) <= tol
    norm = lambda rows: sorted(tuple(sorted(map(str, r.values()))) for r in rows)
    return norm(actual['table']) == norm(expected['table'])


# ---------------------------------------------------------------- the gate
def schema_errors(rule):
    errs = []
    for k in ('id', 'version', 'engine', 'pattern', 'slots', 'plan', 'positives', 'negatives'):
        if k not in rule: errs.append(f'missing {k}')
    if errs: return errs
    if not re.fullmatch(r'[a-z][a-z0-9_.-]{2,80}', rule['id']): errs.append('id must be a lowercase dotted name')
    if type(rule['version']) is not int or rule['version'] < 1: errs.append('version must be a positive integer')
    eng, plan = rule['engine'], rule['plan']
    if eng == 'literal_lookup':
        if plan.get('kind') != 'sql': errs.append('literal_lookup rules produce sql plans')
    elif eng in PRODUCT:
        if plan.get('kind') != 'operator' or plan.get('operation') != eng: errs.append(f'{eng} rules produce an operator node for {eng}')
    else:
        errs.append(f'engine must be literal_lookup or one of {sorted(PRODUCT)}')
    if not rule['positives'] or not rule['negatives']: errs.append('at least one positive and one negative example')
    used = {m.group(1) for m in compiler_rules.SLOT.finditer(rule['pattern'])}
    if used - set(rule['slots']): errs.append(f'undeclared slots {sorted(used - set(rule["slots"]))}')
    if set(rule['slots']) - used: errs.append(f'unused slots {sorted(set(rule["slots"]) - used)}')
    return errs


def evaluate(rule, items, profiles, base, base_rules, elements):
    """-> receipt dict (status accepted / rejected with reasons)."""
    import question_coverage as qc
    reasons, detail = schema_errors(rule), {}
    if reasons:
        return _receipt(rule, 'rejected', reasons, detail, profiles)
    rule = {**rule, '_digest': compiler_rules.digest(rule)}
    rules = [r for r in base_rules if r['id'] != rule['id']] + [rule]
    applicable = 0
    for name, (profile, stack) in profiles.items():
        d = detail[name] = {'positives': [], 'negatives': [], 'captured': []}
        if not compiler_rules._requires_met(rule, profile):
            d['skipped'] = 'requires not met on this profile'
            continue
        applicable += 1
        # (a) own examples
        for pos in rule['positives']:
            g, why = compiler_rules.match(rule, pos['question'], profile, LIBRARY_AS_OF)
            exp = pos.get('expect') or {}
            node = g['nodes'][-1] if g else None
            ok = g is not None and (not exp.get('operation') or node['operation'] == exp['operation']) and \
                (not exp.get('returns') or node['parameters'].get('returns') == exp['returns']) and \
                all(node['parameters'].get(k) == v for k, v in (exp.get('parameters') or {}).items())
            if ok and node['operation'] == 'sql':
                try: run_sql(stack, node['parameters'])
                except Exception as e: ok = False; why = {'reason': f'SQL failed: {e}'}
            d['positives'].append({'question': pos['question'], 'ok': ok, **({} if ok else {'why': (why or {}).get('reason')})})
            if not ok: reasons.append(f"{name}: positive not matched as expected: {pos['question']} ({(why or {}).get('reason')})")
        for neg in rule['negatives']:
            g, _ = compiler_rules.match(rule, neg, profile, LIBRARY_AS_OF)
            d['negatives'].append({'question': neg, 'ok': g is None})
            if g is not None: reasons.append(f'{name}: negative matched: {neg}')
        # (b)/(c) corpus: only questions every earlier route declined can reach the rule
        with compiler_rules.using(rules):
            for it in items:
                key = it['kind'] + ':' + it['id']
                if not base[name][key]['declined']: continue
                low = compiler_rules.normalize(it['question'], it['as_of'])
                if not compiler_rules._compile_pattern(rule, profile).search(low): continue  # cheap pre-filter
                g, info = _compile(it['question'], profile, it['as_of'])
                if g is None or (info.get('compiler_rule') or {}).get('id') != rule['id']: continue
                node = g['nodes'][-1]
                cap = {'kind': it['kind'], 'id': it['id'], 'operation': node['operation']}
                problem = None
                if node['operation'] == 'sql':
                    try:
                        actual = run_sql(stack, node['parameters'])
                    except Exception as e:
                        problem = f'SQL failed: {e}'
                if not problem and it['kind'] == 'library':
                    row = qc.classify(it['item'], elements, profile, lambda q, p, a: (g, info), it['as_of'], PRODUCT)
                    cap['bucket'] = row['bucket']; cap['expected'] = row['engine']
                    if row['bucket'] != 'answered locally':
                        problem = f"captures library {it['id']} ({row['bucket']}; labelled {row['engine']})"
                elif not problem and it['kind'] == 'acceptance':
                    exp = it['item']['expect_operation']
                    cap['expected'] = exp
                    if node['operation'] != exp or (it['item'].get('expect_measure') and node['parameters'].get('measure') != it['item']['expect_measure']):
                        problem = f"captures acceptance case {it['id']} expecting {exp}"
                elif not problem and it['kind'] == 'bench':
                    if node['operation'] != 'sql':
                        problem = f"captures bench {it['id']} with a non-SQL plan"
                    elif name != REFERENCE_PROFILE:
                        ref_ok = any(c['id'] == it['id'] and c.get('reproduces') for c in detail.get(REFERENCE_PROFILE, {}).get('captured', []))
                        if not ref_ok: problem = f"captures bench {it['id']} on {name} without a reproduced reference on {REFERENCE_PROFILE}"
                    else:
                        try:
                            ok = reproduces(it['item'], actual)
                        except Exception as e:
                            ok, cap['reference_error'] = False, str(e)[:200]
                        cap['reproduces'] = ok
                        if not ok: problem = f"captures bench {it['id']} but does not reproduce its reference answer"
                if problem:
                    cap['problem'] = problem; reasons.append(f'{name}: {problem}')
                d['captured'].append(cap)
    if not applicable:
        reasons.append('the rule applies to neither profile (requires not met)')
    return _receipt(rule, 'rejected' if reasons else 'accepted', reasons, detail, profiles)


def _receipt(rule, status, reasons, detail, profiles):
    captured = {n: Counter(c['kind'] for c in d.get('captured', []) if not c.get('problem')) for n, d in detail.items()}
    return {'schema': VERSION, 'rule_id': rule['id'], 'version': rule.get('version'), 'rule_digest': compiler_rules.digest({k: v for k, v in rule.items() if not k.startswith('_')}),
            'engine': rule.get('engine'), 'status': status, 'reasons': reasons[:50],
            'profiles': {n: {'source_digest': p.get('source_digest'), 'stack': str(s)} for n, (p, s) in profiles.items()},
            'captured_counts': {n: dict(c) for n, c in captured.items()}, 'detail': detail,
            'code_identity': code_identity(), 'interpreter': compiler_rules.VERSION,
            'created': time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime())}


def next_sequence(directory):
    seqs = []
    for p in Path(directory).glob('*.json'):
        try: seqs.append(json.loads(p.read_text()).get('sequence') or 0)
        except ValueError: pass
    return max(seqs, default=0) + 1


def gate(candidates, *, write=False, receipts_dir=None, profiles=None, base_rules=None, refresh=False, log=print, items=None, elements=None):
    """Evaluate candidates in order; each accepted rule joins the base for the next. -> [receipt]."""
    receipts_dir = Path(receipts_dir or compiler_rules.RECEIPTS_DIR)
    profiles = profiles or load_profiles()
    if items is None: items, elements = corpus()  # tests pass a small corpus
    elif elements is None: elements = corpus()[1]
    ids = {c['id'] for c in candidates}
    base_rules = [r for r in (compiler_rules.load() if base_rules is None else base_rules) if r['id'] not in ids]
    base = baseline(items, profiles, base_rules, refresh=refresh)
    out = []
    for rule in candidates:
        receipt = evaluate(rule, items, profiles, base, base_rules, elements)
        out.append(receipt)
        caps = receipt['captured_counts']
        log(f"{receipt['status']:8s} {rule['id']}@{rule.get('version')} captured {caps}" + ('' if receipt['status'] == 'accepted' else
            '\n          ' + '\n          '.join(receipt['reasons'][:6])))
        if receipt['status'] == 'accepted':
            clean = {k: v for k, v in rule.items() if not k.startswith('_')}
            base_rules = base_rules + [clean]
            for name, d in receipt['detail'].items():  # captured questions are no longer declined for later candidates
                for c in d.get('captured', []):
                    base[name][c['kind'] + ':' + c['id']]['declined'] = False
            if write:
                receipt['sequence'] = next_sequence(receipts_dir)
                receipts_dir.mkdir(parents=True, exist_ok=True)
                compiler_rules.receipt_path(clean, receipts_dir).write_text(json.dumps(receipt, indent=1, default=str) + '\n')
        if not write or receipt['status'] != 'accepted':
            scratch = CACHE / 'receipts'
            scratch.mkdir(parents=True, exist_ok=True)
            compiler_rules.receipt_path({'id': rule['id'], 'version': rule.get('version')}, scratch).write_text(json.dumps(receipt, indent=1, default=str) + '\n')
    return out


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument('files', nargs='+', help='rule files (config/agere/compiler_rules/*.json or drafts)')
    ap.add_argument('--rule', action='append', default=[], help='only these rule ids')
    ap.add_argument('--write', action='store_true', help='write receipts for accepted rules (makes them live)')
    ap.add_argument('--refresh', action='store_true', help='recompute the cached baseline')
    a = ap.parse_args(argv)
    candidates = []
    for f in a.files:
        for r in json.loads(Path(f).read_text()).get('rules', []):
            if not a.rule or r['id'] in a.rule:
                if a.write and compiler_rules.accepted(r):
                    print(f"unchanged {r['id']}@{r['version']} (accepted receipt matches)"); continue
                candidates.append(r)
    receipts = gate(candidates, write=a.write, refresh=a.refresh)
    print(json.dumps(Counter(r['status'] for r in receipts)))
    return 0 if all(r['status'] == 'accepted' for r in receipts) else 1


if __name__ == '__main__':
    sys.exit(main())
