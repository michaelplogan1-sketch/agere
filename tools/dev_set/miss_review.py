"""Build the clinical review page for one dev3 run's misses (Mike 2026-10-26: grade every miss clinically wrong / acceptable / trivial).

Reads a saved run (tools/dev_set/run.py --out, with responses) and the dev3 questions with keys v2. Writes the data the review page embeds:
for every question not scored correct, the question as asked, Agere's answer, the key's answer and evidence, and why the scorer marked
it wrong, in plain words. CHECK half only by design: held-out questions are never read (split 'heldout' is refused).

    .venv/bin/python -m tools.dev_set.miss_review RUN.json OUT.json
"""
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))


def _short(v, n=600):
    s = v if isinstance(v, str) else json.dumps(v, ensure_ascii=False, default=str)
    return s if len(s) <= n else s[:n] + ' …'


def key_view(q):
    """The key's answer in plain form: value, absence reason, required facts, and the evidence passages it rests on."""
    k = q['key']
    out = {'definition': k.get('sql')}
    if q.get('answer_type') == 'absent':
        out['answer'] = 'Not in the data: ' + str(k.get('reason') or '')
    elif isinstance(k.get('answer'), dict) and k['answer'].get('required_facts'):
        out['answer'] = [f.get('claim') for f in k['answer']['required_facts']]
    else:
        out['answer'] = k.get('answer')
    out['evidence'] = [{'record_id': e.get('record_id'), 'quote': e.get('quote')} for e in q.get('evidence') or [] if e.get('quote')]
    return out


def agere_view(row):
    resp = row.get('response') or {}
    a = resp.get('answer') if isinstance(resp.get('answer'), dict) else {}
    out = {'status': row.get('status'), 'route': row.get('route'),
           'headline': a.get('headline') or resp.get('error') or '(no answer)'}
    for k in ('value', 'count'):
        if a.get(k) is not None: out[k] = a[k]
    if a.get('table'): out['table'] = _short(a['table'][:8] if isinstance(a['table'], list) else a['table'], 900)
    srcs = [{'record_id': s.get('record_id'), 'quote': _short(s.get('quote') or '', 300)} for s in a.get('sources') or [] if isinstance(s, dict) and s.get('quote')]
    if srcs: out['sources'] = srcs[:3]
    if a.get('verification'): out['verification'] = a['verification']
    return out


def why(q, row):
    """The scorer's reason, in plain words."""
    d = row.get('detail') or {}
    if row.get('correct') is None:
        return 'Agere did not answer: ' + str(d.get('status') or row.get('status') or 'refused') + '.'
    if 'got' in d and 'expected' in d:
        if d['got'] is None: return f"Agere gave no number; the key's answer is {d['expected']!r}."
        return f"Agere's number {d['got']!r} does not match the key's {d['expected']!r}."
    if 'fact_share' in d:
        missing = [f.get('claim') for f in (q['key'].get('answer') or {}).get('required_facts') or [] if not (d.get('facts') or {}).get(f.get('id'))]
        return f"The key needs {len(d.get('facts') or {})} facts; the answer carries {round(d['fact_share'] * len(d.get('facts') or {}))}. Missing: " + '; '.join(m for m in missing if m)
    if 'stated_absent' in d:
        return ('The key says this is not in the data, but Agere gave an answer.' if not d.get('stated_absent') else
                'Agere said the data is absent, but not the thing the key names as absent.')
    if 'answer_match' in d:
        parts = []
        if not d.get('answer_match'): parts.append("the answer does not match the key's answer")
        if not d.get('cited_supporting_record'): parts.append("it does not quote the record the key cites")
        elif not d.get('passage_supports_claim'): parts.append("its quoted passage does not carry the key's evidence")
        return 'Marked wrong because ' + ' and '.join(parts or ['of the passage check']) + (f" ({d['passage_check']})" if d.get('passage_check') else '') + '.'
    if 'rows_expected' in d:
        return f"Table rows: {d.get('rows_matched')} of {d.get('rows_expected')} expected rows match."
    return 'Scorer detail: ' + _short(d, 300)


def build(run_path):
    from tools.dev_set.run_inprocess import read
    from tools.dev_set.run import apply_keys_v2
    qs = {q['id']: q for q in apply_keys_v2(read('dev3'))}
    out = []
    for row in json.loads(Path(run_path).read_text()):
        if row.get('correct') is True: continue
        q = qs[row['id']]
        if q.get('split') == 'heldout': continue  # never: held-out questions stay unread
        out.append({'id': row['id'], 'bucket': row['bucket'], 'question': row.get('asked') or q['question'],
                    'follow_up': bool(q.get('follow_up_of')), 'parent': (qs.get(q.get('follow_up_of')) or {}).get('question'), 'agere': agere_view(row), 'key': key_view(q), 'why': why(q, row),
                    'seconds': row.get('seconds')})
    return out


def page(run_path, out_html, *, candidate, label='dev3 check half'):
    """Fill tools/dev_set/miss_review_page.html with this run's misses (scorer v2, keys v2)."""
    rows = json.loads(Path(run_path).read_text())
    data = {'set': label, 'total': len(rows), 'scorer': 'scorer v2 with keys v2', 'candidate': candidate,
            'exact_score': f"{sum(r.get('correct') is True for r in rows)}/{len(rows)}", 'misses': build(run_path)}
    blob = json.dumps(data, ensure_ascii=False, default=str).replace('</', '<\\/')
    html = (ROOT / 'tools' / 'dev_set' / 'miss_review_page.html').read_text().replace('__DATA__', blob)
    Path(out_html).write_text(html)
    return data


if __name__ == '__main__':
    d = page(sys.argv[1], sys.argv[2], candidate=sys.argv[3] if len(sys.argv) > 3 else 'unknown')
    print(len(d['misses']), 'misses ->', sys.argv[2])
