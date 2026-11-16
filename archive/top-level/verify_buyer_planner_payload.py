"""Read-only exact synthetic payload audit; no provider calls or credentials."""
import json
import sqlite3
import hashlib
from pathlib import Path
from types import SimpleNamespace
from clinical_core.agere.clinical import question_decision as q, decision_planner as p
import importlib
from verify_buyer_decision_http import capabilities,BUYER,OUT

config=json.loads(Path('config/agere/active_warehouse.json').read_text());path=Path(config['build'])/'stack'
for module in ('medication_source_review','patient_fit_work','reference_applicability','reference_lifecycle',
               'option_evidence_registry','option_component_registry','cardiometabolic_components','appointment_navigation',
               'literal_reference_runtime','structured_transport','reference_table_context','reference_reconciliation_transport',
               'interpretation_recovery','linked_claim_integrity'):
    importlib.import_module('clinical_core.agere.clinical.'+module).install()
importlib.import_module('clinical_core.agere.clinical.meaning_source_dispatch').install(path)
retriever=SimpleNamespace(path=path);OUT.mkdir(exist_ok=True);reports=[]
for domain in ['respiratory','payer','cardiometabolic','appointment']:
    probe=json.loads((Path('artifacts/agere-question-decision-v1')/(domain+'-source-probe.json')).read_text())
    answer,context,binding=q.source_binding(retriever,probe['binding']['answer_id'])
    ids=list(context['model_context']['sources'])
    with sqlite3.connect(path/'stack.sqlite3') as db:
        records=[json.loads(r[0]) for r in db.execute('SELECT payload FROM records WHERE id IN ('+','.join('?' for _ in ids)+')',ids)]
    assert len(records)==len(ids) and all(r.get('synthetic') is True and r['patient_id']==answer['patient_id'] for r in records)
    wire=p.inputs(answer,context,binding,{'buyer_id':BUYER,'revision':1,'capabilities':capabilities()});groups=p.batches(wire)
    reports.append({'domain':domain,'patient_id':answer['patient_id'],'saved_answer':answer['id'],
        'exact_synthetic_received_records':len(records),'all_records_explicitly_synthetic':True,
        'findings':len(answer['findings']),'request_bytes':[len(json.dumps(p.body(g)).encode()) for g in groups],
        'payload_sha256':[hashlib.sha256(json.dumps(p.body(g)).encode()).hexdigest() for g in groups],
        'payload_content':'Original question, saved synthetic findings and existing exact excerpts, six invented buyer review definitions; no full charts or employer data',
        'destination':'Existing authorized Anthropic messages model integration','new_services':False})
report={'status':'verified_synthetic_only','source_release':config['source_release'],
    'source_sha256':config['snapshot']['source_dataset']['source_sha256'],
    'buyer':'Six invented services declared for local development; no real organization data',
    'maximum_request_bytes':19500,'ledger':'Existing uncapped ledger with failed-key protection','domains':reports}
(OUT/'payload-preflight.json').write_text(json.dumps(report,indent=2));print(json.dumps(report,indent=2))
