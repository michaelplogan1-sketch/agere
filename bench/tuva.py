"""frontier_tuva: an LLM over the Tuva test warehouse, the way Databricks Genie / Snowflake Cortex Analyst would sit on it.

The model gets Tuva's own published documentation (dbt manifest descriptions) for the semantic layer and core, a compact
catalog of the data marts and terminology, the raw landing zone the engineer kept for feeds Tuva doesn't model, and one
read-only DuckDB SQL tool. It never sees Agere internals. Run with the .venv-tuva interpreter (it has duckdb).
"""
from __future__ import annotations

import json
from pathlib import Path
import re
import threading

from bench.adapters import CONTRACT, MODEL, SEMANTIC_SQL_TOOL, _tool_loop, budget_prompt, post_json
from bench.common import ROOT

TUVA_DB = ROOT.parent / 'artifacts/test-warehouse/tuva.duckdb'
RAW_DB = ROOT.parent / 'artifacts/bench-db/v28-sample-indexed.sqlite3'
MANIFEST = ROOT.parent / 'test_warehouse/agere_connector/target/manifest.json'
FULL_DOCS = ('semantic_layer', 'core')
MART_DOCS = ('cms_hcc', 'hcc_suspecting', 'hcc_recapture', 'chronic_conditions', 'quality_measures', 'ed_classification',
             'ahrq_quality_indicators', 'main', 'connector_gap_fill')
CATALOG_ONLY = ('terminology', 'value_sets')
HIDDEN = ('synthetic_data', 'provider_data')  # Tuva's demo data and NPPES: not part of this client's warehouse

TUVA_RULES = '''You are a data analyst answering questions over a health system's analytics warehouse (DuckDB SQL). It was
built by the data engineering team with the open-source Tuva Project: source feeds are mapped into Tuva's input layer, and
Tuva builds the core data model (claims and clinical, with its encounter grouper and member months), the data marts
(CMS-HCC, chronic conditions, quality measures and readmissions, ED classification, AHRQ, CCSR) and a semantic layer.
Feeds Tuva doesn't model stay in the raw landing zone (raw.records, roster raw.patients) (one row per received record, native fields in the
JSON column payload; use json_extract_string(payload, '$.field')). Plan from the documentation below; query only to
compute the answer or to check a value you cannot get from the documentation. Answer the question as asked: follow its
literal scope, definitions, dates and units. Deduplicate people. Do arithmetic in SQL. You have at most 8 query_sql
calls; tools may return explicit errors or truncation, never treat either as zero. The warehouse reflects data received
through the as_of date given with the question.
'''

TUVA_SQL_TOOL = {**SEMANTIC_SQL_TOOL, 'description': 'Read-only DuckDB SELECT/WITH over the warehouse schemas documented in '
                 'the system prompt (schema-qualify tables, e.g. core.encounter, raw.records). Results are capped '
                 '(2000 rows / 60K characters) and truncation is explicit: aggregate in SQL for complete results.'}

_CONTEXT = {}


class TuvaWarehouse:
    """Read-only DuckDB over the Tuva warehouse plus the raw landing zone; SELECT/WITH only, time-limited, capped."""

    def __init__(self, path=TUVA_DB, raw=RAW_DB, timeout_s=180, manifest=MANIFEST):
        import duckdb
        self.duckdb = duckdb
        self.path, self.raw, self.manifest = Path(path), (Path(raw) if raw else None), Path(manifest)
        self.db = duckdb.connect(str(path), read_only=True)
        if raw:  # a native warehouse may already carry its raw schema; attach the SQLite landing zone only when given
            self.db.execute("ATTACH '" + str(raw).replace("'", "''") + "' AS raw (TYPE sqlite, READ_ONLY)")
        self.db.execute('SET enable_external_access = false')
        self.db.execute('SET lock_configuration = true')
        self.timeout_s = timeout_s
        self.lock = threading.Lock()

    def query(self, sql, *, as_of=None, max_rows=2000, max_chars=60000):
        if not isinstance(sql, str) or not sql.strip() or len(sql) > 30000:
            raise ValueError('A bounded SQL statement is required')
        try:
            statements = self.duckdb.extract_statements(sql)
        except self.duckdb.Error as exc:
            raise ValueError(str(exc).splitlines()[0][:500])
        if len(statements) != 1 or statements[0].type != self.duckdb.StatementType.SELECT:
            raise ValueError('Only one SELECT/WITH statement is allowed')
        if re.search(r'\b(' + '|'.join(HIDDEN) + r')\s*\.', sql, re.I):
            raise ValueError('Schema not available in this warehouse')
        cursor = self.db.cursor()
        timer = threading.Timer(self.timeout_s, cursor.interrupt)
        timer.start()
        try:
            rel = cursor.execute(sql)
            cols = [d[0] for d in rel.description]
            rows, chars, truncated = [], 0, False
            for r in rel.fetchmany(max_rows + 1):
                row = {c: (v if isinstance(v, (int, float, str, type(None))) else str(v)) for c, v in zip(cols, r)}
                chars += len(json.dumps(row, default=str))
                if len(rows) >= max_rows or chars > max_chars:
                    truncated = True
                    break
                rows.append(row)
            return {'columns': cols, 'rows': rows, 'truncated': truncated}
        except self.duckdb.InterruptException:
            raise ValueError(f'Query exceeded {self.timeout_s}s')
        except self.duckdb.Error as exc:
            raise ValueError(str(exc).splitlines()[0][:500])
        finally:
            timer.cancel()
            cursor.close()

    def close(self):
        self.db.close()


def tuva_context(warehouse):
    """Documentation text, built once per warehouse file: Tuva's manifest descriptions + live table list + raw streams."""
    path = Path(getattr(warehouse, 'path', TUVA_DB))
    manifest_path = Path(getattr(warehouse, 'manifest', MANIFEST))
    key = (str(path), path.stat().st_mtime, str(manifest_path))
    if key in _CONTEXT:
        return _CONTEXT[key]
    manifest = json.loads(manifest_path.read_text())
    live = {(s, t) for s, t in warehouse.db.execute(
        "SELECT schema_name, table_name FROM duckdb_tables() UNION SELECT schema_name, view_name FROM duckdb_views() "
        "WHERE NOT internal").fetchall()}
    docs = {}
    for n in manifest['nodes'].values():
        if n['resource_type'] in ('model', 'seed') and (n['schema'], n['alias']) in live:
            docs[(n['schema'], n['alias'])] = n
    out = []
    for schema in FULL_DOCS + MART_DOCS + CATALOG_ONLY:
        tables = sorted(t for s, t in live if s == schema)
        if not tables:
            continue
        out.append(f'\n## Schema {schema}')
        for t in tables:
            n = docs.get((schema, t))
            cols = [r[0] for r in warehouse.db.execute(
                'SELECT column_name FROM duckdb_columns() WHERE schema_name = ? AND table_name = ? ORDER BY column_index',
                [schema, t]).fetchall()]
            desc = ' '.join((n or {}).get('description', '').split())[:400]
            if schema in FULL_DOCS:
                out.append(f'\n### {schema}.{t}' + (f' — {desc}' if desc else ''))
                cdocs = (n or {}).get('columns', {})
                for c in cols:
                    d = ' '.join((cdocs.get(c) or {}).get('description', '').split())[:200]
                    out.append(f'- {c}' + (f': {d}' if d else ''))
            elif schema in MART_DOCS:
                out.append(f'- {schema}.{t}' + (f' — {desc[:200]}' if desc else '') + f' | columns: {", ".join(cols)}')
            else:
                out.append(f'- {schema}.{t}({", ".join(cols[:12])}{", …" if len(cols) > 12 else ""})')
    streams = warehouse.db.execute('SELECT stream, COUNT(*) FROM raw.records GROUP BY 1 ORDER BY 1').fetchall()
    pfields = sorted({k for (p,) in warehouse.db.execute('SELECT payload FROM raw.patients LIMIT 50').fetchall()
                      for k in json.loads(p)})
    out.append('\n## Raw landing zone (attached database raw; always write raw.records / raw.patients)\n'
               '- raw.patients(release, id, payload JSON): the patient roster; payload fields: ' + ', '.join(pfields) + '\n'
               '- raw.records(release, id, patient, stream, event, arrival, identity, supersedes, hash, payload JSON): one row '
               'per received record; a record is superseded when another record names it in supersedes. Streams (record '
               'counts): ' + '; '.join(f'{s} ({n})' for s, n in streams))
    text = '\n'.join(out)
    _CONTEXT[key] = text
    return text


def frontier_tuva(question, warehouse, *, key=None, model=MODEL, max_tools=8, transport=post_json, max_tokens=8192,
                  deadline=None):
    docs = tuva_context(warehouse)
    system = [{'type': 'text', 'text': budget_prompt(TUVA_RULES, max_tools, 8) + CONTRACT},
              {'type': 'text', 'text': docs, 'cache_control': {'type': 'ephemeral'}}]
    user = json.dumps({'question': question['question'], 'as_of': question['as_of']})

    def execute(use):
        if use.get('name') != 'query_sql' or set(use.get('input', {})) != {'sql'}:
            raise ValueError('Only query_sql with one sql string is supported')
        return warehouse.query(use['input']['sql'])

    result = _tool_loop(user, system, [TUVA_SQL_TOOL], execute, key=key, model=model, max_tools=max_tools,
                        transport=transport, extra={'cache_control': {'type': 'ephemeral'}}, max_tokens=max_tokens,
                        deadline=deadline)
    result.update(context_chars=len(docs), tool_call_count=len(result['tool_calls']))
    return result
