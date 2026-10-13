"""Bottling step 2: measure the deterministic extractor against frontier labels; adjudicate held-out disagreements; admit.

  dev         precision/recall vs Sonnet labels on the dev split, with error listing (used to write rules)
  test        same on the held-out split (report only)
  adjudicate  Opus judges every held-out disagreement (unmatched extractor or label fact) against the text
  admit       per-fact-type adjudicated precision/recall; types with precision >= BAR and >= MIN_N extractor facts are
              admitted -> clinical_core/agere/clinical/note_facts_admission.json (+ artifacts report)
Usage: python tools/notes_extraction/evaluate.py dev|test|adjudicate|admit [--show fact_type] [--model ...]
"""
import argparse, json, math, sys
from collections import Counter, defaultdict
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from common import OUT, ROOT, call, read_jsonl, norm_value, note_facts  # noqa: E402

BAR, MIN_N = 0.90, 10
LABEL_MODEL, JUDGE_MODEL = 'claude-sonnet-5-5', 'claude-opus-5-5'
ADMISSION = ROOT / 'clinical_core/agere/clinical/note_facts_admission.json'


def key(f):
    return (f['fact_type'], norm_value(f['fact_type'], f['value']), bool(f['negated']))


def valid_label(f):
    if f.get('experiencer') == 'family': return False
    if f['fact_type'] == 'follow_up' and norm_value('follow_up', f['value']) in ('0', ''): return False
    return f['fact_type'] in note_facts.FACT_DEFINITIONS


def compare(split, model=LABEL_MODEL):
    units = {u['unit_id']: u for u in read_jsonl(OUT / 'sample.jsonl') if u['split'] == split}
    labels = {r['unit_id']: r for r in read_jsonl(OUT / f'labels-{model}.jsonl') if 'error' not in r}
    out = []
    for uid, u in units.items():
        if uid not in labels: continue
        ext = note_facts.extract(u['text'], u['stream'], u['field'])
        gold = [f for f in labels[uid]['facts'] if valid_label(f)]
        ek, gk = Counter(map(key, ext)), Counter(map(key, gold))
        agreed = ek & gk
        temp = [(k[0], next(f['temporality'] for f in ext if key(f) == k) == next(f['temporality'] for f in gold if key(f) == k))
                for k in agreed]
        out.append({'unit': u, 'ext': ext, 'gold': gold, 'agreed': agreed, 'ext_only': ek - agreed, 'gold_only': gk - agreed,
                    'temporality': temp})
    return out


def table(rows, judged=None):
    """Per fact_type tp/ext/gold; with judged={(unit_id, side, key): bool} counts adjudicated correctness."""
    s = defaultdict(lambda: Counter())
    for r in rows:
        uid = r['unit']['unit_id']
        for k, n in r['agreed'].items(): s[k[0]].update(tp=n, ext=n, gold=n)
        for ft, same in r['temporality']: s[ft].update(temp_n=1, temp_same=int(same))
        for k, n in r['ext_only'].items():
            s[k[0]].update(ext=n)
            if judged is not None and judged.get((uid, 'ext', k)): s[k[0]].update(tp=n, gold=n)
        for k, n in r['gold_only'].items():
            if judged is None or judged.get((uid, 'gold', k), False): s[k[0]].update(gold=n)
    res = {}
    for ft, c in sorted(s.items()):
        p = c['tp'] / c['ext'] if c['ext'] else None
        res[ft] = {'extractor_facts': c['ext'], 'reference_facts': c['gold'], 'true_positive': c['tp'],
                   'precision': round(p, 3) if p is not None else None,
                   'precision_wilson_low': round(wilson(c['tp'], c['ext']), 3) if c['ext'] else None,
                   'recall': round(c['tp'] / c['gold'], 3) if c['gold'] else None,
                   'temporality_agreement': round(c['temp_same'] / c['temp_n'], 3) if c['temp_n'] else None}
    return res


def wilson(k, n, z=1.96):
    if not n: return 0.0
    p = k / n; d = 1 + z * z / n
    return (p + z * z / (2 * n) - z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n))) / d


def show(rows, ft):
    for r in rows:
        eo = [k for k in r['ext_only'] if k[0] == ft]; go = [k for k in r['gold_only'] if k[0] == ft]
        if not eo and not go: continue
        u = r['unit']
        print('=' * 60, u['unit_id'], u['stream'])
        for side, ks, facts in (('EXT ONLY', eo, r['ext']), ('LABEL ONLY', go, r['gold'])):
            for k in ks:
                f = next(f for f in facts if key(f) == k)
                ctx = u['text'][max(0, f['span_start'] - 160):f['span_end'] + 100].replace('\n', ' / ')
                print(f'  {side} {k} q={f.get("qualifier")} t={f.get("temporality")} :: ...{ctx}...')
        same = [f for f in r['gold'] if f['fact_type'] == ft and key(f) in r['agreed']]
        if same: print('  (agreed:', [key(f) for f in same], ')')


JUDGE_SYSTEM = """You adjudicate candidate clinical facts extracted from synthetic EHR text. Two extractors disagreed; for each
candidate decide whether it is a CORRECT fact under the definitions below (right fact_type, value and negation, stated about
the patient, not merely hypothetical/conditional/instructional such as return precautions or things to ask about).
Definitions:
""" + '\n'.join(f'- {t}: {d}' for t, d in note_facts.FACT_DEFINITIONS.items()) + """
Judge each candidate independently; a fact can be correct even if another candidate for the same span is also correct.
Return one verdict per candidate_id with a short reason."""

JUDGE_SCHEMA = {'type': 'object', 'additionalProperties': False, 'required': ['verdicts'],
                'properties': {'verdicts': {'type': 'array', 'items': {
                    'type': 'object', 'additionalProperties': False, 'required': ['candidate_id', 'correct', 'reason'],
                    'properties': {'candidate_id': {'type': 'string'}, 'correct': {'type': 'boolean'}, 'reason': {'type': 'string'}}}}}}


def adjudicate(jobs=6):
    rows = compare('test')
    items = []
    for r in rows:
        u = r['unit']; cands = []
        for side, keys, facts, ev in (('ext', r['ext_only'], r['ext'], 'evidence_text'), ('gold', r['gold_only'], r['gold'], 'quote')):
            for k in keys:
                f = next(f for f in facts if key(f) == k)
                cands.append({'candidate_id': f'{side}|{"|".join(map(str, k))}', 'fact_type': k[0], 'value': k[1], 'negated': k[2],
                              'qualifier': f.get('qualifier'), 'evidence': f[ev]})
        if cands: items.append((u, cands))
    batches, cur, size = [], [], 0
    for it in items:
        if cur and (size + len(it[0]['text']) > 8000 or len(cur) >= 5): batches.append(cur); cur, size = [], 0
        cur.append(it); size += len(it[0]['text'])
    if cur: batches.append(cur)

    def run(batch):
        payload = [{'unit_id': u['unit_id'], 'text': u['text'],
                    'candidates': [{**c, 'candidate_id': u['unit_id'] + '::' + c['candidate_id']} for c in cands]} for u, cands in batch]
        try:
            value, usd, _ = call(JUDGE_MODEL, JUDGE_SYSTEM, json.dumps(payload, ensure_ascii=False), effort='high', schema=JUDGE_SCHEMA)
        except Exception as e:  # noqa: BLE001
            print('judge error', str(e)[:200]); return [], 0.0
        return value['verdicts'], usd
    verdicts, total = [], 0.0
    with ThreadPoolExecutor(jobs) as ex:
        for v, usd in ex.map(run, batches): verdicts += v; total += usd
    (OUT / f'adjudication-{JUDGE_MODEL}.jsonl').write_text('\n'.join(json.dumps(v, ensure_ascii=False) for v in verdicts) + '\n')
    cost = json.loads((OUT / 'cost.json').read_text()) if (OUT / 'cost.json').exists() else {}
    cost[f'adjudicate:{JUDGE_MODEL}'] = cost.get(f'adjudicate:{JUDGE_MODEL}', 0) + total
    (OUT / 'cost.json').write_text(json.dumps(cost, indent=1))
    print(f'{len(items)} disputed units, {sum(len(c) for _, c in items)} candidates, {len(verdicts)} verdicts, new spend ${total:.2f}')


def load_judged():
    judged = {}
    for v in read_jsonl(OUT / f'adjudication-{JUDGE_MODEL}.jsonl'):
        uid, cid = v['candidate_id'].split('::', 1)
        side, ft, val, neg = cid.split('|')
        judged[(uid, side, (ft, val, neg == 'True'))] = v['correct']
    return judged


def admit():
    rows = compare('test')
    judged = load_judged()
    missing = sum(1 for r in rows for side, ks in (('ext', r['ext_only']), ('gold', r['gold_only'])) for k in ks
                  if (r['unit']['unit_id'], side, k) not in judged)
    vs_label = table(rows)
    adj = table(rows, judged)
    admitted = sorted(ft for ft, m in adj.items() if m['precision'] is not None and m['extractor_facts'] >= MIN_N and m['precision'] >= BAR)
    cost = json.loads((OUT / 'cost.json').read_text()) if (OUT / 'cost.json').exists() else {}
    report = {'schema': 'agere-note-facts-admission/1', 'extractor': note_facts.EXTRACTOR, 'precision_bar': BAR, 'min_extractor_facts': MIN_N,
              'admitted': admitted, 'held_out_units': len(rows), 'unadjudicated_disagreements': missing,
              'label_model': LABEL_MODEL, 'judge_model': JUDGE_MODEL, 'api_cost_usd': {k: round(v, 2) for k, v in cost.items()},
              'held_out_vs_label_model': vs_label, 'held_out_adjudicated': adj,
              'basis': 'precision = extractor facts judged correct / extractor facts on held-out units; reference = facts both '
                       'agreed on + disputed facts the judge model accepted (not clinician reviewed); synthetic notes only.'}
    ADMISSION.write_text(json.dumps(report, indent=1))
    (OUT / 'admission-report.json').write_text(json.dumps(report, indent=1))
    print(json.dumps({k: report[k] for k in ('admitted', 'held_out_units', 'unadjudicated_disagreements', 'api_cost_usd')}, indent=1))
    for ft, m in adj.items(): print(f'{ft:26s} adj P={m["precision"]} R={m["recall"]} n_ext={m["extractor_facts"]} | vs label P={vs_label.get(ft, {}).get("precision")} R={vs_label.get(ft, {}).get("recall")}')


if __name__ == '__main__':
    ap = argparse.ArgumentParser()
    ap.add_argument('step', choices=['dev', 'test', 'adjudicate', 'admit'])
    ap.add_argument('--show')
    a = ap.parse_args()
    if a.step in ('dev', 'test'):
        rows = compare(a.step)
        if a.show: show(rows, a.show)
        else:
            for ft, m in table(rows).items(): print(f'{ft:26s} P={m["precision"]} R={m["recall"]} ext={m["extractor_facts"]} ref={m["reference_facts"]} temporality_agree={m["temporality_agreement"]}')
    elif a.step == 'adjudicate': adjudicate()
    else: admit()
