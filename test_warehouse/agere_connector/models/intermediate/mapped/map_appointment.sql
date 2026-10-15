-- Tuva appointment: one row per appointment_id with its latest status
-- (booked -> fulfilled / attended / arrived / no_show / cancelled ...).
-- The latest arrival wins; on an arrival tie a terminal status beats booked.
-- Status, type and reasons stay source-native as Tuva requires.
with a as (
    select *
    from {{ stream('appointments') }}
    where record_id not in (select parent_record_id from {{ ref('stg_raw__supersession') }} where stream = 'appointments')
    qualify row_number() over (
        partition by {{ js('appointment_id') }}
        order by arrival_date desc, case when {{ js('status') }} = 'booked' then 1 else 0 end, record_id desc) = 1
)
select
    m.person_id
  , a.patient_id
  , {{ js('appointment_id') }}                    as appointment_id
  , coalesce({{ js('completed_encounter_id') }}, {{ js('encounter_id') }}) as encounter_id
  , coalesce({{ jts('slot_start') }}, {{ jts('scheduled_time') }}, {{ jdate('scheduled_date') }}::timestamp, a.event_date::timestamp) as start_datetime
  , coalesce({{ jts('scheduled_end_time') }}, {{ jts('slot_start') }} + to_minutes(coalesce({{ jint('slot_minutes') }}, 0))) as end_datetime
  , {{ jint('slot_minutes') }}                    as duration
  , {{ js('organization_id') }}                   as location_id
  , {{ js('clinician_id') }}                      as practitioner_id
  , coalesce({{ js('visit_type') }}, {{ js('modality') }}, {{ js('test_kind') }}) as type
  , {{ js('status') }}                            as status
  , coalesce({{ js('purpose') }}, {{ js('reason') }}) as reason
  , coalesce({{ js('cancellation_reason') }}, case when {{ js('status') }} = 'cancelled' then {{ js('reason') }} end) as cancellation_reason
  , 'ehr'                                         as data_source
  , {{ js('cancellation_channel') }}              as x_cancellation_channel
  , {{ jts('booked_at') }}                        as x_booked_at
  , {{ jint('lead_time_days') }}                  as x_lead_time_days
  , {{ js('source_system') }}                     as x_source_system
  , a.patient_id                                  as x_patient_id
  , a.arrival_date                                as x_available_date
from a
join {{ ref('int_person_match') }} m on m.patient_id = a.patient_id
