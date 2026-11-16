"""Recompute declared development acceptance and verify its served read-only view.

No inference, fitting, clinical labeling or work dispatch. Broader capabilities
need their own dedicated verifier receipts; this runner does not create them.
"""
import argparse
import hashlib
import json
from pathlib import Path
import urllib.request
from clinical_core.agere.workflow_scorecard import build

OUT=Path('artifacts/agere-release-acceptance-v1')
def read(p):return json.loads(Path(p).read_text())
def write(p,r):Path(p).write_text(json.dumps(r,indent=2)+'\n')

def main():
    parser=argparse.ArgumentParser();parser.add_argument('--http',action='store_true');args=parser.parse_args()
    before=read(OUT/'before-scorecard.json')
    report=build(before['plan_path'],'artifacts/agere-workflow-evaluation-v1/baseline',
        read('artifacts/agere-planner-contracts-v1/planning-trace-map.json'),read(OUT/'stage-trace-map.json'))
    report['assessment_basis']='Recomputed saved development executions with five independent task reopens. No new inference or source-selection qualification.'
    report['known_gaps']=[c['id']+': '+', '.join(x['id']+'='+x['status'] for x in c['acceptance']['checks'] if x['status']!='passed') for c in report['cases']]
    for key in ('previous_release_planning','previous_release_source_interpretation','usage_accounting'):
        if key in before:report[key]=before[key]
    assert report['stage_counts']==before['stage_counts']
    assert report['complete_workflows']==sum(c['acceptance']['status']=='passed' for c in report['cases'])==0
    assert report['engineering_status_counts']=={'failed':3,'blocked':3}
    assert sum(next(ch for ch in c['acceptance']['checks'] if ch['id']=='work_persists')['status']=='passed' for c in report['cases'])==5
    assert report['release_acceptance']['scope_areas']==list(range(1,11))
    assert report['release_acceptance']['engineering_requirements_total']==9
    for path,sha in report['trace_sha256'].items():assert hashlib.sha256(Path(path).read_bytes()).hexdigest()==sha
    manifest=read('BUILD_OWNER.json')['protected_manifest'];protected=read(manifest)['protected_files']
    for path,sha in protected.items():assert hashlib.sha256(Path(path).read_bytes()).hexdigest()==sha,path
    write(OUT/'scorecard.json',report);write('artifacts/agere-workflow-evaluation-v1/scorecard.json',report)
    http=None
    if args.http:
        with urllib.request.urlopen('http://localhost:8885/api/stack/workflow-evaluation',timeout=30) as response:served=json.load(response)
        assert served==report
        with urllib.request.urlopen('http://localhost:8885/stack-evaluation',timeout=30) as response:html=response.read().decode()
        assert 'Full release requirements' in html and 'Engineering acceptance' in html
        http={'status':'passed','served_report_matches_generated':True,'computed_completion':True}
        write(OUT/'http-verification.json',http)
    result={'status':'passed','source_stage_results_unchanged':True,'computed_complete_workflows':report['complete_workflows'],
        'engineering_status_counts':report['engineering_status_counts'],'source_linked_handoffs':report['connected_answer_handoffs'],
        'independent_task_reopens_passed':5,'whole_release_requirements':12,'engineering_requirements':9,
        'protected_files_unchanged':len(protected),'trace_hashes_verified':len(report['trace_sha256']),
        'http':http,'new_provider_calls':0,'clinical_validated':False,'full_product_acceptance':False}
    write(OUT/'verification.json',result);print(json.dumps(result,indent=2))

if __name__=='__main__':main()
