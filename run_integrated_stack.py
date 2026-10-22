"""Run the connected stack. Paid routes are opt-in and share the existing ledger."""
import argparse,json,os
from pathlib import Path
from clinical_core.agere.clinical.stack_runtime import Stack
from clinical_core.agere.clinical.analytic_registry import execute,compute_all,CATALOG
from clinical_core.agere.clinical.cohorts import select

DESKTOP_KEYS = {'typesafe': 'TYPESAFE_API_KEY', 'jev': 'TYPESAFE_API_KEY', 'perplexity': 'PERPLEXITY_API_KEY',
                'anthropic': 'ANTHROPIC_API_KEY', 'open ai': 'OPENAI_API_KEY', 'openai': 'OPENAI_API_KEY'}


def desktop_keys(path=Path.home()/'Desktop'/'typesafe_key.txt'):
    """Mike's key file: a bare first line is the TypeSafe (Jev) key, then 'Label: key' lines. Never printed."""
    if not path.exists(): return
    lines = [l.strip() for l in path.read_text().splitlines() if l.strip()]
    for i, line in enumerate(lines):
        label, sep, value = line.partition(':')
        name = DESKTOP_KEYS.get(label.strip().lower()) if sep else ('TYPESAFE_API_KEY' if i == 0 else None)
        value = value.strip() if sep else line
        if name and value: os.environ[name] = value.strip('"\'')


def keys():
    desktop_keys()  # the Desktop file is the current source; the older env file only fills what it lacks
    for line in Path('/Users/mikel/insight-engine/.env').read_text().splitlines():
        line=line.strip().removeprefix('export ')
        if '=' not in line:continue
        key,value=line.split('=',1)
        if key.strip() in ('TYPESAFE_API_KEY','PERPLEXITY_API_KEY','ANTHROPIC_API_KEY'):
            os.environ.setdefault(key.strip(),value.strip().strip('\"\''))

if __name__=='__main__':
    p=argparse.ArgumentParser();p.add_argument('--stack',type=Path,default=Path('artifacts/agere-cohort-stack-v2'))
    p.add_argument('--question');p.add_argument('--patient');p.add_argument('--plan',type=Path)
    p.add_argument('--operator',choices=sorted(CATALOG));p.add_argument('--cohort',type=Path)
    p.add_argument('--analysis-window',type=Path);p.add_argument('--cohort-only',action='store_true')
    p.add_argument('--view',default='all',choices=['all','declining','missing_inputs','source_conflicts'])
    p.add_argument('--drug-class',choices=['bb','arni','mra','sglt2'])
    p.add_argument('--review',choices=['jev','perplexity']);p.add_argument('--live',action='store_true')
    p.add_argument('--operators',action='store_true');p.add_argument('--output',type=Path)
    a=p.parse_args()
    cohort=json.loads(a.cohort.read_text()) if a.cohort else None
    window=json.loads(a.analysis_window.read_text()) if a.analysis_window else None
    if a.operators:result=compute_all(a.stack)
    elif a.cohort_only:result=select(a.stack,cohort,patient=a.patient)
    elif a.operator:result=execute(a.stack,a.operator,patient=a.patient,drug_class=a.drug_class,view=a.view,
                                 cohort=cohort,analysis_window=window)
    else:
        if (a.live or a.plan or a.review) and (a.cohort or a.analysis_window):
            raise ValueError('Use cohort/window fields inside the typed plan, or express constraints in the question')
        if a.live:keys()
        s=Stack(a.stack,allow_live=a.live)
        try:
            if a.review:result=s.review(a.question,a.patient,a.review)
            elif a.plan:result=s.run_plan(json.loads(a.plan.read_text()))
            elif a.live:result=s.ask(a.question,live=True)
            else:
                selected=select(a.stack,cohort,patient=a.patient)
                result={'synthetic':True,'retrieval':s.retriever.search(a.question,patient=a.patient,
                         patient_ids=selected['patient_ids'],analysis_window=window),
                         'cohort':{k:v for k,v in selected.items() if k not in ('patient_ids','unknown_patient_ids')}}
        finally:s.close()
    if a.output:a.output.write_text(json.dumps(result,indent=2,allow_nan=False))
    print(json.dumps(result,indent=2,allow_nan=False))
