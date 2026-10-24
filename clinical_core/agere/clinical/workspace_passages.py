"""Incremental, memory-bounded passage index for connected workspaces (the 'retrieve' node's input).

    update(pipeline_dir)                        # sync <pipeline>/passages.sqlite3 with stack.sqlite3 records (FTS5 only)
    index = PassageIndex(pipeline_dir, encoder=minilm, meta=run_meta)
    index.search(question, patient=None, limit=8, patient_ids=None, analysis_window=None)   # warehouse_retrieval shape

- Every record gets lexical coverage: a compact structured rendering (stream + scalar fields, no ids/dates/lineage) and
  its narrative fields (narrative_sources.text_fields plus OCR/extracted text), cut into character windows that keep
  source offsets. Identical window texts are stored once (contents); FTS5 is an external-content index over them.
- Updates are incremental: records are read in rowid pages, a payload hash decides whether a record is re-indexed,
  removed records lose their passages and orphan contents are collected. Memory is one page, never the whole store.
- Dense vectors (MiniLM) are computed lazily for narrative windows only: lexical candidates are embedded at query time
  (bounded), embed_pending() fills the rest in batches, and the dense scan streams stored vectors in chunks.
- Scores pick passages for inspection; they are relevance, not correctness. No hosted call anywhere.
"""
from __future__ import annotations

from collections import Counter
import hashlib
import heapq
import json
from pathlib import Path
import re
import sqlite3
import threading
import time

import numpy as np

from ...contracts import digest

VERSION = 'workspace-passage-index/1'
DB_NAME = 'passages.sqlite3'
WINDOW, OVERLAP = 700, 100            # characters; 700 chars stays well under MiniLM's 512-token limit
PAGE = 2000                            # records per update page
STRUCTURED_MAX = 2000                  # characters of structured rendering per record
LAZY_EMBED = 64                        # lexical candidates embedded per query
SCAN_CHUNK = 4096                      # stored vectors per dense-scan chunk
DIM = 384
NARRATIVE_EXTRA = ('ocr_text', 'extracted_text', 'document_text', 'note_text', 'report_text')
_SKIP_SUFFIX = ('_id', '_ids', '_time', '_date', '_hash', '_precision', '_sha256', '_digest')
_SKIP = {'record_id', 'patient_id', 'source_table', 'synthetic', 'identity_status', 'identity_link_basis',
         'source_patient_id', 'supersedes_record_id', 'content_hash', 'relative_path', 'text_lines', 'source_system',
         'organization_id'}
_STOP = set('a an and are as at be by for from has have how in is it of on or that the this to was were what which who '
            'with did does do any all many much patients patient show list find'.split())
ALIASES = {'kidney': ['renal', 'ckd', 'egfr', 'creatinine', 'nephrology'],
           'heart failure': ['hfref', 'chf', 'ejection fraction'],
           'financial': ['afford', 'bills', 'cost', 'assistance'],
           'beta blocker': ['metoprolol', 'carvedilol', 'bisoprolol'],
           'sglt2': ['empagliflozin', 'dapagliflozin']}


def windows(text, size=WINDOW, overlap=OVERLAP):
    """[(start, end)] character windows covering text, cut at whitespace where possible."""
    n = len(text)
    if n <= size: return [(0, n)] if text.strip() else []
    out, a = [], 0
    while a < n:
        b = min(a + size, n)
        if b < n:
            cut = max(text.rfind(' ', a + size // 2, b), text.rfind('\n', a + size // 2, b))
            if cut > a: b = cut
        out.append((a, b))
        if b >= n: break
        a = max(b - overlap, a + 1)
    return out


def _scalar_fields(r):
    for k in sorted(r):
        v = r[k]
        if k.startswith('_') or k in _SKIP or k.endswith(_SKIP_SUFFIX) or k in NARRATIVE_EXTRA: continue
        if isinstance(v, bool) or v is None or v == '': continue
        if isinstance(v, (int, float)): yield k, v
        elif isinstance(v, str) and len(v) <= 200: yield k, v
        elif isinstance(v, list) and v and all(isinstance(x, (str, int, float)) for x in v[:20]): yield k, ', '.join(map(str, v[:20]))


def parts(r, extracted=None):
    """Searchable parts of one record: [{'field','kind','text','dense'}]. Structured rendering is lexical only."""
    stream = r.get('source_table') or r.get('stream') or ''
    out, seen = [], set()
    rendering = ' '.join(f'{k}={v}' for k, v in _scalar_fields(r))[:STRUCTURED_MAX]
    if rendering: out.append({'field': 'structured_fields', 'kind': 'structured_rendering', 'text': f'{stream} {rendering}', 'dense': False})
    try:
        from ..narrative_sources import text_fields
        for p in text_fields(r):
            if p['field'] in seen: continue
            seen.add(p['field']); out.append({'field': p['field'], 'kind': 'received_text', 'text': p['text'], 'dense': True})
    except (KeyError, TypeError, ValueError):
        pass
    for k in NARRATIVE_EXTRA:
        v = r.get(k)
        if k not in seen and isinstance(v, str) and v.strip():
            seen.add(k); out.append({'field': k, 'kind': 'received_text', 'text': v, 'dense': True})
    if extracted and isinstance(extracted.get('text'), str) and extracted['text'].strip() and 'extracted_text' not in seen:
        out.append({'field': 'extracted_text', 'kind': 'extracted_text', 'text': extracted['text'], 'dense': True,
                    'method': extracted.get('method')})
    return out


def connect(path):
    db = sqlite3.connect((Path(path) / DB_NAME).resolve().as_uri(), uri=True, timeout=60, check_same_thread=False)
    db.execute('PRAGMA journal_mode=WAL'); db.execute('PRAGMA synchronous=NORMAL')
    db.executescript('''CREATE TABLE IF NOT EXISTS meta(key TEXT PRIMARY KEY, value TEXT);
      CREATE TABLE IF NOT EXISTS indexed(record TEXT PRIMARY KEY, hash TEXT, patient TEXT);
      CREATE TABLE IF NOT EXISTS contents(id INTEGER PRIMARY KEY, hash TEXT UNIQUE, text TEXT, dense INTEGER DEFAULT 0, vector BLOB);
      CREATE TABLE IF NOT EXISTS passages(id INTEGER PRIMARY KEY, record TEXT, patient TEXT, content INTEGER, stream TEXT,
        field TEXT, kind TEXT, start INTEGER, "end" INTEGER, event_date TEXT, available_date TEXT, source TEXT);
      CREATE INDEX IF NOT EXISTS passage_record ON passages(record);
      CREATE INDEX IF NOT EXISTS passage_patient ON passages(patient, content);
      CREATE INDEX IF NOT EXISTS passage_content ON passages(content);
      CREATE INDEX IF NOT EXISTS content_pending ON contents(dense) WHERE vector IS NULL;
      CREATE VIRTUAL TABLE IF NOT EXISTS search USING fts5(text, content='contents', content_rowid='id', tokenize='unicode61');
      CREATE TABLE IF NOT EXISTS extractions(record TEXT PRIMARY KEY, patient TEXT, text TEXT, method TEXT);
      CREATE TABLE IF NOT EXISTS result_cache(id TEXT PRIMARY KEY, payload TEXT);''')
    return db


def _meta(db):
    return dict(db.execute('SELECT key, value FROM meta'))


def _set_meta(db, **kv):
    db.executemany('INSERT OR REPLACE INTO meta VALUES (?,?)', [(k, json.dumps(v)) for k, v in kv.items()])


def _stack_uri(path):
    return (Path(path) / 'stack.sqlite3').resolve().as_uri() + '?mode=ro'


def signature(path):
    """Cheap identity of the record store this index must mirror (records count, max rowid, delivery digest)."""
    run = json.loads((Path(path) / 'run.json').read_text())
    s = sqlite3.connect(_stack_uri(path), uri=True)
    try: n, top = s.execute('SELECT count(*), max(rowid) FROM records').fetchone()
    finally: s.close()
    return digest([VERSION, run.get('source_digest'), run.get('source_run'), n, top])


def add_extractions(path, rows):
    """Bind document text (OCR / reader output) to records: rows of {record_id, patient_id, text, method}. The next
    update() re-indexes those records with an 'extracted_text' passage. -> rows written."""
    db = connect(path)
    try:
        with db:
            db.executemany('INSERT OR REPLACE INTO extractions VALUES (?,?,?,?)',
                           [(r['record_id'], r.get('patient_id'), r['text'], r.get('method')) for r in rows if r.get('text')])
            _set_meta(db, signature=None)  # force the next update to look
        return db.total_changes
    finally:
        db.close()


def _content(db, text, dense, cache):
    h = hashlib.sha1(text.encode()).hexdigest()
    cid = cache.get(h)
    if cid is None:
        row = db.execute('SELECT id, dense FROM contents WHERE hash=?', (h,)).fetchone()
        if row is None:
            cid = db.execute('INSERT INTO contents(hash, text, dense) VALUES (?,?,?)', (h, text, int(dense))).lastrowid
            db.execute('INSERT INTO search(rowid, text) VALUES (?,?)', (cid, text))
        else:
            cid = row[0]
            if dense and not row[1]: db.execute('UPDATE contents SET dense=1 WHERE id=?', (cid,))
        cache[h] = cid
    return cid


def _drop_records(db, ids):
    for i in range(0, len(ids), 500):
        chunk = ids[i:i + 500]; q = ','.join('?' * len(chunk))
        db.execute(f'DELETE FROM passages WHERE record IN ({q})', chunk)
        db.execute(f'DELETE FROM indexed WHERE record IN ({q})', chunk)


def _collect_orphans(db):
    n = 0
    while True:
        rows = db.execute('SELECT id, text FROM contents c WHERE NOT EXISTS (SELECT 1 FROM passages p WHERE p.content=c.id) LIMIT 2000').fetchall()
        if not rows: return n
        db.executemany("INSERT INTO search(search, rowid, text) VALUES ('delete', ?, ?)", rows)
        db.executemany('DELETE FROM contents WHERE id=?', [(r[0],) for r in rows])
        n += len(rows)


def update(path, *, page=PAGE, max_records=None, force=False, progress=None):
    """Sync the passage index with the pipeline's record store. Incremental and page-bounded; FTS only (vectors are lazy).
    -> report {'status': 'current'|'updated'|'partial', 'records', 'reindexed', 'removed', 'passages', 'contents', ...}."""
    path = Path(path); started = time.perf_counter()
    db = connect(path)
    try:
        meta = _meta(db)
        if meta and json.loads(meta.get('version', 'null')) != VERSION:
            db.close()
            for extra in ('', '-wal', '-shm'): Path(str(path / DB_NAME) + extra).unlink(missing_ok=True)
            db = connect(path); meta = {}
        sig = signature(path)
        if not force and meta.get('signature') and json.loads(meta['signature']) == sig and json.loads(meta.get('complete', 'false')):
            return {**report(db), 'status': 'current', 'elapsed_seconds': time.perf_counter() - started}
        db.execute('ATTACH DATABASE ? AS s', (_stack_uri(path),))
        extracted = {r[0]: {'text': r[1], 'method': r[2]} for r in db.execute('SELECT record, text, method FROM extractions')}
        # removed records (in the index, gone from the store)
        gone = [r[0] for r in db.execute('SELECT record FROM indexed WHERE record NOT IN (SELECT id FROM s.records)')]
        with db: _drop_records(db, gone)
        last, seen, reindexed, cache, complete = 0, 0, 0, {}, True
        while True:
            rows = db.execute('SELECT rowid, id, patient, stream, payload FROM s.records WHERE rowid>? ORDER BY rowid LIMIT ?',
                              (last, page)).fetchall()
            if not rows: break
            last = rows[-1][0]; seen += len(rows)
            ids = [r[1] for r in rows]
            have = dict(db.execute(f"SELECT record, hash FROM indexed WHERE record IN ({','.join('?' * len(ids))})", ids))
            with db:
                for _, rid, pid, stream, payload in rows:
                    ex = extracted.get(rid)
                    h = hashlib.sha1((payload + (ex['text'] if ex else '')).encode()).hexdigest()
                    if have.get(rid) == h: continue
                    if rid in have: _drop_records(db, [rid])
                    try: r = json.loads(payload)
                    except ValueError: r = {'text': payload}
                    r.setdefault('source_table', stream)
                    source = {'source_system': r.get('source_system'), 'organization_id': r.get('organization_id'), 'record_hash': h}
                    for part in parts(r, ex):
                        for a, b in windows(part['text']):
                            if not part['text'][a:b].strip(): continue
                            cid = _content(db, part['text'][a:b], part['dense'], cache)
                            db.execute('INSERT INTO passages(record, patient, content, stream, field, kind, start, "end", event_date, '
                                       'available_date, source) VALUES (?,?,?,?,?,?,?,?,?,?,?)',
                                       (rid, pid, cid, stream, part['field'], part['kind'], a, b, r.get('event_date'),
                                        r.get('available_date'), json.dumps({**source, **({'extraction_method': part['method']} if part.get('method') else {})})))
                    db.execute('INSERT OR REPLACE INTO indexed VALUES (?,?,?)', (rid, h, pid))
                    reindexed += 1
            if len(cache) > 200000: cache.clear()
            if progress: progress(seen)
            if max_records and seen >= max_records:
                complete = False; break
        with db:
            orphans = _collect_orphans(db)
            gen = json.loads(meta.get('generation', '0')) + (1 if (reindexed or gone) else 0)
            _set_meta(db, version=VERSION, signature=sig if complete else None, complete=complete, generation=gen,
                      updated_at=time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime()))
        db.execute('DETACH DATABASE s')
        return {**report(db), 'status': 'updated' if complete else 'partial', 'records_seen': seen, 'reindexed': reindexed,
                'removed': len(gone), 'orphan_contents_collected': orphans, 'elapsed_seconds': time.perf_counter() - started}
    finally:
        db.close()


def report(db):
    m = _meta(db)
    n_pass = db.execute('SELECT count(*) FROM passages').fetchone()[0]
    n_cont, n_dense, n_vec = db.execute('SELECT count(*), coalesce(sum(dense),0), count(vector) FROM contents').fetchone()
    return {'schema': VERSION, 'records': db.execute('SELECT count(*) FROM indexed').fetchone()[0], 'passages': n_pass,
            'contents': n_cont, 'dense_contents': n_dense, 'embedded_contents': n_vec,
            'generation': json.loads(m.get('generation', '0')), 'complete': json.loads(m.get('complete', 'false'))}


def embed(db, encoder, ids=None, *, limit=256, batch=64):
    """Embed pending narrative contents (all pending, or among ids). -> number embedded. Bounded by limit."""
    if encoder is None: return 0
    if ids is not None:
        ids = list(ids)[:2000]
        if not ids: return 0
        rows = db.execute(f"SELECT id, text FROM contents WHERE dense=1 AND vector IS NULL AND id IN ({','.join('?' * len(ids))}) LIMIT ?",
                          [*ids, limit]).fetchall()
    else:
        rows = db.execute('SELECT id, text FROM contents WHERE dense=1 AND vector IS NULL LIMIT ?', (limit,)).fetchall()
    done = 0
    for i in range(0, len(rows), batch):
        chunk = rows[i:i + batch]
        vecs = np.asarray(encoder.encode([t for _, t in chunk]), dtype=np.float32)
        with db:
            db.executemany('UPDATE contents SET vector=? WHERE id=?', [(v.tobytes(), cid) for (cid, _), v in zip(chunk, vecs)])
        done += len(chunk)
    return done


def embed_pending(path, encoder, *, max_texts=20000, batch=64):
    """Fill MiniLM vectors for narrative windows in bounded batches (run when idle). -> {'embedded', 'pending'}."""
    db = connect(path)
    try:
        done = 0
        while done < max_texts:
            n = embed(db, encoder, limit=min(1024, max_texts - done), batch=batch)
            if not n: break
            done += n
        pending = db.execute('SELECT count(*) FROM contents WHERE dense=1 AND vector IS NULL').fetchone()[0]
        return {'embedded': done, 'pending': pending}
    finally:
        db.close()


def _terms(question):
    expanded = question.lower()
    for topic, aliases in ALIASES.items():
        if topic in expanded: expanded += ' ' + ' '.join(aliases)
    return [t for t in dict.fromkeys(re.findall(r'[a-z0-9]{2,}', expanded)) if t not in _STOP][:80]


class PassageIndex:
    """warehouse_retrieval.Retriever.search over a workspace's incremental passage index."""

    def __init__(self, path, *, encoder=None, reranker=None, meta=None, auto_update=True):
        self.path = Path(path); self.encoder = encoder; self.reranker = reranker
        self.meta = meta or json.loads((self.path / 'run.json').read_text())
        self.lock = threading.Lock()
        self.build = update(self.path) if auto_update else None
        self.db = connect(self.path)
        self.db.execute('ATTACH DATABASE ? AS s', (_stack_uri(self.path),))

    def close(self):
        self.db.close()

    def refresh(self):
        """Re-sync after the record store changed (cheap when it did not)."""
        with self.lock:
            self.build = update(self.path)

    def _scope(self, patient, patient_ids, window):
        db = self.db
        db.execute('CREATE TEMP TABLE IF NOT EXISTS scope_passages(id INTEGER PRIMARY KEY)')
        db.execute('DELETE FROM scope_passages')
        sql, params, conds = 'INSERT INTO scope_passages SELECT p.id FROM passages p', [], []
        if patient_ids is not None or patient:
            db.execute('CREATE TEMP TABLE IF NOT EXISTS scope_patients(id TEXT PRIMARY KEY)')
            db.execute('DELETE FROM scope_patients')
            members = sorted(set(patient_ids)) if patient_ids is not None else [patient]
            if patient: members = [p for p in members if p == patient]
            db.executemany('INSERT OR IGNORE INTO scope_patients VALUES (?)', ((p,) for p in members))
            sql += ' JOIN scope_patients sp ON sp.id=p.patient'
        if window['basis'] != 'all_observed':
            field = 'p.event_date' if window['basis'] == 'source_event' else 'p.available_date'
            conds.append(f'{field} IS NOT NULL AND {field}<=?'); params.append(window['end'] or self.meta['snapshot']['as_of'])
            if window['start']: conds.append(f'{field}>=?'); params.append(window['start'])
        if conds: sql += ' WHERE ' + ' AND '.join(conds)
        db.execute(sql, params)
        return ' JOIN scope_passages qs ON qs.id=p.id'

    def _query_vector(self, question):
        texts = [question[a:b] for a, b in windows(question)] or [question]
        q = np.asarray(self.encoder.encode(texts), dtype=np.float32).mean(axis=0)
        return q / max(float(np.linalg.norm(q)), 1e-12)

    def _dense(self, q, scope_join, patient, k=80):
        if scope_join:
            sql = ('SELECT c.id, c.vector FROM contents c WHERE c.vector IS NOT NULL AND c.id IN '
                   '(SELECT p.content FROM passages p' + scope_join + ')')
        else:
            sql = 'SELECT id, vector FROM contents WHERE vector IS NOT NULL'
        heap = []
        cur = self.db.execute(sql)
        while True:
            rows = cur.fetchmany(SCAN_CHUNK)
            if not rows: break
            m = np.frombuffer(b''.join(r[1] for r in rows), dtype=np.float32).reshape(len(rows), -1)
            scores = m @ q
            top = np.argsort(-scores, kind='stable')[:k]
            for i in top:
                item = (float(scores[i]), -rows[i][0])
                if len(heap) < k: heapq.heappush(heap, item)
                elif item > heap[0]: heapq.heapreplace(heap, item)
        ordered = [-cid for _, cid in sorted(heap, reverse=True)]
        out = []
        for cid in ordered:
            vals, cond = [cid], ''
            if patient: cond = ' AND p.patient=?'; vals.append(patient)
            out.extend(r[0] for r in self.db.execute('SELECT p.id FROM passages p' + scope_join + ' WHERE p.content=?' + cond +
                                                     ' ORDER BY p.patient, p.id LIMIT 4', vals))
        return out

    def search(self, question, *, patient=None, limit=8, patient_ids=None, analysis_window=None):
        from .clinical_events import validate_window
        from .question_input import validate_question
        window = validate_window(analysis_window, self.meta['snapshot']['as_of'])
        validate_question(question)
        if type(limit) is not int or not 1 <= limit <= 20: raise ValueError('Bounded result count required')
        if window['basis'] == 'clinical':
            raise ValueError('Missing input: clinical-event bindings (workspace passage index filters on source event or available dates)')
        if patient_ids is not None and (not isinstance(patient_ids, list) or any(not isinstance(p, str) for p in patient_ids)):
            raise ValueError('Explicit cohort membership required')
        with self.lock:
            if patient and not self.db.execute('SELECT 1 FROM s.patients WHERE id=?', (patient,)).fetchone():
                raise ValueError('Unknown patient')
            t = time.perf_counter()
            stats = report(self.db)
            membership = sorted(set(patient_ids)) if patient_ids is not None else None
            key = digest([VERSION, self.meta.get('source_digest'), stats['generation'], stats['embedded_contents'],
                          getattr(self.encoder, 'identity', None), getattr(self.reranker, 'identity', None),
                          question, patient, limit, membership, window])
            old = self.db.execute('SELECT payload FROM result_cache WHERE id=?', (key,)).fetchone()
            if old: return {**json.loads(old[0]), 'cache_hit': True, 'elapsed_seconds': time.perf_counter() - t, 'new_paid_api_calls': 0}
            scoped = patient_ids is not None or window['basis'] != 'all_observed'
            scope_join = self._scope(patient, patient_ids, window) if (scoped or patient) else ''
            terms = _terms(question)
            lexical = []
            if terms:
                expression = ' OR '.join('"' + x + '"' for x in terms)
                if scope_join:  # filter before ranking: a small cohort's passages need not be in the global top matches
                    sql = ('SELECT p.id FROM search JOIN passages p ON p.content=search.rowid' + scope_join +
                           ' WHERE search MATCH ? ORDER BY bm25(search), p.id LIMIT 80')
                else:
                    sql = ('SELECT p.id FROM (SELECT rowid AS cid, bm25(search) AS score FROM search WHERE search MATCH ? '
                           'ORDER BY score LIMIT 400) m JOIN passages p ON p.content=m.cid ORDER BY m.score, p.id LIMIT 80')
                lexical = [r[0] for r in self.db.execute(sql, (expression,))]
            dense, lazily = [], 0
            if self.encoder is not None:
                try:
                    if lexical:
                        cids = [r[0] for r in self.db.execute(f"SELECT DISTINCT content FROM passages WHERE id IN ({','.join('?' * len(lexical))})", lexical)]
                        lazily = embed(self.db, self.encoder, cids, limit=LAZY_EMBED)
                    dense = self._dense(self._query_vector(question), scope_join, patient)
                except Exception:  # noqa: BLE001 - the dense rung helps ranking; its failure leaves lexical results
                    dense = []
            fusion = Counter()
            for ranking in (lexical, dense):
                for rank, pid in enumerate(dict.fromkeys(ranking), 1): fusion[pid] += 1 / (60 + rank)
            candidates = []
            for pid, _ in fusion.most_common(24):
                text, src, rid, patient_id, stream, field, kind, a, b, ev, av = self.db.execute(
                    'SELECT c.text, p.source, p.record, p.patient, p.stream, p.field, p.kind, p.start, p."end", p.event_date, '
                    'p.available_date FROM passages p JOIN contents c ON c.id=p.content WHERE p.id=?', (pid,)).fetchone()
                source = {'record_id': rid, 'patient_id': patient_id, 'source_table': stream, **json.loads(src), 'field': field, 'kind': kind, 'event_date': ev, 'available_date': av,
                          'start': a, 'end': b, 'quote': text, 'clinical_time': 'Not inferred from source event date'}
                candidates.append({'id': str(pid), 'text': text, 'source': source, 'note_id': source['record_id'],
                                   'start': a, 'protected': False, 'rrf_score': fusion[pid]})
            ranked = self.reranker.rank(question, candidates, top_k=len(candidates)) if self.reranker and candidates else candidates
            selected, seen, by_patient = [], set(), Counter()
            for row in ranked:
                s = row['source']; group = (s['patient_id'], s['record_id'], s['field'])
                if group in seen or (not patient and by_patient[s['patient_id']] >= 2): continue
                selected.append(row); seen.add(group); by_patient[s['patient_id']] += 1
                if len(selected) >= limit: break
            members = None
            if patient_ids is not None: members = len(membership)
            result = {'schema': VERSION, 'synthetic': True, 'question': question, 'patient_id': patient, 'evidence': selected,
                      'candidate_passages': len(candidates),
                      'method': 'FTS5 BM25 + lazily embedded MiniLM cosine -> reciprocal-rank fusion -> source diversity'
                                + (' (reranked)' if self.reranker else '') + ('' if self.encoder is not None else ' (lexical only: no local encoder)'),
                      'snapshot': self.meta['snapshot'], 'source_run': self.meta.get('source_run'), 'cache_hit': False,
                      'elapsed_seconds': time.perf_counter() - t, 'new_paid_api_calls': 0, 'complete_evidence_guarantee': False,
                      'analysis_window': window, 'cohort_patient_count': members, 'scope_applied_before_ranking': bool(scope_join),
                      'index': {**stats, 'embedded_this_query': lazily},
                      'scope': 'Ranked passages for inspection, not a complete cohort or clinical answer. Scores are relevance, not correctness probabilities.'}
            from .evaluation import active as evaluating
            if not evaluating():
                with self.db: self.db.execute('INSERT OR REPLACE INTO result_cache VALUES (?,?)', (key, json.dumps(result, allow_nan=False)))
            return result
