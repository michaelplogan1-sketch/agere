"""Text for scanned documents: cheapest local engine first, one interface, never blocks ingestion.

    engines()                         -> [{'engine', 'available', 'reason'}]       (no model is loaded)
    read_document(path)               -> {'schema': 'agere-ocr-text/1', 'status', 'engine', 'text', 'pages', 'attempts', ...}
    ocr_scanned_documents(warehouse_db, root, release)   v2.3+ image-only outside PDFs -> source_document_ocr rows
    extractions(warehouse_db, release)                   -> [{record_id, patient_id, text, method}] for the passage stores
    index_workspace_documents(pipeline_dir, root)        scanned_documents records without text -> workspace passage index

Engine chain (first one that yields at least min_chars wins; otherwise the longest result is kept):
  pdf_text_layer  standard-library PDF text reader (outside_documents.py): free, exact, no OCR
  apple_vision    macOS PDFKit + Vision (native/agere-ocr, compiled from native/ExtractDocument.swift); images are
                  wrapped into a PDF with sips first
  chandra         Chandra OCR 2 (datalab-to/chandra-ocr-2, layout-aware VLM) for pages Vision cannot recover; runs only
                  when the `chandra` package and its weights are already installed locally (never downloaded here)
  tesseract       when the binary is on PATH (images)
An engine that is missing or fails is recorded in 'attempts' and skipped. No hosted call anywhere.
"""
from __future__ import annotations

import hashlib
import importlib.util
import json
import os
from pathlib import Path
import shutil
import sqlite3
import subprocess
import tempfile
import time

ROOT = Path(__file__).resolve().parents[3]
VERSION = 'agere-ocr-text/1'
IMAGE_EXTS = ('.png', '.jpg', '.jpeg', '.tif', '.tiff', '.heic', '.bmp', '.gif')
DOCUMENT_EXTS = ('.pdf',) + IMAGE_EXTS
CHAIN = ('pdf_text_layer', 'apple_vision', 'chandra', 'tesseract')
MIN_CHARS = 40
CHANDRA_REPOS = ('datalab-to/chandra-ocr-2', 'datalab-to/chandra')
DDL = """CREATE TABLE IF NOT EXISTS source_document_ocr(release TEXT, record_id TEXT, patient_id TEXT, relative_path TEXT,
  sha256 TEXT, status TEXT, engine TEXT, text TEXT, text_chars INTEGER, pages_json TEXT, attempts_json TEXT, reader TEXT,
  created_at TEXT, PRIMARY KEY(release, record_id))"""


def _sha(path):
    with Path(path).open('rb') as f: return hashlib.file_digest(f, 'sha256').hexdigest()


def _native_binary():
    """native/agere-ocr, rebuilt from the Swift source when stale. None off macOS or without swiftc."""
    binary, source = ROOT / 'native' / 'agere-ocr', ROOT / 'native' / 'ExtractDocument.swift'
    if not source.is_file(): return binary if binary.is_file() and os.access(binary, os.X_OK) else None
    if not binary.is_file() or binary.stat().st_mtime < source.stat().st_mtime:
        if not shutil.which('swiftc'): return binary if binary.is_file() else None
        try: subprocess.run(['swiftc', str(source), '-o', str(binary)], check=True, timeout=300, capture_output=True)
        except (OSError, subprocess.SubprocessError): return None
    return binary if os.access(binary, os.X_OK) else None


def chandra_path():
    """Local Chandra weights (AGERE_CHANDRA_PATH or the HF cache), or None."""
    env = os.environ.get('AGERE_CHANDRA_PATH')
    if env: return Path(env) if (Path(env) / 'config.json').is_file() else None
    hub = Path(os.environ.get('HF_HUB_CACHE') or Path(os.environ.get('HF_HOME', Path.home() / '.cache' / 'huggingface')) / 'hub')
    for repo in CHANDRA_REPOS:
        snaps = hub / ('models--' + repo.replace('/', '--')) / 'snapshots'
        for s in sorted(snaps.glob('*')) if snaps.is_dir() else []:
            if (s / 'config.json').is_file() and any(s.glob('*.safetensors')): return s
    return None


def engines():
    """Availability of each engine in chain order (cheap checks only)."""
    mac = os.uname().sysname == 'Darwin'
    native = ROOT / 'native' / 'agere-ocr'
    out = [{'engine': 'pdf_text_layer', 'available': True, 'reason': None}]
    ok = mac and (native.is_file() or bool(shutil.which('swiftc')))
    out.append({'engine': 'apple_vision', 'available': ok, 'reason': None if ok else 'needs macOS with native/agere-ocr or swiftc'})
    cp = chandra_path()
    if importlib.util.find_spec('chandra') is None:
        out.append({'engine': 'chandra', 'available': False, 'reason': 'chandra-ocr package not installed (pip install chandra-ocr)'})
    elif cp is None:
        out.append({'engine': 'chandra', 'available': False, 'reason': 'Chandra weights not found locally (`hf download datalab-to/chandra-ocr-2`)'})
    else:
        out.append({'engine': 'chandra', 'available': True, 'reason': None})
    t = shutil.which('tesseract')
    out.append({'engine': 'tesseract', 'available': bool(t), 'reason': None if t else 'tesseract binary not on PATH'})
    return out


# --- engines: each returns (text, pages, reason) or raises ---------------------------------------------------------------

def _text_layer(path):
    if path.suffix.lower() != '.pdf': return '', [], 'not a PDF'
    from ..outside_documents import read
    got = read(path.read_bytes())
    text = got.get('text') or ''
    return text, ([{'page': None, 'method': 'pdf_text_layer', 'text': text, 'confidence': None}] if text else []), got.get('reason')


def _as_pdf(path, tmp):
    """Images -> single-page PDF via macOS sips (no Python imaging dependency)."""
    if path.suffix.lower() == '.pdf': return path
    out = Path(tmp) / (path.stem + '.pdf')
    subprocess.run(['sips', '-s', 'format', 'pdf', str(path), '--out', str(out)], check=True, timeout=120, capture_output=True)
    return out


def _apple_vision(path):
    binary = _native_binary()
    if binary is None: raise FileNotFoundError('native/agere-ocr unavailable')
    with tempfile.TemporaryDirectory() as tmp:
        pdf = _as_pdf(path, tmp)
        raw = subprocess.run([str(binary), str(pdf)], check=True, capture_output=True, text=True, timeout=300).stdout
    result = json.loads(raw)
    pages = [{'page': p.get('page'), 'method': p.get('method'), 'text': p.get('text') or '',
              'confidence': (sum(r['confidence'] for r in p['regions']) / len(p['regions'])) if p.get('regions') else None}
             for p in result.get('pages') or []]
    return '\n\n'.join(p['text'] for p in pages if p['text']), pages, None


_CHANDRA = {}


def _chandra(path):
    if importlib.util.find_spec('chandra') is None or chandra_path() is None: raise FileNotFoundError('Chandra unavailable')
    from chandra.model import InferenceManager            # lazy: the VLM loads on first use only
    from chandra.model.schema import BatchInputItem
    from chandra.input import load_file
    if 'manager' not in _CHANDRA:
        os.environ.setdefault('MODEL_CHECKPOINT', str(chandra_path()))
        _CHANDRA['manager'] = InferenceManager(method='hf')
    images = load_file(str(path), {})
    pages = []
    for i, image in enumerate(images, 1):
        out = _CHANDRA['manager'].generate([BatchInputItem(image=image, prompt_type='ocr_layout')])[0]
        pages.append({'page': i, 'method': 'chandra_ocr_layout', 'text': getattr(out, 'markdown', '') or '', 'confidence': None})
    return '\n\n'.join(p['text'] for p in pages if p['text']), pages, None


def _tesseract(path):
    exe = shutil.which('tesseract')
    if not exe: raise FileNotFoundError('tesseract unavailable')
    if path.suffix.lower() not in IMAGE_EXTS: return '', [], 'tesseract reads images only here'
    text = subprocess.run([exe, str(path), 'stdout'], check=True, capture_output=True, text=True, timeout=300).stdout
    return text, [{'page': 1, 'method': 'tesseract', 'text': text, 'confidence': None}], None


ENGINES = {'pdf_text_layer': _text_layer, 'apple_vision': _apple_vision, 'chandra': _chandra, 'tesseract': _tesseract}


def read_document(path, *, chain=CHAIN, min_chars=MIN_CHARS, engines_override=None):
    """Text of one scanned/native document by the cheapest engine that recovers it. Never raises for engine trouble.
    -> {'schema', 'status': text_layer|ocr|empty|unsupported|missing, 'engine', 'text', 'text_chars', 'pages', 'sha256',
        'attempts': [{'engine', 'status', 'chars', 'reason', 'seconds'}], 'clinical_correctness_verified': False}"""
    path = Path(path)
    base = {'schema': VERSION, 'source_path': str(path), 'clinical_correctness_verified': False, 'new_paid_api_calls': 0}
    if not path.is_file(): return {**base, 'status': 'missing', 'engine': None, 'text': None, 'text_chars': 0, 'pages': [], 'attempts': []}
    if path.suffix.lower() not in DOCUMENT_EXTS:
        return {**base, 'status': 'unsupported', 'engine': None, 'text': None, 'text_chars': 0, 'pages': [], 'sha256': _sha(path), 'attempts': []}
    table = {**ENGINES, **(engines_override or {})}
    attempts, best = [], None
    for name in chain:
        t = time.perf_counter()
        try:
            text, pages, reason = table[name](path)
        except Exception as exc:  # noqa: BLE001 - a missing/failing engine is skipped; the next one tries
            attempts.append({'engine': name, 'status': 'skipped', 'chars': 0, 'reason': f'{type(exc).__name__}: {exc}'[:300],
                             'seconds': time.perf_counter() - t})
            continue
        text = (text or '').strip()
        attempts.append({'engine': name, 'status': 'ok' if text else 'empty', 'chars': len(text), 'reason': reason,
                         'seconds': time.perf_counter() - t})
        if text and (best is None or len(text) > len(best[1])): best = (name, text, pages)
        if len(text) >= min_chars: break
    if best is None:
        return {**base, 'status': 'empty', 'engine': None, 'text': None, 'text_chars': 0, 'pages': [], 'sha256': _sha(path), 'attempts': attempts}
    name, text, pages = best
    return {**base, 'status': 'text_layer' if name == 'pdf_text_layer' else 'ocr', 'engine': name, 'text': text,
            'text_chars': len(text), 'pages': pages, 'sha256': _sha(path), 'attempts': attempts}


# --- wiring: warehouse builds and workspace passage stores --------------------------------------------------------------------

def _document_path(root, relative):
    """Resolved path inside root/documents (never private/), else None."""
    if not isinstance(relative, str) or not relative.startswith('documents/'): return None
    root = Path(root).resolve(); path = (root / relative).resolve()
    if root / 'documents' not in path.parents or (root / 'private') in path.parents: return None
    return path


def ocr_scanned_documents(warehouse_db, root, release, *, limit=None, statuses=('image_only', 'unsupported'), reader=read_document):
    """OCR the v2.3+ outside PDFs whose text layer is empty (source_document_text rows) into source_document_ocr.
    Each file is read once; already-OCR'd records are skipped (rerun = resume). -> summary counts."""
    db = sqlite3.connect(warehouse_db, timeout=60)
    try:
        db.execute(DDL)
        if not db.execute("SELECT 1 FROM sqlite_master WHERE name='source_document_text'").fetchone():
            return {'status': 'no_documents', 'ocr': 0}
        q = ','.join('?' * len(statuses))
        rows = db.execute(f'SELECT t.record_id, t.patient_id, t.relative_path FROM source_document_text t WHERE t.release=? '
                          f'AND t.status IN ({q}) AND NOT EXISTS (SELECT 1 FROM source_document_ocr o WHERE o.release=t.release '
                          f'AND o.record_id=t.record_id) ORDER BY t.relative_path, t.record_id', (release, *statuses)).fetchall()
        if limit: rows = rows[:limit]
        by_file, counts = {}, {}
        for rid, pid, rel in rows:
            if rel not in by_file:
                path = _document_path(root, rel)
                by_file[rel] = reader(path) if path else {'status': 'missing', 'engine': None, 'text': None, 'text_chars': 0,
                                                           'pages': [], 'attempts': [], 'sha256': None}
            got = by_file[rel]
            with db:
                db.execute('INSERT OR REPLACE INTO source_document_ocr VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)',
                           (release, rid, pid, rel, got.get('sha256'), got['status'], got.get('engine'), got.get('text'),
                            got.get('text_chars') or 0, json.dumps(got.get('pages') or []), json.dumps(got.get('attempts') or []),
                            VERSION, time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime())))
            counts[got['status']] = counts.get(got['status'], 0) + 1
        return {'status': 'done', 'records': len(rows), 'files': len(by_file), 'by_status': counts}
    finally:
        db.close()


def extractions(warehouse_db, release):
    """OCR text bound to records, the shape warehouse_retrieval.build(document_extractions=) and
    workspace_passages.add_extractions read: [{record_id, patient_id, text, method}]."""
    db = sqlite3.connect(Path(warehouse_db).resolve().as_uri() + '?mode=ro', uri=True)
    try:
        if not db.execute("SELECT 1 FROM sqlite_master WHERE name='source_document_ocr'").fetchone(): return []
        return [{'record_id': r, 'patient_id': p, 'text': t, 'method': e} for r, p, t, e in db.execute(
            "SELECT record_id, patient_id, text, engine FROM source_document_ocr WHERE release=? AND status='ocr' AND text_chars>0 "
            'ORDER BY record_id', (release,))]
    finally:
        db.close()


def write_extractions(warehouse_db, release, output):
    """extractions() as the {'records': [...]} JSON file warehouse_retrieval.build reads. -> record count."""
    rows = extractions(warehouse_db, release)
    Path(output).write_text(json.dumps({'schema': VERSION, 'release': release, 'records': rows}, indent=1))
    return len(rows)


def _needs_text(r):
    if r.get('source_table') not in ('scanned_documents', 'documents', 'imported_documents', 'hie_documents'): return False
    if any(isinstance(r.get(k), str) and r[k].strip() for k in ('ocr_text', 'extracted_text', 'text')): return False
    return isinstance(r.get('relative_path'), str)


def index_workspace_documents(pipeline_dir, root, *, limit=None, reader=read_document, update_index=True):
    """Document records in a workspace pipeline that carry no text: read their files (under root/documents) and bind the
    text into the workspace passage index. -> {'documents', 'bound', 'by_status', 'index'}."""
    from ..clinical import workspace_passages as WP
    pipeline_dir = Path(pipeline_dir)
    db = sqlite3.connect((pipeline_dir / 'stack.sqlite3').resolve().as_uri() + '?mode=ro', uri=True)
    try:
        bound_already = set()
        pdb = WP.connect(pipeline_dir)
        try: bound_already = {r[0] for r in pdb.execute('SELECT record FROM extractions')}
        finally: pdb.close()
        todo = []
        for rid, pid, payload in db.execute("SELECT id, patient, payload FROM records WHERE stream IN "
                                            "('scanned_documents','documents','imported_documents','hie_documents')"):
            if rid in bound_already: continue
            try: r = json.loads(payload)
            except ValueError: continue
            if _needs_text(r): todo.append((rid, pid, r['relative_path']))
            if limit and len(todo) >= limit: break
    finally:
        db.close()
    rows, counts, cache = [], {}, {}
    for rid, pid, rel in todo:
        if rel not in cache:
            path = _document_path(root, rel)
            cache[rel] = reader(path) if path else {'status': 'missing', 'text': None}
        got = cache[rel]
        counts[got['status']] = counts.get(got['status'], 0) + 1
        if got.get('text'): rows.append({'record_id': rid, 'patient_id': pid, 'text': got['text'], 'method': got.get('engine')})
    if rows: WP.add_extractions(pipeline_dir, rows)
    index = WP.update(pipeline_dir) if (rows and update_index) else None
    return {'documents': len(todo), 'bound': len(rows), 'by_status': counts, 'index': index}
