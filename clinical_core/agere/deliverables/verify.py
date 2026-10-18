"""The number verifier: every number a deliverable shows must come back from an executed tool result.

verify(document, bundle) checks a wrapped artifact (runtime.wrap) and returns {'passed', 'violations', ...}:
  1. the embedded data block is exactly the bundle (the page cannot carry data the tools did not return);
  2. every number in the visible text (scripts, styles and the data block removed; dates and identifiers skipped) matches
     a bundle number at the displayed precision (as is, x100 for %, /1e3 for K, /1e6 for M; sign-insensitive), and is
     reported with the receipts it resolves to. Integers 0-10 are allowed as structure (step numbers, "top 5");
  3. every data-bind path resolves in the bundle;
  4. numeric literals in page scripts are layout-sized integers (<= 2000), common constants, or bundle numbers, so a page
     cannot type an invented figure into its JavaScript;
  5. no external network: no http(s):// or protocol-relative URLs, imports, iframes, sockets or beacons; fetch only to the
     same-origin /api/workspaces/ write-back path.
Fail closed: a document with any violation is not delivered (the composer gets one repair attempt first).
"""
from __future__ import annotations

from html.parser import HTMLParser
import json
import math
import re

from . import runtime
from .receipts import Bundle, fingerprint

STRUCTURAL_MAX = 10
SCRIPT_INT_MAX = 2000
SCRIPT_CONSTANTS = {0.5, 0.25, 0.75, 0.1, 0.05, 0.95, 0.9, 0.01, 0.001, 0.2, 0.3, 0.4, 0.6, 0.7, 0.8, 0.85, 0.15, 0.35, 0.45, 0.55,
                    0.65, 0.02, 0.025, 0.975, 0.99, 0.005, 1.5, 2.5, 1e3, 1e4, 1e5, 1e6, 1e9, 1e-6, 1e-9, 1e-12, 3600, 86400, 3600000,
                    86400000, 0.08, 0.06, 0.04, 0.03, 0.12, 0.18, 0.35, 0.6, 0.9}
FACTORS = {'': (1.0,), '%': (100.0, 1.0), 'pts': (100.0, 1.0), 'K': (1e-3,), 'M': (1e-6,), 'B': (1e-9,), 'x': (1.0,)}

DATE_RE = re.compile(r'\b\d{4}-\d{2}(?:-\d{2})?(?:T\d{2}:\d{2}(?::\d{2})?Z?)?\b|\b\d{1,2}:\d{2}(?::\d{2})?\b')
IDENT_RE = re.compile(r'\b[A-Za-z][A-Za-z0-9]*(?:[-_][A-Za-z0-9]+)+\b|\b[A-Za-z]+\d[\w]*\b|\b\d+[A-Za-z][A-Za-z0-9]*\b(?<!\d[KMB])')
NUM_RE = re.compile(r'(?<![\w.])([-−]?)\$?(\d{1,3}(?:,\d{3})+|\d+)(\.\d+)?\s?(%|pts\b|K\b|M\b|B\b|x\b)?')
SCRIPT_NUM_RE = re.compile(r'(?<![\w$.#])(\d+\.\d+|\.\d+|\d+)(e[+-]?\d+)?(?![\w$])')
NETWORK_RES = [
    (re.compile(r'https?://', re.I), 'external URL'),
    (re.compile(r'''(?:src|href|action|poster|data)\s*=\s*["']?\s*//''', re.I), 'protocol-relative URL'),
    (re.compile(r'@import', re.I), 'CSS @import'),
    (re.compile(r'<\s*(?:iframe|object|embed|link)\b', re.I), 'embedded external resource tag'),
    (re.compile(r'\bXMLHttpRequest\b|\bWebSocket\b|\bsendBeacon\b|\bEventSource\b', re.I), 'network API'),
    (re.compile(r'\bimport\s*\(|^\s*import\s', re.I | re.M), 'module import'),
    (re.compile(r'''\bfetch\s*\(\s*(?!['"`]/api/workspaces/)(?!\s*['"`]?\s*\+?\s*['"`]/api/workspaces/)(?![A-Za-z_$][\w$]*\s*[,)])''', re.I),
     'fetch to a non-workspace URL'),
]


class _Text(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.text, self.binds, self.scripts, self.styles = [], [], [], []
        self._skip, self._script_buf = None, []

    def handle_starttag(self, tag, attrs):
        a = dict(attrs)
        if 'data-bind' in a:
            self.binds.append(a['data-bind'])
        if tag in ('script', 'style'):
            self._skip = (tag, a.get('id'), a.get('type'))
            self._script_buf = []
        for k in ('title', 'aria-label', 'alt', 'placeholder'):
            if a.get(k) and tag not in ('script', 'style'):
                self.text.append(' ' + a[k] + ' ')

    def handle_endtag(self, tag):
        if self._skip and tag == self._skip[0]:
            body = ''.join(self._script_buf)
            if tag == 'script' and self._skip[1] != 'agere-data':
                self.scripts.append(body)
            elif tag == 'style':
                self.styles.append(body)
            self._skip = None

    def handle_data(self, data):
        if self._skip:
            self._script_buf.append(data)
        else:
            self.text.append(data)


class NumberIndex:
    def __init__(self, bundle):
        self.idx = bundle.numbers()
        self.by = {}
        for b, rids in self.idx.items():
            for f in (1.0, 100.0, 1e-3, 1e-6, 1e-9):
                x = abs(b) * f
                for d in range(0, 4):
                    self.by.setdefault((f, d), {}).setdefault(round(x, d), set()).update(rids)

    def match(self, value, decimals, suffix=''):
        """-> set of receipt ids the displayed number resolves to (empty = no match)."""
        v = abs(value)
        for f in FACTORS.get(suffix, (1.0,)):
            hit = self.by.get((f, min(decimals, 3)), {}).get(round(v, min(decimals, 3)))
            if hit:
                return hit
        return set()

    def exact(self, value):
        if value in self.idx:
            return self.idx[value]
        for d in range(0, 4):
            hit = self.by.get((1.0, d), {}).get(round(abs(value), d))
            if hit and abs(round(abs(value), d) - abs(value)) < 1e-12:
                return hit
        return set()


JS_STRING_RE = re.compile(r"'((?:\\.|[^'\\\n])*)'|\"((?:\\.|[^\"\\\n])*)\"|`((?:\\.|[^`\\])*)`", re.S)


def js_string_numbers(code):
    """Numbers typed inside JS string / template literals (text a script will write into the page). Layout-sized integers and
    integer percentages (CSS) are left to the literal rule; money, grouped numbers, decimals and large integers are checked."""
    code = re.sub(r'/\*.*?\*/', ' ', code, flags=re.S)
    out = []
    for m in JS_STRING_RE.finditer(code):
        s = next(g for g in m.groups() if g is not None)
        s = re.sub(r'\$\{[^}]*\}', ' ', s)
        for tok in text_numbers(s):
            money = '$' in tok['text']
            grouped = ',' in tok['text']
            if not money and not grouped:
                if tok['decimals'] == 0 and tok['value'] <= SCRIPT_INT_MAX:
                    continue
                if tok['decimals'] > 0 and tok['value'] in SCRIPT_CONSTANTS:
                    continue
            out.append(tok)
    return out


def _strip_js(code):
    code = re.sub(r'/\*.*?\*/', ' ', code, flags=re.S)
    code = re.sub(r'(?<![:\\])//[^\n]*', ' ', code)
    code = re.sub(r'`(?:\\.|[^`\\])*`', lambda m: re.sub(r'\$\{[^}]*\}', ' ', m.group(0)) and '``', code)
    code = re.sub(r"'(?:\\.|[^'\\\n])*'|\"(?:\\.|[^\"\\\n])*\"", "''", code)
    return code


def text_numbers(text):
    t = DATE_RE.sub(' ', text)
    t = IDENT_RE.sub(' ', t)
    out = []
    for m in NUM_RE.finditer(t):
        digits = m.group(2).replace(',', '')
        frac = m.group(3) or ''
        try:
            v = float(digits + frac)
        except ValueError:
            continue
        out.append({'text': m.group(0).strip(), 'value': v, 'decimals': len(frac) - 1 if frac else 0, 'suffix': (m.group(4) or '').strip()})
    return out


def verify(document, bundle, *, structural_max=STRUCTURAL_MAX, check_data_block=True):
    bundle = bundle if isinstance(bundle, Bundle) else Bundle.from_json(bundle)
    violations = []
    data_text, rest = runtime.split(document)
    if check_data_block:
        if data_text is None:
            violations.append({'kind': 'data_block_missing', 'detail': 'no <script id="agere-data"> block'})
        else:
            try:
                embedded = json.loads(data_text.replace('<\\/', '</'))
                if fingerprint(embedded.get('results')) != fingerprint(bundle.results) or \
                        fingerprint(embedded.get('receipts')) != fingerprint(bundle.receipts):
                    violations.append({'kind': 'data_block_mismatch', 'detail': 'embedded data differs from the executed tool results'})
            except ValueError as exc:
                violations.append({'kind': 'data_block_invalid', 'detail': str(exc)[:200]})
    p = _Text()
    p.feed(rest)
    idx = NumberIndex(bundle)
    checked = []
    text = ' '.join(p.text)
    for tok in text_numbers(text):
        if not tok['suffix'] and tok['decimals'] == 0 and tok['value'] <= structural_max:
            continue
        rids = idx.match(tok['value'], tok['decimals'], tok['suffix'])
        checked.append({'text': tok['text'], 'receipts': sorted(rids)[:5]})
        if not rids:
            violations.append({'kind': 'number_not_in_data', 'detail': tok['text'],
                               'context': _context(text, tok['text'])})
    unresolved = []
    for path in p.binds:
        try:
            bundle.get(path)
        except (KeyError, IndexError, TypeError):
            unresolved.append(path)
    for path in unresolved:
        violations.append({'kind': 'binding_unresolved', 'detail': path})
    lit_checked = 0
    for code in p.scripts:
        if code.strip() == runtime.JS.strip():
            continue
        for tok in js_string_numbers(code):
            rids = idx.match(tok['value'], tok['decimals'], tok['suffix'])
            checked.append({'text': tok['text'], 'receipts': sorted(rids)[:5], 'where': 'script string'})
            if not rids:
                violations.append({'kind': 'number_in_script_text_not_in_data', 'detail': tok['text'], 'context': _context(code, tok['text'])})
        clean = _strip_js(code)
        for m in SCRIPT_NUM_RE.finditer(clean):
            try:
                v = float(m.group(1) + (m.group(2) or ''))
            except ValueError:
                continue
            lit_checked += 1
            if not math.isfinite(v):
                continue
            if (v == int(v) and v <= SCRIPT_INT_MAX) or v in SCRIPT_CONSTANTS or idx.exact(v):
                continue
            violations.append({'kind': 'script_literal_not_in_data', 'detail': m.group(0), 'context': _context(clean, m.group(0))})
    for code_or_markup in [rest]:
        for rx, label in NETWORK_RES:
            hit = rx.search(_without_runtime(code_or_markup))
            if hit:
                violations.append({'kind': 'network', 'detail': label, 'context': _context(code_or_markup, hit.group(0))})
    seen, uniq = set(), []
    for v in violations:
        k = (v['kind'], v['detail'])
        if k not in seen:
            seen.add(k)
            uniq.append(v)
    return {'passed': not uniq, 'violations': uniq[:200], 'violation_count': len(uniq), 'numbers_checked': len(checked),
            'numbers': checked[:400], 'bindings': len(p.binds), 'unresolved_bindings': unresolved[:50],
            'script_literals_checked': lit_checked, 'receipts': len(bundle.receipts)}


def _without_runtime(doc):
    return doc.replace(runtime.JS, '')


def _context(text, needle, width=70):
    i = text.find(needle)
    if i < 0:
        return ''
    return re.sub(r'\s+', ' ', text[max(0, i - width): i + len(needle) + width]).strip()
