"""One training/eval exporter: existing builders' outputs -> a shared example schema in Parquet (+ MLX JSONL) with a manifest,
a shared 70/15/15 split rule for new tasks, and an eval DENYLIST enforced at export.

  python tools/export/training_parquet.py --out artifacts/training-export/<name> --task bottled=artifacts/bottling/data-v2 \
      --task normalizer=artifacts/training/datasets/normalizer [--denylist extra.json ...] [--stack <stack_dir>]

Layout:  examples/<task>.parquet | mlx/<task>/{train,valid,test}.jsonl | warehouse/<table>.parquet (with --stack) | MANIFEST.json
Example schema: example_id, task, split, group_key, source_release, release_digest, input, target, weight, provenance(json).

Splits: a task directory that already has train/valid/test(or heldout) files keeps ITS split (current models stay comparable;
'heldout' is exported as 'test'); rows without a split get sha256(group_key) -> 70/15/15 (group_key = template family / cluster /
household / patient, else the input text). Parquet is written by DuckDB (no pyarrow/pandas needed for examples).

Denylist (exact + normalized-text sha256): bench/{questions,acceptance,capability}, docs/agere/study, the eval bank
(/Users/mikel/agere-eval-bank-2/bank.json), plus any --denylist file or directory and any precomputed hash list (*.sha256, one hex
per line: the way a SEALED set is excluded without this tool ever opening it). Paths under agere-sealed-eval or the repo's private/ are never read.
A hit QUARANTINES the row (dropped, listed in the manifest with the list it hit); --strict makes any hit fail the export instead.
"""
import argparse
import hashlib
import json
import re
import sys
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

VERSION = 'training-export/1'
SHARES = (70, 15)   # train %, valid %; the rest is test
DEFAULT_DENYLIST = [ROOT / 'bench' / 'questions', ROOT / 'bench' / 'acceptance', ROOT / 'bench' / 'capability',
                    ROOT / 'docs' / 'agere' / 'study', Path('/Users/mikel/agere-eval-bank-2/bank.json')]
SPLIT_FILES = {'train': 'train', 'valid': 'valid', 'val': 'valid', 'test': 'test', 'heldout': 'test'}
TEXT_KEYS = ('question', 'canonical', 'prompt', 'text', 'q', 'input', 'query_text', 'utterance', 'variant', 'base')


class DenylistHit(Exception):
    pass


def norm(text):
    return ' '.join(re.findall(r'[a-z0-9]+', str(text).lower()))


def digests(text):
    t = str(text).strip()
    return {hashlib.sha256(t.encode()).hexdigest(), hashlib.sha256(norm(t).encode()).hexdigest()} if t else set()


def refuse(path):
    p = Path(path).expanduser()
    s = str(p.resolve())
    private = [ROOT / 'private', ROOT.parent / 'private', Path('/Users/mikel/clinical-insights-local/private')]
    return 'agere-sealed-eval' in s or any(s == str(q.resolve()) or s.startswith(str(q.resolve()) + '/') for q in private)


def _strings(node, out, depth=0):
    if depth > 8: return
    if isinstance(node, str):
        if len(node) >= 12: out.append(node)
    elif isinstance(node, dict):
        for k, v in node.items():
            if isinstance(v, str) and (k in TEXT_KEYS or len(v) >= 12): _strings(v, out, depth + 1)
            elif isinstance(v, (dict, list)): _strings(v, out, depth + 1)
    elif isinstance(node, list):
        for v in node: _strings(v, out, depth + 1)


def load_denylist(paths):
    """-> ({hash: list_name}, report). Missing files are skipped and reported; sealed/private paths are refused and reported."""
    hashes, report = {}, {'lists': {}, 'skipped': []}
    for p in map(Path, paths):
        if refuse(p): report['skipped'].append({'path': str(p), 'why': 'sealed/private: never read (supply a *.sha256 hash list instead)'}); continue
        if not p.exists(): report['skipped'].append({'path': str(p), 'why': 'missing'}); continue
        files = [p] if p.is_file() else sorted(f for f in p.rglob('*') if f.is_file() and f.suffix in ('.json', '.jsonl', '.sha256', '.md'))
        n = 0
        for f in files:
            if refuse(f): continue
            try:
                raw = f.read_text()
            except (OSError, UnicodeDecodeError):
                continue
            if f.suffix == '.sha256':
                for line in raw.split():
                    if re.fullmatch(r'[0-9a-f]{64}', line): hashes.setdefault(line, p.name); n += 1
                continue
            texts = []
            if f.suffix == '.md':
                texts = [l.strip(' -*>|`') for l in raw.splitlines() if len(l.strip()) >= 12 and '?' in l]
            else:
                try:
                    docs = [json.loads(raw)] if f.suffix == '.json' else [json.loads(l) for l in raw.splitlines() if l.strip()]
                except ValueError:
                    continue
                for d in docs: _strings(d, texts)
            for t in texts:
                for h in digests(t): hashes.setdefault(h, p.name); n += 1
        report['lists'][str(p)] = {'files': len(files), 'hashes': n}
    return hashes, report


def split_for(group_key):
    b = int(hashlib.sha256(str(group_key).encode()).hexdigest()[:8], 16) % 100
    return 'train' if b < SHARES[0] else 'valid' if b < SHARES[0] + SHARES[1] else 'test'


def adapt(row):
    """-> (input, target, group_key, texts_to_check). Understands MLX chat rows, builder rows and plain input/target rows."""
    texts = [row[k] for k in TEXT_KEYS if isinstance(row.get(k), str)]
    if isinstance(row.get('messages'), list) and row['messages']:
        msgs = row['messages']
        users = [m.get('content', '') for m in msgs if m.get('role') == 'user']
        inp = json.dumps([m for m in msgs if m.get('role') != 'assistant'], ensure_ascii=False)
        tgt = next((m.get('content', '') for m in reversed(msgs) if m.get('role') == 'assistant'), '')
        texts += users[-1:] + ([row['input']] if isinstance(row.get('input'), str) else [])
    else:
        inp = row.get('input') or row.get('question') or row.get('text') or ''
        tgt = next((row[k] for k in ('target', 'sql', 'plan', 'label', 'slots', 'steps', 'canonical') if row.get(k) is not None), '')
        if not isinstance(inp, str): inp = json.dumps(inp, ensure_ascii=False)
        texts.append(inp)
    if not isinstance(tgt, str): tgt = json.dumps(tgt, ensure_ascii=False, sort_keys=True)
    group = next((str(row[k]) for k in ('group_key', 'template_family', 'group', 'cluster', 'household_id', 'patient_id') if row.get(k)), None)
    return inp, tgt, group or hashlib.sha256(norm(inp).encode()).hexdigest()[:16], texts


def read_task(path):
    """A task source: a directory with split files, or a single jsonl. -> [(split|None, row)]"""
    path = Path(path)
    out = []
    if path.is_file():
        out += [(None, json.loads(l)) for l in path.read_text().splitlines() if l.strip()]
    else:
        for f in sorted(path.glob('*.jsonl')):
            stem = f.stem.split('-')[0] if f.stem.startswith('heldout') else f.stem
            split = SPLIT_FILES.get(stem)
            if split is None: continue
            out += [(split, json.loads(l)) for l in f.read_text().splitlines() if l.strip()]
    return out


def build(tasks, out, denylist, release='unknown', strict=False, source_release=None):
    """tasks: {name: path}. -> manifest dict; writes examples/, mlx/, MANIFEST.json under `out`."""
    import duckdb
    out = Path(out)
    hashes, dl_report = load_denylist(denylist)
    examples, quarantined = [], []
    release_digest = hashlib.sha256(json.dumps(sorted((k, str(v)) for k, v in tasks.items())).encode()).hexdigest()[:16]
    for name, path in tasks.items():
        seen = set()
        for given, row in read_task(path):
            inp, tgt, group, texts = adapt(row)
            hit = next((hashes[h] for t in texts for h in digests(t) if h in hashes), None)
            if hit:
                if strict: raise DenylistHit(f'{name}: {inp[:80]!r} is in eval list {hit}')
                quarantined.append({'task': name, 'list': hit, 'input': inp[:160]}); continue
            eid = hashlib.sha256(f'{name}\x00{inp}\x00{tgt}'.encode()).hexdigest()[:20]
            if eid in seen: continue
            seen.add(eid)
            examples.append({'example_id': eid, 'task': name, 'split': given or split_for(group), 'group_key': group,
                             'source_release': source_release or release, 'release_digest': release_digest, 'input': inp,
                             'target': tgt, 'weight': float(row.get('weight', 1.0)),
                             'provenance': json.dumps({'source': str(path), 'split_rule': 'builder' if given else 'sha256(group_key) 70/15/15',
                                                       'id': row.get('id')}, sort_keys=True)})
    # a group never straddles splits inside one task (group-level split for rows that were not given one by their builder)
    out.mkdir(parents=True, exist_ok=True)
    (out / 'examples').mkdir(exist_ok=True)
    files, counts = {}, {}
    con = duckdb.connect()
    for name in tasks:
        rows = [e for e in examples if e['task'] == name]
        counts[name] = dict(Counter(e['split'] for e in rows))
        target = out / 'examples' / f'{name}.parquet'
        con.execute('CREATE OR REPLACE TABLE t(example_id VARCHAR, task VARCHAR, split VARCHAR, group_key VARCHAR, source_release VARCHAR, '
                    'release_digest VARCHAR, input VARCHAR, target VARCHAR, weight DOUBLE, provenance VARCHAR)')
        if rows:
            con.executemany('INSERT INTO t VALUES (?,?,?,?,?,?,?,?,?,?)', [tuple(e[k] for k in e) for e in rows])
        con.execute(f"COPY t TO '{target}' (FORMAT PARQUET)")
        files[str(target.relative_to(out))] = sha_of(target)
        for split in ('train', 'valid', 'test'):
            d = out / 'mlx' / name; d.mkdir(parents=True, exist_ok=True)
            f = d / f'{split}.jsonl'
            with f.open('w') as fh:
                for e in rows:
                    if e['split'] == split: fh.write(json.dumps({'messages': _mlx(e)}, ensure_ascii=False) + '\n')
            files[str(f.relative_to(out))] = sha_of(f)
    manifest = {'version': VERSION, 'release': release, 'release_digest': release_digest, 'tasks': {k: str(v) for k, v in tasks.items()},
                'rows': counts, 'files': files, 'split_rule': 'builder split kept; else sha256(group_key) -> 70/15/15',
                'denylist': {**dl_report, 'hashes': len(hashes), 'digest': hashlib.sha256(''.join(sorted(hashes)).encode()).hexdigest()[:16]},
                'quarantined': quarantined, 'quarantined_count': len(quarantined)}
    (out / 'MANIFEST.json').write_text(json.dumps(manifest, indent=1))
    return manifest


def _mlx(e):
    try:
        prior = json.loads(e['input'])
        if isinstance(prior, list) and prior and isinstance(prior[0], dict) and 'role' in prior[0]:
            return prior + [{'role': 'assistant', 'content': e['target']}]
    except ValueError:
        pass
    return [{'role': 'user', 'content': e['input']}, {'role': 'assistant', 'content': e['target']}]


def sha_of(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def export_warehouse(stack, out):
    """semantic.sqlite3 tables -> warehouse/<table>.parquet via DuckDB (sqlite scanner if installable, else row copy)."""
    import duckdb, sqlite3
    out = Path(out) / 'warehouse'; out.mkdir(parents=True, exist_ok=True)
    src = sqlite3.connect(f'file:{Path(stack) / "semantic.sqlite3"}?mode=ro', uri=True)
    con = duckdb.connect()
    written = {}
    for (t,) in src.execute("SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'").fetchall():
        cur = src.execute(f'SELECT * FROM "{t}"'); cols = [c[0] for c in cur.description]; rows = cur.fetchall()
        if not rows: continue
        con.execute(f'CREATE OR REPLACE TABLE w AS SELECT * FROM (SELECT 1) WHERE 1=0') if False else None
        con.execute('DROP TABLE IF EXISTS w')
        con.execute('CREATE TABLE w(' + ','.join(f'"{c}" VARCHAR' for c in cols) + ')')
        con.executemany('INSERT INTO w VALUES (' + ','.join('?' * len(cols)) + ')', [tuple(None if v is None else str(v) for v in r) for r in rows])
        target = out / f'{t}.parquet'
        con.execute(f"COPY w TO '{target}' (FORMAT PARQUET)")
        written[t] = len(rows)
    return written


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.split('\n')[0])
    ap.add_argument('--out', required=True)
    ap.add_argument('--task', action='append', default=[], help='name=path (dir with train/valid/test|heldout jsonl, or one jsonl)')
    ap.add_argument('--denylist', action='append', default=[])
    ap.add_argument('--no-default-denylist', action='store_true')
    ap.add_argument('--stack', help='also export semantic.sqlite3 tables as warehouse/*.parquet')
    ap.add_argument('--release', default='unknown')
    ap.add_argument('--strict', action='store_true')
    a = ap.parse_args(argv)
    tasks = dict(t.split('=', 1) for t in a.task)
    deny = ([] if a.no_default_denylist else list(DEFAULT_DENYLIST)) + a.denylist
    m = build(tasks, a.out, deny, release=a.release, strict=a.strict)
    if a.stack: m['warehouse'] = export_warehouse(a.stack, a.out); (Path(a.out) / 'MANIFEST.json').write_text(json.dumps(m, indent=1))
    print(json.dumps({'rows': m['rows'], 'quarantined': m['quarantined_count'], 'denylist_hashes': m['denylist']['hashes']}))


if __name__ == '__main__':
    main()
