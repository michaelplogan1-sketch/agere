"""Automatic learning loop (search-engine query-log learning) over one stack's learning store.

Inputs are read-only: <stack>/learning.sqlite3 (outcomes, verified pairs, corrections), optionally answers.sqlite3 and
conversations.sqlite3 (what was shown for a flagged question) and <stack>/provider-receipts (router decisions and spend).
Outputs go to an output directory (default artifacts/learning-loop/<stack name>/), never into the stack:

  gap-report.json          declined/escalated/corrected questions clustered by decline reason + TF-IDF text similarity
  regressions/<id>.json    one regression case per user correction (also written to bench/regressions/ with --cases-dir)
  grammar-proposals.json   compiler grammar additions for recurring decline clusters the router/hosted ladder answered
                           consistently; proposals for human review, local_compiler.py is never edited
  routing-dataset.jsonl    executed, uncorrected routing decisions (labelled data for a learned router)
  bottling-promoted.jsonl  new verified (question -> SQL) pairs in tools/bottling/harvest.py format
  retrain.json             whether the bottled model should be retrained now
  loop.json                summary served read-only at /api/stack/learning-loop

Benchmark questions (bench/questions, bench/acceptance, smoke probes) never enter any dataset: exact or near
(token Jaccard >= 0.5) matches are excluded, the same rule tools/bottling/generate_questions.py uses.
"""
import hashlib, json, math, re, sqlite3, time
from collections import Counter, defaultdict
from pathlib import Path

VERSION = 'learning-loop/1'
ROOT = Path(__file__).resolve().parents[2]
INFRA = re.compile(r'credit balance|HTTP [45]\d\d|timed? ?out|Timeout|Connection|rate limit|overloaded|Missing [A-Z_]+_KEY', re.I)
GAP_STATUSES = ('declined', 'unsupported', 'grounding_failed', 'partial')
STOP = set('a an and are as at be by did do does for from had has have how i in is it its many me much of on or our over per '
           'show tell that the their them there these this those to us was we were what when where which who why with would '
           'you your can could should any all each give list number please than then'.split())


# ---- text helpers ----------------------------------------------------------------------------------------------
def norm(q):
    return ' '.join(str(q).lower().split()).strip(' ?.!')


def tokens(q):
    return set(re.findall(r'[a-z0-9]+', str(q).lower()))


def terms(q):
    return [t for t in re.findall(r"[a-z][a-z0-9'-]*|\d+", str(q).lower()) if t not in STOP and len(t) > 1]


def stable_id(*parts):
    return hashlib.sha256('\x1f'.join(str(p) for p in parts).encode()).hexdigest()[:12]


# ---- benchmark exclusion ---------------------------------------------------------------------------------------
def benchmark_questions(root=ROOT):
    out = []
    for sub in ('bench/questions', 'bench/acceptance'):
        for f in sorted((Path(root) / sub).glob('*.json')):
            try:
                data = json.loads(f.read_text())
            except ValueError:
                continue
            out += [q['question'] for q in data if isinstance(q, dict) and isinstance(q.get('question'), str)]
    smoke = Path(root) / 'bench/smoke.py'
    if smoke.exists():  # PROBES literals without importing the module
        out += re.findall(r"^\s*\('([^']+)',\s*'[a-z_]+'", smoke.read_text(), re.M)
    return out


class BenchFilter:
    def __init__(self, questions, jaccard=0.5):
        self.exact = {norm(q) for q in questions}
        self.sets = [tokens(q) for q in questions]
        self.jaccard = jaccard
        self._cache = {}

    def __call__(self, q):
        key = norm(q)
        if key not in self._cache:
            if key in self.exact: hit = True
            else:
                t = tokens(q)
                hit = any(len(t & b) / max(len(t | b), 1) >= self.jaccard for b in self.sets)
            self._cache[key] = hit
        return self._cache[key]


# ---- reading the store (read-only) ----------------------------------------------------------------------------
def _ro(path):
    return sqlite3.connect(f'file:{Path(path)}?mode=ro', uri=True)


def read_store(stack):
    """Rows of the learning store as dicts; the file is opened read-only and never migrated."""
    db = _ro(Path(stack) / 'learning.sqlite3')
    try:
        def rows(table):
            if not db.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (table,)).fetchone(): return []
            cur = db.execute(f'SELECT * FROM {table}')
            cols = [c[0] for c in cur.description]
            return [dict(zip(cols, r)) for r in cur]
        return {'outcomes': rows('outcomes'), 'verified': rows('verified'), 'corrections': rows('corrections')}
    finally:
        db.close()


def observed_answers(stack):
    """question(normalised) -> list of what the service returned (answers.sqlite3 + conversation summaries)."""
    out = defaultdict(list)
    answers = Path(stack) / 'answers.sqlite3'
    if answers.exists():
        db = _ro(answers)
        try:
            for at, q, route, status, atype, calls, usd, err in db.execute(
                    'SELECT at, question, route, status, answer_type, api_calls, api_usd, error FROM answers'):
                out[norm(q)].append({'at': at, 'route': route, 'status': status, 'answer_type': atype, 'api_calls': calls,
                                     'api_usd': usd, 'error': err or None})
        except sqlite3.Error:
            pass
        finally:
            db.close()
    conv = Path(stack) / 'conversations.sqlite3'
    if conv.exists():
        db = _ro(conv)
        try:
            for created, q, rq, plan, answer, err in db.execute(
                    'SELECT created, question, resolved_question, plan_summary, answer_summary, error FROM turns'):
                a = json.loads(answer) if answer else {}
                p = json.loads(plan) if plan else {}
                for key in {norm(q), norm(rq)}:
                    out[key].append({'at': created, 'route': p.get('route'), 'status': a.get('status'),
                                     'answer_type': a.get('answer_type'), 'headline': a.get('headline'), 'error': err or None})
        except sqlite3.Error:
            pass
        finally:
            db.close()
    for v in out.values(): v.sort(key=lambda r: r.get('at') or 0)
    return out


# ---- 1. gap clustering ----------------------------------------------------------------------------------------
def reason_family(reason):
    """(family, salient words) for a decline reason string."""
    r = (reason or '').strip()
    if not r: return 'no_reason', []
    if INFRA.search(r): return 'infrastructure', []
    m = re.match(r'(outreach )?words not interpreted locally:\s*(.*)', r)
    if m:
        return ('outreach_uninterpreted_words' if m.group(1) else 'uninterpreted_words'), [w.strip() for w in m.group(2).split(',') if w.strip()]
    if '"or" joins' in r: return 'or_semantics', []
    if 'cannot be both' in r: return 'cross_record_sequence', re.findall(r'both (\w+) and (\w+)', r)[0] if re.search(r'both (\w+) and (\w+)', r) else []
    if 'analytic intent' in r: return 'analytic_intent', []
    m = re.match(r"unrecognised (\w+) label \[(.*)\]", r)
    if m: return f'unrecognised_{m.group(1)}_label', re.findall(r"'([^']+)'", m.group(2))
    if 'without a measurement' in r: return 'statistic_without_measure', []
    if 'no population or statistic' in r: return 'no_population_or_statistic', []
    return 'other:' + ' '.join(re.sub(r"[\d'\"\[\]()]", ' ', r.lower()).split()[:6]), []


def gap_questions(store):
    """Per distinct question: every outcome that shows a gap (compiler declined, unsupported, declined, corrected)."""
    corrected = {norm(c['question']) for c in store['corrections']}
    groups = {}
    for o in store['outcomes']:
        q = o['question']; key = norm(q)
        gap = bool(o.get('reason')) or o.get('status') in GAP_STATUSES or o.get('route') == 'declined' or key in corrected
        if not gap: continue
        g = groups.setdefault(key, {'question': q, 'occurrences': 0, 'rungs': Counter(), 'statuses': Counter(), 'reasons': Counter(),
                                    'usd': 0.0, 'paid_calls': 0, 'corrected': key in corrected, 'last': 0})
        g['occurrences'] += 1
        g['rungs'][o.get('route') or 'unknown'] += 1
        g['statuses'][o.get('status') or 'unknown'] += 1
        if o.get('reason'): g['reasons'][o['reason']] += 1
        g['usd'] += float(o.get('usd') or 0); g['paid_calls'] += int(o.get('paid_calls') or 0)
        g['last'] = max(g['last'], o.get('created') or 0)
    for key in corrected - set(groups):  # a correction on a question with no logged outcome still counts
        c = next(c for c in store['corrections'] if norm(c['question']) == key)
        groups[key] = {'question': c['question'], 'occurrences': 1, 'rungs': Counter({'unknown': 1}), 'statuses': Counter({'corrected': 1}),
                       'reasons': Counter(), 'usd': 0.0, 'paid_calls': 0, 'corrected': True, 'last': c.get('created') or 0}
    for g in groups.values():
        reason = g['reasons'].most_common(1)[0][0] if g['reasons'] else ''
        g['family'], g['salient'] = reason_family(reason)
        if g['family'] == 'no_reason' and g['corrected']: g['family'] = 'user_corrected'
        elif g['family'] == 'no_reason': g['family'] = 'unsupported_or_declined'
    return groups


class TfIdf:
    def __init__(self, docs):
        df = Counter(t for d in docs for t in set(d))
        self.idf = {t: math.log((1 + len(docs)) / (1 + n)) + 1 for t, n in df.items()}

    def vector(self, doc):
        tf = Counter(doc)
        v = {t: c * self.idf.get(t, 1.0) for t, c in tf.items()}
        n = math.sqrt(sum(x * x for x in v.values())) or 1.0
        return {t: x / n for t, x in v.items()}


def _cos(a, b):
    if len(a) > len(b): a, b = b, a
    return sum(x * b.get(t, 0.0) for t, x in a.items())


def cluster(groups, threshold=0.3):
    """Leader clustering inside each reason family over TF-IDF vectors of the question plus salient decline words."""
    items = sorted(groups.values(), key=lambda g: (-g['occurrences'], g['question']))
    docs = [terms(g['question']) + [w.lower() for w in g['salient'] for _ in range(2)] for g in items]
    model = TfIdf(docs)
    clusters = []
    for g, doc in zip(items, docs):
        v = model.vector(doc)
        best, score = None, threshold
        for c in clusters:
            if c['family'] != g['family']: continue
            if g['family'] == 'infrastructure': best = c; break  # provider/transport failures are one bucket, not a grammar gap
            s = _cos(v, c['centroid'])
            if s >= score: best, score = c, s
        if best is None:
            best = {'family': g['family'], 'members': [], 'centroid': {}, 'sum': Counter()}
            clusters.append(best)
        best['members'].append(g)
        best['sum'].update(v)
        n = math.sqrt(sum(x * x for x in best['sum'].values())) or 1.0
        best['centroid'] = {t: x / n for t, x in best['sum'].items()}
    out = []
    for c in clusters:
        m = c['members']
        rungs, statuses, reasons, salient = Counter(), Counter(), Counter(), Counter()
        for g in m:
            rungs.update(g['rungs']); statuses.update(g['statuses']); reasons.update(g['reasons']); salient.update(w.lower() for w in g['salient'])
        top_terms = [t for t, _ in sorted(c['centroid'].items(), key=lambda kv: -kv[1])[:6]]
        out.append({'id': stable_id(c['family'], *sorted(norm(g['question']) for g in m)[:20]), 'family': c['family'],
                    'label': ', '.join(top_terms), 'actionable': c['family'] != 'infrastructure',
                    'distinct_questions': len(m), 'occurrences': sum(g['occurrences'] for g in m),
                    'corrected_questions': sum(g['corrected'] for g in m),
                    'caught_by': dict(rungs.most_common()), 'statuses': dict(statuses.most_common()),
                    'usd': round(sum(g['usd'] for g in m), 4), 'paid_calls': sum(g['paid_calls'] for g in m),
                    'top_reasons': [{'reason': r[:200], 'count': n} for r, n in reasons.most_common(3)],
                    'salient_terms': [w for w, _ in salient.most_common(8)],
                    'examples': [g['question'] for g in m[:5]],
                    'questions': [g['question'] for g in m]})
    out.sort(key=lambda c: (-c['actionable'], -c['occurrences'], -c['usd'], c['id']))
    return out


# ---- 2. regression cases --------------------------------------------------------------------------------------
def expectation(expected, note=''):
    """Normalise a correction's expected payload into replayable checks."""
    checks = {}
    if isinstance(expected, dict):
        for k in ('value', 'count'):
            if isinstance(expected.get(k), (int, float)) and not isinstance(expected.get(k), bool): checks['value'] = float(expected[k])
        for k, dest in (('route', 'route_prefix'), ('operation', 'operation'), ('status', 'status'), ('answer_type', 'answer_type'),
                        ('headline_contains', 'headline_contains'), ('not_route', 'not_route'), ('tolerance', 'tolerance')):
            if expected.get(k) is not None: checks[dest] = expected[k]
    elif isinstance(expected, (int, float)) and not isinstance(expected, bool):
        checks['value'] = float(expected)
    elif isinstance(expected, str) and expected.strip():
        checks['headline_contains'] = expected.strip()
    if not checks:
        low = (note or '').lower()
        if re.search(r'\b(not answerable|should decline|unsupported|no such data)\b', low): checks['status'] = 'unsupported'
    if not checks: checks['differs_from_observed'] = True  # weak: the flagged answer must not come back unchanged
    return checks


def regression_cases(store, observed):
    cases = []
    for c in store['corrections']:
        try:
            expected = json.loads(c['expected']) if isinstance(c.get('expected'), str) else c.get('expected')
        except ValueError:
            expected = c.get('expected')
        seen = [o for o in observed.get(norm(c['question']), []) if (o.get('at') or 0) <= (c.get('created') or float('inf'))]
        obs = dict(seen[-1]) if seen else {}
        if seen and not obs.get('headline'):  # conversation turns carry the headline; answers rows carry route/status
            obs['headline'] = next((o.get('headline') for o in reversed(seen) if o.get('headline')), None)
        checks = expectation(expected, c.get('note'))
        cases.append({'schema': 'agere-regression/1', 'id': 'R' + stable_id(norm(c['question']), c.get('created')),
                      'question': c['question'], 'note': c.get('note'), 'expected': expected, 'checks': checks,
                      'weak': set(checks) == {'differs_from_observed'}, 'observed': obs or None,
                      'corrected_at': c.get('created'), 'source': 'learning-store corrections (/api/stack/correct)'})
    return cases


def write_cases(cases, directory):
    directory = Path(directory); directory.mkdir(parents=True, exist_ok=True)
    written = []
    for case in cases:
        target = directory / f"{case['id']}.json"
        if target.exists(): continue  # permanent: an existing case is never rewritten
        target.write_text(json.dumps(case, indent=1, sort_keys=True, default=str))
        written.append(case['id'])
    return written


# ---- router decisions from provider receipts -------------------------------------------------------------------
def _digest(body):
    from clinical_core.contracts import digest
    return digest({'contract': 'context-student/1', 'provider': 'claude', 'body': body})


def recover_router_decisions(questions, as_of, receipts, *, router=None, hasher=None):
    """Router choices (operator + filled parameters) for questions, by recomputing the request hash the router would
    have sent and reading the saved receipt. Requests sent under an older menu/schema do not match and stay unknown."""
    receipts = Path(receipts)
    if not receipts.exists() or not questions: return {}, {'attempted': len(questions), 'recovered': 0, 'status': 'no_receipts'}
    try:
        if router is None:
            from clinical_core.agere.clinical import operator_router as router
        hasher = hasher or _digest
        menu = router.menu()
    except Exception as exc:  # noqa: BLE001 - recovery is best effort
        return {}, {'attempted': len(questions), 'recovered': 0, 'status': f'unavailable: {type(exc).__name__}'}
    system = router.SYSTEM.format(as_of=as_of)
    tool_cache, out = {}, {}

    def lookup(body):
        f = receipts / (hasher(body) + '.json')
        if not f.exists(): return None, None
        try:
            d = json.loads(f.read_text())
        except ValueError:
            return None, None
        calls = [b for b in (d.get('response') or {}).get('content', []) if b.get('type') == 'tool_use']
        return (calls[0] if len(calls) == 1 else None), f.stem

    for q in questions:
        body = {'model': router.MODEL, 'max_tokens': 1200, 'system': system, 'tools': menu,
                'tool_choice': {'type': 'any', 'disable_parallel_tool_use': True}, 'messages': [{'role': 'user', 'content': q}]}
        picked, key = lookup(body)
        if not picked: continue
        rec = {'decision': picked['name'], 'choose_receipt': key, 'parameters': None, 'population': None}
        if picked['name'] not in ('literal_lookup', 'not_answerable'):
            try:
                if picked['name'] not in tool_cache: tool_cache[picked['name']] = router.tool(picked['name'])
                fill = {'model': router.MODEL, 'max_tokens': 1200, 'system': system, 'tools': [tool_cache[picked['name']]],
                        'tool_choice': {'type': 'tool', 'name': picked['name'], 'disable_parallel_tool_use': True},
                        'messages': [{'role': 'user', 'content': q}]}
                filled, fkey = lookup(fill)
                if filled:
                    rec.update(parameters=(filled.get('input') or {}).get('parameters'), population=(filled.get('input') or {}).get('population'),
                               fill_receipt=fkey)
            except Exception:  # noqa: BLE001
                pass
        elif picked['name'] == 'not_answerable':
            rec['missing'] = (picked.get('input') or {}).get('missing')
        out[norm(q)] = rec
    return out, {'attempted': len(questions), 'recovered': len(out), 'with_parameters': sum(1 for r in out.values() if r.get('parameters') is not None),
                 'status': 'ok', 'method': 'request-hash reconstruction against provider receipts (current router menu/schemas)'}


def receipt_spend(receipts):
    receipts = Path(receipts)
    if not receipts.exists(): return None
    paid = failures = 0; usd = 0.0; models = Counter()
    for f in receipts.glob('*.json'):
        if f.stem.endswith('-failure'): failures += 1; continue
        if f.stem.endswith('-revalidated'): continue
        try:
            r = json.loads(f.read_text()).get('receipt')
        except ValueError:
            continue
        if not r: continue
        paid += 1; usd += ((r.get('cost_estimate') or {}).get('estimated_usd') or 0); models[r.get('model')] += 1
    return {'paid_receipts': paid, 'failures': failures, 'estimated_usd': round(usd, 4), 'by_model': dict(models.most_common())}


# ---- 3. grammar proposals -------------------------------------------------------------------------------------
def sql_signature(sql):
    sql = sql or ''
    streams = sorted(set(re.findall(r"stream\s*=\s*'([a-z_]+)'", sql)) | {s for grp in re.findall(r"stream\s+IN\s*\(([^)]*)\)", sql, re.I)
                                                                       for s in re.findall(r"'([a-z_]+)'", grp)})
    ctes = {t.lower() for t in re.findall(r'(?:\bWITH|,)\s*([A-Za-z_][A-Za-z0-9_]*)\s+AS\s*\(', sql, re.I)}
    tables = sorted(set(t.lower() for t in re.findall(r'\b(?:FROM|JOIN)\s+([A-Za-z_][A-Za-z0-9_]*)', sql, re.I)) - {'select', 'lateral'} - ctes)
    return {'tables': tables, 'streams': streams}


def latest_verified(store, corrected):
    out = {}
    for v in sorted(store['verified'], key=lambda v: v.get('created') or 0):
        key = norm(v['question'])
        if key in corrected: continue
        try:
            plan = json.loads(v['plan'])
        except (TypeError, ValueError):
            continue
        out[key] = {**v, 'plan': plan}
    return out


def proposals(clusters, verified, router, *, min_support=2, consistency=0.8, groups=None, is_bench=lambda q: False):
    """Benchmark questions never support a proposal (tuning the compiler on them would leak the benchmark)."""
    out = []
    groups_salient = {k: g['salient'] for k, g in (groups or {}).items()}
    for c in clusters:
        if not c['actionable'] or c['family'] in ('user_corrected',): continue
        resolved = []
        for q in c['questions']:
            if is_bench(q): continue
            r = router.get(norm(q))
            if r and r['decision'] not in ('not_answerable',):
                params = r.get('parameters') if isinstance(r.get('parameters'), dict) else {}
                resolved.append(('operator', r['decision'], q, {'operator': r['decision'], 'parameters': params, 'population': r.get('population')}))
                continue
            v = verified.get(norm(q))
            if v and v.get('plan_kind') == 'sql':
                sig = sql_signature(v['plan'].get('query'))
                key = 'sql:' + ','.join(sig['tables']) + '|' + ','.join(sig['streams'])
                resolved.append(('sql', key, q, {'query': v['plan'].get('query'), 'returns': v['plan'].get('returns'), 'agreement': v.get('agreement'),
                                                 'models': json.loads(v['models']) if isinstance(v.get('models'), str) else v.get('models'), **sig}))
        if not resolved: continue
        by_target = defaultdict(list)
        for r in resolved: by_target[r[1]].append(r)
        for top, support in sorted(by_target.items(), key=lambda kv: -len(kv[1])):
            n = len({norm(r[2]) for r in support})
            share = len(support) / len(resolved)
            # One consistent resolution for most of the cluster, or a recurring sub-pattern answered the same way 3+ times.
            if n < min_support or (share < consistency and n < 3): continue
            kind = support[0][0]
            if kind == 'operator':
                shared = None
                for _, _, _, e in support:
                    flat = {k: json.dumps(v, sort_keys=True) for k, v in (e['parameters'] or {}).items()}
                    shared = flat if shared is None else {k: v for k, v in shared.items() if flat.get(k) == v}
                target = {'kind': 'operator', 'operator': top, 'shared_parameters': {k: json.loads(v) for k, v in (shared or {}).items()},
                          'parameters_recovered': sum(1 for *_, e in support if e['parameters'])}
            else:
                e = support[0][3]
                target = {'kind': 'verified_sql_template', 'tables': e['tables'], 'streams': e['streams'],
                          'returns': sorted({str(x[3]['returns']) for x in support}), 'example_sql': e['query']}
            qs = [r[2] for r in support]
            decline = Counter(w.lower() for q in qs for w in (groups_salient.get(norm(q)) or []))
            common = Counter(t for q in qs for t in set(terms(q)))
            pattern_terms = [w for w, k in decline.most_common() if k >= 2] or [w for w, _ in decline.most_common(4)]
            question_terms = [t for t, k in common.most_common(8) if k >= max(2, len(qs) // 2)]
            out.append({'id': 'P' + stable_id(c['id'], top), 'cluster': c['id'], 'status': 'proposed_for_review', 'auto_applied': False,
                        'reason_family': c['family'],
                        'pattern': {'decline_terms': pattern_terms, 'question_terms': question_terms,
                                    'regex_hint': (r'\b(?:' + '|'.join(re.escape(t) for t in pattern_terms[:6]) + r')\b') if pattern_terms else None},
                        'target': target,
                        'support': {'questions': n, 'resolved_in_cluster': len(resolved), 'cluster_questions': c['distinct_questions'],
                                    'consistency': round(share, 3), 'cluster_occurrences': c['occurrences'], 'cluster_usd_spent': c['usd']},
                        'examples': qs[:8],
                        'verified_outcomes': [{'question': r[2], **({'operator': r[3]['operator'], 'parameters': r[3]['parameters']} if kind == 'operator'
                                                                  else {'agreement': r[3]['agreement'], 'models': r[3]['models'], 'returns': r[3]['returns']})}
                                              for r in support[:8]],
                        'review_note': 'Proposal only. A reviewer adds the phrasing to clinical_core/agere/clinical/local_compiler.py '
                                       'if the target is the right local plan; nothing is edited automatically.'})
    return out


# ---- 4. datasets ----------------------------------------------------------------------------------------------
def routing_rows(store, router, verified, corrected, is_bench):
    latest = {}
    for o in sorted(store['outcomes'], key=lambda o: o.get('created') or 0):
        latest[norm(o['question'])] = o
    rows, skipped = {}, Counter()
    for key, o in latest.items():
        if key in corrected: skipped['corrected'] += 1; continue
        if is_bench(o['question']): skipped['benchmark'] += 1; continue
        if o.get('route') == 'operator_router' and o.get('status') not in GAP_STATUSES + ('declined',):
            r = router.get(key)
            if not r: skipped['router_decision_unrecovered'] += 1; continue
            rows[key] = {'question': o['question'], 'label': r['decision'], 'parameters': r.get('parameters'),
                         'population': r.get('population'), 'source': 'operator_router_executed',
                         'receipt': r.get('fill_receipt') or r.get('choose_receipt')}
        elif key in verified and o.get('route') in ('hosted_verified_sql', 'learned_verified_sql'):
            rows[key] = {'question': o['question'], 'label': 'literal_lookup', 'parameters': None, 'population': None,
                         'source': 'verified_sql_executed', 'agreement': verified[key].get('agreement')}
    return list(rows.values()), dict(skipped)


def existing_bottling_questions(dirs):
    seen = set()
    for d in dirs:
        d = Path(d)
        if not d.exists(): continue
        for f in list(d.glob('answers-*.jsonl')) + list(d.glob('promoted-*.jsonl')):
            for line in f.read_text().splitlines():
                try:
                    r = json.loads(line)
                except ValueError:
                    continue
                if r.get('route') in ('hosted_verified_sql', 'local_compiler', 'learned_verified_sql') and r.get('query'):
                    seen.add(norm(r['question']))
    return seen


def trained_questions(dirs):
    seen = set()
    for d in dirs:
        for f in (Path(d) / 'data').glob('*.jsonl') if (Path(d) / 'data').exists() else []:
            for line in f.read_text().splitlines():
                try:
                    msgs = json.loads(line)['messages']
                except (ValueError, KeyError):
                    continue
                m = re.search(r'\nQ: (.*)$', next((x['content'] for x in msgs if x['role'] == 'user'), ''), re.S)
                if m: seen.add(norm(m.group(1)))
    return seen


def promote_pairs(verified, is_bench, existing):
    out, skipped = [], Counter()
    for key, v in sorted(verified.items()):
        sql = (v['plan'] or {}).get('query')
        if v.get('plan_kind') != 'sql' or not sql or not re.search(r'(?i)\bselect\b', sql): skipped['not_sql'] += 1; continue
        if is_bench(v['question']): skipped['benchmark'] += 1; continue
        if key in existing: skipped['already_in_bottling'] += 1; continue
        out.append({'question': v['question'], 'route': 'hosted_verified_sql', 'agreement': v.get('agreement'), 'query': sql,
                    'returns': v['plan'].get('returns'), 'status': 'answered', 'usd': 0, 'source': VERSION, 'context': v.get('context'),
                    'verified_at': v.get('created')})
    return out, dict(skipped)


# ---- 5. retrain decision ---------------------------------------------------------------------------------------
def retrain_decision(new_pairs, trained, available, *, threshold=100, ratio=0.25):
    reasons = []
    if new_pairs >= threshold: reasons.append(f'{new_pairs} new verified pairs >= threshold {threshold}')
    if trained and new_pairs / trained >= ratio: reasons.append(f'new pairs are {new_pairs / trained:.0%} of the {trained} trained pairs (>= {ratio:.0%})')
    recommend = bool(reasons) and new_pairs > 0
    return {'recommend_retrain': recommend, 'new_verified_pairs': new_pairs, 'trained_pairs': trained, 'available_bottling_pairs': available,
            'threshold_pairs': threshold, 'threshold_ratio': ratio,
            'reason': '; '.join(reasons) if recommend else f'{new_pairs} new verified pairs is below {threshold} and below {ratio:.0%} of {trained} trained',
            'next_steps': ['.venv/bin/python tools/bottling/prepare.py artifacts/bottling/data artifacts/bottling/answers-*.jsonl '
                           'artifacts/bottling/promoted-*.jsonl',
                           '.venv/bin/python -m mlx_lm lora -c tools/bottling/lora.yaml  (new adapter_path)',
                           're-run bench/smoke.py and bench/regressions.py before switching the bottled rung'] if recommend else []}


# ---- driver ---------------------------------------------------------------------------------------------------
def run(stack, out, *, receipts=None, cases_dir=None, bottling_dir=None, existing_bottling=(), name=None, as_of=None,
        router_override=None, root=ROOT, min_support=2, retrain_threshold=100, write_bottling=False):
    stack, out = Path(stack), Path(out)
    out.mkdir(parents=True, exist_ok=True)
    store = read_store(stack)
    is_bench = BenchFilter(benchmark_questions(root))
    corrected = {norm(c['question']) for c in store['corrections']}

    groups = gap_questions(store)
    clusters = cluster(groups)
    for c in clusters:  # benchmark questions show where gaps are but never feed proposals or datasets
        c['benchmark_questions'] = sum(1 for q in c['questions'] if is_bench(q))

    cases = regression_cases(store, observed_answers(stack))
    written = write_cases(cases, out / 'regressions')
    written_bench = write_cases(cases, cases_dir) if cases_dir else []

    receipts = Path(receipts) if receipts else stack / 'provider-receipts'
    if as_of is None and (stack / 'run.json').exists():
        as_of = json.loads((stack / 'run.json').read_text()).get('snapshot', {}).get('as_of')
    router_questions = sorted({o['question'] for o in store['outcomes'] if o.get('route') == 'operator_router' or o.get('reason')})
    if router_override is not None:
        router, recovery = router_override, {'attempted': len(router_questions), 'recovered': len(router_override), 'status': 'provided'}
    else:
        router, recovery = recover_router_decisions(router_questions, as_of, receipts)

    executed = {norm(o['question']) for o in store['outcomes'] if o.get('route') == 'operator_router'}
    recovery = {**recovery, 'router_executed_questions': len(executed), 'router_executed_recovered': len(executed & set(router))}
    verified = latest_verified(store, corrected)
    props = proposals(clusters, verified, router, min_support=min_support, groups=groups, is_bench=is_bench)

    routing, routing_skipped = routing_rows(store, router, verified, corrected, is_bench)
    prior = out / 'routing-dataset.jsonl'
    known = {norm(json.loads(l)['question']): json.loads(l) for l in prior.read_text().splitlines() if l.strip()} if prior.exists() else {}
    new_routing = [r for r in routing if norm(r['question']) not in known]
    for r in routing:
        if not is_bench(r['question']): known[norm(r['question'])] = r
    known = {k: v for k, v in known.items() if k not in corrected and not is_bench(v['question'])}
    prior.write_text(''.join(json.dumps(r, sort_keys=True) + '\n' for r in known.values()))

    bottling_dir = Path(bottling_dir) if bottling_dir else root / 'artifacts/bottling'
    existing_dirs = [Path(d) for d in (existing_bottling or [bottling_dir])]
    existing = existing_bottling_questions(existing_dirs)
    trained = trained_questions(existing_dirs)
    promoted, promo_skipped = promote_pairs(verified, is_bench, existing)
    (out / 'bottling-promoted.jsonl').write_text(''.join(json.dumps(p, sort_keys=True) + '\n' for p in promoted))
    if write_bottling and promoted:
        bottling_dir.mkdir(parents=True, exist_ok=True)
        (bottling_dir / f"promoted-{time.strftime('%Y%m%d')}.jsonl").write_text(''.join(json.dumps(p, sort_keys=True) + '\n' for p in promoted))
    new_vs_trained = sum(1 for p in promoted if norm(p['question']) not in trained)
    retrain = retrain_decision(new_vs_trained, len(trained), len(existing) + len(promoted), threshold=retrain_threshold)

    gap_report = {'schema': VERSION, 'clusters': [{k: v for k, v in c.items() if k != 'questions'} | {'questions': c['questions'][:50]}
                                                  for c in clusters]}
    (out / 'gap-report.json').write_text(json.dumps(gap_report, indent=1, default=str))
    (out / 'grammar-proposals.json').write_text(json.dumps({'schema': VERSION, 'status': 'for human review; local_compiler.py is not edited',
                                                           'proposals': props}, indent=1, default=str))
    (out / 'retrain.json').write_text(json.dumps(retrain, indent=1))

    outcomes = store['outcomes']
    actionable = [c for c in clusters if c['actionable']]
    summary = {
        'schema': VERSION, 'generated_at': time.strftime('%Y-%m-%dT%H:%M:%S'), 'stack': name or stack.parent.name,
        'inputs': {'outcomes': len(outcomes), 'distinct_questions': len({norm(o['question']) for o in outcomes}),
                   'verified_rows': len(store['verified']), 'verified_questions': len({norm(v['question']) for v in store['verified']}),
                   'corrections': len(store['corrections'])},
        'gaps': {'questions': len(groups), 'occurrences': sum(g['occurrences'] for g in groups.values()), 'clusters': len(clusters),
                 'actionable_clusters': len(actionable),
                 'infrastructure_occurrences': sum(c['occurrences'] for c in clusters if not c['actionable']),
                 'usd_on_gap_questions': round(sum(c['usd'] for c in clusters), 4),
                 'top': [{'id': c['id'], 'family': c['family'], 'label': c['label'], 'distinct_questions': c['distinct_questions'], 'benchmark_questions': c['benchmark_questions'],
                          'occurrences': c['occurrences'], 'caught_by': c['caught_by'], 'usd': c['usd'], 'example': c['examples'][0]}
                         for c in actionable[:10]]},
        'regressions': {'cases': len(cases), 'new_cases_written': len(written), 'written_to_bench': written_bench,
                        'weak_cases': sum(c['weak'] for c in cases)},
        'proposals': {'count': len(props), 'ids': [p['id'] for p in props],
                      'by_target': dict(Counter(p['target'].get('operator') or 'verified_sql_template' for p in props))},
        'routing_dataset': {'rows': len(known), 'new_rows': len(new_routing), 'labels': dict(Counter(r['label'] for r in known.values())),
                            'skipped': routing_skipped, 'router_recovery': recovery},
        'bottling': {'promoted_new_pairs': len(promoted), 'skipped': promo_skipped, 'existing_bottling_pairs': len(existing),
                     'written_to': str(bottling_dir) if write_bottling and promoted else None},
        'retrain': {k: retrain[k] for k in ('recommend_retrain', 'new_verified_pairs', 'trained_pairs', 'reason')},
        'provider_receipts': receipt_spend(receipts),
        'status': 'ok',
        'detail': f"{len(actionable)} gap clusters, {len(cases)} regression cases, {len(props)} grammar proposals, "
                  f"{len(promoted)} new verified pairs; retrain {'recommended' if retrain['recommend_retrain'] else 'not yet'}",
    }
    (out / 'loop.json').write_text(json.dumps(summary, indent=1, default=str))
    return summary


def card(summary):
    g, r = summary['gaps'], summary['retrain']
    lines = [f"Learning loop · {summary['stack']} · {summary['generated_at']}",
             f"  store: {summary['inputs']['outcomes']} outcomes / {summary['inputs']['distinct_questions']} questions, "
             f"{summary['inputs']['verified_questions']} verified, {summary['inputs']['corrections']} corrections",
             f"  gaps: {g['questions']} questions in {g['actionable_clusters']} actionable clusters "
             f"(+{g['infrastructure_occurrences']} infrastructure failures), ${g['usd_on_gap_questions']:.2f} spent on gap questions"]
    for c in g['top'][:6]:
        lines.append(f"    - [{c['family']}] {c['label']} · {c['distinct_questions']} q / {c['occurrences']}x · "
                     f"{', '.join(f'{k}:{v}' for k, v in list(c['caught_by'].items())[:3])} · ${c['usd']:.2f}")
    lines += [f"  regressions: {summary['regressions']['cases']} cases ({summary['regressions']['new_cases_written']} new)",
              f"  grammar proposals: {summary['proposals']['count']} {summary['proposals']['by_target']}",
              f"  routing dataset: {summary['routing_dataset']['rows']} rows (+{summary['routing_dataset']['new_rows']}); "
              f"router decisions recovered from receipts for {summary['routing_dataset']['router_recovery'].get('router_executed_recovered')}/"
              f"{summary['routing_dataset']['router_recovery'].get('router_executed_questions')} router-executed questions",
              f"  bottling: +{summary['bottling']['promoted_new_pairs']} verified pairs · retrain: "
              f"{'YES' if r['recommend_retrain'] else 'no'} ({r['reason']})"]
    return '\n'.join(lines)
