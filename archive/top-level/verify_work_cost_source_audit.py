import json,sqlite3
from pathlib import Path
from clinical_core.contracts import digest
from clinical_core.agere.clinical import financial_accounts as F
out=Path('artifacts/agere-work-cost-context-v1');context=json.loads((out/'preview.json').read_text());mapping=F.mapping();db=sqlite3.connect('file:artifacts/agere-healthcare-v123/stack/stack.sqlite3?mode=ro',uri=True)
counts={'patients':0,'accounts':0,'account_record_bindings':0,'snapshot_field_comparisons':0,'financial_receipts_reconstructed':0}
for item in context['financial_reports']:
 pid=item['patient_id'];report=item['report'];records=[json.loads(r[0]) for r in db.execute('SELECT payload FROM records WHERE patient=? ORDER BY id',(pid,))];byid={r['record_id']:r for r in records}
 projected=F.project(pid,records,context['available_by']);assert projected=={k:v for k,v in report.items() if k not in ('id','base')};counts['financial_receipts_reconstructed']+=1;counts['patients']+=1
 for account in report['accounts']:
  counts['accounts']+=1
  for rid in account['source_record_ids']:
   r=byid[rid];assert r['patient_id']==pid and r['available_date']<=context['available_by'];counts['account_record_bindings']+=1
  for ref in account['selected_snapshots']:
   raw=byid[ref['record_id']];assert digest(raw)==ref['record_hash']
   for key in mapping['snapshot_amounts']:
    assert account['snapshot_amounts'][key]==raw.get(key);counts['snapshot_field_comparisons']+=1
 assert digest(report)==item['receipt_hash']
db.close();counts.update(status='passed',receipt_cutoff=context['available_by'],program_attribution_inferred=False,amounts_netted_against_cost=False,hidden_generator_or_evaluation_records_read=False)
(out/'source-audit.json').write_text(json.dumps(counts,indent=2)+'\n');print(json.dumps(counts,indent=2))
