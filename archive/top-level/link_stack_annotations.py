"""Attach the frozen reader's existing source-bound annotations to retrieval."""
from pathlib import Path
import argparse,json
p=argparse.ArgumentParser();p.add_argument('--stack',type=Path,required=True)
p.add_argument('--annotations',type=Path,default=Path('artifacts/agere-evidence-queries-v1'))
a=p.parse_args();s=json.loads((a.stack/'run.json').read_text());m=json.loads((a.annotations/'run.json').read_text())
if s['source_run']!=m['source_run'] or m.get('synthetic') is not True:raise ValueError('Snapshot mismatch')
link={'path':str(a.annotations.resolve()),'model_identity':m['model_identity'],'source_run':m['source_run'],
      'authority':'Existing unvalidated chart-context annotations; weights and labels unchanged.'}
(a.stack/'annotation-link.json').write_text(json.dumps(link,indent=2));print(json.dumps(link,indent=2))
