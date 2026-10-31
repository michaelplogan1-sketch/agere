"""PlanFence: validate derived work against its CURRENT premises before it is reused or acted on (DAIR review 2026-10-20
39,59-67; research report 2026-10-18 26,166).

A saved plan (a verified question -> SQL pair replayed by learned_verified_sql / semantic_cache, a bottled query distilled
from verified pairs, a paraphrase-reused contract) was right for the premises it was derived under. premises(graph) reads
what its executable text depends on; validate() re-checks each premise against the stack as it is now:
  schema        every table it reads still exists in the semantic layer, and every table.column it names still exists
  definitions   no clinical definition decided since supersedes it (clinical_definitions.superseded)
  corrections   no user correction of the same question family was recorded after the pair was saved
  context       the pair's saved data-release context equals the current one (learning.context)
  drift         no concept / field / metric it reads drifted in this release (release_drift.touching) when the pair was
                saved before the drift index was installed or carried forward from an earlier release
A failed premise rejects THAT reuse only (proposers.Cascade.graph -> reject; the next proposer re-derives the answer:
heal, never block) and the receipt names the premise. answer_dependencies.validate calls premise_issues() so saved
answers in workspaces are re-validated the same way at action time. Free, read-only.
"""
import json
import re
import sqlite3
import time
from functools import lru_cache
from pathlib import Path

VERSION = 'plan-fence/1'
FENCED = ('learned_verified_sql', 'semantic_cache', 'template_decoding', 'bottled_sql', 'paraphrase_reuse')
_TABLE = re.compile(r'\b(?:from|join)\s+"?([A-Za-z_][A-Za-z0-9_]*)"?', re.I)
_QUALIFIED = re.compile(r'\b([a-z_][a-z0-9_]*)\.([a-z_][a-z0-9_]*)\b')
_CTE = re.compile(r'(?:\bwith\b|,)\s*([A-Za-z_][A-Za-z0-9_]*)\s+as\s*\(', re.I)


def _sqls(graph):
    out = []
    for n in (graph or {}).get('nodes') or []:
        p = n.get('parameters') if isinstance(n, dict) else None
        for k in ('query', 'membership_query', 'unknown_membership_query'):
            if isinstance(p, dict) and isinstance(p.get(k), str): out.append(p[k])
    return out


@lru_cache(maxsize=8)
def _parsed_dependencies(sqls):
    """Reuse pure SQL parsing, never current schema, source or acceptance checks.

    Keys are the complete, ordered executable SQL strings. Immutable results
    prevent a caller from changing another request's dependency set.
    """
    import sqlglot
    from sqlglot import exp
    from sqlglot.optimizer.scope import traverse_scope
    tables, cols = set(), set()
    for sql in sqls:
        # A schema-qualified table is not a table named "main"; quoted CTEs,
        # nested aliases and the bound population relation are not missing feeds.
        tree=sqlglot.parse_one(sql,read='sqlite')
        for scope in traverse_scope(tree) or []:
            for _,(_,source) in scope.selected_sources.items():
                if isinstance(source,exp.Table) and isinstance(source.this,exp.Identifier) and source.name.lower()!='population':
                    tables.add(source.name.lower())
            for column in scope.columns:
                source=scope.sources.get(column.table)
                if isinstance(source,exp.Table) and source.name.lower() in tables:
                    cols.add((source.name.lower(),column.name.lower()))
    return tuple(sorted(tables)),tuple(sorted(cols))


def premises(graph):
    """-> {'tables': [...], 'columns': [(table, column)], 'text'} the executable plan depends on."""
    from .literal_check import executable_text
    tables,cols=_parsed_dependencies(tuple(_sqls(graph)))
    return {'tables':list(tables),'columns':list(cols),
            'text':executable_text([n.get('parameters')for n in (graph or{}).get('nodes')or[]])}


def _schema(path):
    target = Path(path) / 'semantic.sqlite3'
    if not target.exists(): return None
    db = sqlite3.connect(target.resolve().as_uri()+'?mode=ro', uri=True)
    try:
        names = [r[0] for r in db.execute("SELECT name FROM sqlite_master WHERE type IN ('table','view')")]
        schema = {n.lower(): [r[1].lower() for r in db.execute('PRAGMA table_info("'+n.replace('"','""')+'")')] for n in names}
        # The investigator creates this registered documentation view per
        # connection. Its absence from persistent storage is not source drift;
        # exact columns remain a versioned application contract. Arbitrary
        # temporary/result tables are still rejected below.
        from .analyst_context import VIEW,COLUMNS
        schema[VIEW]=list(COLUMNS)
        if 'meta' not in schema: return schema
        day = db.execute("SELECT value FROM meta WHERE key='as_of'").fetchone()
    finally:
        db.close()
    if day:
        # Recreate exactly the registered owner views, not arbitrary missing
        # aliases. Their base relations/columns still have to compile.
        from .semantic_analyst import SemanticWarehouse
        wh = SemanticWarehouse(target)
        try:
            for name in wh.define(day[0]):
                try: cursor = wh.db.execute('SELECT * FROM "'+name.replace('"','""')+'" LIMIT 0')
                except sqlite3.Error: continue
                schema[name.lower()] = [c[0].lower() for c in cursor.description]
        finally:
            wh.close()
    return schema


def _saved_row(path, graph):
    """The verified-store row the replayed SQL came from ({'created', 'context', 'provenance', 'question'}), or None."""
    target = Path(path) / 'learning.sqlite3'
    sqls = _sqls(graph)
    if not target.exists() or not sqls: return None
    with sqlite3.connect(f'file:{target}?mode=ro', uri=True) as db:
        if not db.execute("SELECT 1 FROM sqlite_master WHERE name='verified'").fetchone(): return None
        cols = [r[1] for r in db.execute('PRAGMA table_info(verified)')]
        for row in db.execute('SELECT * FROM verified ORDER BY created DESC'):
            rec = dict(zip(cols, row))
            try: plan = json.loads(rec.get('plan') or '{}')
            except ValueError: continue
            if isinstance(plan, dict) and plan.get('query') and plan['query'].strip() == sqls[0].strip():
                return rec
    return None


def premise_issues(path, question, graph, *, saved=None, schema=None):
    """Readable reasons the plan's premises no longer hold (empty list = current)."""
    issues = []
    prem = premises(graph)
    schema = _schema(path) if schema is None else schema
    if schema is not None:
        gone = [t for t in prem['tables'] if t not in schema]
        if gone: issues.append('schema: table(s) ' + ', '.join(gone) + ' no longer exist in the semantic layer')
        lost = [f'{t}.{c}' for t, c in prem['columns'] if t in schema and c not in schema[t]]
        if lost: issues.append('schema: column(s) ' + ', '.join(lost[:6]) + ' no longer exist')
        try:
            from .clinical_definitions import superseded
            plan = {'query': '\n'.join(_sqls(graph))}
            if superseded(question, plan, schema): issues.append('definitions: a clinical definition decided since supersedes this saved plan')
        except Exception:  # noqa: BLE001 - no definitions module: nothing superseded
            pass
    if saved:
        try:
            from .learning import context
            if saved.get('context') and saved['context'] != context(path):
                issues.append('context: saved under data release / semantic layer ' + str(saved['context'])[:80] + ', not the current one')
        except Exception:  # noqa: BLE001 - no run.json (fixtures)
            pass
        created = float(saved.get('created') or 0)
        try:
            from .request_shapes import family
            target = Path(path) / 'learning.sqlite3'
            with sqlite3.connect(f'file:{target}?mode=ro', uri=True) as db:
                later = [q for q, c in db.execute('SELECT question, created FROM corrections') if (c or 0) > created and family(q) == family(question)]
            if later: issues.append(f'corrections: {len(later)} correction(s) of this question family were recorded after the pair was saved')
        except Exception:  # noqa: BLE001 - no corrections table
            pass
        from . import release_drift
        doc = release_drift.load(path)
        if doc:
            hits = release_drift.touching(path, prem['text'], None)
            installed = doc.get('generated_at') or ''
            saved_at = time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime(created)) if created else ''
            carried = bool(saved.get('provenance'))
            if hits and (carried or (saved_at and saved_at < installed)):
                issues.append('drift: ' + '; '.join(h['message'][:140] for h in hits[:2]) + ' (the pair predates this release)')
    return issues


def validate(stack, question, graph, receipt=None, *, tier=None):
    """-> {'version', 'tier', 'current', 'issues', 'premises': {'tables', 'columns'}, 'saved_at'}"""
    path = getattr(stack, 'path', None)
    saved = _saved_row(path, graph) if path and tier in ('learned_verified_sql', 'semantic_cache') else None
    issues = premise_issues(path, question, graph, saved=saved) if path else []
    prem = premises(graph)
    return {'version': VERSION, 'tier': tier, 'current': not issues, 'issues': issues,
            'premises': {'tables': prem['tables'], 'columns': [f'{t}.{c}' for t, c in prem['columns']][:40]},
            'saved_at': (saved or {}).get('created')}
