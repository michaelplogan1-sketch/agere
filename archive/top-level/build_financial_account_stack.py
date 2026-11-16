"""Exercise financial reconciliation through the actual current-warehouse question path."""
import argparse,json
from pathlib import Path
from clinical_core.agere.active import stack_path
from clinical_core.agere.clinical.stack_runtime import Stack
from clinical_core.agere.clinical.graph_workbench import financial_examples,snapshot_examples
from clinical_core.agere.clinical import financial_accounts as F


def main():
    p=argparse.ArgumentParser();p.add_argument('--output',required=True,type=Path);a=p.parse_args();a.output.mkdir(exist_ok=False)
    stack=Stack(stack_path(),allow_live=False)
    try:
        ex=snapshot_examples(financial_examples(),stack.retriever.meta['snapshot']['as_of'])[0]
        answer=stack.compose(ex['question']);(a.output/'question.json').write_text(json.dumps(answer,indent=2)+'\n')
        r=answer['results'][-1]['result'];repeat=stack.compose(ex['question'])
        (a.output/'replay.json').write_text(json.dumps(repeat,indent=2)+'\n')
        receipt=F.inspect(stack.path,r['patients'][0]['id']);(a.output/'patient-example.json').write_text(json.dumps(receipt,indent=2)+'\n')
        print(json.dumps({k:r[k] for k in ('patients_evaluated','accounts_evaluated','account_status_counts','equation_status_counts','cash_comparison_counts','unassigned_source_count','elapsed_seconds')},indent=2))
    finally:stack.close()


if __name__=='__main__':main()
