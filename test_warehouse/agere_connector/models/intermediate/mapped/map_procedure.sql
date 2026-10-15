-- Tuva procedure (clinical). Performed services from the EHR and diagnostic
-- feeds. Local procedure names are mapped to HCPCS/CPT through
-- seeds/local_procedure_codes.csv (Tuva ships no local-name crosswalk); rows
-- whose local name has no mapping keep the local name with code_system 'local'.
with src as (
    select record_id, patient_id, {{ js('encounter_id') }} as encounter_id,
           coalesce({{ jdate('performed_time') }}, event_date) as procedure_date,
           coalesce({{ js('procedure_name') }}, {{ js('procedure') }}) as local_name,
           coalesce({{ js('performing_clinician_id') }}) as practitioner_id,
           {{ js('status') }} as status, {{ js('source_system') }} as source_system, arrival_date, 'procedures' as source_stream
    from {{ stream('procedures') }}
    union all
    select record_id, patient_id, {{ js('encounter_id') }}, coalesce({{ jdate('performed_date') }}, event_date),
           case when {{ js('method') }} is not null then {{ js('method') }}
                when (j ->> 'birads_category') is not null then 'screening_mammography' end,
           {{ js('interpreting_clinician_id') }}, {{ js('result_status') }}, {{ js('source_system') }}, arrival_date, 'cancer_screening_results'
    from {{ stream('cancer_screening_results') }}
    union all
    select record_id || ':' || cast(p.idx as varchar), patient_id, {{ js('encounter_id') }}, coalesce({{ jdate('procedure_start') }}, event_date),
           p.name, {{ js('performing_clinician_id') }}, 'completed', {{ js('source_system') }}, arrival_date, 'endoscopy_reports'
    from {{ stream('endoscopy_reports') }},
         lateral (select unnest(from_json(j -> 'performed_procedures', '["VARCHAR"]')) as name,
                         unnest(range(1, len(from_json(j -> 'performed_procedures', '["VARCHAR"]')) + 1)) as idx) p
    union all
    select record_id, patient_id, {{ js('encounter_id') }}, coalesce({{ jdate('acquired_time') }}, event_date),
           lower({{ js('modality') }}) || '_' || {{ js('anatomic_site') }}, cast(null as varchar), {{ js('status') }}, {{ js('source_system') }}, arrival_date, 'imaging_studies'
    from {{ stream('imaging_studies') }}
    union all
    select record_id, patient_id, {{ js('encounter_id') }}, event_date,
           {{ js('hcpcs_code') }}, cast(null as varchar), {{ js('status') }}, {{ js('source_system') }}, arrival_date, 'wellness_visit_services'
    from {{ stream('wellness_visit_services') }}
    where (j ->> 'hcpcs_code') is not null
)
select
    m.person_id
  , s.patient_id
  , s.record_id                                       as source_procedure_id
  , s.encounter_id
  , s.procedure_date
  , case when s.source_stream = 'wellness_visit_services' then 'hcpcs' else coalesce(c.code_system, 'local') end as code_system
  , case when s.source_stream = 'wellness_visit_services' then s.local_name else coalesce(c.code, s.local_name) end as source_code
  , coalesce(c.description, s.local_name)            as source_description
  , case when s.local_name = 'incomplete_colonoscopy' then '53' end as modifier_1
  , s.practitioner_id
  , 'ehr'                                             as data_source
  , s.source_stream                                   as x_source_stream
  , s.local_name                                      as x_local_procedure_name
  , s.status                                          as x_status
  , s.source_system                                   as x_source_system
  , s.patient_id                                      as x_patient_id
  , s.arrival_date                                    as x_available_date
from src s
join {{ ref('int_person_match') }} m on m.patient_id = s.patient_id
left join {{ ref('local_procedure_codes') }} c on c.source_procedure = s.local_name
where s.local_name is not null
