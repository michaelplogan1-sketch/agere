-- Tuva immunization: administered vaccines (immunizations stream, status
-- completed) coded in CVX; CPT product code kept as an extension.
select
    m.person_id
  , i.patient_id
  , i.record_id                                   as immunization_id
  , {{ js('encounter_id') }}                      as encounter_id
  , 'cvx'                                         as source_code_type
  , {{ js('cvx_code') }}                          as source_code
  , {{ js('vaccine') }}                           as source_description
  , case {{ js('administration_status') }} when 'completed' then 'completed' else {{ js('administration_status') }} end as status
  , i.event_date                                  as occurrence_date
  , {{ js('dose') }} || ' ' || {{ js('dose_unit') }} as source_dose
  , {{ js('lot_number') }}                        as lot_number
  , {{ js('site') }}                              as body_site
  , {{ js('route') }}                             as route
  , {{ js('organization_id') }}                   as location_id
  , {{ js('administered_by_clinician_id') }}      as practitioner_id
  , 'ehr'                                         as data_source
  , {{ js('cpt') }}                               as x_cpt_code
  , {{ js('product') }}                           as x_product
  , {{ js('source_system') }}                     as x_source_system
  , i.patient_id                                  as x_patient_id
  , i.arrival_date                                as x_available_date
from {{ stream('immunizations') }} i
join {{ ref('int_person_match') }} m on m.patient_id = i.patient_id
where i.record_id not in (select parent_record_id from {{ ref('stg_raw__supersession') }} where stream = 'immunizations')
