"""Conservative chronology and contradictory evidence, without majority-vote truth."""
from .facts import eligible

VERSION='clinical-reconciliation/1'


def reported_state(facts,concept):
    relevant=[f for f in facts if f['concept']==concept and f['subject'] in {'patient','patient_context'}]
    excluded=[f for f in relevant if f['assertion'] in {'uncertain','retracted','hypothetical'} or f['temporality'] in {'historical','resolved'}]
    usable=[f for f in relevant if f not in excluded]
    if not usable:return {'status':'unknown','value':None,'support':[],'excluded':[f['fact_id'] for f in excluded],'history':[f['fact_id'] for f in relevant]}
    if any(f['evidence'][0]['event_date'] is None for f in usable):
        return {'status':'unknown_statement_chronology','value':None,'support':[f['fact_id'] for f in usable],
                'excluded':[f['fact_id'] for f in excluded],'history':[f['fact_id'] for f in relevant]}
    # Event date is statement chronology; arrival date must not overwrite newer clinical observations.
    newest=max(f['evidence'][0]['event_date'] for f in usable)
    current=[f for f in usable if f['evidence'][0]['event_date']==newest]
    values={str(f['value']) for f in current}
    conflict=len(values)>1
    prior=[f for f in usable if f not in current]
    return {'status':'conflicting_reports' if conflict else 'latest_reported_state',
            'value':None if conflict else current[0]['value'],'source_event_date':newest,
            'support':[f['fact_id'] for f in current],'prior':[f['fact_id'] for f in prior],
            'excluded':[f['fact_id'] for f in excluded],
            'prior_disagreement':any(str(f['value']) not in values for f in prior),
            'current_patient_state_established':False,'date_basis':'Source statement date; physiological date may be unknown'}


def ef_state(facts):
    all_ef=[f for f in facts if f['concept']=='ef'];valid=[f for f in all_ef if eligible(f)]
    current=[f for f in valid if f['temporality']!='historical']
    if not current:
        return {'status':'historical_only' if valid else 'unknown','latest_range':None,
                'support':[f['fact_id'] for f in valid],'excluded':[f['fact_id'] for f in all_ef if f not in valid],
                'hfref_diagnosis_established':False}
    if any(f['evidence'][0]['event_date'] is None for f in current):
        return {'status':'unknown_statement_chronology','latest_range':None,'support':[f['fact_id'] for f in current],
                'excluded':[f['fact_id'] for f in all_ef if f not in valid],'hfref_diagnosis_established':False}
    newest=max(f['evidence'][0]['event_date'] for f in current)
    selected=[f for f in current if f['evidence'][0]['event_date']==newest]
    low=max(f['value']['lower'] for f in selected);high=min(f['value']['upper'] for f in selected)
    return {'status':'conflicting_reports' if low>high else 'latest_documented_range',
            'latest_range':None if low>high else [low,high],'source_event_date':newest,
            'support':[f['fact_id'] for f in selected],
            'historical_low_ef':any(f['value']['upper']<=40 for f in valid if f not in selected),
            'excluded':[f['fact_id'] for f in all_ef if f not in valid],
            'hfref_diagnosis_established':False}


def reconcile(bundle):
    facts=bundle['facts']
    return {'version':VERSION,'patient_id':bundle['patient_id'],'as_of':bundle['as_of'],
            'ef':ef_state(facts),'affordability':reported_state(facts,'financial_hardship'),
            'outside_data_context':[f['fact_id'] for f in facts if f['concept']=='outside_care' and eligible(f)],
            'facts':facts,'model_extraction_proposals':bundle.get('model_extraction_proposals',[]),
            'clinical_gap_established':False,'unseen_data_state':'unknown'}
