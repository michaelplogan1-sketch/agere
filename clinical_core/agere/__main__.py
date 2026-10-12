"""Agere local stack commands. Run `python -m clinical_core.agere --help`."""
import argparse,json
from pathlib import Path
from .runtime import ROOT

def main():
    p=argparse.ArgumentParser(description=__doc__);sub=p.add_subparsers(dest='command',required=True)
    run=sub.add_parser('run');run.add_argument('--release',type=Path,required=True);run.add_argument('--output',type=Path,required=True)
    run.add_argument('--models',type=Path,default=ROOT/'artifacts/agere-full/action-specialists-v1');run.add_argument('--as-of',default='2026-09-30')
    run.add_argument('--document-extractions',type=Path);run.add_argument('--projection',type=Path);run.add_argument('--neural-cases',type=int,default=12)
    train=sub.add_parser('train');train.add_argument('--training',type=Path,required=True);train.add_argument('--output',type=Path,required=True)
    ocr=sub.add_parser('ocr');ocr.add_argument('pdf',type=Path);ocr.add_argument('--output',type=Path,required=True)
    math=sub.add_parser('analyze');math.add_argument('operator',choices=['causal','facilities','shrinkage','allocation','fdr','network','coding','contract','information-value','change-points']);math.add_argument('input',type=Path)
    policy=sub.add_parser('policy');policy.add_argument('spec',type=Path);policy.add_argument('facts',type=Path);policy.add_argument('--as-of',required=True)
    serve=sub.add_parser('serve');serve.add_argument('--run',type=Path,required=True);serve.add_argument('--port',type=int,default=8877);serve.add_argument('--annotations',type=Path);serve.add_argument('--evidence-queries',type=Path);serve.add_argument('--stack',type=Path);serve.add_argument('--allow-live-stack',action='store_true')
    query=sub.add_parser('query');query.add_argument('--run',type=Path,required=True);query.add_argument('question')
    search=sub.add_parser('search');search.add_argument('--run',type=Path,required=True);search.add_argument('--patient');search.add_argument('question')
    a=p.parse_args()
    if a.command=='run':
        from .runtime import run as execute
        result=execute(a.release,a.output,model_path=a.models,as_of=a.as_of,max_neural_cases=a.neural_cases,projection=a.projection,document_extractions=a.document_extractions)
        print(json.dumps({'summary':result['summary'],'compute':result['compute'],'report':str(a.output/'index.html')},indent=2))
    elif a.command=='search':
        from .evidence_index import search
        print(json.dumps(search(a.run/'evidence.sqlite3',a.question,patient_id=a.patient),indent=2))
    elif a.command=='train':
        from .training import fit
        fit(a.training,a.output)
    elif a.command=='ocr':
        from .documents import extract_pdf
        r=extract_pdf(a.pdf,a.output);print(json.dumps({'pages':len(r['pages']),'output':str(a.output)}))
    elif a.command=='policy':
        from .policy import Program
        print(json.dumps(Program(json.loads(a.spec.read_text())).evaluate(json.loads(a.facts.read_text()),a.as_of),indent=2))
    elif a.command=='serve':
        from .service import serve
        if a.allow_live_stack:
            import os
            for line in (ROOT.parent/'insight-engine/.env').read_text().splitlines():
                line=line.strip().removeprefix('export ')
                if '=' not in line:continue
                key,value=line.split('=',1)
                if key.strip() in ('TYPESAFE_API_KEY','ANTHROPIC_API_KEY','PERPLEXITY_API_KEY'):
                    os.environ.setdefault(key.strip(),value.strip().strip('\"\''))
        serve(a.run,a.port,annotations=a.annotations,evidence_queries=a.evidence_queries,stack=a.stack,allow_live_stack=a.allow_live_stack)
    elif a.command=='query':
        from .service import query
        print(json.dumps(query(a.run,a.question),indent=2))
    else:
        from .analytics import crossfit_effect,risk_adjusted_facilities,network_usage,coding_reconciliation
        from .mathematics import facility_shrinkage,allocate,benjamini_hochberg
        from .policy import contract_score
        from ..information_value import expected_information_value
        from ..signal_math import gaussian_change_points
        data=json.loads(a.input.read_text())
        f={'causal':crossfit_effect,'facilities':risk_adjusted_facilities,'shrinkage':facility_shrinkage,
           'allocation':allocate,'fdr':benjamini_hochberg,'network':network_usage,'coding':coding_reconciliation,
           'contract':contract_score,'information-value':expected_information_value,'change-points':gaussian_change_points}[a.operator]
        result=f(**data);print(json.dumps(result,indent=2,allow_nan=False))

if __name__=='__main__':main()
