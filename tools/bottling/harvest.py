"""Bottling step 2: answer generated questions through the running Agere service and keep verified (question -> plan) pairs.
Usage: .venv/bin/python tools/bottling/harvest.py <questions.jsonl> <out.jsonl> [jobs]"""
import json, sys, urllib.request
from concurrent.futures import ThreadPoolExecutor


def ask(q):
    req = urllib.request.Request('http://127.0.0.1:8885/api/stack/compose', data=json.dumps({'question': q['question']}).encode(),
                                 headers={'content-type': 'application/json'})
    try:
        d = json.load(urllib.request.urlopen(req, timeout=600))
    except urllib.error.HTTPError as e:
        return {**q, 'error': e.read().decode()[:300]}
    except Exception as e:
        return {**q, 'error': str(e)[:300]}
    pr = d.get('planner_receipt') or {}
    a = d.get('answer') or {}
    return {**q, 'route': pr.get('route'), 'agreement': (pr.get('hosted_sql') or {}).get('agreement'),
            'query': a.get('query'), 'answer_type': a.get('answer_type'), 'status': a.get('status'),
            'usd': (d.get('planning_economics') or {}).get('known_api_estimate_usd') or 0}


if __name__ == '__main__':
    qs = [json.loads(l) for l in open(sys.argv[1]) if l.strip()]
    if '--budget' in sys.argv:  # active labeling: spend the paid harvest on uncertain + diverse questions (+ an audit slice)
        i = sys.argv.index('--budget'); budget = int(sys.argv[i + 1]); del sys.argv[i:i + 2]
        sys.path.insert(0, str(__import__('pathlib').Path(__file__).resolve().parents[2]))
        from clinical_core.agere.clinical import active_labeling
        try:
            from clinical_core.agere.clinical.schema_rerank import load_encoder
            enc = load_encoder()
        except Exception:
            enc = None
        qs, rep = active_labeling.select(qs, budget, lambda q: active_labeling.question_uncertainty(q['question'], enc),
                                         text=lambda q: q['question'], ident=lambda q: q['question'])
        print('active labeling:', rep)
    with ThreadPoolExecutor(int(sys.argv[3]) if len(sys.argv) > 3 else 3) as ex, open(sys.argv[2], 'w') as out:
        for r in ex.map(ask, qs):
            out.write(json.dumps(r) + '\n'); out.flush()
    rows = [json.loads(l) for l in open(sys.argv[2])]
    from collections import Counter
    print(Counter(r.get('route') or 'error' for r in rows), 'usd %.2f' % sum(r.get('usd') or 0 for r in rows))
