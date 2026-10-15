-- Tuva observation.
--   vital-signs : vitals (wide layout unpivoted; long layout measurement/value)
--                 and pediatric growth_observations, LOINC via seeds/vital_sign_codes.csv.
--   survey      : PHQ-9 totals (mood_questionnaires), HRA PHQ-2 / falls / self-rated
--                 health and need items, AHC-HRSN item answers (LOINC per item,
--                 panel_id = the screen record) and brief needs interview items.
-- Codes and categories for instruments live in seeds/observation_codes.csv.
with superseded as (
    select parent_record_id from {{ ref('stg_raw__supersession') }}
    where stream in ('vitals', 'growth_observations', 'mood_questionnaires', 'health_risk_assessments', 'social_screens')
      and link_type = 'supersedes'
),
vitals_src as (
    select * from {{ stream('vitals') }} where record_id not in (select parent_record_id from superseded)
),
{% set wide_fields = ['blood_pressure_systolic', 'blood_pressure_diastolic', 'heart_rate', 'respiratory_rate', 'temperature_C',
                      'oxygen_saturation_percent', 'weight_kg', 'height_cm', 'bmi_kg_m2', 'pain_reported_0_to_10', 'capillary_refill_seconds'] %}
vitals_wide as (
    {%- for f in wide_fields %}
    select record_id, patient_id, {{ js('encounter_id') }} as encounter_id, coalesce({{ jdate('event_time') }}, event_date) as observation_date,
           '{{ f }}' as source_field, {{ js(f) }} as result, coalesce(j -> 'units' ->> '{{ f }}', null) as source_units,
           record_id as panel_id, {{ js('source_system') }} as source_system, arrival_date, 'vitals' as source_stream
    from vitals_src where (j ->> '{{ f }}') is not null
    {{ 'union all' if not loop.last }}
    {%- endfor %}
),
vitals_long as (
    select record_id, patient_id, {{ js('encounter_id') }}, coalesce({{ jdate('event_time') }}, event_date),
           {{ js('measurement') }}, {{ js('value') }}, {{ js('unit') }}, record_id, {{ js('source_system') }}, arrival_date, 'vitals'
    from vitals_src where (j ->> 'measurement') is not null and (j ->> 'value') is not null
),
growth as (
    {%- for f in ['height_cm', 'weight_kg', 'bmi_kg_m2'] %}
    select record_id, patient_id, {{ js('encounter_id') }}, event_date, '{{ f }}', {{ js(f) }}, cast(null as varchar), record_id,
           {{ js('source_system') }}, arrival_date, 'growth_observations'
    from {{ stream('growth_observations') }} where (j ->> '{{ f }}') is not null and record_id not in (select parent_record_id from superseded)
    {{ 'union all' if not loop.last }}
    {%- endfor %}
),
vital_rows as (
    select v.record_id || ':' || v.source_field as observation_id, v.patient_id, v.encounter_id, v.panel_id, v.observation_date,
           'vital-signs' as observation_type, 'loinc' as source_code_type, c.loinc_code as source_code, c.description as source_description,
           v.result, coalesce(v.source_units, c.unit) as source_units, v.source_system, v.arrival_date, v.source_stream
    from (select * from vitals_wide union all select * from vitals_long union all select * from growth) v
    join {{ ref('vital_sign_codes') }} c on c.source_field = v.source_field
),
phq9 as (
    select record_id || ':phq9_total' as observation_id, patient_id, {{ js('encounter_id') }} as encounter_id, record_id as panel_id,
           event_date as observation_date, 'PHQ-9 total' as item, {{ js('total') }} as result, {{ js('source_system') }} as source_system,
           arrival_date, 'mood_questionnaires' as source_stream
    from {{ stream('mood_questionnaires') }} where record_id not in (select parent_record_id from superseded)
),
hra as (
    {%- for f in ['phq2_total', 'falls_past_year_reported', 'general_health_reported', 'stress_reported', 'loneliness_reported',
                  'memory_concern_reported', 'hearing_concern_reported', 'vision_concern_reported', 'food_access_difficulty_reported'] %}
    select record_id || ':{{ f }}' as observation_id, patient_id, {{ js('encounter_id') }} as encounter_id, record_id as panel_id,
           event_date as observation_date, '{{ f }}' as item, {{ js(f) }} as result,
           {{ js('source_system') }} as source_system, arrival_date, 'health_risk_assessments' as source_stream
    from {{ stream('health_risk_assessments') }} where (j ->> '{{ f }}') is not null and record_id not in (select parent_record_id from superseded)
    {{ 'union all' if not loop.last }}
    {%- endfor %}
),
brief as (
    {%- for f in ['food_difficulty_reported', 'transport_difficulty_reported', 'difficulty_paying_for_care_reported'] %}
    select record_id || ':{{ f }}' as observation_id, patient_id, {{ js('encounter_id') }} as encounter_id, record_id as panel_id,
           event_date as observation_date, '{{ f }}' as item, {{ js(f) }} as result,
           {{ js('source_system') }} as source_system, arrival_date, 'social_screens' as source_stream
    from {{ stream('social_screens') }} where (j ->> '{{ f }}') is not null and record_id not in (select parent_record_id from superseded)
    {{ 'union all' if not loop.last }}
    {%- endfor %}
),
instrument_rows as (
    select i.*, c.observation_type, c.code_type, c.code, c.description
    from (select * from phq9 union all select * from hra union all select * from brief) i
    join {{ ref('observation_codes') }} c on c.source_stream = i.source_stream and c.source_item = i.item
),
hrsn_items as (
    select s.record_id, s.patient_id, s.encounter_id, s.event_date, s.source_system, s.arrival_date, s.item
    from (
        select record_id, patient_id, {{ js('encounter_id') }} as encounter_id, event_date, {{ js('source_system') }} as source_system, arrival_date,
               unnest(from_json(j -> 'items', '[{"loinc":"VARCHAR","item":"VARCHAR","answer":"VARCHAR"}]')) as item
        from {{ stream('social_screens') }}
        where {{ js('instrument') }} = 'AHC_HRSN' and record_id not in (select parent_record_id from superseded)
    ) s
),
hrsn_rows as (
    select record_id || ':' || item.item as observation_id, patient_id, encounter_id, record_id as panel_id, event_date as observation_date,
           'survey' as observation_type, case when item.loinc is not null then 'loinc' else 'local' end as source_code_type,
           coalesce(item.loinc, item.item) as source_code, 'AHC-HRSN: ' || item.item as source_description,
           item.answer as result, cast(null as varchar) as source_units, source_system, arrival_date, 'social_screens' as source_stream
    from hrsn_items
    union all
    -- panel row: positive domain count for the completed screen
    select record_id || ':panel', patient_id, {{ js('encounter_id') }}, record_id, event_date, 'survey', 'loinc', '96777-8',
           'AHC-HRSN screening: number of positive domains', cast(json_array_length(j -> 'positive_domains') as varchar), '{{ "{#}" }}',
           {{ js('source_system') }}, arrival_date, 'social_screens'
    from {{ stream('social_screens') }}
    where {{ js('instrument') }} = 'AHC_HRSN' and record_id not in (select parent_record_id from superseded)
),
u as (
    select observation_id, patient_id, encounter_id, panel_id, observation_date, observation_type, source_code_type, source_code,
           source_description, result, source_units, source_system, arrival_date, source_stream
    from vital_rows
    union all
    select observation_id, patient_id, encounter_id, panel_id, observation_date, observation_type, code_type, code, description,
           result, cast(null as varchar), source_system, arrival_date, source_stream
    from instrument_rows
    union all
    select * from hrsn_rows
)
select
    m.person_id
  , u.patient_id
  , u.observation_id
  , u.encounter_id
  , u.panel_id
  , u.observation_date
  , u.observation_type
  , u.source_code_type
  , u.source_code
  , u.source_description
  , u.result
  , u.source_units
  , 'ehr'                as data_source
  , u.source_stream      as x_source_stream
  , u.source_system      as x_source_system
  , u.patient_id         as x_patient_id
  , u.arrival_date       as x_available_date
from u
join {{ ref('int_person_match') }} m on m.patient_id = u.patient_id
