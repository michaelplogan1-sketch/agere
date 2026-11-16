"""Exercise a fresh packet-follow-up context through the main synthesis route."""
import json
from pathlib import Path
from verify_stable_references_http import api

OUT=Path('artifacts/agere-default-stable-v1')
CONTEXT='744de15fc0327a22661f33477e2325c79ea53fcb1545c2f3f367371eff080624'
PREVIOUS=[
 '975024996abefa0c26ade1337cdb55810996df6397704e765c3efe4ed8655658',
 '0227207af37a3865659acbd25461b5a1eb5d6eee00ce23f4a17d259f3cbac08b',
 'd7d96be6f304b5649e24c329e38648d1ef6475c579c1645498dcd7fd177c8799',
 '3c26f4b1afff1ff8048117a7c55bed945dcd40306ec1324c1240af2dd3cc8c36']


if __name__=='__main__':
 OUT.mkdir(exist_ok=True)
 previous={key:api('/api/stack/case-synthesis?id='+key) for key in PREVIOUS}
 plan=api('/api/stack/context/plan',{'context_id':CONTEXT})
 (OUT/'live-plan.json').write_text(json.dumps(plan,indent=2))
 assert plan['source_passes']==2 and plan['source_units']==11 and plan['can_dispatch']
 assert plan['spending_limit']=='uncapped'
 answer=api('/api/stack/context/synthesize',{'context_id':CONTEXT})
 (OUT/'live-answer.json').write_text(json.dumps(answer,indent=2))
 assert answer['schema']=='stable-reference-reconciliation/1'
 assert len(answer['reference_manifest'])==11 and answer['new_paid_api_calls']<=3
 parent=api('/api/stack/case-synthesis?id='+answer['parent_answer_id'])
 assert parent['schema']=='source-stage-synthesis/1' and parent['reconciliation']['groups']==[]
 assert answer['passes']==parent['passes']
 saved=api('/api/stack/case-synthesis?id='+answer['id'])
 replay=api('/api/stack/context/synthesize',{'context_id':CONTEXT})
 assert replay['cache_hit'] and replay['new_paid_api_calls']==0 and replay['id']==answer['id']
 assert api('/api/stack/context/plan',{'context_id':CONTEXT})['saved_answer_id']==answer['id']
 progress=api('/api/stack/context/progress?context_id='+CONTEXT)
 assert progress['status']=='complete'
 for key,value in previous.items():assert api('/api/stack/case-synthesis?id='+key)==value
 needs=api('/api/stack/evidence-needs',{'answer_id':answer['id'],'selectors':[]})
 for step in saved['review_steps']:
  if step.get('evidence_references'):
   need=next(n for n in needs['needs'] if n['id']==step['id'])
   assert {r['record_id'] for r in step['evidence_references']}<={r['record_id'] for r in need['selectors']}
   assert need['semantic_resolution']=='pending'
 assessment=api('/api/stack/work/prepare',{'program':'hf_review','patient':answer['patient_id'],'synthesis_ids':[answer['id']]})
 assert assessment['synthesis_assessments']==1
 summary={'actual_http_verified':True,'context_id':CONTEXT,'answer_id':answer['id'],'source_stage_id':parent['id'],
  'source_passes':len(answer['passes']),'reconciliation_groups':len(answer['reconciliation']['groups']),
  'legacy_reconciliation_calls':0,'new_paid_api_calls':answer['new_paid_api_calls'],
  'creation_cost_estimate_usd':sum(u.get('cost_estimate',{}).get('estimated_usd',0) for u in answer['usage']),
  'exact_replay_new_paid_calls':0,'stable_units':len(answer['reference_manifest']),
  'findings':len(answer['findings']),'exact_citations':sum(len(f['citations']) for f in answer['findings']),
  'needs_summary':needs['summary'],'previous_answers_preserved':len(previous),
  'program_assessment_attached':True,'clinical_accuracy_validated':False,'human_reviews_created':0,'external_actions_executed':0}
 for name,value in [('source-stage',parent),('live-saved',saved),('live-replay',replay),('live-needs',needs),('live-assessment',assessment),('live-progress',progress),('http-verification',summary)]:
  (OUT/(name+'.json')).write_text(json.dumps(value,indent=2))
 print(json.dumps(summary,indent=2))
