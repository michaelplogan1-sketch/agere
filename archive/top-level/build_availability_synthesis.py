"""Bounded synthetic mobility evidence review; does not change source or benefit state."""
import argparse,json,hashlib
from pathlib import Path
from clinical_core.agere.active import stack_path
from clinical_core.agere.clinical import record_context, source_synthesis, availability_source_stage as stage
from clinical_core.agere.gateway import BudgetLedger
from run_integrated_stack import keys

OUT=Path('artifacts/agere-availability-synthesis-v1')
PATIENT='SYN-69283717-971'
RECORDS=['WHL-R-f3eff181891cc76ba82d90','WHL-R-4161dc2f4159ac11c819aa','WHL-R-024ae696ac5e8db3959864','WHL-R-c3f535803e8b47bf0b8c06']
QUESTION='What do the received functional examination, daily-function interview and care-management accounts establish about mobility and support needs, what remains unresolved for home nursing or therapy, and which source-review steps are justified? Separate dated clinical accounts, received but unread records, current patient preferences, payer eligibility and execution authority.'
def write(name,value):
    (OUT/(name+'.json')).write_text(json.dumps(value,indent=2)+'\n')
def main():
    p=argparse.ArgumentParser();p.add_argument('--live',action='store_true');args=p.parse_args();OUT.mkdir(exist_ok=True)
    path=stack_path();context=record_context.create(path,PATIENT,QUESTION,RECORDS)
    write('context',context);plan=source_synthesis.preview(path,context['id']);write('plan',plan)
    row,bundle,links,packets,key=stage.prepare(path,context['id'])
    requests=[{'sha256':hashlib.sha256(json.dumps(body,allow_nan=False).encode()).hexdigest(),'bytes':len(json.dumps(body,allow_nan=False).encode()),'model':body['model'],'source_units':len(units)} for body,units in packets]
    write('synthetic-provenance',{'patient_id':PATIENT,'source':'Original-cohort public synthetic v1.23 imported stack','source_root':str(path),'record_ids':RECORDS,'synthetic':context['synthetic'],'identity_matched':True,'context_id':context['id'],'destination':'Existing configured Anthropic Claude API','complete_selected_units':True,'outside_record_contents_sent':False,'requests':requests,'user_authorization':'Continue full Agere build with existing integrations, uncapped spending; bounded synthetic development review only','no_financial_account_dispatch':True})
    print(json.dumps({'context_id':context['id'],'plan':{k:plan[k] for k in ('source_units','source_passes','request_bytes')},'live':args.live}),flush=True)
    if not args.live:return
    keys();ledger=BudgetLedger(Path('artifacts/agere-full/provider-budget.sqlite3'))
    try:
        write('ledger-before',ledger.receipt());answer=source_synthesis.synthesize(path,context['id'],ledger,allow_live=True);write('answer',answer)
        replay=source_synthesis.synthesize(path,context['id'],ledger,allow_live=False)
        assert replay['id']==answer['id'] and replay['new_paid_api_calls']==0 and replay['cache_hit']
        write('replay',{'answer_id':replay['id'],'cache_hit':replay['cache_hit'],'new_paid_api_calls':replay['new_paid_api_calls']});write('ledger-after',ledger.receipt())
        print(json.dumps({'answer_id':answer['id'],'source_passes':len(answer['passes']),'reconciliation_groups':len(answer['reconciliation']['groups']),'findings':len(answer['findings']),'new_paid_api_calls':answer['new_paid_api_calls']}),flush=True)
    finally:ledger.close()
if __name__=='__main__':main()
