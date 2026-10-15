-- Tuva condition (clinical). Sources:
--   conditions            : problem list / coded conditions, current versions
--                           (records superseded by a later version are dropped).
--                           ICD-10-CM when present, else SNOMED CT.
--   encounters            : ranked diagnoses on the current EHR encounter version
--                           (condition_type encounter_diagnosis, rank = condition_rank).
--   condition_assessments : clinician condition assessments carrying ICD-10-CM.
--   adt_messages          : DG1 admitting (type W) and final (type F) diagnoses,
--                           latest per visit (data_source hl7_adt).
with superseded as (
    select parent_record_id from {{ ref('stg_raw__supersession') }}
    where stream in ('conditions', 'condition_assessments') and link_type = 'supersedes'
),
problem as (
    select
        record_id                                     as source_condition_id
      , patient_id
      , {{ js('encounter_id') }}                      as encounter_id
      , event_date                                    as recorded_date
      , coalesce({{ jdate('onset_date') }}, {{ jdate('onset') }}) as onset_date
      , {{ jdate('abatement_date') }}                 as resolved_date
      , coalesce({{ js('clinical_status') }}, case when (j ->> 'abatement_date') is not null then 'resolved' else 'active' end) as status
      , case when {{ js('problem_type') }} = 'problem_list' or {{ js('diagnosis_basis') }} = 'ehr_problem_list' then 'problem'
             when {{ js('diagnosis_basis') }} = 'clinical_encounter' then 'encounter_diagnosis'
             when {{ js('diagnosis_basis') }} = 'laboratory_evidence' then 'laboratory_evidence'
             else 'problem' end                       as condition_type
      , case when (j ->> 'icd10') is not null then 'icd-10-cm' when (j ->> 'snomed') is not null then 'snomed-ct' else 'unknown' end as code_system
      , coalesce({{ js('icd10') }}, {{ js('snomed') }}) as source_code
      , {{ js('description') }}                       as source_description
      , cast(null as integer)                         as condition_rank
      , 'ehr'                                         as data_source
      , 'conditions'                                  as x_source_stream
      , {{ js('snomed') }}                            as x_snomed_code
      , {{ js('source_system') }}                     as x_source_system
      , arrival_date                                  as x_available_date
    from {{ stream('conditions') }}
    where record_id not in (select parent_record_id from superseded)
      and coalesce(j ->> 'icd10', j ->> 'snomed') is not null
),
enc_dx_raw as (
    select e.encounter_id, e.patient_id, e.start_ts, e.event_date, e.source_system, e.arrival_date,
           unnest(from_json(e.diagnoses, '[{"rank":"INTEGER","icd10":"VARCHAR","type":"VARCHAR"}]')) as dx
    from {{ ref('int_ehr_encounters') }} e
    where e.diagnoses is not null
),
enc_dx as (
    select
        encounter_id || ':dx:' || cast(row_number() over (partition by encounter_id order by dx.rank nulls last, dx.icd10) as varchar) as source_condition_id
      , patient_id
      , encounter_id
      , coalesce(cast(start_ts as date), event_date)  as recorded_date
      , cast(null as date)                            as onset_date
      , cast(null as date)                            as resolved_date
      , 'active'                                      as status
      , 'encounter_diagnosis'                         as condition_type
      , 'icd-10-cm'                                   as code_system
      , dx.icd10                                      as source_code
      , cast(null as varchar)                         as source_description
      , dx.rank                                       as condition_rank
      , 'ehr'                                         as data_source
      , 'encounters'                                  as x_source_stream
      , cast(null as varchar)                         as x_snomed_code
      , source_system                                 as x_source_system
      , arrival_date                                  as x_available_date
    from enc_dx_raw
    where dx.icd10 is not null
),
assess as (
    select
        record_id
      , patient_id
      , {{ js('encounter_id') }}
      , event_date
      , cast(null as date)
      , cast(null as date)
      , 'active'
      , 'condition_assessment'
      , 'icd-10-cm'
      , {{ js('icd10') }}
      , {{ js('condition') }}
      , cast(null as integer)
      , 'ehr'
      , 'condition_assessments'
      , cast(null as varchar)
      , {{ js('source_system') }}
      , arrival_date
    from {{ stream('condition_assessments') }}
    where record_id not in (select parent_record_id from superseded)
      and (j ->> 'icd10') is not null
),
adt_dx as (
    select v.visit_number || ':admit_dx', v.patient_id, v.visit_number, cast(coalesce(v.registration_ts, v.first_message_ts) as date),
           cast(null as date), cast(null as date), 'active', 'admitting_diagnosis', 'icd-10-cm', v.admit_diagnosis_code,
           cast(null as varchar), cast(null as integer), 'hl7_adt', 'adt_messages', cast(null as varchar), v.source_system, v.last_arrival_date
    from {{ ref('int_hl7_adt_visits') }} v where v.admit_diagnosis_code is not null
    union all
    select v.visit_number || ':discharge_dx', v.patient_id, v.visit_number, cast(coalesce(v.discharge_ts, v.last_message_ts) as date),
           cast(null as date), cast(null as date), 'active', 'discharge_diagnosis', 'icd-10-cm', v.discharge_diagnosis_code,
           cast(null as varchar), 1, 'hl7_adt', 'adt_messages', cast(null as varchar), v.source_system, v.last_arrival_date
    from {{ ref('int_hl7_adt_visits') }} v where v.discharge_diagnosis_code is not null
),
u as (
    select * from problem
    union all select * from enc_dx
    union all select * from assess
    union all select * from adt_dx
)
select m.person_id, u.*, u.patient_id as x_patient_id
from u
join {{ ref('int_person_match') }} m on m.patient_id = u.patient_id
