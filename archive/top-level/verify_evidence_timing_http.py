"""Verify local timing diagnostics/date selection without model dispatch."""
import json
from pathlib import Path
from verify_stable_references_http import api

OUT=Path('artifacts/agere-evidence-timing-v1')


if __name__=='__main__':
    cases=[('account',{'kind':'context','id':'8cf5a23c65c52da0dc815e87b69e90926c203adf476fdd5cd986c566ae909356'},'2026-12-01','2027-02-01')]
    earlier=json.loads(Path('artifacts/agere-need-candidates-v1/http-verification.json').read_text())
    for d in earlier['domains']:
        if d['domain'] in ('medication','outside-care'):
            cases.append((d['domain'],{'kind':'candidates','id':d['candidate_id']},'2029-01-01' if d['domain']=='medication' else '2028-09-21','2029-08-15'))
    results=[]
    for name,request,start,end in cases:
        before=api('/api/stack/context?id='+request['id']) if request['kind']=='context' else api('/api/stack/evidence-needs/candidates?id='+request['id'])
        row=api('/api/stack/evidence-timing',request)
        assert row['new_paid_api_calls']==0 and not row['source_dates_rewritten'] and not row['meaning_resolved']
        assert api('/api/stack/evidence-timing',request)['cache_hit']
        assert api('/api/stack/evidence-timing?id='+row['id'])=={k:v for k,v in row.items() if k!='cache_hit'}
        w=api('/api/stack/evidence-timing/window',{'timing_id':row['id'],'window':{'basis':'source_event','start':start,'end':end}})
        assert sum(w['counts'].values())==len(row['records'])
        assert api('/api/stack/evidence-timing/window?id='+w['id'])==w
        clinical=api('/api/stack/evidence-timing/window',{'timing_id':row['id'],'window':{'basis':'clinical','start':start,'end':end}})
        selected=None;plan=None
        if w['counts']['within']+w['counts']['unresolved']:
            selection={'window_id':w['id'],'include_unresolved':True}
            selected=api('/api/stack/evidence-timing/select',selection)
            assert api('/api/stack/evidence-timing/select',selection)==selected
            assert api('/api/stack/evidence-timing/select?id='+selected['id'])==selected
            assert selected['budget']['omitted_units']==0
            plan=api('/api/stack/context/plan',{'context_id':selected['context_id']});assert plan['can_dispatch']
        else:
            try:api('/api/stack/evidence-timing/select',{'window_id':w['id'],'include_unresolved':False})
            except RuntimeError as exc:assert '400' in str(exc) and 'absent care' in str(exc)
            else:raise AssertionError('Empty window created a context')
        try:api('/api/stack/evidence-timing/window',{'timing_id':row['id'],'window':{'basis':'clinical','start':'2030-01-01','end':''}})
        except RuntimeError as exc:assert '400' in str(exc)
        else:raise AssertionError('Window beyond snapshot accepted')
        after=api('/api/stack/context?id='+request['id']) if request['kind']=='context' else api('/api/stack/evidence-needs/candidates?id='+request['id'])
        assert before==after
        for label,value in [('timing',row),('window',w),('clinical-window',clinical),('selection',selected),('plan',plan)]:
            (OUT/(name+'-'+label+'.json')).write_text(json.dumps(value,indent=2))
        results.append({'domain':name,'timing_id':row['id'],'diagnostics':row['summary'],'source_event_window':w['counts'],
            'clinical_window':clinical['counts'],'selection_id':selected['id'] if selected else None,
            'selected_records':len(selected['record_scope']['record_ids']) if selected else 0,
            'additional_linked_records':len(selected['linked_records_outside_selected_roots']) if selected else 0,
            'original_receipt_unchanged':True,'replay_verified':True,'out_of_snapshot_window_rejected':True,'new_paid_api_calls':0})
    summary={'actual_http_verified':True,'domains':results,'no_model_dispatch':True,'clinical_or_temporal_quality_validated':False}
    (OUT/'http-verification.json').write_text(json.dumps(summary,indent=2));print(json.dumps(summary,indent=2))
