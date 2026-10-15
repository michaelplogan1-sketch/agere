-- Tuva lab_result. Current versions only: a result is dropped when a later
-- record supersedes it (supersedes_record_id) or replaces it
-- (replaces_result_record_id, e.g. eGFR recalculated after an age correction).
-- Component code: LOINC when delivered, else the lab's local code
-- (source_component_type 'local'). Status is the source result_status
-- (final / corrected / cancelled / not_calculated). Reference ranges "lo-hi"
-- are split; censored values ("<5", ">60") are kept as delivered in result.
with replaced as (
    select parent_record_id from {{ ref('stg_raw__supersession') }}
    where stream = 'labs' and link_type in ('supersedes', 'replaces_result')
),
l as (
    select * from {{ stream('labs') }}
    where record_id not in (select parent_record_id from replaced)
)
select
    m.person_id
  , l.patient_id
  , l.record_id                                           as lab_result_id
  , {{ js('encounter_id') }}                              as encounter_id
  , {{ js('specimen_record_id') }}                        as accession_number
  , case when (j ->> 'loinc') is not null then 'loinc' when (j ->> 'local_code') is not null then 'local' end as source_component_type
  , coalesce({{ js('loinc') }}, {{ js('local_code') }})   as source_component_code
  , coalesce({{ js('test') }}, {{ js('name') }})          as source_component_description
  , coalesce({{ js('result_status') }}, 'final')          as status
  , {{ js('value') }}                                     as result
  , coalesce({{ jts('resulted_time') }}, {{ jts('result_time') }}, event_date::timestamp) as result_datetime
  , coalesce({{ jts('specimen_time') }}, {{ jts('collected_time') }}, {{ jdate('specimen_date') }}::timestamp) as collection_datetime
  , {{ js('unit') }}                                      as source_units
  , try_cast(nullif(split_part({{ js('reference_range') }}, '-', 1), '') as varchar) as source_reference_range_low
  , try_cast(nullif(split_part({{ js('reference_range') }}, '-', 2), '') as varchar) as source_reference_range_high
  , coalesce({{ js('abnormal_flag') }}, {{ js('flag') }}) as source_abnormal_code
  , 'lab_interface'                                       as data_source
  , {{ js('source_system') }}                             as x_source_system
  , {{ js('local_code') }}                                as x_local_code
  , {{ js('comment') }}                                   as x_comment
  , {{ js('laboratory_order_record_id') }}                as x_order_record_id
  , l.patient_id                                          as x_patient_id
  , l.arrival_date                                        as x_available_date
from l
join {{ ref('int_person_match') }} m on m.patient_id = l.patient_id
