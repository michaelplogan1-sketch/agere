"""Execute the two already-declared payer source groups via the real API."""
import json
import time
import urllib.request
import urllib.error
from pathlib import Path

OUT = Path('artifacts/agere-source-plan-answers-v1')


def call(route, body):
    req = urllib.request.Request('http://localhost:8885/api/stack/'+route,
        json.dumps(body).encode(), {'Content-Type': 'application/json'})
    try:
        with urllib.request.urlopen(req, timeout=1200) as response: return json.load(response)
    except urllib.error.HTTPError as exc:
        raise RuntimeError(str(exc.code)+' '+exc.read().decode()) from exc


def main():
    OUT.mkdir(exist_ok=True)
    saved = json.loads(Path('artifacts/agere-retrieval-source-plan-v1/payer-source-plan.json').read_text())
    restored = call('retrieval-source-plan/inspect', {'id': saved['plan']['id']})
    assert restored['plan'] == saved['plan'] and restored['contexts'] == saved['contexts']
    results = []
    for group, context in zip(saved['plan']['groups'], saved['contexts']):
        number = group['group_number']; output = OUT/('payer-group-'+str(number)+'.json')
        if output.exists():
            answer = json.loads(output.read_text())
        else:
            p = call('context/plan', {'context_id': context['id']})
            assert p['can_dispatch'] and p['spending_limit'] == 'uncapped'
            assert p['source_units'] == context['budget']['candidate_units']
            (OUT/('payer-group-'+str(number)+'-plan.json')).write_text(json.dumps(p, indent=2)+'\n')
            t = time.perf_counter()
            try: answer = call('context/synthesize', {'context_id': context['id']})
            except Exception as exc:
                (OUT/('payer-group-'+str(number)+'-error.json')).write_text(json.dumps({'error':str(exc),'elapsed_seconds':time.perf_counter()-t},indent=2)+'\n')
                raise
            output.write_text(json.dumps(answer, indent=2)+'\n')
        assert answer['context_id'] == context['id'] and answer['coverage']['complete_declared_scope']
        results.append({'group':number,'answer_id':answer['id'],'source_units':answer['coverage']['submitted_units'],
            'source_passes':len(answer['passes']),'reconciliation_groups':answer['coverage']['reconciliation_groups'],
            'new_paid_api_calls':answer['new_paid_api_calls'],'new_token_count_calls':answer['new_token_count_calls']})
        print(json.dumps(results[-1]),flush=True)
    (OUT/'live-source-answers.json').write_text(json.dumps(results,indent=2)+'\n')


if __name__ == '__main__': main()
