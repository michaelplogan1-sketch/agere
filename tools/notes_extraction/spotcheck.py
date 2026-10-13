"""Corpus-weighted precision spot check: a simple random sample of materialised note_fact rows, judged by the frontier
judge with the surrounding text. Complements the stratified held-out evaluation (which over-represents rare streams).
Usage: python tools/notes_extraction/spotcheck.py <note_facts.sqlite3> [n=200]
"""
import json, random, sqlite3, sys
from collections import Counter
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from common import OUT, STACK, call, note_facts  # noqa: E402
from evaluate import JUDGE_SYSTEM, JUDGE_SCHEMA, JUDGE_MODEL, wilson  # noqa: E402


def main(db_path, n=200, seed=11):
    db = sqlite3.connect(f'file:{db_path}?mode=ro', uri=True)
    rows = db.execute('SELECT rowid, record_id, source_field, fact_type, value, negated, qualifier, temporality, span_start, span_end '
                      'FROM note_fact').fetchall()
    pick = random.Random(seed).sample(rows, min(n, len(rows)))
    src = sqlite3.connect(f'file:{STACK / "semantic.sqlite3"}?mode=ro', uri=True)
    items = []
    for rowid, rid, field, ft, value, neg, qual, temp, a, b in pick:
        text = json.loads(src.execute('SELECT payload FROM record WHERE record_id = ?', (rid,)).fetchone()[0])[field]
        lo, hi = max(0, a - 400), min(len(text), b + 300)
        items.append({'candidate_id': str(rowid), 'fact_type': ft, 'value': value, 'negated': bool(neg), 'qualifier': qual,
                      'temporality': temp, 'evidence': text[a:b], 'context': text[lo:hi]})
    batches = [items[i:i + 10] for i in range(0, len(items), 10)]

    def run(batch):
        payload = [{'unit_id': c['candidate_id'], 'text': c['context'], 'candidates': [{k: v for k, v in c.items() if k != 'context'}]}
                   for c in batch]
        value, usd, _ = call(JUDGE_MODEL, JUDGE_SYSTEM, json.dumps(payload, ensure_ascii=False), effort='high', schema=JUDGE_SCHEMA)
        return value['verdicts'], usd
    verdicts, total = [], 0.0
    with ThreadPoolExecutor(6) as ex:
        for v, usd in ex.map(run, batches): verdicts += v; total += usd
    ok = {v['candidate_id']: v for v in verdicts}
    by = Counter(); good = Counter()
    for c in items:
        by[c['fact_type']] += 1; good[c['fact_type']] += int(ok.get(c['candidate_id'], {}).get('correct', False))
    k, m = sum(good.values()), sum(by.values())
    report = {'sample_rows': m, 'correct': k, 'precision': round(k / m, 3), 'wilson_low': round(wilson(k, m), 3),
              'by_fact_type': {ft: f'{good[ft]}/{by[ft]}' for ft in by}, 'judge': JUDGE_MODEL, 'new_spend_usd': round(total, 2),
              'errors': [{**c, 'reason': ok.get(c['candidate_id'], {}).get('reason')} for c in items
                         if not ok.get(c['candidate_id'], {}).get('correct', False)]}
    (OUT / 'spotcheck.json').write_text(json.dumps(report, indent=1, ensure_ascii=False))
    cost = json.loads((OUT / 'cost.json').read_text()) if (OUT / 'cost.json').exists() else {}
    cost[f'spotcheck:{JUDGE_MODEL}'] = cost.get(f'spotcheck:{JUDGE_MODEL}', 0) + total
    (OUT / 'cost.json').write_text(json.dumps(cost, indent=1))
    print(json.dumps({k: v for k, v in report.items() if k != 'errors'}, indent=1))
    for e in report['errors']: print('-', e['fact_type'], e['value'], e['negated'], '|', e['evidence'][:90], '|', (e['reason'] or '')[:150])


if __name__ == '__main__':
    main(sys.argv[1], int(sys.argv[2]) if len(sys.argv) > 2 else 200)
