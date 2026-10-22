"""Training sets for every local learner, built from exactly labeled factory items (label.py output).

  normalizer   Qwen3-4B LoRA, paraphrase -> canonical question the free parser reads (local_normalizer.prompt format);
               follow-up chains as "Previous question / Follow-up" -> standalone canonical.
  typed        question -> typed contract (request / {"program"} / cohort slots) in a compact prompt (TYPED_SYSTEM +
               only the tables the request reads); the runtime typed prompt is ~35k tokens, too long to fine-tune locally.
  router       question -> learned_router label (a PRODUCT operator, literal_lookup, not_answerable); VBC library seeds keep
               their curated expected_engine even when they do not execute.
  shape        question -> answer shape (count / value / table / patient_set / composite): a MiniLM student head.
  cache        verified (question -> SQL) pairs for seeding learned_verified_sql / semantic cache and (question -> request)
               pairs for paraphrase reuse; seeding a stack is explicit (seed_stores, train_all --seed-stores).
  decompose    question -> grammar-read sub-questions + exact combine (why / trend / multi-step chains; atomic decomposition).
  bottled      (question -> SQL) for bottled SQL (bottled.messages, materialized at training time) and
               (question -> cohort slots) for the narrow rung (bottled.narrow_messages).
Splits hold out whole TEMPLATE FAMILIES (family/form), never single paraphrases, so held-out scores measure
generalization to unseen phrasings and structures. Benchmark questions (bench/) and near-duplicates are excluded.

Usage: python tools/training/datasets.py --labels artifacts/training/labels --out artifacts/training/datasets
"""
import argparse
import hashlib
import json
import random
import re
import sys
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

VERSION = 'training-datasets/1'
HELDOUT, VALID = 15, 10  # percent of template families
LEARNERS = ('normalizer', 'typed', 'router', 'shape', 'cache', 'bottled', 'narrow', 'decompose')
TYPED_SYSTEM = ('Interpret the analytics question about the connected synthetic warehouse into ONE typed request JSON: '
                'source, select, where, group_by, order_by, limit, unresolved (optional analyses for registered engines), or '
                '{"program": ...} for a multi-part request, or {"cohort_family_slots": ...} for a cohort-family question. '
                'Every quote is a literal span of the question. Use only the tables and fields shown. JSON only.')


def split_of(template_family):
    h = int(hashlib.sha256(template_family.encode()).hexdigest()[:8], 16) % 100
    return 'heldout' if h < HELDOUT else 'valid' if h < HELDOUT + VALID else 'train'


def norm(q): return ' '.join(q.strip().rstrip('?.!').split()).casefold()
def tokens(q): return set(re.findall(r'[a-z0-9]+', q.lower()))


class Bench:
    """Benchmark questions (bench/) never become training material: exact match on any wording, token Jaccard >= 0.6 on
    the canonical (inverted index; the runtime NEAR_DUPLICATE rule)."""

    def __init__(self, questions=None):
        if questions is None:
            from clinical_core.agere.clinical import bottled
            questions = [t for _, t, _ in bottled._bench_questions()]
        self.exact = {norm(q) for q in questions}
        self.sets = [tokens(q) for q in questions]
        self.index = {}
        for i, s in enumerate(self.sets):
            for t in s: self.index.setdefault(t, []).append(i)

    def near(self, question, threshold=0.6):
        if norm(question) in self.exact: return True
        t = tokens(question)
        hits = Counter(i for w in t for i in self.index.get(w, ()))
        return any(n / max(1, len(t | self.sets[i])) >= threshold for i, n in hits.items() if n >= threshold * len(t) * 0.5)


def label_dirs(labels):
    return [Path(x) for x in (labels.split(',') if isinstance(labels, str) else labels if isinstance(labels, (list, tuple)) else [labels]) if str(x)]


def rows(labels):
    """Labeled rows from one or more directories (factory labels, live traffic). Traffic questions a user corrected
    (corrections-*.jsonl beside them) are excluded."""
    for d in label_dirs(labels):
        corrected = {norm(json.loads(l)['question']) for p in d.glob('corrections-*.jsonl') for l in p.read_text().splitlines() if l.strip()}
        for p in sorted(x for x in d.glob('labels-*.jsonl') if not x.name.endswith('.rejects.jsonl')):
            with p.open() as f: lines = f.readlines()
            for line in lines:
                r = json.loads(line)
                if r.get('status') != 'ok' or not r.get('request'): continue
                if r.get('family') == 'traffic' and (norm(r['canonical']) in corrected or
                                                     any(norm(x['text']) in corrected for x in r.get('paraphrases') or [])): continue
                yield r


def wordings(r):
    """(input text, style) pairs: the canonical, then every paraphrase (follow-ups carry their previous question)."""
    out = [] if r.get('chain') else [(r['canonical'], 'canonical')]
    for p in r.get('paraphrases') or []:
        text = f"Previous question: {p['previous']}\nFollow-up: {p['text']}" if p.get('previous') else p['text']
        out.append((text, '+'.join(p.get('styles') or ['paraphrase'])))
    return out


def typed_target(r):
    req = r['request']
    if req['kind'] == 'program': return {'program': req['value']}
    if req['kind'] == 'analysis': return {'analysis': req['value']}
    if req['kind'] == 'cohort_family_slots': return {'cohort_family_slots': req['value']}
    return req['value']


def typed_messages(question, catalog_tables, tables, target=None):
    """The compact typed-interpreter prompt (training == serving contract)."""
    from clinical_core.agere.clinical import request_vocabulary as V
    schema = {t: catalog_tables[t] for t in dict.fromkeys(['patient', *tables]) if t in catalog_tables}
    concepts = sorted(V.concept_definitions()['condition'])
    user = json.dumps({'question': question, 'tables': schema, 'condition_concepts': concepts}, separators=(',', ':'))
    out = [{'role': 'system', 'content': TYPED_SYSTEM}, {'role': 'user', 'content': user}]
    if target is not None: out.append({'role': 'assistant', 'content': json.dumps(target, separators=(',', ':'), sort_keys=True)})
    return out


def router_label(r, labels):
    if r.get('router_label') in labels: return r['router_label']  # hard families name the operator that answers them
    for e in r.get('engines') or []:
        if e in labels: return e
    return 'literal_lookup'  # executed by the shared compiler (plain SQL, governed metric, cohort family, program)


def shape_label(r):
    req, ans = r['request'], r['answer']
    if req['kind'] == 'program': return 'composite'
    if req['kind'] == 'engine': return 'count' if ans.get('answer_type') == 'count' else 'patient_set'
    return ans.get('answer_type') or ans.get('returns') or ('table' if 'rows' in ans else 'value')


def build(labels, out, catalog_tables=None, bench=None, normalizer_targets='grammar', vbc=True, seed=7):
    from clinical_core.agere.clinical import local_normalizer as N, learned_router as LR
    if catalog_tables is None:
        from clinical_core.agere.clinical import request_plan as R
        from tools.training.question_factory import active_stack
        catalog_tables = R.Catalog.load(active_stack()).tables
    bench = bench or Bench()
    out = Path(out)
    sets = {name: {'train': [], 'valid': [], 'heldout': []} for name in LEARNERS}
    stats = Counter()
    for r in rows(labels):
        stats['labeled_items'] += 1
        if bench.near(r['canonical']): stats['benchmark_excluded'] += 1; continue
        split, group = split_of(r['template_family']), r['template_family']
        texts = [(t, s) for t, s in wordings(r) if norm(t.split('Follow-up: ')[-1]) not in bench.exact]
        stats[f'{split}_items'] += 1
        meta = {'id': r['id'], 'family': r['family'], 'template_family': group}
        # (a) normalizer: only targets the free parser reads (the runtime contract), unless asked for all
        if r.get('grammar_reads') or normalizer_targets == 'all':
            for text, style in texts:
                if style == 'canonical' and split != 'train': continue  # an identity pair is not a held-out measurement
                if not text.startswith('Previous question:') and N.guard(text, r['canonical']): stats['guard_dropped'] += 1; continue
                sets['normalizer'][split].append({**meta, 'style': style, 'input': text, 'target': r['canonical'],
                                                  'messages': [*N.prompt(text), {'role': 'assistant', 'content': r['canonical']}]})
        # (b) typed interpreter: canonical wording only (quotes are literal spans of the wording they were bound to)
        if not r.get('chain') and r['request']['kind'] in ('request', 'program', 'analysis', 'cohort_family_slots'):
            sets['typed'][split].append({**meta, 'question': r['canonical'], 'target': typed_target(r),
                                         'tables': r['provenance']['tables'], 'request_kind': r['request']['kind']})
        # (c) router and (f) shape head: every wording
        for text, style in texts:
            sets['router'][split].append({**meta, 'question': text, 'label': router_label(r, LR.LABELS), 'group': group, 'how': 'exact_label'})
            sets['shape'][split].append({**meta, 'question': text, 'label': shape_label(r), 'group': group})
        # (d) cache seeding + (e) bottled SQL: plain compiled SQL answers
        query = r['provenance'].get('query')
        if query and not r.get('engines') and r['request']['kind'] in ('request', 'cohort_family_slots', 'sql'):
            returns = r['answer'].get('returns') or r['answer'].get('answer_type')
            plan = {'query': query, 'returns': returns}
            sets['cache'][split].append({**meta, 'question': r['canonical'], 'paraphrases': [t for t, s in texts if s != 'canonical'],
                                         'plan_kind': 'sql', 'plan': plan, 'request': r['request'] if r['request']['kind'] == 'request' else None,
                                         'answer_key': r['answer_key'], 'grammar_reads': bool(r.get('grammar_reads'))})
            for text, style in texts:
                if not text.startswith('Previous question:'):
                    sets['bottled'][split].append({**meta, 'question': text, 'sql': query, 'answer_key': r['answer_key']})
        if r['request']['kind'] == 'cohort_family_slots':
            for text, style in texts:
                sets['narrow'][split].append({**meta, 'question': text, 'slots': r['request']['value']})
        # (g) decomposition (System One #1 atomic sub-questions): question -> grammar-read steps + exact combine
        if r['request']['kind'] == 'steps':
            for text, style in texts:
                if not text.startswith('Previous question:'):
                    sets['decompose'][split].append({**meta, 'question': text, 'steps': r['request']['value']['steps'],
                                                     'combine': r['request']['value']['combine'], 'answer_key': r['answer_key']})
    if vbc:  # curated router seeds from the VBC library (expected_engine), held out by domain like any template family
        lib = json.loads((ROOT / 'config' / 'agere' / 'vbc_questions.json').read_text())['questions']
        for q in lib:
            label = {'unsupported': 'not_answerable'}.get(q.get('expected_engine'), q.get('expected_engine'))
            if label not in LR.LABELS or bench.near(q['question']): continue
            group = f"vbc_seed/{q.get('domain')}"
            sets['router'][split_of(group)].append({'id': q['id'], 'family': 'vbc_seed', 'template_family': group,
                                                    'question': q['question'], 'label': label, 'group': group, 'how': 'vbc_library'})
            stats['vbc_router_seeds'] += 1
    rng = random.Random(seed)
    manifest = {'version': VERSION, 'labels': [str(d) for d in label_dirs(labels)], 'heldout_percent': HELDOUT, 'valid_percent': VALID,
                'split_unit': 'template_family', 'stats': dict(stats), 'learners': {}}
    for name, parts in sets.items():
        d = out / name; d.mkdir(parents=True, exist_ok=True)
        for split, items in parts.items():
            rng.shuffle(items)
            with (d / f'{split}.jsonl').open('w') as f:
                for item in items: f.write(json.dumps(item, separators=(',', ':'), default=str) + '\n')
        fams = {split: len({i['template_family'] for i in items}) for split, items in parts.items()}
        manifest['learners'][name] = {'rows': {k: len(v) for k, v in parts.items()}, 'template_families': fams}
        if name in ('router', 'shape'):
            manifest['learners'][name]['labels'] = dict(Counter(i['label'] for i in parts['train']))
    leaks = {name: len({i['template_family'] for i in p['train']} & {i['template_family'] for i in p['heldout']}) for name, p in sets.items()}
    manifest['template_family_leakage'] = leaks  # must be all zero
    (out / 'manifest.json').write_text(json.dumps(manifest, indent=1))
    return manifest


# ------------------------------------------------------------------------------------- materialization for training
def mlx_normalizer(dataset, out, limit=None, seed=7):
    """datasets/normalizer -> MLX chat {train,valid,test}.jsonl (test = held-out template families)."""
    out = Path(out); out.mkdir(parents=True, exist_ok=True)
    counts = {}
    for split, name in (('train', 'train'), ('valid', 'valid'), ('heldout', 'test')):
        rows_ = [json.loads(l) for l in (Path(dataset) / f'{split}.jsonl').read_text().splitlines()]
        if limit: rows_ = rows_[:limit if split == 'train' else max(20, limit // 10)]
        (out / f'{name}.jsonl').write_text(''.join(json.dumps({'messages': r['messages']}) + '\n' for r in rows_))
        counts[name] = len(rows_)
    return counts


def mlx_typed(dataset, out, catalog_tables, limit=None):
    out = Path(out); out.mkdir(parents=True, exist_ok=True)
    counts = {}
    for split, name in (('train', 'train'), ('valid', 'valid'), ('heldout', 'test')):
        rows_ = [json.loads(l) for l in (Path(dataset) / f'{split}.jsonl').read_text().splitlines()]
        if limit: rows_ = rows_[:limit if split == 'train' else max(20, limit // 10)]
        (out / f'{name}.jsonl').write_text(''.join(json.dumps({'messages': typed_messages(r['question'], catalog_tables, r['tables'], r['target'])}) + '\n'
                                                   for r in rows_))
        counts[name] = len(rows_)
    return counts


def mlx_bottled(dataset, out, stack, limit=None):
    from clinical_core.agere.clinical import bottled, source_profile
    profile = source_profile.load(stack)
    as_of = json.loads((Path(stack) / 'run.json').read_text())['snapshot']['as_of']
    out = Path(out); out.mkdir(parents=True, exist_ok=True)
    counts = {}
    for split, name in (('train', 'train'), ('valid', 'valid'), ('heldout', 'test')):
        rows_ = [json.loads(l) for l in (Path(dataset) / f'{split}.jsonl').read_text().splitlines()]
        if limit: rows_ = rows_[:limit if split == 'train' else max(20, limit // 10)]
        (out / f'{name}.jsonl').write_text(''.join(json.dumps({'messages': bottled.messages(r['question'], profile, as_of, r['sql'])}) + '\n'
                                                   for r in rows_))
        counts[name] = len(rows_)
    return counts


def mlx_narrow(dataset, out, limit=None):
    from clinical_core.agere.clinical import bottled
    out = Path(out); out.mkdir(parents=True, exist_ok=True)
    counts = {}
    for split, name in (('train', 'train'), ('valid', 'valid'), ('heldout', 'test')):
        rows_ = [json.loads(l) for l in (Path(dataset) / f'{split}.jsonl').read_text().splitlines()]
        if limit: rows_ = rows_[:limit if split == 'train' else max(20, limit // 10)]
        (out / f'{name}.jsonl').write_text(''.join(json.dumps({'messages': bottled.narrow_messages(r['question'], r['slots'])}) + '\n'
                                                   for r in rows_))
        counts[name] = len(rows_)
    return counts


# ---------------------------------------------------------------------------------------------------- store seeding
def seed_stores(stack, cache_rows, dry_run=True, split_filter=('train',)):
    """Seed a stack's free tiers with exactly labeled pairs: learned_verified_sql / semantic cache (learning.verified) for
    questions the free parser does not read, and paraphrase reuse (question-plans shared_requests) for their typed
    requests. Exact labels from the deterministic compiler, so they are verified by construction. -> counts."""
    from clinical_core.agere.clinical import learning, request_plan as R
    import sqlite3
    counts = Counter()
    for r in cache_rows:
        if r.get('grammar_reads'): counts['skipped_grammar_reads'] += 1; continue  # the free parser already answers it exactly
        counts['seed'] += 1
        if dry_run: continue
        learning.record_verified(stack, r['question'], 'sql', r['plan'], {'answer_key': r['answer_key']}, 'training_factory',
                                 [], 'exact:deterministic-compiler')
        if r.get('request'):
            payload = {'question': r['question'], 'request': r['request']['value'],
                       'interpretation': {'interpreter': 'training_factory', 'review': 'exact_deterministic_label',
                                          'execution_accepted': True, 'attempts': []}}
            with sqlite3.connect(Path(stack) / 'question-plans.sqlite3') as db:
                db.execute('CREATE TABLE IF NOT EXISTS shared_requests(id TEXT PRIMARY KEY,payload TEXT NOT NULL)')
                db.execute('INSERT OR IGNORE INTO shared_requests VALUES (?,?)',
                           ('training:' + R.fingerprint(payload), json.dumps(payload)))
            counts['paraphrase_reuse_seeded'] += 1
    return dict(counts)


def main(argv=None):
    from tools.training.question_factory import DEFAULT_OUT
    ap = argparse.ArgumentParser()
    ap.add_argument('--labels', default=str(DEFAULT_OUT / 'labels')); ap.add_argument('--out', default=str(DEFAULT_OUT / 'datasets'))
    ap.add_argument('--normalizer-targets', choices=('grammar', 'all'), default='grammar')
    a = ap.parse_args(argv)
    print(json.dumps(build(a.labels, a.out, normalizer_targets=a.normalizer_targets), indent=1))


if __name__ == '__main__':
    main()
