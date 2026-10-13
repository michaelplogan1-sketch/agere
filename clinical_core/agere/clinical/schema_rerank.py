"""Schema reranker: choose which semantic tables, columns, record streams, fields and stored values a question needs.

Search-engine retrieval for the verified SQL ladder. nl2sql.schema_for used to show every semantic table, the six
streams a lexical scorer picked, and only the short value lists; a hosted model then failed when the stream, field or
exact stored value it needed was missing or buried. This module:

1. builds a catalog of schema items (table, column, stream, field, value) from the TABLES doc, the received-data
   profile, the semantic layer's small vocabularies (read-only) and the concept / drug-class libraries;
2. extracts gold items from verified SQL (`gold_items`, a careful tokenizer - no sqlglot dependency);
3. generates candidates (BM25 over item descriptions + local sentence-encoder similarity + glossary / literal value
   matches) and scores them with small per-type logistic models trained on verified (question -> SQL) harvests
   (features: lexical, embedding, parent scores, value matches, co-occurrence priors from verified SQL, glossary hits);
4. renders an ordered schema slice: full docs for the selected tables, compact name(columns) for the rest, the top
   streams with their fields and values, a short list of further streams, and the exact stored values that match the
   question - greedily filled within the prompt budget of the current slice (never larger).

Synthetic data only, no network, no paid calls. Without the encoder the reranker falls back to lexical features.
"""
from __future__ import annotations

import hashlib, json, math, os, re, sqlite3, threading
from collections import Counter, defaultdict
from pathlib import Path

import numpy as np

VERSION = 'schema-rerank/1'
ROOT = Path(__file__).resolve().parents[3]
MODEL_FILE = Path(__file__).resolve().parent / 'assets' / 'schema_rerank_model.json'
KINDS = ('table', 'column', 'stream', 'field', 'value')
SEMANTIC_VOCAB_MAX = 400          # distinct values for a semantic text column to be offered as a value item
_STOP = {'the', 'and', 'for', 'with', 'how', 'many', 'what', 'which', 'who', 'are', 'was', 'were', 'have', 'has', 'had',
         'from', 'that', 'this', 'there', 'their', 'our', 'any', 'all', 'per', 'each', 'did', 'does', 'not', 'than', 'more',
         'into', 'over', 'between', 'whose', 'they', 'them', 'been', 'its', 'only', 'also', 'use', 'list', 'show', 'give',
         'count', 'number', 'patients', 'patient'}


def tokens(text):
    """Prefix-6 word tokens (same normalisation as nl2sql._tokens) minus question stop words."""
    return [t[:6] for t in re.findall(r'[a-z0-9]+', str(text).lower().replace('_', ' ')) if len(t) > 2 and t not in _STOP]


def _words(regex_text):
    """Plain words inside a library regex (drug names in a class pattern, synonyms in a concept pattern)."""
    return ' '.join(w for w in re.findall(r'[a-z][a-z0-9-]{2,}', str(regex_text).lower()) if w not in {'excludes'})


# --------------------------------------------------------------------------------------------- catalog
def parse_tables_doc(doc):
    """TABLES doc -> {table: {'columns': [...], 'doc': full text incl. trailing comment lines, 'enums': {col: [values]}}}."""
    tables, last = {}, None
    for line in doc.splitlines():
        m = re.match(r'([a-z_]+)\((.*?)\)\s*(--.*)?$', line) if not line.startswith('--') else None
        if m and '(' in line.split('--')[0]:
            head = line.split('  --')[0]
            name = head.split('(')[0]
            inner = head[len(name) + 1:head.rfind(')')]
            cols, enums = [], {}
            for part in re.split(r',\s*(?![^\[]*\])', inner):
                cm = re.match(r'\s*([a-z0-9_]+)\s*(\[(.*)\])?', part)
                if not cm: continue
                cols.append(cm.group(1))
                if cm.group(3): enums[cm.group(1)] = [v for v in re.findall(r"'([^']+)'", cm.group(3))]
            tables[name] = {'columns': cols, 'doc': line, 'enums': enums, 'compact': f"{name}({', '.join(cols)})"}
            last = name
        elif line.startswith('--') and last:
            tables[last]['doc'] += '\n' + line
    return tables


class Catalog:
    """Every schema item a question could need, with a description for retrieval."""

    def __init__(self, profile, tables_doc, semantic_vocab=None):
        from . import concepts, source_profile
        from .drug_classes import CLASSES, GROUPS
        self.profile = profile
        self.tables = parse_tables_doc(tables_doc)
        self.items, self.index = [], {}
        skip = source_profile.PLANNER_SKIP

        def add(iid, kind, text, parent=None, **extra):
            if iid in self.index: return
            self.index[iid] = len(self.items)
            self.items.append({'id': iid, 'kind': kind, 'text': text, 'parent': parent, **extra})

        for t, spec in self.tables.items():
            comment = ' '.join(re.findall(r'--\s*(.*?)(?=--|$)', spec['doc'].replace('\n', ' ')))
            add(f'table:{t}', 'table', f"{t.replace('_', ' ')} table: {' '.join(c.replace('_', ' ') for c in spec['columns'])}. {comment}")
            for c in spec['columns']:
                enum = ' '.join(spec['enums'].get(c, []))
                add(f'column:{t}.{c}', 'column', f"{t.replace('_', ' ')} {c.replace('_', ' ')} {enum}".strip(), f'table:{t}')
        for s, spec in profile['streams'].items():
            fields = [f for f, fs in spec['fields'].items() if fs.get('populated') and f not in skip]
            add(f'stream:{s}', 'stream', f"{s.replace('_', ' ')} records: " + ' '.join(f.replace('_', ' ') for f in fields[:40]))
            for f in fields:
                vals = spec['fields'][f].get('values') or {}
                add(f'field:{s}.{f}', 'field', f"{s.replace('_', ' ')} {f.replace('_', ' ')} " + ' '.join(str(v) for v in list(vals)[:10]),
                    f'stream:{s}')
                for v in vals:
                    if len(str(v)) > 80: continue
                    add(f'value:{s}.{f}={v}', 'value', f"{str(v).replace('_', ' ')} ({f.replace('_', ' ')} of {s.replace('_', ' ')})",
                        f'field:{s}.{f}', value=str(v), owner=s, col=f, n_values=len(vals))
        cat = concepts.catalog()
        lib = {**concepts.CONDITIONS, **concepts.MEASUREMENTS, **concepts.ENCOUNTERS}
        for group, ids in cat.items():
            for k, label in ids.items():
                spec = lib.get(k, {})
                add(f'concept:{k}', 'value', f"{k.replace('_', ' ')}: {label} {_words(spec.get('text') or spec.get('names') or '')}",
                    None, value=k, owner='concept', col=group, n_values=len(ids))
        members = defaultdict(list)
        for g, cs in GROUPS.items():
            for c in cs: members[c].append(g)
        for k, rx in CLASSES.items():
            add(f'drug_class:{k}', 'value', f"{k.replace('_', ' ')} drug class: {_words(rx)} {' '.join(members.get(k, []))}".replace('_', ' '),
                'column:medication.drug_class', value=k, owner='medication', col='drug_class', n_values=len(CLASSES))
        for (t, c), vals in sorted((semantic_vocab or {}).items()):
            if t not in self.tables or c in ('concept', 'type_concept', 'drug_class', 'stream', 'source_stream'): continue
            for v in vals:
                if len(str(v)) > 80: continue
                add(f'value:{t}.{c}={v}', 'value', f"{str(v).replace('_', ' ')} ({c.replace('_', ' ')} of {t.replace('_', ' ')})",
                    f'column:{t}.{c}', value=str(v), owner=t, col=c, n_values=len(vals))
        for t, spec in self.tables.items():  # enumerations documented inline in TABLES are values too
            for c, vals in spec['enums'].items():
                for v in vals:
                    add(f'value:{t}.{c}={v}', 'value', f"{v.replace('_', ' ')} ({c.replace('_', ' ')} of {t.replace('_', ' ')})",
                        f'column:{t}.{c}', value=v, owner=t, col=c, n_values=len(vals))
        self.kind_of = np.array([KINDS.index(it['kind']) for it in self.items])
        self.parent_idx = np.array([self.index.get(it['parent'], -1) if it['parent'] else -1 for it in self.items])
        self.digest = hashlib.sha256(json.dumps([it['id'] + '|' + it['text'] for it in self.items]).encode()).hexdigest()[:16]
        self._bm25 = None

    # -- BM25 over item descriptions
    def bm25(self, question):
        if self._bm25 is None:
            docs = [Counter(tokens(it['text'])) for it in self.items]
            df = Counter(t for d in docs for t in d)
            lens = np.array([sum(d.values()) for d in docs], dtype=np.float32)
            post = defaultdict(list)
            for i, d in enumerate(docs):
                for t, c in d.items(): post[t].append((i, c))
            n = len(docs)
            self._bm25 = {'post': {t: (np.array([i for i, _ in p]), np.array([c for _, c in p], dtype=np.float32)) for t, p in post.items()},
                          'idf': {t: math.log(1 + (n - df[t] + 0.5) / (df[t] + 0.5)) for t in df}, 'lens': lens, 'avg': float(lens.mean())}
        b = self._bm25
        out = np.zeros(len(self.items), dtype=np.float32)
        for t in set(tokens(question)):
            if t not in b['post']: continue
            idx, tf = b['post'][t]
            norm = tf + 1.2 * (0.25 + 0.75 * b['lens'][idx] / b['avg'])
            out[idx] += b['idf'][t] * tf * 2.2 / norm
        return out


def semantic_vocabulary(path):
    """Small text vocabularies of the semantic layer tables (read-only), cached outside the live stack by digest."""
    target = Path(path) / 'semantic.sqlite3'
    if not target.exists(): return {}
    db = sqlite3.connect(f'file:{target}?mode=ro', uri=True)
    try:
        meta = dict(db.execute('SELECT key, value FROM meta').fetchall())
        cache = ROOT / 'artifacts' / 'schema-rerank' / f"vocab-{meta.get('source_digest', 'x')[:16]}-{meta.get('version', 'x')}.json"
        if cache.exists():
            return {tuple(k.split('.', 1)): v for k, v in json.loads(cache.read_text()).items()}
        from .nl2sql import TABLES
        out = {}
        for t, spec in parse_tables_doc(TABLES).items():
            have = {r[1]: (r[2] or '').upper() for r in db.execute(f'PRAGMA table_info({t})')}
            for c in spec['columns']:
                if c not in have or c.endswith(('_id', '_date', '_time', '_cents')) or c in ('payload', 'evidence_text', 'value_canonical', 'onset'):
                    continue
                if have[c] and 'TEXT' not in have[c]: continue
                n = db.execute(f'SELECT count(DISTINCT {c}) FROM {t}').fetchone()[0]
                if not 1 < n <= SEMANTIC_VOCAB_MAX: continue
                vals = [r[0] for r in db.execute(f'SELECT {c}, count(*) FROM {t} WHERE {c} IS NOT NULL GROUP BY 1 ORDER BY 2 DESC')]
                if not vals or not all(isinstance(v, str) for v in vals): continue
                out[(t, c)] = vals
        try:
            cache.parent.mkdir(parents=True, exist_ok=True)
            cache.write_text(json.dumps({f'{t}.{c}': v for (t, c), v in out.items()}))
        except OSError:
            pass
        return out
    finally:
        db.close()


# --------------------------------------------------------------------------------------------- gold extraction
_TOKEN_RX = re.compile(r"""('(?:[^']|'')*')|("[^"]*")|([A-Za-z_][A-Za-z0-9_]*(?:\.[A-Za-z_][A-Za-z0-9_]*)?)|(\d+(?:\.\d+)?)|(<>|!=|<=|>=|\|\||[=<>(),*+\-/;%])""")
_KEYWORDS = {'select', 'from', 'where', 'join', 'left', 'right', 'inner', 'outer', 'cross', 'on', 'and', 'or', 'not', 'in', 'is',
             'null', 'as', 'group', 'by', 'order', 'having', 'limit', 'union', 'all', 'distinct', 'case', 'when', 'then', 'else',
             'end', 'with', 'exists', 'between', 'like', 'asc', 'desc', 'over', 'partition', 'materialized', 'using', 'natural',
             'except', 'intersect', 'offset', 'filter', 'window', 'recursive', 'glob', 'escape', 'collate', 'cast', 'full'}
_STREAM_COLS = {'stream', 'source_stream'}
_FUNCS_TRANSPARENT = {'lower', 'upper', 'trim', 'coalesce', 'ifnull'}


def _lex(sql):
    sql = re.sub(r'--[^\n]*', ' ', sql)
    out = []
    for m in _TOKEN_RX.finditer(sql):
        s, dq, ident, num, op = m.groups()
        if s is not None: out.append(('str', s[1:-1].replace("''", "'")))
        elif dq is not None: out.append(('id', dq[1:-1]))
        elif ident is not None: out.append(('kw', ident.lower()) if ident.lower() in _KEYWORDS else ('id', ident))
        elif num is not None: out.append(('num', num))
        else: out.append(('op', op))
    return out


def gold_items(sql, catalog):
    """Schema items a (verified) query uses: tables, columns, streams, payload fields and stored literal values.
    Returns {'items': set(ids), 'unresolved': [literals that match no catalog value]}."""
    toks = _lex(sql)
    tables = catalog.tables
    alias, ctes, used_tables = {}, set(), []
    raw_records = set()  # raw benchmark store: records(stream, payload, patient)
    for i, (k, v) in enumerate(toks):  # CTE names
        if k == 'id' and i + 2 < len(toks) and toks[i + 1] == ('kw', 'as') and toks[i + 2] == ('op', '(') and \
                (i == 0 or toks[i - 1] in (('kw', 'with'), ('op', ','), ('kw', 'recursive'))):
            ctes.add(v.lower())
    i = 0
    while i < len(toks):
        k, v = toks[i]
        if (k == 'kw' and v in ('from', 'join')) or (k == 'op' and v == ',' and _in_from(toks, i)):
            j = i + 1
            if j < len(toks) and toks[j][0] == 'id':
                name = toks[j][1].lower()
                a = None
                if j + 1 < len(toks) and toks[j + 1] == ('kw', 'as') and j + 2 < len(toks) and toks[j + 2][0] == 'id': a = toks[j + 2][1].lower()
                elif j + 1 < len(toks) and toks[j + 1][0] == 'id': a = toks[j + 1][1].lower()
                if name in tables and name not in ctes:
                    used_tables.append(name)
                    alias[name] = name
                    if a: alias[a] = name
                elif name == 'records':
                    raw_records.add(name); alias['records'] = 'record'
                    if a: alias[a] = 'record'
        i += 1
    items, unresolved = set(), []
    for t in used_tables: items.add(f'table:{t}')
    scope = list(dict.fromkeys(used_tables)) or (['record'] if raw_records else [])

    def resolve(ref):
        """column reference -> list of (table, column)."""
        if '.' in ref:
            a, c = ref.lower().split('.', 1)
            t = alias.get(a)
            return [(t, c)] if t and (c in tables.get(t, {}).get('columns', []) or (t == 'record' and c in ('stream', 'payload', 'patient'))) else []
        c = ref.lower()
        return [(t, c) for t in scope if c in tables.get(t, {}).get('columns', [])]

    for k, v in toks:  # columns
        if k == 'id':
            for t, c in resolve(v):
                if f'column:{t}.{c}' in catalog.index: items.add(f'column:{t}.{c}')
    # literal comparisons: <ref> op 'lit' | <ref> [NOT] IN ('a', ...) | <ref> [NOT] LIKE 'x'
    streams_named = []
    lits = []  # (kind, target, literal, op)
    n = len(toks)
    for i in range(n):
        target = _target_before(toks, i)
        if target is None: continue
        j = i
        if toks[j] == ('kw', 'not'): j += 1
        if j >= n: continue
        op = toks[j]
        if op in (('op', '='), ('op', '<>'), ('op', '!=')) or op == ('kw', 'like') or op == ('kw', 'glob'):
            if j + 1 < n and toks[j + 1][0] == 'str': lits.append((target, toks[j + 1][1], op[1]))
        elif op == ('kw', 'in') and j + 1 < n and toks[j + 1] == ('op', '('):
            m, vals = j + 2, []
            while m < n and toks[m] != ('op', ')'):  # literal lists only, never a subquery
                if toks[m][0] == 'str': vals.append(toks[m][1])
                elif toks[m] != ('op', ','): vals = None; break
                m += 1
            for lit in vals or []: lits.append((target, lit, 'in'))
    for target, lit, op in lits:
        if target[0] == 'col':
            for t, c in resolve(target[1]):
                if c in _STREAM_COLS:
                    if lit in catalog.profile['streams']: streams_named.append(lit); items.add(f'stream:{lit}')
                    continue
                if c in ('concept', 'type_concept'):
                    (items.add(f'concept:{lit}') if f'concept:{lit}' in catalog.index else unresolved.append(f'{t}.{c}={lit}'))
                    continue
                if c == 'drug_class':
                    (items.add(f'drug_class:{lit}') if f'drug_class:{lit}' in catalog.index else unresolved.append(f'{t}.{c}={lit}'))
                    continue
                if c == 'field' and t == 'link': continue
                hit = _value_ids(catalog, f'value:{t}.{c}=', lit, op)
                if hit: items.update(hit)
                elif not re.fullmatch(r'[\d\-: %.]*', lit): unresolved.append(f'{t}.{c}={lit}')
    # payload fields (resolved after streams are known)
    for target, lit, op in lits:
        if target[0] == 'json':
            owners = [s for s in streams_named if f'field:{s}.{target[1]}' in catalog.index]
            for s in owners:
                hit = _value_ids(catalog, f'value:{s}.{target[1]}=', lit, op)
                if hit: items.update(hit)
                elif not re.fullmatch(r'[\d\-: %.]*', lit): unresolved.append(f'{s}.{target[1]}={lit}')
    for m in re.finditer(r"json_extract\(\s*(?:\w+\.)?payload\s*,\s*'\$\.([A-Za-z0-9_]+)", sql, re.I):
        f = m.group(1)
        for s in streams_named or []:
            if f'field:{s}.{f}' in catalog.index: items.add(f'field:{s}.{f}')
    return {'items': items, 'unresolved': unresolved}


def _in_from(toks, i):
    """Is the comma at i inside a FROM list (old-style join)?"""
    depth = 0
    for k in range(i - 1, -1, -1):
        t = toks[k]
        if t == ('op', ')'): depth += 1
        elif t == ('op', '('):
            if depth == 0: return False
            depth -= 1
        elif depth == 0 and t[0] == 'kw':
            if t[1] == 'from': return True
            if t[1] in ('select', 'where', 'on', 'group', 'order', 'having', 'join', 'by', 'in', 'values'): return False
    return False


def _target_before(toks, i):
    """The column / json field whose comparison starts at token i (i is the operator or NOT)."""
    if i == 0: return None
    p = toks[i - 1]
    if p[0] == 'id': return ('col', p[1])
    if p == ('op', ')'):  # json_extract(x.payload,'$.f') or lower(col)
        depth, k = 0, i - 1
        while k >= 0:
            if toks[k] == ('op', ')'): depth += 1
            elif toks[k] == ('op', '('):
                depth -= 1
                if depth == 0: break
            k -= 1
        if k <= 0: return None
        fn = toks[k - 1]
        inner = toks[k + 1:i - 1]
        if fn[0] == 'id' and fn[1].lower() == 'json_extract':
            path = [v for kk, v in inner if kk == 'str']
            if path and path[0].startswith('$.'): return ('json', re.split(r'[.\[]', path[0][2:])[0])
        if fn[0] == 'id' and fn[1].lower() in _FUNCS_TRANSPARENT:
            if inner and inner[0][0] == 'id' and (len(inner) == 1 or inner[1] == ('op', ',')): return ('col', inner[0][1])
            if inner and inner[0] == ('id', 'json_extract') or (inner and inner[0][0] == 'id' and inner[0][1].lower() == 'json_extract'):
                path = [v for kk, v in inner if kk == 'str']
                if path and path[0].startswith('$.'): return ('json', re.split(r'[.\[]', path[0][2:])[0])
    return None


def _value_ids(catalog, prefix, lit, op):
    """Catalog value ids under prefix matching a literal (exact, case-insensitive, or LIKE pattern; capped)."""
    if op in ('like', 'glob'):
        core = lit.strip('%*').lower()
        if not core or '%' in core or '_' in core.replace('_', '') : pass
        rx = re.compile('^' + '.*'.join(re.escape(p) for p in re.split(r'[%*]', lit.lower())) + '$')
        hits = [it['id'] for it in _by_prefix(catalog, prefix) if rx.match(it['value'].lower())]
        return hits[:8]
    exact = prefix + lit
    if exact in catalog.index: return [exact]
    low = lit.lower()
    return [it['id'] for it in _by_prefix(catalog, prefix) if it['value'].lower() == low][:1]


def _by_prefix(catalog, prefix):
    cache = catalog.__dict__.setdefault('_prefix_cache', {})
    if prefix not in cache:
        cache[prefix] = [it for it in catalog.items if it['id'].startswith(prefix)]
    return cache[prefix]


# --------------------------------------------------------------------------------------------- features / scorer
GROUPS = ('table', 'column', 'stream', 'field', 'concept', 'value')
FEATURES = ('bias', 'bm25_log', 'bm25_rel', 'emb', 'emb_gap', 'emb_rank', 'parent_bm25', 'parent_emb', 'root_emb',
            'literal', 'name_cover', 'prior', 'cooc_max', 'cooc_mean', 'parent_cooc', 'glossary', 'legacy_rank',
            'n_values_log', 'has_encoder')
CANDIDATES = {'table': 16, 'column': 400, 'stream': 160, 'field': 300, 'concept': 120, 'value': 400}


def _group(item):
    if item['kind'] != 'value': return item['kind']
    return 'concept' if item['id'].startswith(('concept:', 'drug_class:')) else 'value'


def _qgrams(question):
    t = tokens(question)
    return set(t) | {a + '_' + b for a, b in zip(t, t[1:])}


def _norm_text(text):
    return ' ' + re.sub(r'[^a-z0-9]+', ' ', str(text).lower().replace('_', ' ')).strip() + ' '


class Priors:
    """Co-occurrence of question terms with gold schema items in verified SQL (training questions only)."""

    def __init__(self, pairs=(), *, m=2.0):
        self.m, self.n = m, 0
        self.item = Counter(); self.term = Counter(); self.joint = defaultdict(Counter)
        for q, gold in pairs: self.add(q, gold)

    def add(self, question, gold):
        self.n += 1
        g = set(gold)
        for t in _qgrams(question):
            self.term[t] += 1
            for i in g: self.joint[t][i] += 1
        for i in g: self.item[i] += 1

    def to_json(self, min_joint=2):
        return {'n': self.n, 'm': self.m, 'item': dict(self.item),
                'term': {t: c for t, c in self.term.items() if c >= min_joint},
                'joint': {t: {i: c for i, c in js.items() if c >= min_joint} for t, js in self.joint.items()
                          if self.term[t] >= min_joint and any(c >= min_joint for c in js.values())}}

    @classmethod
    def from_json(cls, data):
        p = cls(m=data['m']); p.n = data['n']
        p.item = Counter(data['item']); p.term = Counter(data['term'])
        p.joint = defaultdict(Counter, {t: Counter(js) for t, js in data['joint'].items()})
        return p

    def features(self, question, catalog, own=None):
        """(prior, cooc_max, cooc_mean) arrays over catalog items; own = this question's gold to leave out (training)."""
        n_items = len(catalog.items)
        own = set(own) if own else set()
        loo = 1 if own else 0
        n = max(self.n - loo, 1)
        prior = np.full(n_items, math.log(0.5 / (n + 1)), dtype=np.float32)
        base = {}
        for i, c in self.item.items():
            k = catalog.index.get(i)
            if k is None: continue
            c = c - (1 if i in own else 0)
            base[k] = (c + 0.5) / (n + 1)
            prior[k] = math.log(base[k])
        mx = np.zeros(n_items, dtype=np.float32); sm = np.zeros(n_items, dtype=np.float32)
        grams = [t for t in _qgrams(question) if t in self.joint]
        for t in grams:
            nt = self.term[t] - loo
            if nt <= 0: continue
            for i, c in self.joint[t].items():
                k = catalog.index.get(i)
                if k is None: continue
                c = c - (1 if i in own else 0)
                if c <= 0: continue
                p = base.get(k, 0.5 / (n + 1))
                lift = math.log((c + self.m * p) / (nt + self.m) / p)
                if lift > mx[k]: mx[k] = lift
                sm[k] += max(lift, 0.0)
        return prior, mx, sm / max(len(grams), 1)


class Reranker:
    """Candidate generation + per-group logistic scorer over schema items."""

    def __init__(self, catalog, encoder=None, model=None, priors=None, legacy=None, vectors=None):
        self.catalog, self.encoder = catalog, encoder
        self.weights = {g: np.array(w, dtype=np.float64) for g, w in (model or {}).items()}
        self.priors = priors or Priors()
        self.legacy = legacy  # callable question -> ranked stream names (the current lexical retriever)
        items = catalog.items
        self.group = np.array([GROUPS.index(_group(it)) for it in items])
        self.parent = catalog.parent_idx
        self.root = np.array([self.parent[p] if p >= 0 else -1 for p in self.parent])
        self.n_values = np.array([math.log1p(it.get('n_values', 0)) for it in items], dtype=np.float32)
        self.names = [_norm_text(it.get('value') or it['id'].split(':', 1)[1].split('.')[-1]) for it in items]
        self.name_tokens = [set(tokens(n)) for n in self.names]
        self.stream_of = {it['id'][7:]: k for k, it in enumerate(items) if it['kind'] == 'stream'}
        self.vectors = vectors if vectors is not None else (self._item_vectors() if encoder is not None else None)

    def _item_vectors(self):
        enc = self.encoder
        cache = ROOT / 'artifacts' / 'schema-rerank' / f"vectors-{self.catalog.digest}-{str(enc.identity)[:12]}.npy"
        if cache.exists():
            v = np.load(cache)
            if v.shape[0] == len(self.catalog.items): return v
        v = enc.encode([it['text'][:400] for it in self.catalog.items], batch_size=64).astype(np.float32)
        try:
            cache.parent.mkdir(parents=True, exist_ok=True); np.save(cache, v)
        except OSError:
            pass
        return v

    def features(self, question, own=None):
        cat, n = self.catalog, len(self.catalog.items)
        X = np.zeros((n, len(FEATURES)), dtype=np.float32)
        X[:, 0] = 1.0
        bm = cat.bm25(question)
        X[:, 1] = np.log1p(bm)
        for g in range(len(GROUPS)):
            m = self.group == g
            if m.any():
                top = bm[m].max()
                X[m, 2] = bm[m] / top if top > 0 else 0
        if self.vectors is not None:
            qv = self.encoder.encode([question[:1000]])[0].astype(np.float32)
            emb = self.vectors @ qv
            X[:, 3] = emb
            for g in range(len(GROUPS)):
                m = np.where(self.group == g)[0]
                if not len(m): continue
                X[m, 4] = emb[m] - emb[m].max()
                order = m[np.argsort(-emb[m])]
                X[order, 5] = 1.0 / np.log2(2 + np.arange(len(order)))
            X[:, 18] = 1.0
        has_p = self.parent >= 0
        X[has_p, 6] = X[self.parent[has_p], 2]
        X[has_p, 7] = X[self.parent[has_p], 3]
        has_r = self.root >= 0
        X[has_r, 8] = X[self.root[has_r], 3]
        qn = _norm_text(question)
        qt = set(tokens(question))
        for k, name in enumerate(self.names):
            if len(name) > 4 and (name in qn or name.rstrip() + 's ' in qn): X[k, 9] = 1.0
            nt = self.name_tokens[k]
            if nt: X[k, 10] = len(nt & qt) / len(nt)
        prior, mx, mean = self.priors.features(question, cat, own)
        X[:, 11], X[:, 12], X[:, 13] = prior, mx, mean
        X[has_p, 14] = mx[self.parent[has_p]]
        low = question.lower()
        from .nl2sql import GLOSSARY
        for s in [s for pattern, names in GLOSSARY if re.search(pattern, low) for s in names]:
            k = self.stream_of.get(s)
            if k is not None: X[k, 15] = 1.0
        if self.legacy is not None:
            for r, s in enumerate(self.legacy(question)):
                k = self.stream_of.get(s)
                if k is not None: X[k, 16] = 1.0 / (1 + r)
        X[has_p, 15] = np.maximum(X[has_p, 15], X[self.parent[has_p], 15])
        X[has_p, 16] = np.maximum(X[has_p, 16], X[self.parent[has_p], 16])
        X[has_r, 16] = np.maximum(X[has_r, 16], X[self.root[has_r], 16])
        X[:, 17] = self.n_values
        return X

    def candidates(self, X):
        """Cheap union of lexical, embedding, literal, prior and glossary signals -> candidate mask per group."""
        quick = X[:, 2] + X[:, 3] + 2 * X[:, 9] + X[:, 10] + 0.5 * X[:, 12] + X[:, 15] + X[:, 16] + 0.5 * X[:, 6] + 0.3 * X[:, 7]
        mask = np.zeros(len(quick), dtype=bool)
        for g, name in enumerate(GROUPS):
            idx = np.where(self.group == g)[0]
            mask[idx[np.argsort(-quick[idx])[:CANDIDATES[name]]]] = True
        return mask, quick

    def score(self, question, X=None):
        """Relevance probability for every catalog item (-inf outside the candidate set)."""
        X = self.features(question) if X is None else X
        mask, quick = self.candidates(X)
        out = np.full(len(X), -np.inf)
        for g, name in enumerate(GROUPS):
            idx = np.where((self.group == g) & mask)[0]
            w = self.weights.get(name)
            out[idx] = 1 / (1 + np.exp(-np.clip(X[idx] @ w, -30, 30))) if w is not None else quick[idx] / 10
        return out

    def rank(self, question, group=None, scores=None):
        s = self.score(question) if scores is None else scores
        order = np.argsort(-s)
        return [(self.catalog.items[k]['id'], float(s[k])) for k in order if np.isfinite(s[k]) and
                (group is None or GROUPS[self.group[k]] == group)]


def fit_logistic(X, y, l2=1.0, iters=50):
    """Newton-IRLS logistic regression with L2 (bias unpenalised); X includes the bias column."""
    d = X.shape[1]
    w = np.zeros(d)
    reg = np.full(d, l2); reg[0] = 1e-6
    for _ in range(iters):
        p = 1 / (1 + np.exp(-np.clip(X @ w, -30, 30)))
        g = X.T @ (p - y) + reg * w
        H = (X * (p * (1 - p))[:, None]).T @ X + np.diag(reg)
        step = np.linalg.solve(H, g)
        w -= step
        if np.abs(step).max() < 1e-6: break
    return w


def train(reranker, pairs, *, seed=7, hard=80, random_neg=40):
    """pairs: [(question, gold ids)] -> per-group weights. reranker.priors must hold these pairs; each question's own
    labels are left out of its co-occurrence features."""
    rng = np.random.default_rng(seed)
    rows = {g: ([], []) for g in GROUPS}
    for q, gold in pairs:
        X = reranker.features(q, own=gold)
        mask, quick = reranker.candidates(X)
        gidx = {reranker.catalog.index[i] for i in gold if i in reranker.catalog.index}
        for g, name in enumerate(GROUPS):
            cand = np.where((reranker.group == g) & mask)[0]
            pos = [k for k in cand if k in gidx]
            if not pos: continue
            neg = np.array([k for k in cand if k not in gidx])
            if len(neg) > hard + random_neg:
                top = neg[np.argsort(-quick[neg])][:hard]
                rest = np.setdiff1d(neg, top)
                neg = np.concatenate([top, rng.choice(rest, size=min(random_neg, len(rest)), replace=False)])
            for k in pos: rows[name][0].append(X[k]); rows[name][1].append(1.0)
            for k in neg: rows[name][0].append(X[k]); rows[name][1].append(0.0)
    return {name: fit_logistic(np.array(xs, dtype=np.float64), np.array(ys)) for name, (xs, ys) in rows.items()
            if xs and sum(ys) > 0}


# --------------------------------------------------------------------------------------------- slice rendering
# Tuned by grouped cross-validation on harvest pairs (tools/schema_rerank_eval.py). Every TABLES note stays in full
# (tables_full_min=16): compacting low-ranked tables saved ~700 chars but dropped 3-5% of needed table notes.
SELECT = {'tables_full_min': 16, 'table_tau': 0.15, 'streams_min': 1, 'streams_max': 6, 'stream_tau': 0.0,
          'values_max': 24, 'value_tau': 0.1, 'other_streams': 10}


def render(rr, question, profile, *, scores=None, budget=None, extra=(), params=None):
    """Ordered schema slice within `budget` characters. Returns text, the streams shown in full, and the item ids
    shown at all (`shown`) or with their full documentation (`full`)."""
    from .nl2sql import stream_line, concept_line
    P = {**SELECT, **(params or {})}
    cat = rr.catalog
    s = rr.score(question) if scores is None else scores
    ranked = defaultdict(list)
    for k in np.argsort(-s):
        if np.isfinite(s[k]): ranked[GROUPS[rr.group[k]]].append((cat.items[k], float(s[k])))
    tables = [(it['id'][6:], p) for it, p in ranked['table']]
    streams = [(it['id'][7:], p) for it, p in ranked['stream'] if it['id'][7:] in profile['streams']]
    forced_streams = [x for x in extra if x in profile['streams']]
    concept = concept_line(profile)
    blocks = []  # (priority, kind, key, forced)
    for n, (t, p) in enumerate(tables):
        if n < P['tables_full_min']: blocks.append((2.0 + p, 'table', t, True))
        elif p >= P['table_tau']: blocks.append((p, 'table', t, False))
    for x in forced_streams: blocks.append((3.0, 'stream', x, True))
    chosen_streams = [x for x, p in streams if x not in forced_streams][:P['streams_max']]
    for n, (x, p) in enumerate(streams):
        if x in forced_streams or x not in chosen_streams: continue
        if n < P['streams_min']: blocks.append((1.5 + p, 'stream', x, False))  # first pick, still within budget
        elif p >= P['stream_tau']: blocks.append((p, 'stream', x, False))
    others = [(x, p) for x, p in streams if x not in chosen_streams and x not in forced_streams][:P['other_streams']]
    for x, p in [(x, p) for x, p in streams if x in chosen_streams and p < P['stream_tau']][:P['other_streams']] + others:
        blocks.append((p * 0.5, 'other', x, False))
    vals = [(it, p) for it, p in ranked['value'] if p >= P['value_tau']][:P['values_max']]
    for it, p in vals: blocks.append((p, 'value', it['id'], False))
    for it, p in [(it, p) for it, p in ranked['concept'] if p >= P.get('concept_tau', 0.5)][:P.get('concepts_max', 6)]:
        blocks.append((p, 'value', it['id'], False))  # re-surface likely concept / drug-class ids (buried in the long id line)

    def build(sel):
        full_t = {k for _, kind, k, _ in sel if kind == 'table'}
        st = [k for _, kind, k, _ in sorted(sel, key=lambda b: -b[0]) if kind == 'stream']
        st_set = set(st)
        oth = [k for _, kind, k, _ in sorted(sel, key=lambda b: -b[0]) if kind == 'other' and k not in st_set]
        lines = []
        for t, spec in cat.tables.items():
            lines.append(spec['doc'] if t in full_t else spec['compact'])
        compact = len(full_t) < len(cat.tables)
        text = ('TABLES (full notes for the tables this question most likely needs; the rest list columns only)\n' if compact
                else 'TABLES\n') + '\n'.join(lines)
        text += '\n\nRELEVANT RECORD STREAMS\n' + '\n'.join(stream_line(profile, x) for x in st)
        if oth:
            text += ('\nOther received streams (fields not shown; usable via record WHERE stream = ...): ' + ', '.join(oth))
        vsel = [cat.items[cat.index[k]] for _, kind, k, _ in sorted(sel, key=lambda b: -b[0]) if kind == 'value']
        shown = _shown_ids(cat, profile, full_t, st, oth)
        # values already listed in a stream line are not repeated; library ids and TABLES enumerations are re-surfaced
        vsel = [it for it in vsel if it['id'] not in shown or it['id'].startswith(('concept:', 'drug_class:')) or it['owner'] in cat.tables]
        if vsel:
            grouped = defaultdict(list)
            for it in vsel:
                key = 'concept id' if it['id'].startswith('concept:') else f"{it['owner']}.{it['col']}"
                grouped[key].append(it['value'])
            text += ('\n\nSTORED VALUES MATCHING THE QUESTION (exact spelling): ' +
                     '; '.join(f"{k} = " + ' | '.join(repr(v) for v in vs) for k, vs in grouped.items()))
            shown |= {it['id'] for it in vsel}
        text += f'\n\n{concept}'
        return text, st, shown, full_t

    budget = budget or 10 ** 9
    sel = [b for b in blocks if b[3]]
    text = build(sel)[0]
    for b in sorted((b for b in blocks if not b[3]), key=lambda b: -b[0]):
        trial = build(sel + [b])[0]
        if len(trial) <= budget: sel.append(b); text = trial
    text, st, shown, full_t = build(sel)
    while len(text) > budget and any(not b[3] for b in sel):  # forced blocks only exceed the budget
        drop = min((b for b in sel if not b[3]), key=lambda b: b[0]); sel.remove(drop)
        text, st, shown, full_t = build(sel)
    st_set = set(st)
    full = {i for i in shown if not (i.startswith('table:') and i[6:] not in full_t) and not (i.startswith('stream:') and i[7:] not in st_set)}
    return {'text': text, 'streams': st, 'shown': shown, 'full': full}


def _shown_ids(cat, profile, full_tables, streams, other_streams):
    from . import source_profile
    if '_always_shown' not in cat.__dict__:
        cat._always_shown = frozenset(it['id'] for it in cat.items if it['kind'] in ('table', 'column') or it['id'].startswith(('concept:', 'drug_class:')))
    shown = set(cat._always_shown)
    for t in full_tables:
        for c, vals in cat.tables[t]['enums'].items(): shown |= {f'value:{t}.{c}={v}' for v in vals}
    for s in streams:
        shown.add(f'stream:{s}')
        for f, fs in profile['streams'][s]['fields'].items():
            if not fs['populated'] or f in source_profile.PLANNER_SKIP or f.endswith(('_time',)): continue
            shown.add(f'field:{s}.{f}')
            vals = fs.get('values') or {}
            if fs['types'].get('true') or fs['types'].get('false'): continue
            if vals and len(vals) <= 12 and max(map(len, vals)) <= 40:
                shown |= {f'value:{s}.{f}={v}' for v in sorted(vals, key=lambda v: -vals[v])[:12]}
    shown |= {f'stream:{s}' for s in other_streams}
    enc = (profile['streams'].get('encounters') or {}).get('fields', {}).get('type', {}).get('values') or {}
    shown |= {f'value:visit.type={v}' for v in sorted(enc, key=lambda v: -enc[v])[:30]}
    return shown


# --------------------------------------------------------------------------------------------- runtime
_ENCODER = {'registered': None, 'loaded': None, 'failed': False}
_RUNTIME = {}
_LOCK = threading.Lock()


def use_encoder(encoder):
    """Reuse an already-loaded local sentence encoder (e.g. stack.retriever.encoder)."""
    _ENCODER['registered'] = encoder


def load_encoder():
    """Registered encoder, else the pinned local MiniLM (own cache outside the live stack), else None."""
    if _ENCODER['registered'] is not None: return _ENCODER['registered']
    if _ENCODER['loaded'] is not None or _ENCODER['failed']: return _ENCODER['loaded']
    if os.environ.get('AGERE_SCHEMA_RERANK_ENCODER', 'on') == 'off':
        _ENCODER['failed'] = True; return None
    path = Path(os.environ.get('AGERE_SCHEMA_ENCODER_PATH') or ROOT / 'models' / 'minilm-l6-v2')
    try:
        from ...local_encoders import LocalEncoder
        cache = ROOT / 'artifacts' / 'schema-rerank' / 'encoder-cache.sqlite3'
        cache.parent.mkdir(parents=True, exist_ok=True)
        enc = LocalEncoder(path, pooling='mean', cache=cache)
        enc.db.close(); enc.db = sqlite3.connect(cache, check_same_thread=False)
        _ENCODER['loaded'] = enc
    except Exception:  # noqa: BLE001 - any load failure means lexical fallback, never a broken ladder
        _ENCODER['failed'] = True
    return _ENCODER['loaded']


def save_model(rr, lexical_weights=None, target=MODEL_FILE):
    data = {'version': VERSION, 'features': list(FEATURES), 'select': SELECT,
            'encoder_identity': None if rr.encoder is None else str(rr.encoder.identity),
            'weights': {g: [round(float(x), 6) for x in w] for g, w in rr.weights.items()},
            'lexical_weights': {g: [round(float(x), 6) for x in w] for g, w in (lexical_weights or {}).items()},
            'priors': rr.priors.to_json(min_joint=2),
            'training': 'verified harvest pairs (artifacts/bottling/answers-*.jsonl); benchmark questions excluded'}
    Path(target).write_text(json.dumps(data, separators=(',', ':'), sort_keys=True))


def load_model(target=MODEL_FILE):
    data = json.loads(Path(target).read_text())
    if data.get('version') != VERSION or data.get('features') != list(FEATURES): raise ValueError('Schema reranker model version mismatch')
    return data


def reranker_for(profile, path=None):
    """Cached reranker for a profile (+ the semantic layer vocabulary when the build path is known)."""
    key = (profile.get('source_digest'), str(path) if path else None)
    with _LOCK:
        if key in _RUNTIME: return _RUNTIME[key]
        data = load_model()
        from .nl2sql import TABLES, retrieve_streams
        vocab = semantic_vocabulary(path) if path else {}
        cat = Catalog(profile, TABLES, vocab)
        enc = load_encoder()
        if enc is not None and data.get('encoder_identity') and str(enc.identity) != data['encoder_identity']: enc = None
        weights = data['weights'] if enc is not None else (data.get('lexical_weights') or None)
        if not weights: raise ValueError('No schema reranker weights for this configuration')
        rr = Reranker(cat, enc, model=weights, priors=Priors.from_json(data['priors']),
                      legacy=lambda q: retrieve_streams(q, profile, k=20))
        rr.select = {**SELECT, **(data.get('select') or {})}
        _RUNTIME[key] = rr
        return rr


_EXAMPLE_VECTORS = {}


def rank_examples(question, scored, k=4):
    """Few-shot choice for nl2sql.harvested_examples: same number of examples as the current Jaccard rule
    (score >= 0.2, at most k) but ordered by encoder cosine + 0.5 * Jaccard (measured better in grouped CV).
    scored: [(jaccard, question, sql)]. Returns None when no encoder is available (caller keeps the current rule)."""
    n = min(k, sum(1 for s, _, _ in scored if s >= 0.2))
    if n == 0: return []
    enc = load_encoder()
    if enc is None: return None
    missing = [q for _, q, _ in scored if q not in _EXAMPLE_VECTORS]
    if missing:
        for q, v in zip(missing, enc.encode(missing, batch_size=64)): _EXAMPLE_VECTORS[q] = v
    qv = enc.encode([question[:1000]])[0]
    hybrid = sorted(((float(_EXAMPLE_VECTORS[q] @ qv) + 0.5 * s, q, sql) for s, q, sql in scored), key=lambda x: -x[0])
    return [(q, sql) for _, q, sql in hybrid[:n]]


def schema_slice(question, profile, extra=(), path=None):
    """(streams shown, schema text) - same contract as nl2sql.schema_for; never larger than the legacy slice."""
    from .nl2sql import legacy_schema_for
    budget = len(legacy_schema_for(question, profile, extra)[1])
    rr = reranker_for(profile, path)
    with _LOCK:
        out = render(rr, question, profile, budget=budget, extra=extra, params=getattr(rr, 'select', None))
    return out['streams'], out['text']
