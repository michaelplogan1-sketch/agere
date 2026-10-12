"""Same-question local source-selection diagnostics; no provider calls."""
from pathlib import Path
import json
import time
from clinical_core.agere.active import stack_path
from clinical_core.agere.clinical import source_selection as S, record_context as C
from clinical_core.agere.clinical.warehouse_retrieval import Retriever
from clinical_core.agere.clinical.native_snapshot_cache import install
from clinical_core.agere.clinical import literal_reference_runtime, structured_transport
from clinical_core.agere.clinical import reference_table_context, reference_reconciliation_transport, meaning_source_dispatch

OUT = Path('artifacts/agere-source-selection-v1')


def main():
    path = stack_path(); install(path); literal_reference_runtime.install(); structured_transport.install()
    reference_table_context.install(); reference_reconciliation_transport.install(); meaning_source_dispatch.install(path)
    retriever = Retriever(path)
    try:
        cases = json.loads(Path('research/agere/workflow-evaluation-v1/plan.json').read_text())['cases']
        checks = []
        for case in cases:
            target = OUT/(case['id']+'.json'); started = time.perf_counter()
            result = S.prepare(path, case['patient'], case['question'], retriever)
            target.write_text(json.dumps(result, indent=2, allow_nan=False)+'\n')
            selected = set(result['schema_retrieval']['selected_categories'])
            required = set(case['required_categories_if_received'])
            plan = result['source_plan']; rows = plan['contexts'] if plan else []
            checked = {'case': case['id'], 'schema_selected_categories': sorted(selected),
                       'required_not_in_schema_lane': sorted(required-selected),
                       'selected_records': len(result['preview']['selected_record_ids']),
                       'groups': len(rows), 'group_omissions': [len(r['omissions']) for r in rows],
                       'request_fit': [g['request_fit'] for g in plan['plan']['groups']] if plan else [],
                       'status': result['status'], 'seconds': time.perf_counter()-started,
                       'clinical_validated': False}
            checks.append(checked); print(json.dumps(checked), flush=True)
            (OUT/'local-verification.json').write_text(json.dumps(checks, indent=2)+'\n')
    finally: retriever.close()


if __name__ == '__main__': main()
