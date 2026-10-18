"""Customer workspaces: connect a warehouse, auto-map it to the semantic layer, and answer questions on day one.

    ws = Workspace.create('Acme Health', 'snowflake://ACME_DW/CLINICAL')
    ws.run()                      # profile -> propose -> auto-approve -> build -> raw tables -> verify -> docs -> ready
    ws.review_items()             # what a person should look at (low-confidence fields, missing required fields, tables)
    ws.decide(item_id, 'accept' | 'reject' | {'source': 'other_col'})  then  ws.rebuild()
    ws.ask('How many diabetic patients had an A1c over 9 last year?')   # full pipeline when supported, else hybrid core

Day one: fields the matcher scores at or above its accept threshold are accepted automatically and the mapping is
approved as "auto"; everything else waits in the review queue without blocking answers. A person's decisions are
kept and re-applied on every rebuild. Verification results are recorded with the build, never used to block it.
Every source table is accounted for: mapped tables feed the modeled tables, and every source table (mapped or not) is
also copied verbatim as raw_<table>, so the analyst can reach fields the model does not carry.

Answering: a native workspace (staged record store + product semantic layer) answers through the full product pipeline
(local compiler, governed metrics, analytic engines and the hybrid core, reconciled by together.compose_together) over
<slug>/pipeline/, prepared after docs by the 'pipeline' step (workspace_stack.py). Other workspaces, or a full-pipeline
failure, answer through the hybrid core alone (pipeline='hybrid').

State lives in artifacts/workspaces/<slug>/: workspace.json, profile.json, mapping.json, semantic.sqlite3,
analyst_docs/, verification.json, events.jsonl, receipts/, pipeline/ (native), parity.json (native: landing-to-model parity,
recorded by verify as an annotation). Native builds also add delivery_file / delivery_gap (ingest/delivery.py) to the
semantic layer at the build step. Credentials are never stored: a source spec names the
warehouse, and connectors read credentials from the environment (see connectors.py).
"""
from __future__ import annotations

from contextlib import contextmanager
from datetime import date, datetime, timezone
import json
import os
from pathlib import Path
import re
import sqlite3
import threading

from . import apply as apply_mod, connectors, contract, matcher, profile as profile_mod, verify

ROOT = Path(__file__).resolve().parents[3]
WORKSPACES = ROOT / 'artifacts' / 'workspaces'
LEDGER = Path(os.environ.get('AGERE_LEDGER_PATH') or ROOT / 'artifacts' / 'agere-full' / 'provider-budget.sqlite3')
STEPS = ('ingest', 'stage', 'profile', 'propose', 'approve', 'build', 'raw', 'verify', 'docs')
NATIVE = 'native://'
RAW_BATCH = 5000
_LOCKS = {}
_LOCKS_GUARD = threading.Lock()


def _now():
    return datetime.now(timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ')


def slugify(name):
    s = re.sub(r'[^a-z0-9]+', '-', str(name).lower()).strip('-')
    if not s:
        raise ValueError('A workspace needs a name with letters or digits')
    return s[:60]


def _safe_ident(name):
    return re.sub(r'[^A-Za-z0-9_]', '_', str(name)).lower()


def _lock(slug):
    with _LOCKS_GUARD:
        return _LOCKS.setdefault(slug, threading.Lock())


def list_workspaces(root=None):
    root = Path(root or WORKSPACES)
    if not root.is_dir():
        return []
    return [Workspace(p.name, root).summary() for p in sorted(root.iterdir()) if (p / 'workspace.json').is_file()]


class Workspace:
    def __init__(self, slug, root=None):
        self.slug = slugify(slug)
        self.dir = Path(root or WORKSPACES) / self.slug
        if not (self.dir / 'workspace.json').is_file():
            raise FileNotFoundError(f'No workspace {self.slug!r}')

    # --- state -----------------------------------------------------------------------------------------------------

    @classmethod
    def create(cls, name, source, *, as_of=None, raw='all', root=None, credential=None):
        """raw: 'all' copies every source table as raw_<table>, 'unmapped' only tables no entity uses, 'none' skips."""
        if raw not in ('all', 'unmapped', 'none'):
            raise ValueError("raw must be 'all', 'unmapped' or 'none'")
        slug = slugify(name)
        d = Path(root or WORKSPACES) / slug
        if (d / 'workspace.json').exists():
            raise FileExistsError(f'Workspace {slug!r} already exists')
        d.mkdir(parents=True, exist_ok=True)
        state = {'schema': 'agere-workspace/1', 'slug': slug, 'name': name, 'source': str(source), 'as_of': as_of,
                 'raw': raw, 'status': 'created', 'steps': {}, 'decisions': {}, 'created_at': _now(), 'error': None,
                 'credential': credential}
        (d / 'workspace.json').write_text(json.dumps(state, indent=1))
        ws = cls(slug, root)
        ws._event('created', source=str(source))
        return ws

    @property
    def state(self):
        return json.loads((self.dir / 'workspace.json').read_text())

    def _save(self, state):
        tmp = self.dir / 'workspace.json.tmp'
        tmp.write_text(json.dumps(state, indent=1, default=str))
        tmp.replace(self.dir / 'workspace.json')

    def _update(self, **kw):
        s = self.state
        s.update(kw)
        s['updated_at'] = _now()
        self._save(s)
        return s

    def _event(self, kind, **detail):
        with open(self.dir / 'events.jsonl', 'a') as f:
            f.write(json.dumps({'at': _now(), 'event': kind, **detail}, default=str) + '\n')

    def _step(self, name, **detail):
        s = self.state
        s['steps'][name] = {'at': _now(), **detail}
        s['updated_at'] = _now()
        self._save(s)
        self._event(name, **detail)

    @property
    def semantic_path(self):
        return self.dir / 'semantic.sqlite3'

    @property
    def docs_dir(self):
        return self.dir / 'analyst_docs'

    @property
    def native(self):
        return self.state['source'].startswith(NATIVE)

    @property
    def landing_path(self):
        return self.dir / 'landing.sqlite'

    def connect(self):
        """A native delivery is read through its landing database (raw feeds parsed, plus canonical stg_* tables)."""
        if self.native:
            if not self.landing_path.is_file():
                return None  # nothing ingested yet; the ingest step creates it
            return connectors.SQLiteConnector(self.landing_path)
        creds = getattr(self, 'credentials', None)  # hosted: the tenant's decrypted credentials, never os.environ
        return connectors.connect(self.state['source'], credentials=creds) if creds else connectors.connect(self.state['source'])

    def summary(self):
        s = self.state
        out = {k: s.get(k) for k in ('slug', 'name', 'status', 'as_of', 'created_at', 'updated_at', 'error')}
        out['source'] = re.sub(r'//[^/@]*@', '//***@', s['source'])  # never echo inline credentials
        out['steps'] = {k: v.get('at') for k, v in s['steps'].items()}
        out['review_open'] = s['steps'].get('propose', {}).get('review_open')
        out['tables'] = s['steps'].get('build', {}).get('rows')
        out['raw_tables'] = s['steps'].get('raw', {}).get('tables')
        out['verification'] = s['steps'].get('verify', {}).get('passed')
        return out

    # --- pipeline --------------------------------------------------------------------------------------------------

    def run(self, *, through='docs'):
        """Run every step up to and including `through`. Steps that already ran are re-run from `profile` only when
        explicitly asked (rebuild()); a failed step records its error and stops."""
        with _lock(self.slug):
            conn = self.connect()
            try:
                for step in STEPS:
                    if step in ('ingest', 'stage') and not self.native:
                        continue
                    if step in ('profile', 'propose', 'approve') and self.native:
                        continue  # native deliveries are staged into the full canonical model, not mapped field by field
                    getattr(self, '_do_' + step)(conn)
                    if step == 'stage':  # the landing database now exists and holds the staged tables
                        if conn is not None:
                            conn.close()
                        conn = self.connect()
                    if step == through:
                        break
                if through == 'docs':
                    self._do_pipeline(conn)
                    self._update(status='ready', error=None)
            except Exception as exc:
                self._update(status='failed', error=f'{type(exc).__name__}: {exc}'[:800])
                self._event('failed', error=f'{type(exc).__name__}: {exc}'[:800])
                raise
            finally:
                if conn is not None:
                    conn.close()
        return self.summary()

    def rebuild(self):
        """Re-apply the mapping with the reviewer's decisions (no re-profiling) and refresh raw tables, checks, docs."""
        with _lock(self.slug):
            conn = self.connect()
            try:
                self._apply_decisions()
                for step in ('approve', 'build', 'raw', 'verify', 'docs', 'pipeline'):
                    getattr(self, '_do_' + step)(conn)
                self._update(status='ready', error=None)
            except Exception as exc:
                self._update(status='failed', error=f'{type(exc).__name__}: {exc}'[:800])
                raise
            finally:
                conn.close()
        return self.summary()

    @property
    def raw_dir(self):
        """The native delivery folder named by a native:// source (None for other workspaces)."""
        src = self.state['source']
        if not src.startswith(NATIVE):
            return None
        return src[len(NATIVE) - 1:] if src.startswith(NATIVE + '/') else src[len(NATIVE):]

    def _do_ingest(self, conn):
        from ..ingest import landing
        self._update(status='ingesting')
        stats = landing.ingest(self.raw_dir, self.landing_path)
        (self.dir / 'ingest.json').write_text(json.dumps(stats, indent=1, default=str))
        self._step('ingest', files=stats['files'], skipped=stats['skipped'], unparsed=stats['unparsed'],
                   errors=stats['errors'], rows=stats['rows'], tables=len(stats.get('tables', {})))

    def _do_stage(self, conn):
        from ..ingest import canonical
        self._update(status='staging')
        report = canonical.stage(self.landing_path)
        (self.dir / 'stage.json').write_text(json.dumps(report, indent=1, default=str))
        self._step('stage', tables=report.get('tables'), identity=report.get('identity'), issues=report.get('issues'))

    def _do_profile(self, conn):
        self._update(status='profiling')
        prof = profile_mod.profile_source(conn)
        (self.dir / 'profile.json').write_text(json.dumps(prof, indent=1, default=str))
        sm = profile_mod.summary(prof)
        self._step('profile', tables=len(prof.get('tables', {})), summary=sm)

    def _do_propose(self, conn):
        self._update(status='mapping')
        prof = json.loads((self.dir / 'profile.json').read_text())
        mapping = matcher.propose(contract.load_model(), prof)
        matcher.save(mapping, self.dir / 'mapping.json')
        self._apply_decisions()
        self._step('propose', entities={e: v['source_table'] for e, v in mapping['entities'].items()},
                   review_open=len(self.review_items()))

    def _do_approve(self, conn):
        mapping = matcher.load(self.dir / 'mapping.json')
        if mapping['approval'].get('approved') and mapping['approval'].get('reviewer', '').startswith('person:'):
            return  # a person approved this exact mapping; keep their approval
        fields = [f for e in mapping['entities'].values() for f in e['fields'].values()]
        mapping['approval'] = {'approved': True, 'reviewer': 'auto: matcher accept threshold + reviewer decisions',
                               'approved_at': _now(),
                               'accepted_fields': sum(1 for f in fields if f.get('accept')),
                               'rejected_or_unreviewed_fields': sum(1 for f in fields if f.get('source') and not f.get('accept'))}
        matcher.save(mapping, self.dir / 'mapping.json')
        self._step('approve', **{k: v for k, v in mapping['approval'].items() if k != 'approved_at'})

    def _do_build(self, conn):
        self._update(status='building')
        if self.native:
            return self._build_native()
        receipt = apply_mod.apply(matcher.load(self.dir / 'mapping.json'), conn, self.semantic_path)
        as_of = self.state.get('as_of') or self._infer_as_of() or date.today().isoformat()
        db = sqlite3.connect(self.semantic_path)
        try:
            db.execute("DELETE FROM meta WHERE key IN ('as_of', 'workspace')")
            db.executemany('INSERT INTO meta VALUES (?, ?)', [('as_of', as_of), ('workspace', self.slug)])
            db.commit()
        finally:
            db.close()
        self._update(as_of=as_of)
        self._step('build', rows=receipt.get('rows'), transform_stats=receipt.get('transform_stats'), as_of=as_of)

    def _infer_as_of(self):
        """The latest plausible event date in the built layer (data received through then)."""
        db = sqlite3.connect(f'file:{self.semantic_path}?mode=ro', uri=True)
        try:
            best = None
            for table, col in (('visit', 'event_date'), ('claim', 'service_from'), ('measurement', 'event_date'),
                               ('condition', 'event_date'), ('medication', 'event_date')):
                try:
                    v = db.execute(f"SELECT max({col}) FROM {table} WHERE {col} <= date('now', '+1 day')").fetchone()[0]
                except sqlite3.Error:
                    continue
                if v and (best is None or str(v)[:10] > best):
                    best = str(v)[:10]
            return best
        finally:
            db.close()

    def _do_raw(self, conn):
        mode = self.state.get('raw', 'all')
        if mode == 'none':
            self._step('raw', tables={}, mode=mode)
            return
        if self.native or mode == 'all':
            tables = [t for t in conn.tables() if t not in ('ingest_files',)]
        else:
            tables = list(matcher.load(self.dir / 'mapping.json').get('unmapped_source_tables') or [])
        db = sqlite3.connect(self.semantic_path)
        copied = {}
        try:
            existing = {r[0] for r in db.execute("SELECT name FROM sqlite_master WHERE type='table'")}
            for t in tables:
                cols = [c for c, _ in conn.columns(t)]
                if not cols:
                    continue
                name = 'raw_' + _safe_ident(t)
                if name in existing:
                    db.execute(f'DROP TABLE "{name}"')
                safe_cols = []
                for c in cols:
                    s = _safe_ident(c) or 'col'
                    while s in safe_cols:
                        s += '_'
                    safe_cols.append(s)
                db.execute(f'CREATE TABLE "{name}" (' + ', '.join(f'"{c}"' for c in safe_cols) + ')')
                ins = f'INSERT INTO "{name}" VALUES (' + ', '.join('?' * len(cols)) + ')'
                n, batch = 0, []
                for row in conn.rows(t, cols):
                    batch.append(tuple(v if v is None or isinstance(v, (int, float, str, bytes)) else str(v) for v in row))
                    if len(batch) >= RAW_BATCH:
                        db.executemany(ins, batch); n += len(batch); batch = []
                if batch:
                    db.executemany(ins, batch); n += len(batch)
                copied[name] = {'source_table': t, 'rows': n,
                                'renamed_columns': {c: s for c, s in zip(cols, safe_cols) if c != s}}
            db.commit()
        finally:
            db.close()
        self._step('raw', tables={k: v['rows'] for k, v in copied.items()}, mode=mode)
        (self.dir / 'raw_tables.json').write_text(json.dumps(copied, indent=1))

    def _do_verify(self, conn):
        self._update(status='verifying')
        if self.native:
            rep = json.loads((self.dir / 'stage.json').read_text()) if (self.dir / 'stage.json').is_file() else {}
            ident = rep.get('identity') or {}
            checks = [{'name': 'persons_resolved', 'passed': bool(rep.get('people')), 'detail': {'persons': rep.get('people')}},
                      {'name': 'records_without_person', 'passed': (rep.get('issues') or 0) <= 0.001 * (rep.get('records') or 0),
                       'detail': {'records': rep.get('issues'), 'share': round((rep.get('issues') or 0) / max(rep.get('records') or 1, 1), 6),
                                  'by_stream': rep.get('unresolved'), 'threshold': '0.1% of staged records'}},
                      {'name': 'identity_tiers', 'passed': True, 'detail': {k: v for k, v in ident.items() if k != 'largest_clusters'}}]
            checks.append(self._source_parity())
            # source_parity annotates (annotation=True): it is reported, never counted toward passed, never blocks.
            result = {'passed': all(c['passed'] for c in checks if not c.get('annotation')), 'checks': checks,
                      'streams': rep.get('streams')}
            (self.dir / 'verification.json').write_text(json.dumps(result, indent=1, default=str))
            self._step('verify', passed=result['passed'],
                       failed_checks=[c['name'] for c in checks if not c['passed'] and not c.get('annotation')],
                       parity_flagged=checks[-1].get('detail', {}).get('flagged_count'))
            return
        try:
            result = verify.invariants(self.semantic_path, matcher.load(self.dir / 'mapping.json'), conn,
                                       as_of=self.state.get('as_of'))
        except Exception as exc:  # verification annotates; it never blocks the workspace
            result = {'passed': False, 'error': f'{type(exc).__name__}: {exc}'[:500], 'checks': []}
        (self.dir / 'verification.json').write_text(json.dumps(result, indent=1, default=str))
        failed = [c.get('name') for c in result.get('checks', []) if isinstance(c, dict) and not c.get('passed')]
        self._step('verify', passed=result.get('passed'), failed_checks=failed)

    def _source_parity(self):
        """Landing rows/persons per feed, instance and year vs modeled records (onboarding/parity.py). The full report goes
        to parity.json; the check is an annotation and a failure to run it is recorded, never raised."""
        from . import parity
        try:
            report = parity.check(self.landing_path, self.semantic_path, self.dir / 'warehouse.sqlite3')
            (self.dir / 'parity.json').write_text(json.dumps(report, indent=1, default=str))
            detail = parity.summary(report) | {'report': 'parity.json'}
            return {'name': 'source_parity', 'annotation': True, 'passed': report['flagged_count'] == 0, 'detail': detail}
        except Exception as exc:  # noqa: BLE001
            return {'name': 'source_parity', 'annotation': True, 'passed': False,
                    'detail': {'error': f'{type(exc).__name__}: {exc}'[:500]}}

    def _do_docs(self, conn):
        from ..hybrid import catalog
        if self.native:
            rep = json.loads((self.dir / 'stage.json').read_text()) if (self.dir / 'stage.json').is_file() else {}
            lines = [f"Workspace {self.state['name']} ({self.slug}): native feeds ingested and staged into Agere's canonical "
                     f"model; data as of {self.state.get('as_of')}. Persons are Agere-minted ids (AGP-...) resolved across "
                     "EHR MRNs, Medicare MBIs, payer member ids and demographics (see stg_person_map in raw tables).",
                     '', 'File-level delivery questions (late, resent, v2, cut-off, empty or missing files) use delivery_file '
                     '(one row per delivered file: manifest delivered date, sha256 and claimed records, parsed records, parse '
                     'issues) and delivery_gap (expected periods with no file). raw_ingest_files is not loaded: its mtime is '
                     'the local copy time, not the delivery time.',
                     '', 'Canonical records by stream:'] + [f'- {k}: {v}' for k, v in sorted((rep.get('streams') or {}).items())]
            result = catalog.build_docs(self.semantic_path, self.docs_dir, extra_sections={'Workspace data': '\n'.join(lines)})
            self._step('docs', files=result.get('files'), chars=result.get('chars'))
            return
        mapping = matcher.load(self.dir / 'mapping.json')
        gaps = self.review_items()
        lines = [f"Workspace {self.state['name']} ({self.slug}); source {self.summary()['source']}; data as of "
                 f"{self.state.get('as_of')}.", '', 'Entity -> source table:']
        lines += [f"- {e}: {v['source_table'] or 'not mapped (use raw_* tables)'}" for e, v in mapping['entities'].items()]
        if gaps:
            lines += ['', 'Open mapping questions (answers may need raw_* tables for these):']
            lines += [f"- {g['entity']}.{g.get('field') or '*'}: {g['reason']}" for g in gaps[:60]]
        result = catalog.build_docs(self.semantic_path, self.docs_dir, extra_sections={'Workspace mapping': '\n'.join(lines)})
        self._step('docs', files=result.get('files'), chars=result.get('chars'))

    def _do_pipeline(self, conn=None):
        """Native workspaces: prepare <slug>/pipeline for the full answer pipeline (clones + an empty evidence index, a few
        seconds). It annotates and never blocks: a failure is recorded and answers fall back to the hybrid core."""
        if not self.native:
            return
        from . import workspace_stack
        try:
            m = workspace_stack.prepare(self, force=True)
            detail = ({'supported': True, 'methods': m.get('methods'), 'patients': m.get('patients')} if m.get('supported')
                      else {'supported': False, 'reason': m.get('reason')})
        except Exception as exc:  # noqa: BLE001
            detail = {'supported': False, 'error': f'{type(exc).__name__}: {exc}'[:500]}
        self._step('pipeline', **detail)

    # --- review ----------------------------------------------------------------------------------------------------

    def _build_native(self):
        import shutil
        src = self.dir / 'stack' / 'semantic.sqlite3'
        if not src.is_file():
            raise RuntimeError('Staging did not produce stack/semantic.sqlite3')
        tmp = self.semantic_path.with_suffix('.tmp')
        shutil.copyfile(src, tmp)
        as_of = self.state.get('as_of') or json.loads((self.dir / 'stack' / 'run.json').read_text())['snapshot'].get('as_of')
        db = sqlite3.connect(tmp)
        try:
            db.execute("DELETE FROM meta WHERE key IN ('as_of', 'workspace')")
            db.executemany('INSERT INTO meta VALUES (?, ?)', [('as_of', as_of), ('workspace', self.slug)])
            db.commit()
            delivery_summary = self._delivery_tables(db)
            rows = {t: db.execute(f'SELECT count(*) FROM "{t}"').fetchone()[0] for (t,) in
                    db.execute("SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'").fetchall()}
        finally:
            db.close()
        tmp.replace(self.semantic_path)
        self._update(as_of=as_of)
        self._step('build', rows=rows, as_of=as_of, source='canonical staging', delivery=delivery_summary)

    def _delivery_tables(self, db):
        """delivery_file / delivery_gap (file-level delivery facts, ingest/delivery.py) into the semantic layer being built.
        Annotates and never blocks: a failure is recorded in the build step and the semantic layer is kept as staged."""
        from ..ingest import delivery
        try:
            summary = delivery.build(db, self.landing_path, self.raw_dir)
            db.execute("DELETE FROM meta WHERE key = 'delivery_file'")
            db.execute('INSERT INTO meta VALUES (?, ?)', ('delivery_file', json.dumps(summary, default=str)))
            db.commit()
            return summary
        except Exception as exc:  # noqa: BLE001 - delivery facts are an addition; the staged layer stands without them
            db.rollback()
            return {'error': f'{type(exc).__name__}: {exc}'[:500]}

    def review_items(self):
        """Mapping questions for a person, most important first. Each has a stable id used by decide()."""
        path = self.dir / 'mapping.json'
        if not path.is_file():
            return []
        mapping = matcher.load(path)
        decided = self.state.get('decisions', {})
        items = []
        for entity, e in mapping['entities'].items():
            if not e.get('source_table'):
                items.append({'id': f'{entity}.*', 'entity': entity, 'field': None, 'kind': 'table',
                              'reason': 'no source table matched this entity', 'priority': 1,
                              'candidates': [], 'decided': decided.get(f'{entity}.*')})
                continue
            if e.get('confidence', 1) < matcher.ACCEPT_THRESHOLD:
                items.append({'id': f'{entity}.*', 'entity': entity, 'field': None, 'kind': 'table',
                              'reason': f"table {e['source_table']} chosen with low confidence {e['confidence']}",
                              'priority': 2, 'current': e['source_table'], 'candidates': e.get('table_alternatives', []),
                              'decided': decided.get(f'{entity}.*')})
            for field, f in e['fields'].items():
                fid = f'{entity}.{field}'
                if f.get('accept'):
                    continue
                if f.get('source') is None and not f.get('required'):
                    continue  # an optional field the source does not have is not a question
                reason = ('required field has no source column' if f.get('source') is None
                          else f"column {f['source']} proposed with confidence {f['confidence']}")
                items.append({'id': fid, 'entity': entity, 'field': field, 'kind': 'field', 'reason': reason,
                              'priority': 1 if f.get('required') else 3, 'current': f.get('source'),
                              'source_table': e['source_table'], 'candidates': f.get('alternatives', []),
                              'decided': decided.get(fid)})
        items.sort(key=lambda i: (i['decided'] is not None, i['priority'], i['id']))
        return items

    def decide(self, item_id, decision, *, reviewer='person'):
        """decision: 'accept' | 'reject' | {'source': column} (field) | {'source_table': table} (entity)."""
        if decision not in ('accept', 'reject') and not (isinstance(decision, dict) and set(decision) <= {'source', 'source_table'}):
            raise ValueError("decision must be 'accept', 'reject', {'source': column} or {'source_table': table}")
        s = self.state
        s['decisions'][item_id] = {'decision': decision, 'reviewer': reviewer, 'at': _now()}
        self._save(s)
        self._event('decision', item=item_id, decision=decision, reviewer=reviewer)
        self._apply_decisions()
        return self.review_items()

    def _apply_decisions(self):
        path = self.dir / 'mapping.json'
        if not path.is_file():
            return
        mapping = matcher.load(path)
        prof_path = self.dir / 'profile.json'
        prof = json.loads(prof_path.read_text()) if prof_path.is_file() else {'tables': {}}
        for item_id, d in self.state.get('decisions', {}).items():
            entity, _, field = item_id.partition('.')
            e = mapping['entities'].get(entity)
            if e is None:
                continue
            dec = d['decision']
            if field == '*':
                if isinstance(dec, dict) and dec.get('source_table') in prof['tables']:
                    e['source_table'], e['status'], e['confidence'] = dec['source_table'], 'reviewed', 1.0
                elif dec == 'reject':
                    e['source_table'], e['status'], e['fields'] = None, 'rejected', {}
                elif dec == 'accept':
                    e['status'] = 'reviewed'
                continue
            f = e['fields'].get(field)
            if f is None:
                continue
            if dec == 'accept':
                f['accept'] = f.get('source') is not None
            elif dec == 'reject':
                f['accept'] = False
            elif isinstance(dec, dict) and dec.get('source'):
                cols = (prof['tables'].get(e.get('source_table') or '', {}) or {}).get('columns', {})
                if dec['source'] not in cols:
                    raise ValueError(f"{dec['source']} is not a column of {e.get('source_table')}")
                f.update(source=dec['source'], accept=True, confidence=1.0, evidence=['reviewer override'], transforms=[])
            f['reviewed'] = True
        if self.state.get('decisions'):
            mapping['approval']['approved'] = False  # decisions change the mapping: the next approve re-approves it
        matcher.save(mapping, path)

    # --- answering -------------------------------------------------------------------------------------------------

    def customer_ids(self, person_ids):
        """{Agere person id: the customer's own identifier} for a native workspace (identity map in landing.sqlite)."""
        if not self.native or not self.landing_path.is_file() or not person_ids:
            return {}
        db = sqlite3.connect(f'file:{self.landing_path}?mode=ro', uri=True)
        try:
            keys = {}
            ids = list(dict.fromkeys(person_ids))
            for i in range(0, len(ids), 500):
                chunk = ids[i:i + 500]
                q = 'SELECT person_id, source_key FROM stg_person_map WHERE person_id IN (' + ','.join('?' * len(chunk)) + ')'
                for pid, key in db.execute(q, chunk):
                    keys.setdefault(pid, []).append(key)
        finally:
            db.close()
        return {pid: native_id(k) for pid, k in keys.items() if native_id(k)}

    def pipeline_support(self):
        """-> (supported, reason): whether this workspace can answer through the full product pipeline."""
        from . import workspace_stack
        return workspace_stack.support(self)

    def ask(self, question, *, answer_type=None, send_factory=None, pipeline=None, live=True):
        """pipeline: 'full' (local compiler, governed metrics, analytic engines + hybrid core via together.compose_together),
        'hybrid' (the hybrid core alone), or None: 'full' when the workspace supports it, else 'hybrid'. An automatic 'full'
        that fails answers through the hybrid core (out['pipeline_fallback'] says why). live=False keeps the full pipeline
        on its local rungs with no hosted call (the hybrid core's sender refuses)."""
        if self.state['status'] != 'ready':
            raise RuntimeError(f"Workspace {self.slug} is {self.state['status']}; run it first")
        from ..plans.request import is_plan_request
        if is_plan_request(question, answer_type):
            # "create a plan / enroll / route" a cohort: cohort through this method, then the deterministic router
            return self.plan(question=question, send_factory=send_factory, live=live)
        if pipeline not in (None, 'full', 'hybrid'):
            raise ValueError("pipeline must be 'full', 'hybrid' or None")
        fallback = None
        if pipeline in (None, 'full'):
            ok, reason = self.pipeline_support()
            if not ok and pipeline == 'full':
                raise ValueError(f'Full pipeline unavailable on {self.slug}: {reason}')
            if ok:
                try:
                    out = self._ask_full(question, answer_type=answer_type, send_factory=send_factory, live=live)
                except Exception as exc:  # noqa: BLE001 - heal, don't block: the hybrid core still answers
                    if pipeline == 'full':
                        raise
                    fallback = {'from': 'full', 'reason': f'{type(exc).__name__}: {exc}'[:400]}
                else:
                    self._record(question, out)
                    return out
            else:
                fallback = {'from': 'full', 'reason': reason}
        if not live:
            raise ValueError('The hybrid core needs live models (live=False is only for the full pipeline)')
        out = self._ask_hybrid(question, answer_type=answer_type, send_factory=send_factory)
        out['pipeline'] = 'hybrid'
        if fallback:
            out['pipeline_fallback'] = fallback
        self._record(question, out)
        return out

    def plan(self, *, question=None, patient_ids=None, program_kind=None, as_of=None, gap_details=None,
             send_factory=None, live=True, narrative=False, act=True, through=None):
        """A routed enrollment plan (clinical_core/agere/plans): the cohort from patient_ids, else the plan request's cohort
        question answered through ask(..., answer_type='patient_set'), else the deterministic candidates for the program's
        gap; then the deterministic router over this workspace's semantic layer; then (act=True) the action backbone
        (plans/backbone.py): a durable decision and work items in <workspace>/action/program-work.sqlite3, steps driven by
        simulated-counterparty receipts, completion evaluated. Re-running the same plan is an idempotent replay.
        narrative=True drafts prose through the model only when a sender exists (send_factory, or ANTHROPIC_API_KEY for
        the ledgered sender)."""
        from ..plans import answer as plan_answer
        if not self.semantic_path.is_file():
            raise RuntimeError(f'Workspace {self.slug} has no semantic layer yet; run it first')
        if question is None and patient_ids is None and not program_kind:
            raise ValueError('question, patient_ids or program_kind is required')

        def cohort(q):
            return self.ask(q, answer_type='patient_set', send_factory=send_factory, live=live)
        sender = send_factory
        if narrative and sender is None and os.environ.get('ANTHROPIC_API_KEY'):
            sender = ledgered_send_factory(self.dir / 'receipts')
        out = plan_answer.build(self.semantic_path, question, patient_ids=patient_ids, program_kind=program_kind,
                                as_of=as_of or self.state.get('as_of') or None, answer_type='plan',
                                cohort=cohort if question and self.state['status'] == 'ready' else None,
                                gap_details=gap_details, narrative=narrative, send_factory=sender,
                                action_store=self.dir if act else None, workspace=self.slug, through=through)
        env = plan_answer.envelope(out, pipeline='plan')
        self._record(question or f"plan:{env['plan']['program_kind']} for {len(env['cohort_patient_ids'])} patients", env)
        return env

    def _record(self, question, out):
        ids = (out.get('answer') or {}).get('patient_ids') if isinstance(out.get('answer'), dict) else None
        if ids and self.native:
            out['customer_ids'] = self.customer_ids(ids)  # the customer's own identifiers (MRN, member id, EHR id)
        record = {'at': _now(), 'question': question, 'pipeline': out.get('pipeline'), 'decision': out['decision'],
                  'answer': out['answer'], 'cost_usd': out['cost_usd'], 'interpretation': out['interpretation']}
        with open(self.dir / 'questions.jsonl', 'a') as f:
            f.write(json.dumps(record, default=str) + '\n')

    def _ask_full(self, question, *, answer_type=None, send_factory=None, live=True):
        from . import workspace_stack
        from ..clinical import together
        # no send_factory: live stacks meter the hybrid core through together's ledgered sender (same ledger, receipts under
        # pipeline/provider-receipts); offline stacks refuse every hosted call
        stack = workspace_stack.stack_for(self, allow_live=live, send_factory=send_factory)
        return workspace_stack.envelope(together.compose_together(stack, question, answer_type=answer_type), answer_type=answer_type)

    def _ask_hybrid(self, question, *, answer_type=None, send_factory=None):
        from ..clinical.semantic_analyst import SemanticWarehouse
        from ..hybrid import live
        wh = SemanticWarehouse(self.semantic_path, timeout_s=120)
        try:
            out = live.answer(question, self.state['as_of'], wh, send_factory or ledgered_send_factory(self.dir / 'receipts'),
                              answer_type=answer_type, docs_dir=self.docs_dir)
        finally:
            wh.close()
        return out


NATIVE_ID_ORDER = ('epic', 'athena', 'ecw', 'fhir', 'mrn', 'hl7', 'mbi', 'member')


def native_id(source_keys):
    """The customer's own identifier for a person, from Agere's identity map keys (epic:/athena:/ecw:/fhir: as they are;
    a practice MRN or HL7 PID-3 as mrn:<value>; an MBI or payer member id as member:<value>)."""
    by_kind = {}
    for k in source_keys or []:
        kind, _, rest = str(k).partition(':')
        by_kind.setdefault(kind, []).append(rest)
    for kind in NATIVE_ID_ORDER:
        for rest in sorted(by_kind.get(kind, [])):
            if kind in ('epic', 'athena', 'ecw', 'fhir'):
                return f'{kind}:{rest}'
            if kind in ('mrn', 'hl7'):
                return 'mrn:' + rest.split(':', 1)[-1]
            if kind in ('mbi', 'member'):
                return 'member:' + rest.split(':', 1)[-1]
    return None


def _answer_record(qid, out, answer_type=None, id_map=None):
    """One answers-file row in the shared submission shape: {id, status, answer fields, interpretation, ...}.
    id_map (person id -> customer identifier) rewrites patient lists into the customer's own identifiers."""
    a = out.get('answer')
    row = {'id': qid, 'answer_type': out.get('answer_type') or answer_type, 'status': 'answered' if a else 'abstain',
           'interpretation': out.get('interpretation'), 'cost_usd': out.get('cost_usd'), 'decision': out.get('decision')}
    decline = out.get('decline') or {}
    status = (a or {}).get('status') if isinstance(a, dict) else None
    if decline.get('kind') == 'clarify' or status == 'clarify':
        return row | {'status': 'clarify', 'readings': decline.get('readings') or (a or {}).get('readings') or []}
    if decline.get('kind') == 'unsupported' or status == 'unsupported':
        return row | {'status': 'unsupported', 'missing': decline.get('missing') or (a or {}).get('missing') or []}
    if a:
        if 'patient_ids' in a:
            ids = a['patient_ids']
            row['patient_ids'] = [id_map.get(i) or i for i in ids] if id_map else ids
        elif 'table' in a:
            row['table'] = [{'key': ' -> '.join(str(k) for k in r['keys']), 'value': r['value']} for r in a['table']]
        else:
            row.update({k: v for k, v in a.items() if k in ('count', 'value', 'rows')})
    return row


def ask_many(ws, questions, *, workers=3, send_factory=None, out_path=None, on_usage=None, native_ids=False, pipeline=None):
    """Answer a list of {id, question, answer_type?} against one workspace. Writes answers.jsonl (submission shape)
    and returns the rows. A question that fails records its error instead of stopping the batch."""
    from concurrent.futures import ThreadPoolExecutor

    def one(q):
        try:
            t0 = __import__('time').time()
            kw = {'pipeline': pipeline} if pipeline else {}
            out = ws.ask(q['question'], answer_type=q.get('answer_type'), send_factory=send_factory, **kw)
            seconds = round(__import__('time').time() - t0, 2)
            if on_usage:
                on_usage(ws.slug, {'question': q['question'][:300], 'cost_usd': out.get('cost_usd'),
                                   'seconds': round(__import__('time').time() - t0, 2), 'decision': out.get('decision')})
            ids = ((out.get('answer') or {}).get('patient_ids') or []) if native_ids else []
            rec = _answer_record(q['id'], out, q.get('answer_type'), ws.customer_ids(ids) if ids else None)
            return {**rec, 'seconds': seconds, 'pipeline': out.get('pipeline'), 'route': out.get('interpretation', '')[:200]}
        except Exception as exc:  # noqa: BLE001
            return {'id': q['id'], 'status': 'abstain', 'error': f'{type(exc).__name__}: {exc}'[:300]}
    with ThreadPoolExecutor(max_workers=workers) as pool:
        rows = list(pool.map(one, questions))
    out_path = Path(out_path or ws.dir / f"answers-{datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ')}.jsonl")
    out_path.write_text(''.join(json.dumps(r, default=str) + '\n' for r in rows))
    return rows


def ledgered_send_factory(receipts_dir):
    """The product's metered sender (budget ledger, response cache, receipts), one ledger connection per thread."""
    from ..gateway import BudgetLedger
    from ..clinical.context_student import send as ledgered
    from ..clinical import together
    receipts_dir = Path(receipts_dir)
    receipts_dir.mkdir(parents=True, exist_ok=True)

    @contextmanager
    def factory():
        ledger = BudgetLedger(LEDGER)
        try:
            def send(body):
                raw, receipt = ledgered(body, 'claude', ledger, receipts_dir, parse=lambda r: r,
                                        timeout=together.SEND_TIMEOUT_S, transport=together.TRANSPORT,
                                        max_bytes=together.REQUEST_MAX_BYTES)
                if receipt.get('cache_hit'):
                    return raw, 0.0, True
                return raw, (receipt.get('cost_estimate') or {}).get('estimated_usd'), False
            yield send
        finally:
            ledger.close()
    return factory
