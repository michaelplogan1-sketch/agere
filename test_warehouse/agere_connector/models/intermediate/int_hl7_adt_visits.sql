-- HL7 v2 ADT feed from outside hospitals, one row per visit (PV1-19 visit_number).
-- Messages are ordered by MSH-7 message time. A01/A04 open the visit, A03
-- closes it, A06 changes the patient class (ED -> inpatient), A08 updates.
-- The visit's class is the class on its latest message; dates are the first
-- registration and the last discharge.
with m as (
    select
        record_id, patient_id, arrival_date, event_date
      , {{ js('visit_number') }}                   as visit_number
      , {{ js('trigger_event') }}                  as trigger_event
      , {{ js('patient_class') }}                  as patient_class
      , {{ js('prior_patient_class') }}            as prior_patient_class
      , {{ js('assigned_location') }}              as assigned_location
      , {{ js('discharge_disposition') }}          as discharge_disposition
      , {{ js('admit_reason') }}                   as admit_reason
      , j -> 'admit_diagnosis'                     as admit_dx
      , j -> 'discharge_diagnosis'                 as discharge_dx
      , {{ js('organization_id') }}                as organization_id
      , {{ js('source_system') }}                  as source_system
      , coalesce(try_strptime(left({{ js('message_datetime') }}, 14), '%Y%m%d%H%M%S'), event_date::timestamp) as message_ts
    from {{ stream('adt_messages') }}
)
select
    visit_number
  , arg_max(patient_id, message_ts)                                         as patient_id
  , arg_max(organization_id, message_ts)                                    as organization_id
  , arg_max(source_system, message_ts)                                      as source_system
  , arg_max(patient_class, message_ts)                                      as final_patient_class
  , max(case when trigger_event = 'A06' or prior_patient_class = 'E' then 1 else 0 end) as ed_to_inpatient_flag
  , max(case when patient_class = 'E' then 1 else 0 end)                    as had_emergency_class_flag
  , min(message_ts) filter (where trigger_event in ('A01', 'A04'))          as registration_ts
  , min(message_ts)                                                          as first_message_ts
  , max(message_ts) filter (where trigger_event = 'A03')                    as discharge_ts
  , max(message_ts)                                                          as last_message_ts
  , arg_max(discharge_disposition, message_ts) filter (where discharge_disposition is not null) as discharge_disposition
  , arg_max(admit_reason, message_ts) filter (where admit_reason is not null) as admit_reason
  , arg_max(admit_dx ->> 'code', message_ts) filter (where (admit_dx ->> 'code') is not null) as admit_diagnosis_code
  , arg_max(discharge_dx ->> 'code', message_ts) filter (where (discharge_dx ->> 'code') is not null) as discharge_diagnosis_code
  , arg_max(assigned_location, message_ts)                                  as last_assigned_location
  , count(*)                                                                 as message_count
  , list(trigger_event order by message_ts)                                  as trigger_sequence
  , list(record_id order by message_ts)                                      as source_record_ids
  , max(arrival_date)                                                        as last_arrival_date
from m
group by visit_number
