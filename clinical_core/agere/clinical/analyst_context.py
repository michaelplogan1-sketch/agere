"""Lossless data-model documentation lookup through the existing SQL tool.

Rules/decision defaults remain resident. Every full documentation section stays
in a request-local read-only view; the index is not a clinical summary or a
restriction on which tables, conditions or analytical capabilities may be used.
"""
import hashlib
import json
import re

VERSION = 'analyst-documentation/2'
VIEW = 'agere_data_model_context'
COLUMNS = ('section_id','title','sha256','documentation_text')
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
        previous=db.execute('SELECT type FROM sqlite_temp_master WHERE name=?',(VIEW,)).fetchone()
        if previous:
            if previous[0]not in ('view','table'):raise ValueError('Unexpected native documentation object')
            db.execute(f'DROP {previous[0].upper()} temp.{VIEW}')
    finally:
        db.execute('PRAGMA query_only=ON')
        db.set_authorizer(semantic._authorize)
    columns = list(COLUMNS)
    define_result(semantic, VIEW, columns, [[r[c] for c in columns] for r in rows])
    semantic._local.documentation_identity = identity
    semantic._local.documentation_sections = {r['section_id']: r for r in rows}
    # Column signatures remain in full native text. The index names the table;
    # it never substitutes an abbreviated signature for the actual contract.
    index = [{'section_id': r['section_id'],
              'title': re.sub(r'^(### [a-zA-Z_][\w]*)\(.*$', r'\1', r['title'])}
             for r in rows]
    # These exact versioned defaults govern every subsequent interpretation.
    # Keep them resident instead of making each actor rediscover their meaning.
    pinned = [r for r in rows if r['title'].startswith(('GOVERNED SQL MACROS', 'CLINICAL OWNER DEFINITIONS'))]
    resident = ''.join('\nPINNED DOCUMENTATION '+r['section_id']+' sha256='+r['sha256']+'\n'+r['documentation_text'] for r in pinned)
    return prefix + resident + '\nCOMPLETE DATA MODEL DOCUMENTATION INDEX (' + VERSION + '):\n' + json.dumps(index) + '\n' + (
        f'All sections above are available in full through query_sql: '
        f'SELECT section_id, title, sha256, documentation_text FROM {VIEW} WHERE section_id=\'doc_0\'. '
        'Use the applicable section IDs from the index before using their columns, units or definitions. '
        'Query additional sections whenever needed; no table or capability has been removed. '
        'Titles alone do not establish semantics. This view contains documentation, never patient evidence. '
        'A truncated tool preview is not a complete section; request that section alone or read its remaining text. '
        'All decision defaults and owner rules above still apply.\n')


def bind_loaded(semantic, sql, result):
    """Identify complete first-party sections actually returned by native SQL.

    A matching column name, claimed SHA or preview is insufficient. The query
    may read only the installed documentation view, and every returned row must
    match its exact current section. Partial rows stay ordinary native results.
    """
    import sqlglot
    from sqlglot import exp
    rows = result.get('rows')
    if not rows or not isinstance(rows, list):return result
    local = getattr(semantic, '_local', None)
    identity = getattr(local, 'documentation_identity', None)
    known = getattr(local, 'documentation_sections', {})
    if not identity or not known:return result
    try:
        tree = sqlglot.parse_one(sql, read='sqlite')
    except sqlglot.errors.SqlglotError:return result
    tables = list(tree.find_all(exp.Table)) if tree is not None else []
    if not tables or any(t.name != VIEW for t in tables):return result
    sections = {}
    for row in rows:
        if not isinstance(row, dict) or not {'section_id', 'documentation_text'} <= set(row) or set(row)-set(COLUMNS):return result
        expected = known.get(row['section_id'])
        if expected is None or any(expected[k] != value for k, value in row.items()):return result
        if hashlib.sha256(row['documentation_text'].encode()).hexdigest() != expected['sha256']:return result
        sections[expected['section_id']] = expected
    return {**result, 'loaded_documentation': {'identity': identity, 'sections': list(sections.values())}}
