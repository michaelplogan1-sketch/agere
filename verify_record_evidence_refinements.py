"""Recheck exact bindings and review provenance using saved paid answers only."""
import json
from urllib.error import HTTPError
from verify_record_evidence_http import OUT, ROOT, STACK, request, program_work, BudgetLedger

def main():
    original=json.loads((OUT/'answer.json').read_text())
    old=json.loads((ROOT/'artifacts/agere-multipass-synthesis-v1/answer.json').read_text())
    budget=BudgetLedger(ROOT/'artifacts/agere-full/provider-budget.sqlite3')
    try:before=budget.receipt()
    finally:budget.close()
    answer=request('/api/stack/case-synthesis?id='+original['id'])
    assert answer['findings']==original['findings']
    assert request('/api/stack/case-synthesis?id='+old['id'])['findings']==old['findings']
    needs=request('/api/stack/evidence-needs',{'answer_id':old['id'],'selectors':[]})
    bound=next(n for n in needs['needs'] if n['selectors'])
    followup=request('/api/stack/evidence-needs/context',{'receipt_id':needs['id'],'need_id':bound['id']})
    assert followup['context_id']==answer['context_id']
    derived=request('/api/stack/evidence-needs',{'answer_id':answer['id'],'selectors':[]})
    assert derived['explicit_selectors']==[]
    aliases=[a for n in derived['needs'] for a in n['unit_alias_bindings']]
    assert any(a['pass_number']==1 and a['alias']=='u6' and a['record_id']=='WHM-R-f07f0c5291aacbe0494b18' and a['field']=='text' for a in aliases)
    explicit=request('/api/stack/evidence-needs',{'answer_id':answer['id'],'selectors':[{'record_id':'WHM-R-f07f0c5291aacbe0494b18','field':'text'}]})
    assert len(explicit['explicit_selectors'])==1 and explicit['explicit_selectors'][0]['field']=='text'
    assert explicit['explicit_selectors'][0]['record_id']=='WHM-R-f07f0c5291aacbe0494b18'
    invalid=[]
    for selectors in ({},False,''):
        try:request('/api/stack/evidence-needs',{'answer_id':answer['id'],'selectors':selectors})
        except HTTPError as e:invalid.append(e.code)
    assert invalid==[400,400,400]
    before_work=request('/api/stack/work?program=hf_review&offset=0')['summary']
    proposal=request('/api/stack/work/prepare',{'program':'hf_review','patient':'P-1001','synthesis_ids':[answer['id']]})
    db=program_work.database(STACK)
    try:assessment=program_work.load(db,'work_assessments',proposal['patients'][0]['assessment_id'])
    finally:db.close()
    assert assessment['synthesis_context']['answers'][0]['evidence_needs_id']==derived['id']
    assert request('/api/stack/work?program=hf_review&offset=0')['summary']==before_work
    budget=BudgetLedger(ROOT/'artifacts/agere-full/provider-budget.sqlite3')
    try:after=budget.receipt()
    finally:budget.close()
    assert before==after
    report=json.loads((OUT/'http-verification.json').read_text())
    report.update(final_need_provenance_verified=True,source_pass_alias_binding_verified=True,
        source_pass_alias_bindings=len(aliases),explicit_selector_isolation_verified=True,
        invalid_selector_types_rejected=invalid,refinement_new_paid_api_calls=0)
    for name,value in [('evidence-needs',needs),('followup-context',followup),('followup-evidence-needs',derived),
            ('explicit-evidence-need',explicit),('work-proposal',proposal),('assessment',assessment),('http-verification',report)]:
        (OUT/(name+'.json')).write_text(json.dumps(value,indent=2)+'\n')
    print(json.dumps({'alias_bindings':aliases,'explicit_selector_isolation':True,'needs_attached':True,'new_paid_api_calls':0}))

if __name__=='__main__':main()
