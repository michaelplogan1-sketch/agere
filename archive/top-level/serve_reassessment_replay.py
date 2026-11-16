"""Production HTTP service over the isolated development replay; no hosted calls."""
import json
from pathlib import Path
from clinical_core.agere.clinical import stack_runtime
from clinical_core.agere.service import serve
root=Path('artifacts/agere-decision-reassessment-v1')
run=root/'replay-run';run.mkdir(exist_ok=True)
source=Path(json.loads(Path('config/agere/active_warehouse.json').read_text())['build'])
(run/'run.json').write_bytes((source/'clinical/run.json').read_bytes())
class LocalReplayStack(stack_runtime.Stack):
 def __init__(self,path,*,allow_live=False,rerank=False):
  super().__init__(path,allow_live=False,rerank=False)
stack_runtime.Stack=LocalReplayStack
serve(run,8887,stack=root/'replay',allow_live_stack=False)
