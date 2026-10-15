-- Owned-hospital ADT events (stream adt) rolled up per encounter_id. These
-- encounter_ids are the EHR encounter ids, so they enrich the EHR encounter.
select
    {{ js('encounter_id') }}                                                            as encounter_id
  , min(event_date) filter (where {{ js('event') }} in ('admission', 'inpatient_admission', 'emergency_registration', 'observation_start', 'transfer_to_observation')) as adt_start_date
  , max(event_date) filter (where {{ js('event') }} = 'discharge')                      as adt_discharge_date
  , arg_max(coalesce({{ js('discharge_disposition') }}, {{ js('destination') }}), event_date) filter (where {{ js('event') }} = 'discharge') as adt_discharge_disposition
  , max({{ js('admit_source') }})                                                       as adt_admit_source
  , max(case when {{ js('event') }} = 'inpatient_admission' or {{ js('patient_class') }} = 'inpatient' then 1 else 0 end) as adt_inpatient_flag
  , max(case when {{ js('event') }} in ('observation_start', 'transfer_to_observation') or {{ js('patient_class') }} in ('observation', 'outpatient_observation') then 1 else 0 end) as adt_observation_flag
  , max(case when {{ js('event') }} = 'emergency_registration' or {{ js('patient_class') }} = 'emergency' then 1 else 0 end) as adt_emergency_flag
  , max({{ js('transferred_from_encounter_id') }})                                     as transferred_from_encounter_id
  , count(*)                                                                            as adt_event_count
from {{ stream('adt') }}
group by 1
