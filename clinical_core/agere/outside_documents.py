"""Text layer of received outside PDFs (faxes, scans, portal uploads), standard library only.

This is not OCR. A PDF either carries a text layer (text-show operators in its content streams) or is an image-only
scan. `read` returns the text layer as lines, or reports the document as image-only (present but unreadable without an
OCR engine). Fax OCR text supplied by the sender's front office travels in the record itself (`ocr_text`), not here.

Supported: uncompressed and FlateDecode content streams; literal `( )` and hex `< >` strings shown with Tj, TJ, ' and ";
one line per BT..ET block or Td/TD/T* move. Unsupported content (other filters, encrypted files, CID fonts) yields no
text and the reason, never a guess.
"""
import re
import zlib

VERSION = 'outside-document-text/1'
_OBJ_STREAM = re.compile(rb'<<(.*?)>>\s*stream\r?\n', re.S)
_LENGTH = re.compile(rb'/Length\s+(\d+)(?!\s+\d+\s+R)')
_TOKENS = re.compile(rb'\((?:\\.|[^\\)])*\)|<[0-9A-Fa-f\s]*>|\[|\]|BT|ET|T\*|Td|TD|Tj|TJ|\'|"|-?\d+(?:\.\d+)?', re.S)
_ESC = {b'n': b'\n', b'r': b'\r', b't': b'\t', b'b': b'\b', b'f': b'\f', b'(': b'(', b')': b')', b'\\': b'\\'}
DATE_OF_SERVICE = re.compile(r'Date of service:?\s*(\d{1,2})/(\d{1,2})/(\d{4})', re.I)


def _literal(tok):
    body, out, i = tok[1:-1], bytearray(), 0
    while i < len(body):
        c = body[i:i + 1]
        if c == b'\\' and i + 1 < len(body):
            n = body[i + 1:i + 2]
            if n in _ESC: out += _ESC[n]; i += 2; continue
            octal = re.match(rb'[0-7]{1,3}', body[i + 1:i + 4])
            if octal: out.append(int(octal.group(0), 8) & 0xFF); i += 1 + len(octal.group(0)); continue
            i += 1; continue
        out += c; i += 1
    return bytes(out)


def _hex(tok):
    h = re.sub(rb'\s', b'', tok[1:-1])
    if len(h) % 2: h += b'0'
    return bytes.fromhex(h.decode())


def _show(content):
    """Lines of text shown in one content stream."""
    lines, cur = [], []
    for tok in _TOKENS.findall(content):
        if tok in (b'BT', b'ET', b'T*', b'Td', b'TD', b"'", b'"'):
            if cur: lines.append(b''.join(cur)); cur = []
        elif tok.startswith(b'('): cur.append(_literal(tok))
        elif tok.startswith(b'<'): cur.append(_hex(tok))
    if cur: lines.append(b''.join(cur))
    return [l.decode('cp1252', errors='replace') for l in lines]


_OBJ = re.compile(rb'(\d+)\s+(\d+)\s+obj\b(.*?)endobj', re.S)
_REF = re.compile(rb'(\d+)\s+\d+\s+R')


def _objects(data):
    return {int(m.group(1)): m.group(3) for m in _OBJ.finditer(data)}


def _content_refs(objects):
    """Object numbers of page content streams, in page order (only these are parsed for text)."""
    refs = []
    for body in objects.values():
        if not re.search(rb'/Type\s*/Page\b', body): continue
        m = re.search(rb'/Contents\s*(\[[^\]]*\]|\d+\s+\d+\s+R)', body)
        if m: refs += [int(x) for x in _REF.findall(m.group(1))]
    return refs


def read(data):
    """{'text': str or None, 'image_only': bool, 'streams': n, 'reason': why there is no text}."""
    if not data.startswith(b'%PDF'): return {'text': None, 'image_only': False, 'streams': 0, 'reason': 'not_a_pdf'}
    if b'/Encrypt' in data: return {'text': None, 'image_only': False, 'streams': 0, 'reason': 'encrypted'}
    objects = _objects(data)
    images = sum(1 for body in objects.values() if re.search(rb'/Subtype\s*/Image\b', body))
    lines, unsupported, n = [], 0, 0
    for ref in _content_refs(objects):
        m = _OBJ_STREAM.search(objects.get(ref, b''))
        if not m: continue
        obj = objects[ref]; head = m.group(1); length = _LENGTH.search(head); n += 1
        if not length: unsupported += 1; continue
        body = obj[m.end():m.end() + int(length.group(1))]
        filters = re.findall(rb'/(\w+Decode)', head)
        if filters and filters != [b'FlateDecode']: unsupported += 1; continue
        if filters:
            try: body = zlib.decompress(body)
            except zlib.error: unsupported += 1; continue
        if b'BT' in body: lines += _show(body)
    text = '\n'.join(l.rstrip() for l in lines).strip()
    if text: return {'text': text, 'image_only': False, 'streams': n, 'reason': None}
    return {'text': None, 'image_only': images > 0, 'streams': n,
            'reason': 'image_only_no_text_layer' if images else ('unsupported_encoding' if unsupported else 'no_text')}


def date_of_service(text):
    """ISO date from a 'Date of service: MM/DD/YYYY' header line, else None (OCR-damaged years are not repaired)."""
    m = DATE_OF_SERVICE.search(text or '')
    if not m: return None
    month, day, year = int(m.group(1)), int(m.group(2)), int(m.group(3))
    if not (1 <= month <= 12 and 1 <= day <= 31): return None
    return f'{year:04d}-{month:02d}-{day:02d}'
