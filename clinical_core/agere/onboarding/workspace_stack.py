"""The full Agere answer pipeline on a customer workspace: local compiler, governed metrics, the analytic engines and the
hybrid core, reconciled by together.compose_together -- the same path the demo build answers through.

    stack = stack_for(ws)                      # a Stack over <ws>/pipeline (prepared at build time, or here if missing)
    out = together.compose_together(stack, q)  # what Workspace.ask(q, pipeline='full') does

What a product Stack needs and how a workspace gets it (docs/agere/WORKSPACE_FULL_PIPELINE.md has the per-engine table):

- run.json            snapshot {release, as_of}, source_digest, source_run      <- the native staging run (stack/run.json)
- stack.sqlite3       patients + records (+ result_cache / analyses / proposals) <- APFS clone of stack/stack.sqlite3
- semantic.sqlite3    semantic layer at semantic_layer.VERSION, same digest       <- APFS clone of <ws>/semantic.sqlite3
- stream-records.sqlite3  stream-clustered record copy (optional, speed only)     <- APFS clone of stack/stream-records.sqlite3
- source-profile.json local compiler's field profile                             <- copied (rebuilt from stack.sqlite3 if stale)
- annotation-link.json + evidence/   the narrative evidence index every graph opens; a workspace has no machine annotations,
                      so this is an EMPTY index over the workspace's patients: evidence predicates decline as unmapped
- provider-receipts/, question-plans.sqlite3 (created on first plan), operators.json

- passages.sqlite3     incremental passage index (clinical/workspace_passages.py): FTS5 over every record, MiniLM vectors
                      computed lazily for narrative windows. Built at prepare time (an unchanged store costs one count),
                      re-synced when the record store changes. 'retrieve' nodes and Stack.review search it.

The retriever carries the snapshot meta, the local MiniLM encoder (engine-lean router, semantic cache, passage vectors;
free) and the passage index. Without the encoder, passage search is lexical only.

Files are cloned with clonefile(2) (copy-on-write, no extra space, the workspace's own files are never opened for writing).
Where cloning is unavailable they are copied. The pipeline folder is rebuilt when a source file changes (size / mtime).

Warehouse workspaces (onboarding/apply.py) have no record store (stack.sqlite3) and a semantic layer outside the product
schema version; the engines and the compiler need both, so those workspaces answer through the hybrid core only.
"""
from __future__ import annotations

from contextlib import contextmanager
import copy
import ctypes
import ctypes.util
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import shutil
import sqlite3
import threading

SCHEMA = 'agere-workspace-pipeline/1'
DIRNAME = 'pipeline'
EVIDENCE_SCHEMA = 'evidence-query/1'
EMPTY_EVIDENCE_IDENTITY = 'workspace-empty-evidence/1'
_STACKS = {}
_GUARD = threading.Lock()


def _now():
    return datetime.now(timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ')


def _dir(ws):
    return Path(getattr(ws, 'dir', ws)).resolve()


def sources(ws):
    """The workspace files the pipeline folder is derived from."""
    d = _dir(ws)
    return {'stack.sqlite3': d / 'stack' / 'stack.sqlite3', 'run.json': d / 'stack' / 'run.json',
            'semantic.sqlite3': d / 'semantic.sqlite3', 'stream-records.sqlite3': d / 'stack' / 'stream-records.sqlite3',
            'source-profile.json': d / 'stack' / 'source-profile.json'}


def support(ws):
    """-> (True, None) when the full pipeline can run on this workspace, else (False, reason naming the missing input)."""
    src = sources(ws)
    if not src['stack.sqlite3'].is_file() or not src['run.json'].is_file():
        return False, ('no record store (stack/stack.sqlite3 + stack/run.json): the analytic engines and the local compiler '
                       'read the canonical records, which only native (staged) workspaces have')
    if not src['semantic.sqlite3'].is_file():
        return False, 'no semantic layer (semantic.sqlite3): run the workspace build first'
    from ..clinical.semantic_layer import VERSION
    try:
        run = json.loads(src['run.json'].read_text())
        db = sqlite3.connect(f"file:{src['semantic.sqlite3']}?mode=ro", uri=True)
        try:
            meta = dict(db.execute('SELECT key, value FROM meta').fetchall())
        finally:
            db.close()
    except (OSError, ValueError, sqlite3.Error) as exc:
        return False, f'unreadable workspace build: {type(exc).__name__}: {exc}'[:300]
    if meta.get('version') != VERSION:
        return False, f"semantic layer version {meta.get('version')!r} is not {VERSION!r} (the engines' schema)"
    if meta.get('source_digest') != run.get('source_digest'):
        return False, 'semantic layer and record store come from different deliveries (source_digest differs)'
    if not (run.get('snapshot') or {}).get('as_of'):
        return False, 'record store run.json has no snapshot as_of'
    return True, None


# --- building the pipeline folder -------------------------------------------------------------------------------------------

def _clonefile(src, dst):
    """Copy-on-write clone (macOS APFS clonefile(2)); False when the platform or volume cannot clone."""
    try:
        libc = ctypes.CDLL(ctypes.util.find_library('c'), use_errno=True)
        fn = libc.clonefile
    except (OSError, AttributeError, TypeError):
        return False
    fn.argtypes, fn.restype = [ctypes.c_char_p, ctypes.c_char_p, ctypes.c_int], ctypes.c_int
    return fn(os.fsencode(str(src)), os.fsencode(str(dst)), 0) == 0


def _fingerprint(p):
    st = p.stat()
    return {'path': str(p), 'size': st.st_size, 'mtime_ns': st.st_mtime_ns}


def _place(src, dst, *, allow_copy=True):
    """Clone (else copy) src to dst. Returns the method used. Never writes to src."""
    for extra in ('', '-wal', '-shm'):
        Path(str(dst) + extra).unlink(missing_ok=True)
    tmp = dst.with_name(dst.name + '.part')
    tmp.unlink(missing_ok=True)
    if _clonefile(src, tmp):
        method = 'clone'
    elif allow_copy:
        shutil.copyfile(src, tmp)
        method = 'copy'
    else:
        raise OSError(f'cannot clone {src}')
    tmp.replace(dst)
    return method


def _empty_evidence(out, run, patients):
    """An evidence index with the workspace's patient universe and no annotations (graphs open one unconditionally)."""
    ev = out / 'evidence'
    ev.mkdir(exist_ok=True)
    db_path = ev / 'evidence.sqlite3'
    db_path.unlink(missing_ok=True)
    db = sqlite3.connect(db_path)
    try:
        db.executescript('''CREATE TABLE patients(id TEXT PRIMARY KEY);
          CREATE TABLE evidence(id TEXT PRIMARY KEY,patient TEXT,kind TEXT,concept TEXT,assertion TEXT,subject TEXT,time TEXT,
                                relation TEXT,value_json TEXT,payload TEXT);
          CREATE TABLE query_cache(id TEXT PRIMARY KEY,payload TEXT);
          CREATE INDEX patient_evidence ON evidence(patient,kind,concept);
          CREATE INDEX concept_evidence ON evidence(kind,concept,patient);''')
        db.executemany('INSERT INTO patients VALUES (?)', ((p,) for p in patients))
        db.commit()
    finally:
        db.close()
    meta = {'schema': EVIDENCE_SCHEMA, 'synthetic': True, 'source_run': run['source_run'], 'snapshot': run['snapshot'],
            'model_identity': EMPTY_EVIDENCE_IDENTITY, 'counts': {'patients': len(patients), 'structured': 0, 'text': 0},
            'new_paid_api_calls': 0,
            'limitations': ['Workspace build: no machine narrative annotations; evidence predicates are unmapped, never negative']}
    (ev / 'run.json').write_text(json.dumps(meta, indent=1))
    (out / 'annotation-link.json').write_text(json.dumps(
        {'path': str(ev.resolve()), 'model_identity': EMPTY_EVIDENCE_IDENTITY, 'source_run': run['source_run'],
         'authority': 'Empty evidence index: this workspace carries no machine narrative annotations'}, indent=1))


def _stale(out, src):
    manifest = out / 'pipeline.json'
    if not manifest.is_file():
        return True
    try:
        saved = json.loads(manifest.read_text())
    except ValueError:
        return True
    if saved.get('schema') != SCHEMA:
        return True
    for name, p in src.items():
        want = saved.get('sources', {}).get(name)
        have = _fingerprint(p) if p.is_file() else None
        if (want or None) != have:
            return True
    return not all((out / n).exists() for n in ('run.json', 'stack.sqlite3', 'semantic.sqlite3', 'annotation-link.json'))


def prepare(ws, *, force=False, allow_copy=True):
    """Build <ws>/pipeline (cheap: clones, an empty evidence index, small JSON). Idempotent; rebuilt when sources change.
    -> manifest dict, or {'supported': False, 'reason': ...} for a workspace the pipeline cannot run on."""
    ok, reason = support(ws)
    if not ok:
        return {'schema': SCHEMA, 'supported': False, 'reason': reason}
    out = _dir(ws) / DIRNAME
    src = sources(ws)
    if not force and not _stale(out, src):
        return json.loads((out / 'pipeline.json').read_text())
    out.mkdir(exist_ok=True)
    (out / 'pipeline.json').unlink(missing_ok=True)  # incomplete until rewritten
    native = json.loads(src['run.json'].read_text())
    methods = {}
    for name in ('stack.sqlite3', 'semantic.sqlite3', 'stream-records.sqlite3'):
        if src[name].is_file():
            methods[name] = _place(src[name], out / name, allow_copy=allow_copy)
    run = {'schema': SCHEMA, 'synthetic': True, 'workspace': _dir(ws).name, 'source_digest': native['source_digest'],
           # the native run has no retrieval build id; the delivery digest identifies the evidence/semantic pairing
           'source_run': native.get('source_run') or native['source_digest'], 'snapshot': native['snapshot'],
           'patients': native.get('patients'), 'visible_records': native.get('visible_records'),
           'retrieval': 'passages.sqlite3: incremental FTS5 passage index, lazily embedded MiniLM vectors for narrative windows',
           'built_at': _now()}
    (out / 'run.json').write_text(json.dumps(run, indent=1))
    db = sqlite3.connect(out / 'stack.sqlite3', timeout=30)
    try:
        db.executescript('''CREATE TABLE IF NOT EXISTS result_cache(id TEXT PRIMARY KEY,payload TEXT);
          CREATE TABLE IF NOT EXISTS analyses(patient TEXT,operator TEXT,payload TEXT,PRIMARY KEY(patient,operator));
          CREATE TABLE IF NOT EXISTS proposals(id TEXT PRIMARY KEY,payload TEXT);''')
        db.commit()
        patients = [r[0] for r in db.execute('SELECT id FROM patients ORDER BY id')]
    finally:
        db.close()
    from ..clinical.warehouse_concurrency import configure
    concurrency = configure(out)  # WAL + the stream index, on the clone
    if src['source-profile.json'].is_file():
        shutil.copyfile(src['source-profile.json'], out / 'source-profile.json')
    from ..clinical import source_profile
    source_profile._CACHE.pop((str(out), run['source_digest']), None)
    source_profile.load(out)  # validates the copy (or rebuilds it from stack.sqlite3)
    _empty_evidence(out, run, patients)
    from ..clinical import workspace_passages
    try:
        passages = workspace_passages.update(out)
    except Exception as exc:  # noqa: BLE001 - the index is rebuilt lazily on first search; never fail the build
        passages = {'status': 'deferred', 'reason': f'{type(exc).__name__}: {exc}'[:300]}
    from ..clinical import record_linkage
    try:  # linked identities: what the workspace's duplicate-patient questions and enterprise person ids read
        scored = record_linkage.build(out, force=True)
        links = {'status': 'built', 'charts': scored['charts'], 'candidate_pairs': scored['candidate_pairs'],
                 'persons_with_duplicates': len(set(record_linkage.decide(scored)[0].values())), 'file': record_linkage.CACHE}
    except Exception as exc:  # noqa: BLE001 - annotates, never blocks the build
        links = {'status': 'deferred', 'reason': f'{type(exc).__name__}: {exc}'[:300]}
    from ..clinical import med_graph_rag
    try:  # concept graph: the fallback retriever the measured reranker/graph policy reads (warehouse_retrieval.Retriever)
        graph = {'status': 'built', **{k: v for k, v in med_graph_rag.build(out).items() if k in ('nodes', 'edges', 'links')}, 'file': med_graph_rag.FILE}
    except Exception as exc:  # noqa: BLE001 - annotates, never blocks the build
        graph = {'status': 'deferred', 'reason': f'{type(exc).__name__}: {exc}'[:300]}
    from ..clinical import derived_value_index
    try:  # derived values the semantic layer computes or the tables imply, with their recipes (derived_value engine, discover())
        derived = {'status': 'built', **{k: v for k, v in derived_value_index.build(out).items() if k in ('entries', 'with_support', 'by_kind')}, 'file': derived_value_index.FILE}
    except Exception as exc:  # noqa: BLE001 - annotates, never blocks the build
        derived = {'status': 'deferred', 'reason': f'{type(exc).__name__}: {exc}'[:300]}
    from ..clinical.analysis_graph import OPERATIONS
    (out / 'operators.json').write_text(json.dumps(sorted(OPERATIONS)))
    (out / 'provider-receipts').mkdir(exist_ok=True)
    manifest = {'schema': SCHEMA, 'supported': True, 'built_at': _now(), 'path': str(out), 'methods': methods,
                'database_concurrency': concurrency, 'patients': len(patients), 'passage_index': passages, 'person_links': links, 'concept_graph': graph, 'derived_values': derived,
                'sources': {n: _fingerprint(p) for n, p in src.items() if p.is_file()},
                'absent': {'narrative evidence annotations': 'evidence nodes decline',
                           'forecast / longitudinal / clinical-state links': 'nodes needing them decline'}}
    (out / 'pipeline.json').write_text(json.dumps(manifest, indent=1))
    with _GUARD:
        for key in [k for k in _STACKS if k[0] == str(out)]:
            _STACKS.pop(key).close()
    return manifest


# --- the Stack ----------------------------------------------------------------------------------------------------------------

class WorkspaceRetriever:
    """The parts of warehouse_retrieval.Retriever the planner and engines read: meta (snapshot, digests), the local encoder,
    a read connection and passage search over the workspace's incremental passage index (opened on first search)."""
    reranker = None
    annotation_db = None

    def __init__(self, path, *, encoder=True):
        self.path = Path(path)
        self.meta = json.loads((self.path / 'run.json').read_text())
        self.db = sqlite3.connect(f"file:{self.path / 'stack.sqlite3'}?mode=ro", uri=True, check_same_thread=False)
        self.encoder = None
        self.passages = None
        self._passage_lock = threading.Lock()
        if encoder:
            try:
                from ...local_encoders import LocalEncoder
                from ..clinical.stack_runtime import ROOT
                model = ROOT / 'models' / 'minilm-l6-v2'
                if model.is_dir():
                    self.encoder = LocalEncoder(model, pooling='mean', cache=self.path / 'model-cache.sqlite3')
                    self.encoder.db = sqlite3.connect(self.path / 'model-cache.sqlite3', check_same_thread=False)
                    self.meta['encoder_path'] = str(model)
                    self.meta['encoder_identity'] = self.encoder.identity
            except Exception:  # noqa: BLE001 - the encoder only helps routing; its absence is a declined rung
                self.encoder = None

    def search(self, question, *, patient=None, limit=8, patient_ids=None, analysis_window=None):
        with self._passage_lock:
            if self.passages is None:
                from ..clinical.workspace_passages import PassageIndex
                self.passages = PassageIndex(self.path, encoder=self.encoder, reranker=self.reranker, meta=self.meta)
        return self.passages.search(question, patient=patient, limit=limit, patient_ids=patient_ids,
                                    analysis_window=analysis_window)

    def close(self):
        self.db.close()
        if self.passages is not None:
            self.passages.close()
        if self.encoder is not None:
            try:
                self.encoder.close()
            except Exception:  # noqa: BLE001
                pass


def _offline_send_factory():
    @contextmanager
    def factory():
        def send(body):
            raise RuntimeError('live models are off for this workspace stack (no hosted calls)')
        yield send
    return factory


def _decline_reason(op, exc):
    """Name what the engine needed that this workspace build lacks (or say it could not run on this data)."""
    msg = f'{type(exc).__name__}: {exc}'
    issues = getattr(exc, 'issues', None)  # grounding.GroundingError: concepts with no received records
    if isinstance(issues, list) and issues:
        details = '; '.join(str(i.get('detail') or i.get('issue')) for i in issues if isinstance(i, dict) and i.get('severity') == 'error')
        return f'Missing input for {op}: {details or msg}'[:480]
    if isinstance(exc, sqlite3.OperationalError) and 'no such' in str(exc):
        return f'Missing input for {op}: {exc} (not in this workspace build)'
    if isinstance(exc, FileNotFoundError):
        return f'Missing input for {op}: {Path(getattr(exc, "filename", "") or str(exc)).name} (not in this workspace build)'
    if str(exc).startswith('Missing input'):
        return f'{exc} [{op}]'
    if isinstance(exc, ValueError) and any(w in str(exc).lower() for w in ('no ', 'not attached', 'missing', 'unmapped', 'disabled')):
        return f'Missing input for {op}: {exc}'[:480]
    return f'Not computable for {op} on this workspace\'s data ({msg})'[:480]


def _stack_class():
    from ..clinical.stack_runtime import Stack

    class WorkspaceStack(Stack):
        """A product Stack over a workspace's pipeline folder (see module doc). allow_live=False: no hosted call anywhere
        (the planner stays on its local rungs and the hybrid core's sender refuses)."""

        def __init__(self, path, *, allow_live=False, encoder=True, docs_dir=None, ledger_path=None, send_factory=None):
            from ..clinical.warehouse_concurrency import configure
            self.path = Path(path)
            self.database_concurrency = configure(self.path)
            self.retriever = WorkspaceRetriever(self.path, encoder=encoder)
            self.allow_live = allow_live
            self.receipts = self.path / 'provider-receipts'
            self.receipts.mkdir(exist_ok=True)
            self.docs_dir = Path(docs_dir) if docs_dir else None
            self.ledger_path = Path(ledger_path) if ledger_path else None
            # together._run_hybrid uses stack.send_factory when present (the service Stack has none)
            self.send_factory = send_factory or (None if allow_live else _offline_send_factory())
            if self.retriever.encoder is not None:
                from ..clinical import schema_rerank
                schema_rerank.use_encoder(self.retriever.encoder)

        def run_graph(self, plan, *, question=None):
            """Engines that cannot run on this workspace decline as unsupported with the missing input named."""
            try:
                return super().run_graph(plan, question=question)
            except Exception as exc:  # noqa: BLE001 - a missing input is an unsupported answer, never a crash
                ops = [n.get('operation') for n in (plan or {}).get('nodes') or [] if isinstance(n, dict)]
                op = next((o for o in reversed(ops) if o not in ('cohort', 'intersect', 'union', 'difference')), None) or 'graph'
                reason = _decline_reason(op, exc)
                declined = {'interpretation': f'Unsupported on this workspace: {question or plan.get("interpretation")}',
                            'coverage': 'unsupported', 'nodes': [], 'unmet_requirements': [reason[:480]]}
                result = super().run_graph(declined, question=question)
                result['workspace_decline'] = {'operation': op, 'operations': ops, 'missing': reason,
                                               'error_type': type(exc).__name__, 'declined_graph': plan}
                return result

    return WorkspaceStack


def stack_for(ws, *, allow_live=False, encoder=True, prepare_missing=True, send_factory=None):
    """A product Stack over the workspace's pipeline folder (prepared here when missing or stale), or raise ValueError
    naming why the workspace cannot run the full pipeline. One Stack is cached per folder build and liveness; a
    send_factory (the hybrid core's sender) gets a shallow per-call view sharing the cached retriever."""
    ok, reason = support(ws)
    if not ok:
        raise ValueError(f'Full pipeline unavailable: {reason}')
    out = _dir(ws) / DIRNAME
    if prepare_missing:
        manifest = prepare(ws)
    elif _stale(out, sources(ws)):
        raise ValueError('Full pipeline unavailable: the pipeline folder is missing or stale (prepare it first)')
    else:
        manifest = json.loads((out / 'pipeline.json').read_text())
    key = (str(out), bool(allow_live), bool(encoder), manifest.get('built_at'))
    with _GUARD:
        stack = _STACKS.get(key)
        if stack is None:
            from ..gateway import default_ledger_path
            for old in [k for k in _STACKS if k[:3] == key[:3]]:
                _STACKS.pop(old).close()
            stack = _STACKS[key] = _stack_class()(out, allow_live=allow_live, encoder=encoder,
                                                  docs_dir=_dir(ws) / 'analyst_docs', ledger_path=default_ledger_path())
    if send_factory is not None:
        view = copy.copy(stack)
        view.send_factory = send_factory
        return view
    return stack


# --- the workspace answer envelope ---------------------------------------------------------------------------------------------

def _table_rows(table):
    rows = []
    for r in table or []:
        if not isinstance(r, dict):
            rows.append({'keys': [str(r)], 'value': None})
            continue
        if 'value' in r:
            value = r['value']
            keys = [v for k, v in r.items() if k != 'value']
        else:
            items = list(r.items())
            num = [k for k, v in items if isinstance(v, (int, float)) and not isinstance(v, bool)]
            vk = num[-1] if num else (items[-1][0] if items else None)
            value = r.get(vk)
            keys = [v for k, v in items if k != vk]
        rows.append({'keys': [str(k) for k in keys], 'value': value})
    return rows


def envelope(result, *, answer_type=None):
    """compose_together's result in the workspace answer shape (the hybrid core's keys: answer {count|value|patient_ids|
    table[{keys, value}]} or None, decision, interpretation, cost_usd, answer_type) plus the full-pipeline receipts.
    A plan request's result (a 'plan' block from together._compose_plan) becomes answer_type 'plan': answer.patient_ids =
    the patients the plan acts on, plan = the routed plan, and the cohort step's receipts stay under 'result'."""
    if isinstance(result.get('plan'), dict):
        from ..plans import answer as plan_answer
        cohort = envelope({k: v for k, v in result.items() if k != 'plan'}, answer_type='patient_set') if result.get('answer') else {}
        return plan_answer.envelope({'plan': result['plan']}, pipeline='full', base=cohort)
    a = result.get('answer') if isinstance(result.get('answer'), dict) else {}
    graph = result.get('graph') or {}
    ops = [n.get('operation') for n in graph.get('nodes') or [] if isinstance(n, dict)]
    status = a.get('status')
    decline = result.get('workspace_decline')
    answer = None
    want = answer_type or a.get('answer_type')
    if status in ('answered', 'partial') and want == 'patient_set' and isinstance(a.get('patient_ids'), list):
        answer = {'patient_ids': a['patient_ids']}  # a patient list was asked for: return it, not just its size
    elif status in ('answered', 'partial') and want == 'table' and isinstance(a.get('table'), list) and a['table']:
        answer = {'table': _table_rows(a['table'])}
    elif status in ('answered', 'partial'):
        if a.get('count') is not None:
            answer = {'count': a['count']}
        elif a.get('value') is not None and not isinstance(a.get('value'), (dict, list)):
            answer = {'value': a['value']}
        elif isinstance(a.get('patient_ids'), list):
            answer = {'patient_ids': a['patient_ids']}
        elif isinstance(a.get('table'), list) and a['table']:
            answer = {'table': _table_rows(a['table'])}
        elif a.get('headline'):
            answer = {'value': a['headline']}
    together = result.get('together') or {}
    econ = result.get('planning_economics') or {}
    receipt = result.get('planner_receipt') or {}
    missing = list(a.get('missing') or [])
    interpretation = f"full pipeline ({receipt.get('route') or result.get('route')}; {', '.join(ops) or 'no operation'}"
    interpretation += f"; {together.get('decision')})" if together.get('decision') else ')'
    if status not in ('answered', 'partial'):
        interpretation += ': ' + (a.get('headline') or '; '.join(missing) or 'no answer')[:400]
    return {'pipeline': 'full', 'answer': answer, 'answer_type': a.get('answer_type') or answer_type,
            'status': status, 'headline': a.get('headline'), 'confidence': a.get('confidence'),
            'action_context': result.get('action_context'), 'context_error': result.get('context_error'),
            # certified cascade (clinical/cascade.py): the free tier that answered and its error bound; None = not certified
            'certified': result.get('certified'),
            'decision': together.get('decision') or receipt.get('route'), 'interpretation': interpretation,
            'cost_usd': econ.get('new_api_estimate_usd'), 'new_paid_api_calls': result.get('new_paid_api_calls'),
            'route': receipt.get('route') or result.get('route'), 'operations': ops, 'missing': missing,
            'workspace_decline': {k: v for k, v in (decline or {}).items() if k != 'declined_graph'} or None,
            'decline': ({'kind': status, 'readings': a.get('readings') or [], 'missing': missing}
                        if status in ('clarify', 'unsupported') else None),
            'result': {k: result.get(k) for k in ('answer', 'graph', 'route', 'together', 'planning_economics',
                                                   'new_paid_api_calls')}}
