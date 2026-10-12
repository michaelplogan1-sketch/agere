"""Content-addressed dependency graph for local synthetic computation.

Only trusted Python operators execute. No remote retrieval, dynamic eval, or
clinical inference is performed by this cache. Scope keys isolate reuse; they
are not a production authorization system.
"""
from __future__ import annotations

import json
import sqlite3
from dataclasses import dataclass
from pathlib import Path

from .contracts import digest


@dataclass(frozen=True)
class Node:
    node_id: str
    scope: str
    payload: str

    @property
    def value(self):
        return json.loads(self.payload)


class DependencyCache:
    def __init__(self, path):
        Path(path).parent.mkdir(parents=True, exist_ok=True)
        self.db = sqlite3.connect(path)
        self.db.executescript("""
            CREATE TABLE IF NOT EXISTS dag_nodes (
                id TEXT PRIMARY KEY, scope TEXT NOT NULL, name TEXT NOT NULL,
                request TEXT NOT NULL, payload TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS dag_edges (
                parent TEXT NOT NULL, child TEXT NOT NULL, PRIMARY KEY(parent, child));
            CREATE TABLE IF NOT EXISTS dag_heads (
                scope TEXT NOT NULL, name TEXT NOT NULL, node TEXT NOT NULL,
                PRIMARY KEY(scope, name));
        """)
        self.hits = self.misses = 0

    def close(self):
        self.db.close()

    def _head(self, scope, name, node_id):
        self.db.execute("INSERT INTO dag_heads VALUES (?,?,?) ON CONFLICT(scope,name) DO UPDATE SET node=excluded.node", (scope, name, node_id))

    def source(self, scope, name, value):
        if not isinstance(scope, str) or not scope or not isinstance(name, str) or not name:
            raise ValueError("Source scope and name must be explicit")
        request = {"kind": "source", "scope": scope, "name": name, "content": digest(value)}
        node_id = digest(request)
        payload = json.dumps(value, sort_keys=True, allow_nan=False)
        with self.db:
            self.db.execute("INSERT OR IGNORE INTO dag_nodes VALUES (?,?,?,?,?)", (node_id, scope, name, json.dumps(request), payload))
            self._head(scope, name, node_id)
        return Node(node_id, scope, payload)

    def resolve(self, scope, name, revision, dependencies, parameters, compute):
        if not all(isinstance(s, str) and s for s in (scope, name, revision)):
            raise ValueError("An operator requires scope, name and revision")
        if not isinstance(dependencies, dict) or not dependencies:
            raise ValueError("Explicit dependencies required")
        for dep in dependencies.values():
            if dep.scope not in {scope, "shared-policy"}:
                raise ValueError("Cross-patient dependency reuse is forbidden")
            stored = self.db.execute("SELECT scope,payload FROM dag_nodes WHERE id=?", (dep.node_id,)).fetchone()
            if stored != (dep.scope, dep.payload):
                raise ValueError("Dependency is absent or changed")
        request = {"kind": "operator", "scope": scope, "name": name, "revision": revision,
                   "dependencies": {k: v.node_id for k, v in sorted(dependencies.items())}, "parameters": parameters}
        node_id = digest(request)
        stored = self.db.execute("SELECT payload FROM dag_nodes WHERE id=?", (node_id,)).fetchone()
        if stored:
            self.hits += 1
            payload = stored[0]
        else:
            value = compute({k: v.value for k, v in dependencies.items()}, parameters)
            payload = json.dumps(value, sort_keys=True, allow_nan=False)
            with self.db:
                self.db.execute("INSERT INTO dag_nodes VALUES (?,?,?,?,?)", (node_id, scope, name, json.dumps(request, sort_keys=True), payload))
                self.db.executemany("INSERT INTO dag_edges VALUES (?,?)", [(p, node_id) for p in sorted({v.node_id for v in dependencies.values()})])
            self.misses += 1
        with self.db:
            self._head(scope, name, node_id)
        return Node(node_id, scope, payload)

    def affected_heads(self, node_id):
        """Report currently named results that depend on a supplied old revision."""
        rows = self.db.execute("""WITH RECURSIVE descendants(id) AS (
            SELECT child FROM dag_edges WHERE parent=?
            UNION SELECT e.child FROM dag_edges e JOIN descendants d ON e.parent=d.id)
            SELECT scope,name,node FROM dag_heads WHERE node IN (SELECT id FROM descendants)
            ORDER BY scope,name""", (node_id,)).fetchall()
        return [{"scope": s, "name": n, "node_id": i} for s, n, i in rows]

    def lineage(self, node_id):
        row = self.db.execute("SELECT request FROM dag_nodes WHERE id=?", (node_id,)).fetchone()
        if not row:
            raise ValueError("Unknown dependency node")
        return json.loads(row[0])
