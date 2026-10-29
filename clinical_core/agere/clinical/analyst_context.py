"""Lossless data-model documentation lookup through the existing SQL tool.

Rules/decision defaults remain resident. Every full documentation section stays
in a request-local read-only view; the index is not a clinical summary or a
restriction on which tables, conditions or analytical capabilities may be used.
"""
import hashlib
import json
import re

VERSION = 'analyst-documentation/1'
VIEW = 'agere_data_model_context'
START = '# Semantic model documentation'
HEADINGS = re.compile(r'(?m)^(?:#{1,3} [^\n]+|REGISTERED MEDICATION GROUP CONTRACTS[^\n]*|GOVERNED SQL MACROS[^\n]*|CLINICAL OWNER DEFINITIONS[^\n]*)$')


def partition(documentation):
    """Exact prefix and ordered sections; no top-k selection or text clipping."""
    start = documentation.find(START)
    prefix, body = (documentation[:start], documentation[start:]) if start >= 0 else ('', documentation)
    points = [m.start() for m in HEADINGS.finditer(body)]
    if not points or points[0] != 0:
        points.insert(0, 0)
    rows = []
    for i, point in enumerate(points):
        text = body[point:points[i+1] if i+1 < len(points) else len(body)]
        title = text.splitlines()[0] if text.splitlines() else 'Complete documentation'
        rows.append({'section_id': f'doc_{i}', 'title': title,
                     'sha256': hashlib.sha256(text.encode()).hexdigest(), 'documentation_text': text})
    if prefix + ''.join(r['documentation_text'] for r in rows) != documentation:
        raise ValueError('Documentation partition changed native content')
    return prefix, rows


def install(semantic, documentation):
    """Return resident rules/index and install every exact section for SQL lookup."""
    from .backstop_agent import define_result
    prefix, rows = partition(documentation)
    identity = hashlib.sha256(documentation.encode()).hexdigest()
    db = semantic.db
    db.set_authorizer(None)
    db.execute('PRAGMA query_only=OFF')
    try:
        db.execute(f'DROP VIEW IF EXISTS temp.{VIEW}')
    finally:
        db.execute('PRAGMA query_only=ON')
        db.set_authorizer(semantic._authorize)
    columns = ['section_id', 'title', 'sha256', 'documentation_text']
    define_result(semantic, VIEW, columns, [[r[c] for c in columns] for r in rows])
    semantic._local.documentation_identity = identity
    # Column signatures remain in full native text. The index names the table;
    # it never substitutes an abbreviated signature for the actual contract.
    index = [{'section_id': r['section_id'],
              'title': re.sub(r'^(### [a-zA-Z_][\w]*)\(.*$', r'\1', r['title'])}
             for r in rows]
    return prefix + '\nCOMPLETE DATA MODEL DOCUMENTATION INDEX (' + VERSION + '):\n' + json.dumps(index) + '\n' + (
        f'All sections above are available in full through query_sql: '
        f'SELECT section_id, title, documentation_text FROM {VIEW} WHERE section_id=\'doc_0\'. '
        'Use the applicable section IDs from the index before using their columns, units or definitions. '
        'Query additional sections whenever needed; no table or capability has been removed. '
        'Titles alone do not establish semantics. This view contains documentation, never patient evidence. '
        'A truncated tool preview is not a complete section; request that section alone or read its remaining text. '
        'All decision defaults and owner rules above still apply.\n')
