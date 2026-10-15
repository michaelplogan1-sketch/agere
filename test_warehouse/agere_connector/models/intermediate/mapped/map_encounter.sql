-- Tuva clinical encounter (claims-derived encounters are built by Tuva from
-- medical_claim). Two sources:
--   ehr     : EHR encounters (current version), enriched with the owned-hospital
--             ADT events that share their encounter_id. Type from the EHR visit
--             type (seeds/ehr_encounter_type_map.csv), else the place of service
--             (seeds/pos_encounter_type.csv); an ADT inpatient admission makes the
--             encounter acute inpatient.
--   hl7_adt : one encounter per outside-hospital visit_number. Final patient class
--             I -> acute inpatient, E -> emergency department, O -> outpatient
--             hospital or clinic (observation). An ED visit converted to inpatient
--             (A06) is one inpatient encounter, x_ed_to_inpatient = 1.
with ehr as (
    select
        e.encounter_id
      , e.patient_id
      , case when a.adt_inpatient_flag = 1 then 'acute inpatient'
             else coalesce(tm.encounter_type, pm.encounter_type, 'office visit - other') end as encounter_type
      , coalesce(a.adt_start_date, cast(e.start_ts as date), e.event_date)            as encounter_start_date
      , coalesce(a.adt_discharge_date, cast(e.end_ts as date), cast(e.start_ts as date), e.event_date) as encounter_end_date
      , asm.admit_source_code
      , case when e.admission_type = 'emergency' then '1' end                         as admit_type_code
      , dd.discharge_disposition_code
      , e.attending_clinician_id                                                      as attending_provider_id
      , c.display_name                                                                as attending_provider_name
      , o.npi                                                                         as facility_npi
      , o.display_name                                                                as facility_name
      , list_filter(from_json(e.diagnoses, '[{"rank":"INTEGER","icd10":"VARCHAR","type":"VARCHAR"}]'), x -> x.rank = 1)[1].icd10 as primary_diagnosis_code
      , 'ehr'                                                                         as data_source
      , e.source_encounter_type                                                       as x_source_encounter_type
      , e.place_of_service                                                            as x_place_of_service
      , e.status                                                                      as x_encounter_status
      , e.organization_id                                                             as x_organization_id
      , e.source_system                                                               as x_source_system
      , e.disposition                                                                 as x_disposition
      , e.chief_complaint                                                             as x_chief_complaint
      , a.adt_event_count                                                             as x_adt_event_count
      , coalesce(a.adt_observation_flag, case when e.source_encounter_type = 'observation' then 1 else 0 end) as x_observation_stay
      , cast(null as integer)                                                         as x_ed_to_inpatient
      , a.transferred_from_encounter_id                                               as x_transferred_from_encounter_id
      , e.record_id                                                                   as x_source_record_id
      , e.arrival_date                                                                as x_available_date
    from {{ ref('int_ehr_encounters') }} e
    left join {{ ref('int_owned_adt_events') }} a on a.encounter_id = e.encounter_id
    left join {{ ref('ehr_encounter_type_map') }} tm on tm.source_encounter_type = e.source_encounter_type
    left join {{ ref('pos_encounter_type') }} pm on pm.place_of_service_code = e.place_of_service
    left join {{ ref('discharge_disposition_map') }} dd on dd.source_value = coalesce(a.adt_discharge_disposition, e.disposition)
    left join {{ ref('admit_source_map') }} asm on asm.source_value = a.adt_admit_source
    left join {{ ref('stg_raw__organizations') }} o on o.organization_id = e.organization_id
    left join {{ ref('stg_raw__clinicians') }} c on c.clinician_id = e.attending_clinician_id
),
hl7 as (
    select
        v.visit_number                                                 as encounter_id
      , v.patient_id
      , case v.final_patient_class when 'I' then 'acute inpatient'
                                   when 'E' then 'emergency department'
                                   else 'outpatient hospital or clinic' end as encounter_type
      , cast(coalesce(v.registration_ts, v.first_message_ts) as date) as encounter_start_date
      , cast(coalesce(v.discharge_ts, v.last_message_ts) as date)     as encounter_end_date
      , case when v.had_emergency_class_flag = 1 then '7' end         as admit_source_code
      , case when v.had_emergency_class_flag = 1 then '1' end         as admit_type_code
      , dd.discharge_disposition_code
      , cast(null as varchar)                                          as attending_provider_id
      , cast(null as varchar)                                          as attending_provider_name
      , o.npi                                                          as facility_npi
      , coalesce(o.display_name, v.organization_id)                    as facility_name
      , coalesce(v.discharge_diagnosis_code, v.admit_diagnosis_code)   as primary_diagnosis_code
      , 'hl7_adt'                                                      as data_source
      , 'patient_class_' || v.final_patient_class                      as x_source_encounter_type
      , cast(null as varchar)                                          as x_place_of_service
      , case when v.discharge_ts is null then 'open' else 'discharged' end as x_encounter_status
      , v.organization_id                                              as x_organization_id
      , v.source_system                                                as x_source_system
      , v.discharge_disposition                                        as x_disposition
      , v.admit_reason                                                 as x_chief_complaint
      , v.message_count                                                as x_adt_event_count
      , case when v.final_patient_class = 'O' then 1 else 0 end        as x_observation_stay
      , v.ed_to_inpatient_flag                                         as x_ed_to_inpatient
      , cast(null as varchar)                                          as x_transferred_from_encounter_id
      , array_to_string(v.source_record_ids, ',')                      as x_source_record_id
      , v.last_arrival_date                                            as x_available_date
    from {{ ref('int_hl7_adt_visits') }} v
    left join {{ ref('discharge_disposition_map') }} dd on dd.source_value = v.discharge_disposition
    left join {{ ref('stg_raw__organizations') }} o on o.organization_id = v.organization_id
),
u as (select * from ehr union all select * from hl7)
select
    m.person_id
  , u.*
  , case when u.primary_diagnosis_code is not null then 'icd-10-cm' end as primary_diagnosis_code_type
  , u.patient_id as x_patient_id
from u
join {{ ref('int_person_match') }} m on m.patient_id = u.patient_id
