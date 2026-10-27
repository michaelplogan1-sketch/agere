"""MedGraphRAG retrieval challenger: a lightweight concept graph over patients, codes and notes, used as a fallback retriever.

MedGraphRAG (arXiv 2408.04187) links passages to medical concepts and retrieves through the graph instead of only by
surface similarity. Agere's version is deliberately small and is NOT a second patient truth store: every node and edge
points back at a received record in the stack, and retrieval returns the stack's own passages with their source offsets.

  nodes   concepts read from the semantic layer: cond:<concept> (condition.concept / ICD-10 category), drug:<class>
          (medication.drug_class), ing:<ingredient> (first word of the received drug name), lab:<concept>
          (measurement.concept). Each node carries its aliases (concept name, compiler aliases, observed names).
  links   record -> node for every structured row that names the concept, and note/message record -> node for every
          alias mentioned in its received text (whole-word, case-insensitive)
  edges   node <-> node co-occurrence across patients (lift over independence, >= MIN_SUPPORT patients), which carries
          "heart failure" to loop diuretics, BNP and HF beta blockers
build(stack_dir) writes <stack>/med-graph.sqlite3 once per source digest. GraphRetriever.search(question, patient=,
patient_ids=, limit=) links the question's words to nodes, expands one hop along the strongest edges (half weight) and
ranks the linked records' passages by summed node weight, then recency. The hybrid retriever merges these candidates by
reciprocal-rank fusion when the measured comparison (reranker_bakeoff) says the graph helps that query type, or when the
hybrid candidate set is short (warehouse_retrieval.Retriever.search). No model calls; synthetic data only.
"""
from collections import Counter, defaultdict
import json
import math
from pathlib import Path
import re
import sqlite3
import time

VERSION = 'med-graph-rag/1'
FILE = 'med-graph.sqlite3'
MIN_SUPPORT = 15            # patients sharing two concepts before an edge is kept
EDGES_PER_NODE = 6          # strongest neighbours kept per node
TEXT_STREAMS = ('notes', 'telephone_messages', 'medication_followup_reports', 'imported_documents', 'encounters',
                'medication_reconciliation', 'condition_assessments')
LAB_ALIASES = {'hba1c': ['a1c', 'hba1c', 'hemoglobin a1c', 'glycated hemoglobin'], 'egfr': ['egfr', 'gfr', 'kidney function'],
               'ldl': ['ldl', 'ldl cholesterol', 'cholesterol'], 'bnp': ['bnp', 'nt-probnp', 'natriuretic'],
               'systolic_bp': ['blood pressure', 'systolic', 'bp'], 'diastolic_bp': ['blood pressure', 'diastolic'],
               'potassium': ['potassium', 'k+'], 'creatinine': ['creatinine'], 'weight': ['weight'], 'heart_rate': ['heart rate', 'pulse'],
               'uacr': ['uacr', 'albumin creatinine', 'microalbumin', 'urine albumin']}


def _semantic(stack_dir):
    p = Path(stack_dir) / 'semantic.sqlite3'
    if not p.exists():
        from . import semantic_layer
        p = semantic_layer.ensure(stack_dir)
    return p


def _ro(path):
    return sqlite3.connect(Path(path).resolve().as_uri() + '?mode=ro', uri=True, check_same_thread=False)


def _digest(stack_dir):
    run = Path(stack_dir) / 'run.json'
    return json.loads(run.read_text()).get('source_digest') if run.exists() else None


def _ingredient(name):
    m = re.match(r'[A-Za-z][A-Za-z-]{3,}', (name or '').strip())
    return m.group(0).lower() if m else None


def _aliases():
    """Compiler vocabularies: condition concept -> aliases; drug class -> regex alternatives turned into plain aliases."""
    out = defaultdict(set)
    try:
        from .local_compiler import CONDITION_ALIASES, DRUG_ALIASES
        for concept, words in CONDITION_ALIASES.items():
            out['cond:' + concept].update(w for w in words if len(w) > 2)
        for cls, rx in DRUG_ALIASES.items():
            for alt in str(rx).split('|'):
                plain = re.sub(r'\(\?:[^)]*\)|\[[^\]]*\]|[?()\\]', '', alt).replace('s?', '').strip()
                if len(plain) > 3 and re.fullmatch(r'[a-z0-9 -]+', plain): out['drug:' + cls].add(plain)
    except ImportError:
        pass
    for concept, words in LAB_ALIASES.items():
        out['lab:' + concept].update(words)
    return out


def build(stack_dir, *, force=False):
    """Concept graph for one stack -> report. Rebuilt only when the stack's source digest changes."""
    stack_dir = Path(stack_dir)
    out = stack_dir / FILE
    digest = _digest(stack_dir)
    if out.exists() and not force:
        try:
            db = _ro(out)
            meta = dict(db.execute('SELECT key, value FROM meta'))
            db.close()
            if meta.get('version') == VERSION and meta.get('source_digest') == str(digest):
                return json.loads(meta['report'])
        except sqlite3.Error:
            pass
    t0 = time.time()
    sem = _ro(_semantic(stack_dir))
    links = []                      # (record_id, patient_id, node, basis)
    labels = defaultdict(Counter)   # node -> observed names
    try:
        for rid, pid, concept, label, cat in sem.execute(
                "SELECT record_id, patient_id, concept, coalesce(label, normalized_label), icd10_category FROM condition WHERE record_id IS NOT NULL"):
            node = 'cond:' + concept if concept else ('icd:' + cat if cat else None)
            if node:
                links.append((rid, pid, node, 'condition')); labels[node][(label or '').lower()] += 1
        for rid, pid, cls, name in sem.execute("SELECT record_id, patient_id, drug_class, drug_name FROM medication WHERE record_id IS NOT NULL"):
            ing = _ingredient(name)
            if cls:
                links.append((rid, pid, 'drug:' + cls, 'medication'))
                if ing: labels['drug:' + cls][ing] += 1
            if ing:
                links.append((rid, pid, 'ing:' + ing, 'medication')); labels['ing:' + ing][ing] += 1
        for rid, pid, concept, name in sem.execute("SELECT record_id, patient_id, concept, name FROM measurement WHERE concept IS NOT NULL AND concept != '' AND record_id IS NOT NULL"):
            links.append((rid, pid, 'lab:' + concept, 'measurement')); labels['lab:' + concept][(name or '').lower()] += 1
    finally:
        sem.close()
    aliases = _aliases()
    for node, names in labels.items():
        kind, key = node.split(':', 1)
        aliases[node].add(key.replace('_', ' '))
        if kind in ('ing',):
            aliases[node].add(key)
        if kind == 'drug':
            aliases[node].update(n for n, c in names.most_common(8) if len(n) > 3)  # observed ingredients of the class
    # text mentions in received narrative (one alternation regex per alias length bucket keeps this linear)
    alias_to_nodes = defaultdict(set)
    for node, words in aliases.items():
        if node not in labels: continue  # only concepts this warehouse actually holds
        for w in words:
            w = w.strip().lower()
            if len(w) >= 3 and not w.isdigit(): alias_to_nodes[w].add(node)
    rx = re.compile(r'\b(' + '|'.join(sorted((re.escape(a) for a in alias_to_nodes), key=len, reverse=True)) + r')\b', re.I) if alias_to_nodes else None
    stack = _ro(stack_dir / 'stack.sqlite3')
    text_links = 0
    try:
        from ..narrative_sources import text_fields
        marks = ','.join('?' * len(TEXT_STREAMS))
        for rid, pid, payload in stack.execute(f'SELECT id, patient, payload FROM records WHERE stream IN ({marks})', TEXT_STREAMS):
            if rx is None: break
            r = json.loads(payload)
            hits = set()
            for part in text_fields(r):
                for m in rx.finditer(part['text']):
                    hits.update(alias_to_nodes[m.group(1).lower()])
            for node in hits:
                links.append((rid, pid, node, 'text_mention')); text_links += 1
    finally:
        stack.close()
    # co-occurrence edges (lift over independence across patients)
    by_node = defaultdict(set)
    for _rid, pid, node, _b in links: by_node[node].add(pid)
    patients = len({pid for _r, pid, _n, _b in links}) or 1
    nodes = [n for n, ps in by_node.items() if len(ps) >= MIN_SUPPORT]
    pair = Counter()
    per_patient = defaultdict(set)
    for n in nodes:
        for pid in by_node[n]: per_patient[pid].add(n)
    for pid, ns in per_patient.items():
        ns = sorted(ns)
        for i, a in enumerate(ns):
            for b in ns[i + 1:]: pair[(a, b)] += 1
    neigh = defaultdict(list)
    for (a, b), n in pair.items():
        if n < MIN_SUPPORT: continue
        lift = n * patients / (len(by_node[a]) * len(by_node[b]))
        if lift <= 1.2: continue
        w = round(math.log(lift), 4)
        neigh[a].append((w, b, n)); neigh[b].append((w, a, n))
    edges = []
    for a, rows in neigh.items():
        for w, b, n in sorted(rows, reverse=True)[:EDGES_PER_NODE]: edges.append((a, b, w, n))
    tmp = out.with_suffix('.tmp')
    tmp.unlink(missing_ok=True)
    db = sqlite3.connect(tmp)
    try:
        db.executescript('''CREATE TABLE meta(key TEXT PRIMARY KEY, value TEXT);
            CREATE TABLE node(id TEXT PRIMARY KEY, patients INTEGER, aliases TEXT);
            CREATE TABLE link(record TEXT, patient TEXT, node TEXT, basis TEXT);
            CREATE TABLE edge(a TEXT, b TEXT, weight REAL, patients INTEGER);''')
        db.executemany('INSERT INTO node VALUES (?,?,?)', ((n, len(by_node[n]), json.dumps(sorted(aliases.get(n, ()))[:40])) for n in by_node))
        db.executemany('INSERT INTO link VALUES (?,?,?,?)', sorted(set(links)))
        db.executemany('INSERT INTO edge VALUES (?,?,?,?)', edges)
        db.execute('CREATE INDEX link_node ON link(node, patient)')
        db.execute('CREATE INDEX link_patient ON link(patient, node)')
        db.execute('CREATE INDEX edge_a ON edge(a)')
        report = {'schema': VERSION, 'source_digest': digest, 'nodes': len(by_node), 'links': len(set(links)),
                  'text_mention_links': text_links, 'edges': len(edges), 'patients': patients,
                  'seconds': round(time.time() - t0, 1), 'new_paid_api_calls': 0}
        db.executemany('INSERT INTO meta VALUES (?,?)', [('version', VERSION), ('source_digest', str(digest)), ('report', json.dumps(report))])
        db.commit()
    finally:
        db.close()
    tmp.replace(out)
    return report


class GraphRetriever:
    def __init__(self, stack_dir, *, stack_db=None):
        self.path = Path(stack_dir)
        self.db = _ro(self.path / FILE)
        meta = dict(self.db.execute('SELECT key, value FROM meta'))
        if meta.get('version') != VERSION: raise ValueError('Concept graph version changed; rebuild med-graph')
        self.report = json.loads(meta['report'])
        self.identity = f"{VERSION}:{meta.get('source_digest')}"
        self.stack = stack_db or _ro(self.path / 'stack.sqlite3')
        self.alias = defaultdict(set)
        for node, aliases in self.db.execute('SELECT id, aliases FROM node'):
            for a in json.loads(aliases or '[]'):
                self.alias[a.lower()].add(node)
        self._rx = re.compile(r'\b(' + '|'.join(sorted((re.escape(a) for a in self.alias), key=len, reverse=True)) + r')\b', re.I) \
            if self.alias else None

    def close(self):
        self.db.close()

    def link(self, question):
        """Question -> {node: weight}: direct alias hits weight 1, one-hop neighbours along the strongest edges weight 0.5*w."""
        found = {}
        if self._rx is None: return found
        for m in self._rx.finditer(question or ''):
            for node in self.alias[m.group(1).lower()]: found[node] = 1.0
        direct = list(found)
        for node in direct:
            for b, w in self.db.execute('SELECT b, weight FROM edge WHERE a=? ORDER BY weight DESC LIMIT ?', (node, EDGES_PER_NODE)):
                found[b] = max(found.get(b, 0.0), 0.5 * min(1.0, w))
        return found

    def search(self, question, *, patient=None, patient_ids=None, limit=24, scoped_passages=False):
        """-> {'nodes', 'candidates': [{'id','text','source','note_id','start','protected','graph_score'}]}"""
        nodes = self.link(question)
        if not nodes: return {'nodes': {}, 'candidates': []}
        scope = set(patient_ids) if patient_ids is not None else None
        if patient:scope={patient} if scope is None else scope & {patient}
        if scope==set():return {'nodes':nodes,'candidates':[]}
        score = Counter(); patient_of = {}
        for node, w in nodes.items():
            if scope is not None and len(scope) <= 50:
                rows = (r for pid in scope for r in self.db.execute('SELECT record, patient FROM link WHERE node=? AND patient=?', (node, pid)))
            else:
                rows = self.db.execute('SELECT record, patient FROM link WHERE node=?', (node,))
            for rid, pid in rows:
                if scope is not None and pid not in scope: continue
                score[rid] += w; patient_of[rid] = pid
        if not score: return {'nodes': nodes, 'candidates': []}
        top = sorted(score.items(), key=lambda kv: -kv[1])
        if not scoped_passages:top=top[:max(limit * 4,48)]
        candidates = []
        eligible_records=0
        for rid, s in top:
            scope_sql=' AND p.id IN (SELECT id FROM temp.query_scope_passages)' if scoped_passages else ''
            before=len(candidates)
            for pid_, content, source in self.stack.execute(
                    "SELECT p.id, c.text, p.source FROM passages p JOIN contents c ON c.id=p.content WHERE p.patient=? "
                    "AND json_extract(p.source,'$.record_id')=?"+scope_sql+" ORDER BY p.id LIMIT 2", (patient_of[rid], rid)):
                src = json.loads(source)
                candidates.append({'id': str(pid_), 'text': content, 'source': src, 'note_id': src['record_id'], 'start': src['start'],
                                   'protected': False, 'graph_score': round(s, 4)})
            if len(candidates)>before:eligible_records+=1
            if scoped_passages and eligible_records>=max(limit*4,48):break
        candidates.sort(key=lambda r: (-r['graph_score'], -(int((r['source'].get('event_date') or '0000')[:4]) if (r['source'].get('event_date') or '')[:4].isdigit() else 0), r['id']))
        return {'nodes': {k: round(v, 3) for k, v in nodes.items()}, 'candidates': candidates[:limit]}
