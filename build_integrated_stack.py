"""Build the actual observed warehouse retrieval and operator layers, offline."""
from pathlib import Path
import argparse,json
from clinical_core.agere.clinical.warehouse_retrieval import build

if __name__=='__main__':
    p=argparse.ArgumentParser();p.add_argument('--output',type=Path,required=True)
    p.add_argument('--warehouse',type=Path,default=Path('artifacts/agere-full/warehouse.sqlite3'))
    p.add_argument('--clinical-run',type=Path,default=Path('artifacts/agere-clinical-workflows-v2'))
    p.add_argument('--document-extractions',type=Path,default=Path('artifacts/agere-full/document-extractions.json'))
    a=p.parse_args();result=build(a.warehouse,a.clinical_run,a.output,document_extractions=a.document_extractions)
    from clinical_core.agere.clinical.analytic_registry import compute_all,attach_clinical_reviews
    result['analytic_operators']=compute_all(a.output)
    result['clinical_reviews']=attach_clinical_reviews(a.output,a.clinical_run)
    from clinical_core.agere.clinical.program_registry import install_defaults
    install_defaults(a.output)
    print(json.dumps(result,indent=2))
