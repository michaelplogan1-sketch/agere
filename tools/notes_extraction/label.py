"""Bottling step 1: stratified sample of text units -> frontier-model fact labels (strict JSON + evidence quotes).

Sample: every (stream, subtype) stratum capped, telephone messages bucketed by template, plus keyword enrichment for rare
facts. Each unit gets split dev|test by a hash of its id (test is never used to write rules). Labels are cached in the
provider ledger by request hash (rerun = $0) and written to artifacts/notes-extraction/labels-<model>.jsonl.
Usage: python tools/notes_extraction/label.py [sample|label] [--model claude-sonnet-5-5] [--jobs 6]
"""
import argparse, hashlib, json, random, re, sqlite3, sys
from collections import defaultdict
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from common import OUT, STACK, LABEL_SYSTEM, call, units_payload, read_jsonl, note_facts  # noqa: E402

ENRICH = {'ef': r'ejection fraction|LVEF|\bEF\b', 'nyha': r'NYHA', 'goc': r'hospice|palliat|comfort care|DNR|code status|decision',
          'smoke': r'smok|tobacco', 'nonuse': r'not being used|not been restarted|inconsistent', 'cost': r'afford|could not pay',
          'social': r'transport|Helper:|food access|housing|lives alone', 'fracture': r'fractur', 'effusion': r'effusion|edema',
          'walk': r'metres', 'followup': r'within \w+ (?:day|week)|in about \w+ weeks', 'dyspnea': r'breathless|shortness of breath'}
SUBTYPE = ('note_type', 'document_type', 'test_kind', 'source_field', 'test')


def sample(seed=7):
    db = sqlite3.connect(f'file:{STACK / "semantic.sqlite3"}?mode=ro', uri=True)
    strata, pool = defaultdict(list), []
    for rid, pid, stream, payload in db.execute(
            f"SELECT record_id, patient_id, stream, payload FROM record WHERE stream IN ({','.join('?' * len(note_facts.TEXT_SOURCES))})",
            list(note_facts.TEXT_SOURCES)):
        d = json.loads(payload)
        sub = next((str(d[k]) for k in SUBTYPE if d.get(k)), '-')
        for field, text in note_facts.units((rid, stream, payload)):
            u = {'unit_id': f'{rid}#{field}', 'record_id': rid, 'patient_id': pid, 'stream': stream, 'field': field, 'text': text}
            key = (stream, sub if stream != 'telephone_messages' else re.sub(r'\d', '', text[:40]))
            strata[key].append(u); pool.append(u)
    rnd = random.Random(seed)
    chosen = {}
    for key, us in sorted(strata.items()):
        cap = 3 if key[0] == 'telephone_messages' else 8 if key[0] == 'notes' else 5
        for u in rnd.sample(us, min(cap, len(us))): chosen[u['unit_id']] = {**u, 'why': 'stratum'}
    for name, rx in ENRICH.items():
        hits = [u for u in pool if re.search(rx, u['text'], re.I) and u['unit_id'] not in chosen]
        for u in rnd.sample(hits, min(16, len(hits))): chosen[u['unit_id']] = {**u, 'why': 'enrich:' + name}
    rows = sorted(chosen.values(), key=lambda u: u['unit_id'])
    for u in rows:
        u['split'] = 'test' if int(hashlib.sha256(u['unit_id'].encode()).hexdigest(), 16) % 2 else 'dev'
        if len(u['text']) > 6000: u['text'] = u['text'][:6000]
    OUT.mkdir(parents=True, exist_ok=True)
    (OUT / 'sample.jsonl').write_text('\n'.join(json.dumps(u, ensure_ascii=False) for u in rows) + '\n')
    by = defaultdict(int)
    for u in rows: by[(u['stream'], u['split'])] += 1
    print(len(rows), 'units;', dict(sorted(by.items())))


def batches(units, max_chars=9000, max_units=6):
    cur, size = [], 0
    for u in units:
        if cur and (size + len(u['text']) > max_chars or len(cur) >= max_units):
            yield cur; cur, size = [], 0
        cur.append(u); size += len(u['text'])
    if cur: yield cur


def label(model, jobs, effort='medium', budget=None):
    units = read_jsonl(OUT / 'sample.jsonl')
    target = OUT / f'labels-{model}.jsonl'
    previous = []
    if budget:  # active labeling: only the uncertain / diverse / audit units not yet labelled (free local signals decide)
        from clinical_core.agere.clinical import active_labeling
        previous = [r for r in (read_jsonl(target) if target.exists() else []) if 'error' not in r]
        units, rep = active_labeling.select(units, budget, active_labeling.unit_uncertainty, labelled={r['unit_id'] for r in previous})
        print('active labeling:', rep)
    def run(batch):
        try:
            value, usd, hit = call(model, LABEL_SYSTEM, 'Extract facts for each unit.\n' + units_payload(batch), effort=effort)
        except Exception as e:  # noqa: BLE001 - recorded per batch, rerun retries transport failures
            return [{'unit_id': u['unit_id'], 'error': str(e)[:300]} for u in batch], 0.0, False
        got = {r['unit_id']: r['facts'] for r in value.get('units', [])}
        out = []
        for u in batch:
            facts, bad = [], 0
            for f in got.get(u['unit_id'], []):
                i = u['text'].find(f['quote']) if f['quote'] else -1
                if i < 0: bad += 1; continue
                facts.append({**f, 'span_start': i, 'span_end': i + len(f['quote'])})
            out.append({'unit_id': u['unit_id'], 'model': model, 'facts': facts, 'unquoted_dropped': bad,
                        'missing': u['unit_id'] not in got})
        return out, usd, hit
    total, hits, rows = 0.0, 0, []
    with ThreadPoolExecutor(jobs) as ex:
        for out, usd, hit in ex.map(run, list(batches(units))):
            rows += out; total += usd; hits += hit
    rows = previous + rows
    target.write_text('\n'.join(json.dumps(r, ensure_ascii=False) for r in rows) + '\n')
    errs = sum('error' in r for r in rows)
    print(f'{model}: {len(rows)} units labelled, {sum(len(r.get("facts", [])) for r in rows)} facts, errors {errs}, '
          f'unquoted dropped {sum(r.get("unquoted_dropped", 0) for r in rows)}, cache-hit batches {hits}, new spend ${total:.2f}')
    cost = json.loads((OUT / 'cost.json').read_text()) if (OUT / 'cost.json').exists() else {}
    cost[f'label:{model}'] = cost.get(f'label:{model}', 0) + total
    (OUT / 'cost.json').write_text(json.dumps(cost, indent=1))


if __name__ == '__main__':
    ap = argparse.ArgumentParser()
    ap.add_argument('step', choices=['sample', 'label'])
    ap.add_argument('--model', default='claude-sonnet-5-5')
    ap.add_argument('--jobs', type=int, default=6)
    ap.add_argument('--budget', type=int, help='active labeling: label at most this many NEW units, chosen by uncertainty + diversity')
    a = ap.parse_args()
    sample() if a.step == 'sample' else label(a.model, a.jobs, budget=a.budget)
