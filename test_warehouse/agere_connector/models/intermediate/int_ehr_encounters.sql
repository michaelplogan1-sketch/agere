-- EHR encounters, current version per encounter_id. The EHR delivers an
-- in_progress version at check-in and a completed version (with place of
-- service and diagnoses) at sign-off, both under the same encounter_id: the
-- latest arrival wins, completed before in_progress on ties. Records named as
-- superseded by another record are dropped first.
with e as (
    select * from {{ stream('encounters') }}
    where record_id not in (select parent_record_id from {{ ref('stg_raw__supersession') }} where stream = 'encounters')
)
select
    record_id
  , patient_id
  , {{ js('encounter_id') }}            as encounter_id
  , {{ js('type') }}                    as source_encounter_type
  , {{ js('status') }}                  as status
  , {{ js('place_of_service') }}        as place_of_service
  , {{ jts('start_time') }}             as start_ts
  , {{ jts('end_time') }}               as end_ts
  , event_date
  , {{ js('organization_id') }}         as organization_id
  , {{ js('attending_clinician_id') }}  as attending_clinician_id
  , j -> 'diagnoses'                    as diagnoses
  , {{ js('disposition') }}             as disposition
  , {{ js('admission_type') }}          as admission_type
  , {{ js('chief_complaint') }}         as chief_complaint
  , {{ js('referring_encounter_id') }}  as referring_encounter_id
  , {{ js('source_system') }}           as source_system
  , arrival_date
from e
qualify row_number() over (
    partition by {{ js('encounter_id') }}
    order by arrival_date desc, case {{ js('status') }} when 'completed' then 0 else 1 end, record_id desc) = 1
