"""Page/bbox-cited spans of scanned and faxed documents: a small side store beside the stack (stack/ocr-passages.sqlite3).

Sources, both read-only:
  warehouse  semantic.sqlite3 scanned_document_text (front-office OCR and PDF text layers) + scanned_document metadata.
             A one-page document's spans cite page 1; a multi-page text with form-feed page breaks cites each page; a
             multi-page text without page breaks cites page None with its page range (1-N), never a guessed page. These
             texts carry no geometry, so bbox is None.
  ingest     ingest/ocr.read_document spans (index_workspace_documents -> add_documents): the engine's own page numbers,
             and Apple Vision line boxes as [x0, y0, x1, y1] normalized, top-left origin.
Matching tolerates OCR noise: each span keeps a normalized copy (lower case; a digit 0/1/5 inside a word of letters read as
o/l/s, so 'syst0lic' and 'norma1' match) indexed with the FTS5 trigram tokenizer (substring matching survives merged words
such as 'andfunction'). Every hit carries record_id, patient_id, document_id, page, page_count, bbox, engine and the char
offsets of the span in the document text. Synthetic data only; no model calls.
"""
from __future__ import annotations

import json
import os
import re
import sqlite3
import time
from pathlib import Path

VERSION = 'agere-ocr-passages/2'
STORE = 'ocr-passages.sqlite3'
DDL = """
CREATE TABLE meta(key TEXT PRIMARY KEY, value TEXT);
CREATE TABLE spans(id INTEGER PRIMARY KEY, record_id TEXT, patient_id TEXT, document_id TEXT, document_type TEXT, received_via TEXT,
  exam TEXT, review_status TEXT, event_date TEXT, available_date TEXT, page INTEGER, page_count INTEGER, bbox TEXT, engine TEXT, char_start INTEGER,
  char_end INTEGER, text TEXT, norm TEXT, origin TEXT);
CREATE INDEX spans_record ON spans(record_id);
CREATE INDEX spans_patient ON spans(patient_id);
CREATE VIRTUAL TABLE spans_fts USING fts5(norm, content='spans', content_rowid='id', tokenize='trigram');
"""
_OCR_DIGIT = {'0': 'o', '1': 'l', '5': 's'}
_WORD = re.compile(r'[A-Za-z0-9]+')
_EXAM = re.compile(r'^\s*EXAM\s*:\s*(.+)$', re.I | re.M)


def normalize(text):
    """Lower-case text with OCR digit confusions inside letter words undone (codes and numbers are left alone)."""
    def fix(m):
        w = m.group()
        letters = sum(c.isalpha() for c in w)
        if letters >= 3 and letters > (len(w) - letters) and any(c in _OCR_DIGIT for c in w):
            return ''.join(_OCR_DIGIT.get(c, c) for c in w)
        return w
    return _WORD.sub(fix, text or '').lower()


def store_path(stack_dir):
    from .evaluation import operational_path
    return operational_path(stack_dir, STORE)


def _blocks(text):
    from ..ingest.ocr import text_blocks
    return text_blocks(text)


def document_spans(text, page_count):
    """[(page, char_start, char_end)] for one document text (see module doc for the page rule)."""
    out, offset = [], 0
    pages = text.split('\f') if '\f' in text else [text]
    for i, page_text in enumerate(pages, 1):
        page = i if len(pages) > 1 else (1 if (page_count or 1) == 1 else None)
        for a, b in _blocks(page_text):
            out.append((page, offset + a, offset + b))
        offset += len(page_text) + 1
    return out


def _semantic(stack_dir):
    return Path(stack_dir) / 'semantic.sqlite3'


def _signature(source):
    try:
        st = Path(source).stat()
        return json.dumps([VERSION, str(source), st.st_size, int(st.st_mtime)])
    except OSError:
        return None


def build(stack_dir, *, out=None):
    """(Re)build the store from the warehouse's scanned documents. -> {'documents', 'spans', 'seconds', 'path'}."""
    started = time.perf_counter()
    source = _semantic(stack_dir)
    target = Path(out) if out else store_path(stack_dir)
    tmp = target.with_suffix('.tmp')
    if tmp.exists(): tmp.unlink()
    src = sqlite3.connect(f'file:{source}?mode=ro', uri=True)
    db = sqlite3.connect(tmp)
    try:
        db.executescript(DDL)
        have = {r[0] for r in src.execute("SELECT name FROM sqlite_master WHERE name IN ('scanned_document','scanned_document_text')")}
        rows = []
        if have == {'scanned_document', 'scanned_document_text'}:
            cols = {r[1] for r in src.execute('PRAGMA table_info(scanned_document)')}
            review = 'd.review_status' if 'review_status' in cols else 'NULL'
            rows = src.execute('SELECT t.record_id, t.patient_id, d.document_id, d.document_type, d.received_via, d.event_date, '
                               'd.available_date, d.page_count, t.text_source, t.text, ' + review + ' FROM scanned_document_text t '
                               'LEFT JOIN scanned_document d USING(record_id) WHERE t.text IS NOT NULL').fetchall()
        n = 0
        for rid, pid, doc, dtype, via, event, available, pages, engine, text, review in rows:
            exam = (_EXAM.search(text) or [None, None])[1]
            for page, a, b in document_spans(text, pages):
                db.execute('INSERT INTO spans(record_id, patient_id, document_id, document_type, received_via, exam, review_status, event_date, '
                           'available_date, page, page_count, bbox, engine, char_start, char_end, text, norm, origin) '
                           'VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)',
                           (rid, pid, doc, dtype, via, normalize(exam or '').strip()[:200] or None, review, (event or '')[:10] or None,
                            (available or '')[:10] or None, page, pages, None, engine, a, b, text[a:b], normalize(text[a:b]), 'warehouse'))
                n += 1
        db.execute("INSERT INTO spans_fts(spans_fts) VALUES ('rebuild')")
        db.executemany('INSERT INTO meta VALUES (?,?)', [('version', VERSION), ('source', _signature(source) or ''),
                                                         ('documents', str(len(rows))), ('spans', str(n))])
        db.commit()
    finally:
        db.close(); src.close()
    os.replace(tmp, target)
    return {'documents': len(rows), 'spans': n, 'seconds': round(time.perf_counter() - started, 2), 'path': str(target)}


def ensure(stack_dir):
    """The store, rebuilt when missing or built from a different semantic layer. Returns its path (None without sources)."""
    target, source = store_path(stack_dir), _semantic(stack_dir)
    if target.exists():
        valid = False
        try:
            db = sqlite3.connect(f'file:{target}?mode=ro', uri=True)
            try:
                sig = dict(db.execute('SELECT key, value FROM meta')).get('source')
                valid = sig is not None
            finally: db.close()
        except sqlite3.Error:
            sig = None
        if valid and (not source.exists() or sig in (_signature(source), '')): return target
    if not source.exists(): return None
    build(stack_dir)
    return target


def add_documents(stack_dir, rows, *, metadata=None):
    """Ingest-time spans (ocr.read_document 'spans') for documents read from a delivery: rows of {record_id, patient_id,
    text, method, spans}. Replaces earlier spans of the same records. -> spans written."""
    target = store_path(stack_dir)
    fresh = not target.exists()
    db = sqlite3.connect(target)
    try:
        if fresh:
            db.executescript(DDL)
            db.executemany('INSERT INTO meta VALUES (?,?)', [('version', VERSION), ('source', '')])
        n = 0
        with db:
            for r in rows:
                meta = (metadata or {}).get(r['record_id'], {})
                old = [x[0] for x in db.execute('SELECT id FROM spans WHERE record_id=?', (r['record_id'],))]
                for i in old:
                    row = db.execute('SELECT norm FROM spans WHERE id=?', (i,)).fetchone()
                    db.execute("INSERT INTO spans_fts(spans_fts, rowid, norm) VALUES ('delete', ?, ?)", (i, row[0]))
                db.execute('DELETE FROM spans WHERE record_id=?', (r['record_id'],))
                spans = r.get('spans') or [{'page': None, 'text': r['text'][a:b], 'bbox': None, 'engine': r.get('method'),
                                            'char_start': a, 'char_end': b} for a, b in _blocks(r.get('text') or '')]
                pages = max([s['page'] for s in spans if s.get('page')] or [None]) if spans else None
                exam = (_EXAM.search(r.get('text') or '') or [None, None])[1]
                for s in spans:
                    cur = db.execute('INSERT INTO spans(record_id, patient_id, document_id, document_type, received_via, exam, review_status, '
                                     'event_date, available_date, page, page_count, bbox, engine, char_start, char_end, text, norm, origin) '
                                     'VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)',
                                     (r['record_id'], r.get('patient_id'), meta.get('document_id'), meta.get('document_type'),
                                      meta.get('received_via'), normalize(exam or '').strip()[:200] or None, meta.get('review_status'), meta.get('event_date'),
                                      meta.get('available_date'), s.get('page'), pages, json.dumps(s['bbox']) if s.get('bbox') else None,
                                      s.get('engine') or r.get('method'), s.get('char_start'), s.get('char_end'), s['text'],
                                      normalize(s['text']), 'ingest'))
                    db.execute('INSERT INTO spans_fts(rowid, norm) VALUES (?,?)', (cur.lastrowid, normalize(s['text'])))
                    n += 1
        return n
    finally:
        db.close()


# -- reading ------------------------------------------------------------------------------------------------------------
def _fts_phrase(term):
    t = normalize(term).strip()
    return '"' + t.replace('"', '""') + '"' if len(t) >= 3 else None


def hit(row):
    keys = ('id', 'record_id', 'patient_id', 'document_id', 'document_type', 'received_via', 'exam', 'review_status', 'event_date', 'available_date',
            'page', 'page_count', 'bbox', 'engine', 'char_start', 'char_end', 'text', 'origin')
    h = dict(zip(keys, row))
    h['bbox'] = json.loads(h['bbox']) if h['bbox'] else None
    h['citation'] = citation(h)
    return h


def citation(h):
    page = f"page {h['page']}" if h.get('page') else (f"pages 1-{h['page_count']} (page break not in the text)" if (h.get('page_count') or 0) > 1
                                                   else 'page not reported')
    box = f", bbox {h['bbox']}" if h.get('bbox') else ''
    return f"{h['record_id']} {page}, chars {h['char_start']}-{h['char_end']}{box} ({h['engine']})"


_COLS = ('s.id, s.record_id, s.patient_id, s.document_id, s.document_type, s.received_via, s.exam, s.review_status, s.event_date, s.available_date, '
         's.page, s.page_count, s.bbox, s.engine, s.char_start, s.char_end, s.text, s.origin')


def search(stack_dir, terms, *, patient_ids=None, document_types=None, channels=None, exam=None, review_status=None, period=None, as_of=None,
           limit=5000):
    """Spans matching any of terms (OCR-tolerant substring) within the filters. terms empty = every span in scope.
    -> [hit] with citation; each hit adds 'matched' (the terms it contains)."""
    path = ensure(stack_dir)
    if path is None: return []
    db = sqlite3.connect(f'file:{path}?mode=ro', uri=True)
    try:
        where, args = [], []
        phrases = [p for p in (_fts_phrase(t) for t in terms or []) if p]
        if terms and not phrases: return []
        if phrases:
            where.append('s.id IN (SELECT rowid FROM spans_fts WHERE spans_fts MATCH ?)'); args.append(' OR '.join(phrases))
        if document_types:
            where.append(f"lower(s.document_type) IN ({','.join('?' * len(document_types))})"); args += [d.lower() for d in document_types]
        if channels:
            where.append(f"s.received_via IN ({','.join('?' * len(channels))})"); args += list(channels)
        if review_status:
            where.append('s.review_status = ?'); args.append(review_status)
        if exam:
            where.append('s.exam LIKE ?'); args.append(f'%{normalize(exam).strip()}%')
        if patient_ids is not None:
            ids = sorted(set(patient_ids))
            if not ids: return []
            where.append('s.patient_id IN (SELECT value FROM json_each(?))'); args.append(json.dumps(ids))
        period = period or {}
        if period.get('start'): where.append('s.event_date >= ?'); args.append(period['start'])
        if period.get('end'): where.append('s.event_date <= ?'); args.append(period['end'])
        if as_of: where.append('(s.available_date IS NULL OR s.available_date <= ?)'); args.append(str(as_of)[:10])
        sql = f"SELECT {_COLS} FROM spans s {'WHERE ' + ' AND '.join(where) if where else ''} ORDER BY s.record_id, s.char_start LIMIT ?"
        out = []
        for row in db.execute(sql, (*args, int(limit))):
            h = hit(row)
            norm = normalize(h['text'])
            h['matched'] = [t for t in terms or [] if normalize(t).strip() in norm]
            out.append(h)
        return out
    finally:
        db.close()


def patient_passages(stack_dir, patient_id, query, *, limit=3, as_of=None):
    """A patient's best OCR spans for a free-text query (atomic_questions evidence): spans sharing the most query words.
    -> [hit] (page/bbox citation included)."""
    words = [w for w in dict.fromkeys(re.findall(r'[a-z][a-z0-9]{3,}', normalize(query))) if w not in _STOP]
    if not words: return []
    hits = search(stack_dir, words, patient_ids=[patient_id], as_of=as_of, limit=400)
    hits.sort(key=lambda h: (-len(h['matched']), h['event_date'] or '', h['char_start'] or 0))
    return [h for h in hits if h['matched']][:limit]


def locate(stack_dir, record_id, text):
    """Page/bbox citation of a passage of record_id's text (retrieval evidence) or None when it is not an OCR'd document."""
    path = store_path(stack_dir)
    if not path.exists(): return None
    db = sqlite3.connect(f'file:{path}?mode=ro', uri=True)
    try:
        rows = [hit(r) for r in db.execute(f'SELECT {_COLS} FROM spans s WHERE s.record_id=? ORDER BY s.char_start', (record_id,))]
    finally:
        db.close()
    if not rows: return None
    probe = normalize(text or '')[:120].strip()
    for h in rows:
        n = normalize(h['text'])
        if probe and (probe[:60] in n or n[:60] in probe): return h
    return rows[0] if len({h['page'] for h in rows}) == 1 else {**rows[0], 'page': None, 'bbox': None,
                                                              'citation': citation({**rows[0], 'page': None, 'bbox': None})}


_STOP = set('''this that with from have does patient patients show showing report reports document documents scanned faxed
fax mention mentions mentioned only submitted evidence establish there their which what when were been into about'''.split())


def atomic_evidence(stack_dir, patient_id, query, passages, *, already=(), limit=2, as_of=None):
    """OCR spans as atomic_questions evidence for one patient and atom: [{'id', 'text', 'date', 'note_id'}], and their page/bbox
    provenance written into passages[id] (the cited source carries page, bbox and citation). Records already retrieved are
    cited by page through locate() instead of being sent twice. Never raises (no store = no OCR evidence)."""
    try:
        for pid_, meta in list(passages.items()):
            if meta.get('record_id') and 'page' not in meta and pid_ in already:
                found = locate(stack_dir, meta['record_id'], meta.get('text') or '')
                if found: meta.update(page=found['page'], bbox=found['bbox'], citation=found['citation'])
        have = {m.get('record_id') for k, m in passages.items() if k in already}
        out = []
        for h in patient_passages(stack_dir, patient_id, query, limit=limit + len(have), as_of=as_of):
            if h['record_id'] in have or len(out) >= limit: continue
            key = f"ocr:{h['id']}"
            passages[key] = {'record_id': h['record_id'], 'field': 'ocr_text', 'date': h['event_date'], 'page': h['page'],
                             'bbox': h['bbox'], 'citation': h['citation']}
            out.append({'id': key, 'text': f"[{h['document_type'] or 'scanned document'}, {h['citation']}] {h['text']}",
                        'date': h['event_date'] or 'undated', 'note_id': h['record_id']})
        return out
    except (sqlite3.Error, OSError, ValueError):
        return []


def cite_evidence(stack_dir, evidence, limit=12):
    """Page/bbox citations for retrieved passages that come from OCR'd documents (evidence_retrieval receipt).
    -> [{'record_id', 'page', 'bbox', 'citation'}]; empty when none of them is a scanned document. Never raises."""
    out = []
    try:
        for e in evidence or []:
            rid = (e.get('source') or {}).get('record_id') or e.get('record_id')
            found = locate(stack_dir, rid, e.get('text') or '') if rid else None
            if found: out.append({'record_id': rid, 'page': found['page'], 'bbox': found['bbox'], 'citation': found['citation']})
            if len(out) >= limit: break
    except (sqlite3.Error, OSError, ValueError):
        return out
    return out
