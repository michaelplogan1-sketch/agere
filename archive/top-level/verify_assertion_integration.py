"""Replay prior specialist examples into the new contract without network access."""
import json
import sqlite3
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch
from clinical_core.contracts import digest
from clinical_core.agere.clinical import context_passes as cp
from clinical_core.agere.clinical.stack_runtime import Stack

root=Path('artifacts/agere-population-stack-v3');out=Path('artifacts/agere-assertion-reconciliation-v1')
ledger=sqlite3.connect('artifacts/agere-full/provider-budget.sqlite3')
receipts=[]
try:
    for name in ('specialist-replay','affirmed-specialist-replay'):
        old=json.loads(Path('artifacts/agere-context-passes-v1',name+'.json').read_text())
        bundle=cp.inspect(root,old['bundle_id'],verify_sources=True)
        for page in bundle['passes']:
            body,_=cp.request(page,'perplexity');key=digest({'contract':'context-student/1','provider':'perplexity','body':body})
            if ledger.execute('SELECT status FROM calls WHERE id=?',(key,)).fetchone()!=('validated',):
                raise ValueError('Validated cache entry required; this verification never makes paid calls')
        with patch('clinical_core.agere.clinical.context_student.urllib.request.urlopen',side_effect=AssertionError('Network forbidden during replay')):
            result=Stack.review_passes(SimpleNamespace(path=root,allow_live=True),bundle['id'],'perplexity',len(bundle['passes']))
        assert result['new_paid_api_calls']==0
        assert all(p['pass_number'] is not None for p in result['source_reconciliation']['pass_binding'])
        (out/(name+'-reconciled.json')).write_text(json.dumps(result,indent=2))
        receipts.append({'patient_id':result['patient_id'],'specialist_proposals':len(result['proposals']),
          'bound_source_assertions':len(result['source_reconciliation']['assertions']),
          'all_assertions_bound_to_passes':True,'new_paid_api_calls':0})
finally:ledger.close()
(out/'specialist-integration.json').write_text(json.dumps(receipts,indent=2));print(json.dumps(receipts))
