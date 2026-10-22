"""Carry verified question -> SQL pairs forward to a new warehouse release, re-verified on that release.

Verified pairs (clinical/learning.py) are keyed to the context they were verified in (source digest + semantic layer),
so activating a new release used to start the store from nothing (1,118 pairs on the v1.23 stack, 1 on v2.3). Here
every verified pair of the prior stack is replayed against the new build with the same deterministic checks a fresh
capture passes (learning.capture_verified):

  1. the plan is a valid governed SELECT (sql_query.validate);
  2. re-lint against the new release's data dictionary (verify.lint): every stream, payload field and enumerated value
     it names must exist; the statement must prepare on the new semantic layer (every table and column exists); every
     literal it compares a typed column with (=, IN, <>) must occur in that column of the new release;
  3. its dates must not be anchored to the prior snapshot: when the snapshots differ, every year in the SQL's date
     literals must be stated in the question ('last year' compiled to 2028 on a 2029 snapshot is wrong on a 2030 one),
     and the wall clock (date('now')) is never accepted;
  4. it runs on the new release within the governor's time limit, and verify.sanity finds no fatal red flag;
  5. the high-precision answer checks and the deterministic oracles (answer_checks.annotate -> oracles.py: QC-PARTITION,
     QC-PERTURB, QC-SHAPE, QC-EMPTY; metric lint QC-METRIC ...) raise no warning that would have blocked capture
     (learning.QUARANTINE_CONTROLS and verification_v2.ROUTING_CONTROLS);
  6. no user correction exists for the question (prior or new store), and different plans that pass for one question
     agree on the new release (otherwise none is carried: FM-19, an ambiguous question has no single verified plan).

Pairs that pass are written to the new store under the new context with provenance (origin context and release,
original capture time, re-verified on, checks); the rest go to the quarantined table with the reasons, never reused.
Both builds are read-only except the target store: semantic layers are opened directly (never semantic_layer.ensure),
the prior learning store is opened mode=ro. Synthetic data only; no model calls.
"""
from __future__ import annotations

import json, re, shutil, sqlite3, time
from collections import defaultdict
from pathlib import Path

VERSION = 'verified-carry-forward/1'
QUERY_SECONDS = 30
ORACLE_SECONDS = 20
_DATE_LIT = re.compile(r"'((?:19|20)\d\d)(?:-\d\d(?:-\d\d)?)?'")
_WALL_CLOCK = re.compile(r"\b(?:date|datetime|julianday|strftime)\s*\(\s*'now'|\bcurrent_(?:date|timestamp|time)\b", re.I)
_TYPED_LITERAL = re.compile(r"(?<![\w.$'])(?:([a-z_]\w*)\.)?([a-z_]\w*)\s*(=|==|<>|!=|\bnot\s+in\b|\bin\b)\s*('(?:[^']|'')*'|\((?:\s*'(?:[^']|'')*'\s*,?)+\))", re.I)
_TABLE_REF = re.compile(r'\b(?:from|join)\s+([a-z_]\w*)(?:\s+(?:as\s+)?(?!on\b|where\b|join\b|left\b|inner\b|cross\b|group\b|order\b|limit\b|using\b)([a-z_]\w*))?', re.I)
SKIP_VALUE_COLUMNS = {'returns'}


def _stack(path):
    path = Path(path)
    return path / 'stack' if (path / 'stack' / 'run.json').exists() else path


def _ro(path, seconds):
    db = sqlite3.connect(f'file:{path}?mode=ro', uri=True)
    deadline = [time.perf_counter() + seconds]
    db.set_progress_handler(lambda: 1 if time.perf_counter() > deadline[0] else 0, 10000)
    return db, deadline


class Build:
    """A build opened read-only: run.json, the saved source profile and the semantic layer as it is on disk."""

    def __init__(self, path):
        self.stack = _stack(path)
        self.run = json.loads((self.stack / 'run.json').read_text())
        self.as_of = (self.run.get('snapshot') or {}).get('as_of')
        self.semantic = self.stack / 'semantic.sqlite3'
        if not self.semantic.exists(): raise ValueError(f'{self.stack}: no semantic layer')
        profile = self.stack / 'source-profile.json'
        self.profile = json.loads(profile.read_text()) if profile.exists() else None
        if self.profile and self.profile.get('source_digest') != self.run.get('source_digest'):
            raise ValueError(f'{self.stack}: source-profile.json belongs to another source digest')
        db, _ = _ro(self.semantic, 60)
        try:
            self.meta = dict(db.execute('SELECT key, value FROM meta').fetchall())
            self.tables = {n: {r[1] for r in db.execute(f'PRAGMA table_info({n})')}
                           for (n,) in db.execute("SELECT name FROM sqlite_master WHERE type='table'")}
            self.roster = db.execute('SELECT count(*) FROM patient').fetchone()[0]
        finally:
            db.close()
        conn = self.stack.parent / 'connection.json'
        self.release = json.loads(conn.read_text()).get('release') if conn.exists() else None
        self._values = {}
        self.oracle_executor = self.scalar()  # one executor: metrics.universe caches value-set universes per executor

    def scalar(self, seconds=ORACLE_SECONDS):
        """execute(sql) -> first column of the first row (read-only, authorizer, time limit): the oracles' executor."""
        from .clinical import sql_query

        def run(query):
            db, _ = _ro(self.semantic, seconds)
            db.set_authorizer(sql_query._authorizer)
            try:
                row = db.execute(query).fetchone()
            finally:
                db.close()
            return row[0] if row else None
        return run

    def execute(self, params, seconds=QUERY_SECONDS):
        from .clinical import sql_query
        return sql_query.execute(self.stack, params, target=self.semantic, time_limit=seconds)

    def prepares(self, query):
        """None when the statement compiles on this semantic layer, else the error (missing table / column)."""
        from .clinical import sql_query
        db, _ = _ro(self.semantic, 10)
        db.set_authorizer(sql_query._authorizer)
        try:
            db.execute('EXPLAIN ' + query, {'population': '[]'}).fetchone()
            return None
        except sqlite3.DatabaseError as exc:
            return str(exc)
        finally:
            db.close()

    def has_value(self, table, column, value, seconds=10):
        """True / False whether `value` occurs in table.column; None when the scan did not finish in time."""
        key = (table, column, value)
        if key in self._values: return self._values[key]
        if table == 'record' and column == 'stream' and self.profile:
            out = value in self.profile['streams']
        else:
            db, _ = _ro(self.semantic, seconds)
            try:
                out = db.execute(f'SELECT 1 FROM "{table}" WHERE "{column}" = ? LIMIT 1', (value,)).fetchone() is not None
            except sqlite3.OperationalError:
                out = None  # interrupted (time limit) or unreadable: not evidence of absence
            finally:
                db.close()
        self._values[key] = out
        return out

    def context(self):
        """The learning-store key lookups on this build will use (learning.context, without touching the build)."""
        from .clinical import semantic_layer
        return f"{self.run['source_digest']}|{semantic_layer.VERSION}"


def _code(sql):
    return re.sub(r'--[^\n]*', ' ', sql or '')


def absent_literals(build, sql):
    """[(table.column, value)] compared literals that never occur in the new release's typed column."""
    code = _code(sql)
    aliases, tables = {}, []
    for t, alias in _TABLE_REF.findall(code):
        if t.lower() in build.tables:
            tables.append(t.lower())
            aliases[(alias or t).lower()] = t.lower()
    missing = []
    for alias, column, op, literal in _TYPED_LITERAL.findall(code):
        column = column.lower()
        if column in SKIP_VALUE_COLUMNS or 'json_extract' in column: continue
        owners = [aliases[alias.lower()]] if alias and alias.lower() in aliases else [t for t in dict.fromkeys(tables) if column in build.tables[t]]
        owners = [t for t in owners if column in build.tables.get(t, ())]
        if not owners: continue  # a CTE column or expression alias: checked by execution, not here
        for value in re.findall(r"'((?:[^']|'')*)'", literal):
            value = value.replace("''", "'")
            if re.fullmatch(r'\d{4}(?:-\d\d(?:-\d\d)?)?|-?\d+(?:\.\d+)?|', value): continue  # dates and numbers are not vocabulary
            found = [build.has_value(t, column, value) for t in owners]
            if True in found or None in found: continue
            missing.append((f'{owners[0]}.{column}', value))
    return missing


def anchored_years(question, sql, prior_as_of, new_as_of):
    """Years in the SQL's date literals that the question does not state, when the snapshot moved (else [])."""
    if not prior_as_of or prior_as_of == new_as_of: return []
    stated = set(re.findall(r'\b((?:19|20)\d\d)\b', question or ''))
    years = {y for y in _DATE_LIT.findall(_code(sql)) if y not in stated}
    if f"'{prior_as_of[:10]}'" in _code(sql) and prior_as_of[:10] not in (question or ''):
        years.add(prior_as_of[:10])  # the prior snapshot date itself ('2029 YTD' ends on it)
    return sorted(years)


def blocking_controls():
    from .clinical import learning, verification_v2
    return set(learning.QUARANTINE_CONTROLS) | set(verification_v2.ROUTING_CONTROLS)


def replay_pair(build, pair, prior_as_of, corrected=frozenset()):
    """-> {'status': 'pass'|'quarantine', 'reasons': [...], 'result': ..., 'checks': [...]} for one verified row."""
    from .clinical import sql_query, verify, verification_v2
    q = pair['question']
    reasons, checks = [], []
    if q.strip().lower() in corrected: return {'status': 'quarantine', 'reasons': ['a user correction exists for this question']}
    if pair.get('plan_kind') != 'sql': return {'status': 'quarantine', 'reasons': [f"plan kind {pair.get('plan_kind')} is not replayable SQL"]}
    try:
        params = sql_query.validate(json.loads(pair['plan']))
    except (ValueError, TypeError) as exc:
        return {'status': 'quarantine', 'reasons': [f'invalid plan: {exc}']}
    sql = params['query']
    # 2. re-lint: data dictionary, schema, compared values
    if build.profile is not None:
        problems = verify.lint(build.profile, sql)
        if problems: reasons += [f'lint: {p}' for p in problems]
    err = build.prepares(sql)
    if err: reasons.append(f'schema: {err} (absent in the new release)')
    if not err:
        for col, value in absent_literals(build, sql):
            reasons.append(f"value absent in the new release: {col} = '{value[:60]}'")
    # 3. snapshot anchoring
    if _WALL_CLOCK.search(_code(sql)): reasons.append("uses the wall clock (date('now')), not the snapshot")
    years = anchored_years(q, sql, prior_as_of, build.as_of)
    if years:
        reasons.append(f"dates anchored to the prior snapshot ({prior_as_of}): the SQL uses {', '.join(years)}, which the question "
                       f"does not state; on the new snapshot ({build.as_of}) the same words mean another period")
    checks.append('lint')
    if reasons: return {'status': 'quarantine', 'reasons': reasons, 'checks': checks}
    # 4. run it
    try:
        result = build.execute(params)
        probe = result if params['returns'] == 'table' else build.execute({'query': sql, 'returns': 'table'})
    except ValueError as exc:
        return {'status': 'quarantine', 'reasons': [f'execution failed on the new release: {str(exc)[:200]}'], 'checks': checks}
    checks.append('executed')
    fatal, _warn = verify.sanity(q, sql, {'rows': probe.get('rows') or [], 'columns': probe.get('columns') or []}, build.roster) \
        if probe.get('rows') else ([], [])
    if fatal: reasons += [f'sanity: {f}' for f in fatal]
    # 5. answer checks + deterministic oracles (as at capture)
    findings = verification_v2.oracle_for(build.stack, q, build.as_of, execute=build.oracle_executor)({'params': params, 'result': result})
    block = blocking_controls()
    hits = [f for f in findings if f.get('severity', 'warning') == 'warning' and f.get('control') in block]
    reasons += [f"{f['control']}: {f.get('message', '')[:200]}" for f in hits]
    checks.append('oracles')
    out = {'status': 'quarantine' if reasons else 'pass', 'reasons': reasons, 'checks': checks,
           'result': result, 'signature': verification_v2.signature(result)}
    return out


def _rows(store, table, cols):
    if not store.exists(): return []
    db = sqlite3.connect(f'file:{store}?mode=ro', uri=True)
    try:
        have = {r[1] for r in db.execute(f'PRAGMA table_info({table})')}
        if not have: return []
        sel = ', '.join(c if c in have else f'NULL AS {c}' for c in cols)
        return [dict(zip(cols, r)) for r in db.execute(f'SELECT {sel} FROM {table}')]
    finally:
        db.close()


VERIFIED_COLS = ('question', 'plan_kind', 'plan', 'signature', 'route', 'models', 'agreement', 'created', 'context', 'provenance')


def carry_forward(prior, new, out=None, *, limit=None, progress=None, dry_run=False, shard=None):
    """Replay the prior stack's verified pairs on the new build.

    prior, new: build (or stack) directories. out: directory whose learning.sqlite3 receives the carried and quarantined
    pairs; default the new stack. When out is not the new stack, the new stack's existing store (if any) is copied there
    first, so the scratch result is what the new stack would hold. dry_run: replay only, write nothing.
    Returns a summary with counts, reasons and per-pair outcomes."""
    started = time.perf_counter()
    prior_stack, build = _stack(prior), Build(new)
    prior_run = json.loads((prior_stack / 'run.json').read_text())
    prior_as_of = (prior_run.get('snapshot') or {}).get('as_of')
    prior_conn = prior_stack.parent / 'connection.json'
    prior_release = json.loads(prior_conn.read_text()).get('release') if prior_conn.exists() else None
    source = prior_stack / 'learning.sqlite3'
    pairs = _rows(source, 'verified', VERIFIED_COLS)
    corrected = {(r['question'] or '').strip().lower() for r in _rows(source, 'corrections', ('question',))}
    target_store = build.stack / 'learning.sqlite3'
    corrected |= {(r['question'] or '').strip().lower() for r in _rows(target_store, 'corrections', ('question',))}
    new_context = build.context()
    existing = {((r['question'] or '').strip(), r['plan']) for r in _rows(target_store, 'verified', VERIFIED_COLS) if r['context'] == new_context}
    pairs.sort(key=lambda r: (r['created'] or 0))
    if shard:  # (i, n): this process replays the questions whose hash falls in slice i of n (a question never splits)
        import hashlib
        i, n = shard
        pairs = [p for p in pairs if int(hashlib.sha256((p['question'] or '').strip().lower().encode()).hexdigest(), 16) % n == i]
    if limit: pairs = pairs[:limit]
    outcomes = []
    for i, pair in enumerate(pairs):
        if ((pair['question'] or '').strip(), pair['plan']) in existing:
            outcomes.append({'pair': pair, 'status': 'already_present', 'reasons': []})
            continue
        try:
            r = replay_pair(build, pair, prior_as_of, corrected)
        except Exception as exc:  # noqa: BLE001 - one bad pair never stops the replay; it is quarantined with the error
            r = {'status': 'quarantine', 'reasons': [f'replay error: {type(exc).__name__}: {str(exc)[:160]}']}
        outcomes.append({'pair': pair, **r})
        if progress and (i + 1) % 25 == 0: progress(i + 1, len(pairs))
    # FM-19: passing plans of one question must agree on the new release.
    from .clinical import verification_v2
    by_q = defaultdict(list)
    for o in outcomes:
        if o['status'] == 'pass': by_q[o['pair']['question'].strip().lower()].append(o)
    for group in by_q.values():
        if len(group) > 1 and not all(verification_v2.same_answer(group[0]['result'], g['result']) for g in group[1:]):
            for g in group:
                g.update(status='quarantine', reasons=['plans verified for this question disagree on the new release (two readings)'])
    now = time.time()
    origin = {'origin_release': prior_release, 'origin_stack': str(prior_stack), 'origin_as_of': prior_as_of}
    carried = [o for o in outcomes if o['status'] == 'pass']
    quarantined = [o for o in outcomes if o['status'] == 'quarantine']
    written = None
    if not dry_run:
        dest = Path(out) if out else build.stack
        dest.mkdir(parents=True, exist_ok=True)
        if dest.resolve() != build.stack.resolve() and target_store.exists() and not (dest / 'learning.sqlite3').exists():
            shutil.copy2(target_store, dest / 'learning.sqlite3')
        from .clinical import learning
        db = learning._db(dest)
        try:
            for o in carried:
                p = o['pair']
                prov = {**origin, 'origin_context': p['context'], 'origin_created': p['created'], 'reverified_on': new_context,
                        'reverified_release': build.release, 'reverified_as_of': build.as_of, 'reverified_at': now,
                        'checks': o['checks'], 'tool': VERSION, 'prior_provenance': json.loads(p['provenance']) if p.get('provenance') else None}
                db.execute(f"INSERT OR REPLACE INTO verified({', '.join(VERIFIED_COLS)}) VALUES ({','.join('?' * len(VERIFIED_COLS))})",
                           (p['question'].strip(), 'sql', p['plan'], json.dumps(o['signature'], default=str)[:4000], p['route'] or 'hosted_verified_sql',
                            p['models'], p['agreement'], p['created'] or now, new_context, json.dumps(prov, default=str)))
            for o in quarantined:
                p = o['pair']
                prov = {**origin, 'origin_context': p['context'], 'origin_created': p['created'], 'replayed_on': new_context,
                        'replayed_at': now, 'tool': VERSION, 'carry_forward': 'quarantined'}
                db.execute('INSERT INTO quarantined(question, plan_kind, plan, signature, route, agreement, controls, created, context, provenance) '
                           'VALUES (?,?,?,?,?,?,?,?,?,?)',
                           ((p['question'] or '').strip(), p['plan_kind'], p['plan'], (p['signature'] or '')[:4000], p['route'], p['agreement'],
                            json.dumps(o['reasons']), now, new_context, json.dumps(prov, default=str)))
            db.commit()
        finally:
            db.close()
        written = str(dest / 'learning.sqlite3')
    reason_counts = defaultdict(int)
    for o in quarantined:
        for r in o['reasons']: reason_counts[_reason_kind(r)] += 1
    return {'schema': VERSION, 'synthetic': True, 'prior_stack': str(prior_stack), 'prior_release': prior_release,
            'prior_as_of': prior_as_of, 'new_stack': str(build.stack), 'new_release': build.release, 'new_as_of': build.as_of,
            'new_context': new_context, 'new_semantic_on_disk': build.meta.get('version'),
            'pairs': len(pairs), 'distinct_questions': len({(p['question'] or '').strip().lower() for p in pairs}),
            'carried': len(carried), 'carried_questions': len({o['pair']['question'].strip().lower() for o in carried}),
            'quarantined': len(quarantined), 'already_present': sum(o['status'] == 'already_present' for o in outcomes),
            'quarantine_reasons': dict(sorted(reason_counts.items(), key=lambda kv: -kv[1])),
            'written_to': written, 'elapsed_seconds': round(time.perf_counter() - started, 1),
            'outcomes': [{'question': o['pair']['question'], 'origin_context': o['pair']['context'], 'status': o['status'],
                          'reasons': o.get('reasons', [])} for o in outcomes]}


def _reason_kind(reason):
    for prefix in ('lint', 'schema', 'value absent', 'dates anchored', 'uses the wall clock', 'execution failed', 'sanity', 'invalid plan',
                   'a user correction', 'plans verified', 'replay error', 'plan kind'):
        if reason.startswith(prefix): return prefix
    m = re.match(r'(QC-[A-Z]+)', reason)
    return m.group(1) if m else reason[:40]


# ---------------------------------------------------------------- on the question path: carry one question forward on demand
REPO = Path(__file__).resolve().parents[2]
HISTORY = REPO / 'config/agere/activation-history'


def prior_stores(stack):
    """Learning stores whose verified pairs may predate this release: the stack's own store (pairs under an older context, e.g.
    a semantic-layer version bump) and every previously activated build's store (config/agere/activation-history)."""
    stack = _stack(stack)
    out = [stack / 'learning.sqlite3']
    for f in sorted(HISTORY.glob('*.json')) if HISTORY.exists() else []:
        try:
            build = json.loads(f.read_text()).get('build')
        except (OSError, ValueError):
            continue
        if build and (_stack(build) / 'learning.sqlite3').exists() and _stack(build).resolve() != stack.resolve():
            out.append(_stack(build) / 'learning.sqlite3')
    return [p for p in out if p.exists()]


def carry_question(stack, question, *, stores=None):
    """A verified SQL plan for this exact question from an earlier context or release, re-verified NOW on this build with the
    same checks as carry_forward (lint, schema, values, snapshot anchoring, execution, sanity, oracles). A pair that passes
    is written to this stack's store under the current context (provenance: carried on lookup) and returned as
    {'plan_kind', 'plan', 'agreement', 'carried_from'}; one that fails is quarantined with its reasons. None when nothing
    earlier exists or nothing passes. Never raises (the learned tier simply has no proposal)."""
    try:
        stack = _stack(stack)
        build = Build(stack)
        current = build.context()
        norm = (question or '').strip().lower()
        corrected = set()
        for store in stores or prior_stores(stack):
            corrected |= {(r['question'] or '').strip().lower() for r in _rows(store, 'corrections', ('question',))}
        if norm in corrected: return None
        seen = set()
        for store in stores or prior_stores(stack):
            rows = [r for r in _rows(store, 'verified', VERIFIED_COLS)
                    if (r['question'] or '').strip().lower() == norm and r['context'] != current and r['plan_kind'] == 'sql']
            for pair in sorted(rows, key=lambda r: -(r['created'] or 0)):
                if pair['plan'] in seen: continue
                seen.add(pair['plan'])
                origin_run = store.parent / 'run.json'
                prior_as_of = (json.loads(origin_run.read_text()).get('snapshot') or {}).get('as_of') if origin_run.exists() else build.as_of
                r = replay_pair(build, pair, prior_as_of, frozenset(corrected))
                from .clinical import learning
                db = learning._db(stack)
                try:
                    prov = {'origin_store': str(store), 'origin_context': pair['context'], 'origin_created': pair['created'],
                            'origin_as_of': prior_as_of, 'reverified_on': current, 'reverified_at': time.time(),
                            'checks': r.get('checks'), 'tool': VERSION, 'carried_on': 'question_lookup'}
                    if r['status'] == 'pass':
                        db.execute(f"INSERT OR REPLACE INTO verified({', '.join(VERIFIED_COLS)}) VALUES ({','.join('?' * len(VERIFIED_COLS))})",
                                   (pair['question'].strip(), 'sql', pair['plan'], json.dumps(r['signature'], default=str)[:4000],
                                    pair['route'] or 'hosted_verified_sql', pair['models'], pair['agreement'], time.time(), current,
                                    json.dumps(prov, default=str)))
                    else:
                        db.execute('INSERT INTO quarantined(question, plan_kind, plan, signature, route, agreement, controls, created, context, provenance) '
                                   'VALUES (?,?,?,?,?,?,?,?,?,?)', (pair['question'].strip(), 'sql', pair['plan'], (pair['signature'] or '')[:4000],
                                   pair['route'], pair['agreement'], json.dumps(r['reasons']), time.time(), current,
                                   json.dumps({**prov, 'carry_forward': 'quarantined'}, default=str)))
                    db.commit()
                finally:
                    db.close()
                if r['status'] == 'pass':
                    return {'plan_kind': 'sql', 'plan': json.loads(pair['plan']), 'agreement': pair['agreement'],
                            'carried_from': {'store': str(store), 'context': pair['context'], 'checks': r.get('checks')}}
        return None
    except Exception:  # noqa: BLE001 - carry-forward is an optimisation on the learned tier; never blocks the cascade
        return None
