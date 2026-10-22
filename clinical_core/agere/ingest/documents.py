"""Scanned/native documents in a raw delivery (PDF and image files) -> landing table document_text.

detect: any .pdf or image file not claimed by an earlier parser. parse: one row per file with the text recovered by
ocr.read_document (PDF text layer first, then local OCR), the engine used, page texts and per-engine attempts. A
document no engine can read still lands (status empty/unsupported), so nothing a customer sent is silently dropped.
"""
from __future__ import annotations

import json
from pathlib import Path

from . import ocr


def detect(path: Path) -> bool:
    return Path(path).suffix.lower() in ocr.DOCUMENT_EXTS


def parse(path: Path, context: dict):
    from ..outside_documents import date_of_service
    got = ocr.read_document(path)
    yield 'document_text', {
        'relpath': context.get('relpath'), 'file_name': Path(path).name, 'sha256': got.get('sha256'), 'status': got['status'],
        'engine': got.get('engine'), 'text': got.get('text'), 'text_chars': got.get('text_chars') or 0,
        'page_count': len(got.get('pages') or []), 'date_of_service': date_of_service(got.get('text')),
        'pages_json': json.dumps(got.get('pages') or []), 'attempts_json': json.dumps(got.get('attempts') or []),
        'reader': ocr.VERSION}
