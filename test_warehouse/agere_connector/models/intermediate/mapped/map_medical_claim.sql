-- Tuva medical_claim (line grain).
--   data_source payer_feed       : payer adjudicated claims (plan view), one claim per
--                                  adjudication family, final net amounts
--                                  (int_payer_medical_claims), payer copies of
--                                  provider claims removed (int_claim_crosswalk).
--   data_source provider_billing : owned practices / labs / hospitals billing
--                                  (int_provider_medical_claims), adjudicated claims only.
-- Diagnoses: principal first, then the remaining codes in source order; source
-- codes are passed as delivered (Tuva strips dots and validates against ICD-10-CM).
with payer_hdr as (
    select
        'payer_feed'                       as data_source
      , c.claim_family_id                  as claim_id
      , c.patient_id
      , c.member_id
      , c.payer_id                         as payer
      , c.product                          as plan
      , c.claim_type
      , c.service_from                     as claim_start_date
      , c.service_to                       as claim_end_date
      , c.admission_date
      , c.discharge_date
      , c.admission_source                 as admit_source_code
      , c.admission_type                   as admit_type_code
      , c.discharge_status                 as discharge_status_raw
      , case when c.claim_type = 'professional' then c.place_of_service end as place_of_service_code
      , c.type_of_bill
      , c.ms_drg
      , c.rendering_provider_npi           as rendering_npi
      , c.billing_provider_npi             as billing_npi
      , c.final_paid_date                  as paid_date
      , c.net_paid_amount                  as header_paid
      , c.net_patient_responsibility       as header_patient_resp
      , cast(null as double)               as deductible_amount
      , cast(null as double)               as copayment_amount
      , cast(null as double)               as coinsurance_amount
      , case c.network_status when 'in_network' then 1 when 'out_of_network' then 0 end as in_network_flag
      , c.diagnosis_codes
      , c.principal_diagnosis
      , c.final_claim_status
      , c.final_payer_claim_id             as source_claim_id
      , c.version_count
      , array_to_string(c.status_history, '>') as status_history
      , c.source_system
      , c.claim_filing_indicator
      , c.carc
      , cast(null as varchar)              as encounter_id
      , c.last_arrival_date
    from {{ ref('int_payer_medical_claims') }} c
    where c.claim_family_id not in (select claim_family_id from {{ ref('int_claim_crosswalk') }})
),
provider_hdr as (
    select
        'provider_billing'                 as data_source
      , c.claim_id
      , c.patient_id
      , c.member_identifier_submitted      as member_id
      , c.payer_id                         as payer
      , cast(null as varchar)              as plan
      , c.claim_type
      , coalesce(c.admission_date, c.service_date) as claim_start_date
      , coalesce(c.discharge_date, c.service_date) as claim_end_date
      , c.admission_date
      , c.discharge_date
      , cast(null as varchar)              as admit_source_code
      , cast(null as varchar)              as admit_type_code
      , cast(null as varchar)              as discharge_status_raw
      , case when c.claim_type = 'professional' then c.place_of_service end as place_of_service_code
      , c.type_of_bill
      , c.ms_drg
      , c.rendering_provider_npi           as rendering_npi
      , c.billing_provider_npi             as billing_npi
      , c.paid_date
      , c.net_paid_amount                  as header_paid
      , c.patient_responsibility           as header_patient_resp
      , c.deductible_amount
      , c.copayment_amount
      , c.coinsurance_amount
      , cast(null as integer)              as in_network_flag
      , c.diagnosis_codes
      , c.principal_diagnosis
      , c.final_status                     as final_claim_status
      , c.claim_id                         as source_claim_id
      , c.adjudication_count               as version_count
      , array_to_string(c.status_history, '>') as status_history
      , c.source_system
      , cast(null as varchar)              as claim_filing_indicator
      , cast(null as varchar)              as carc
      , c.encounter_id
      , c.last_arrival_date
    from {{ ref('int_provider_medical_claims') }} c
    -- final-action claims only: submissions with no adjudication in the release are pending
    where c.final_status <> 'submitted_not_adjudicated'
),
hdr as (
    select * from payer_hdr
    union all
    select * from provider_hdr
),
dx as (
    select data_source, claim_id,
           list_filter(
             list_distinct(list_concat([principal_diagnosis], coalesce(from_json(diagnosis_codes, '["VARCHAR"]'), []))),
             x -> x is not null and x <> '') as dx_list
    from hdr
)
select
    h.claim_id
  , l.claim_line_number
  , h.claim_type
  , m.person_id
  , h.member_id
  , h.payer
  , h.plan
  , h.claim_start_date
  , h.claim_end_date
  , l.line_start_date                  as claim_line_start_date
  , l.line_end_date                    as claim_line_end_date
  , h.admission_date
  , h.discharge_date
  , h.admit_source_code
  , h.admit_type_code
  , dd.discharge_disposition_code
  , h.place_of_service_code
  , case when length(h.type_of_bill) = 4 and h.type_of_bill like '0%' then right(h.type_of_bill, 3) else h.type_of_bill end as bill_type_code
  , case when h.ms_drg is not null then 'ms-drg' end as drg_code_type
  , lpad(h.ms_drg, 3, '0')             as drg_code
  , l.revenue_code                     as revenue_center_code
  , l.units                            as service_unit_quantity
  , case when upper(l.code_system) in ('CPT', 'HCPCS', 'CPT/HCPCS') then l.hcpcs_code end as hcpcs_code
  , h.rendering_npi
  , h.billing_npi
  -- UB-04 claims are billed by the facility: the billing NPI is the facility NPI
  , case when h.claim_type = 'institutional' then h.billing_npi end as facility_npi
  , h.paid_date
  , l.line_paid_amount                 as paid_amount
  , l.line_allowed_amount              as allowed_amount
  , l.line_charge                      as charge_amount
  , case when l.claim_line_number = 1 then h.coinsurance_amount end as coinsurance_amount
  , case when l.claim_line_number = 1 then h.copayment_amount end   as copayment_amount
  , case when l.claim_line_number = 1 then h.deductible_amount end  as deductible_amount
  , l.line_allowed_amount              as total_cost_amount
  , 'icd-10-cm'                        as diagnosis_code_type
  {%- for i in range(1, 26) %}
  , d.dx_list[{{ i }}]                 as diagnosis_code_{{ i }}
  {%- endfor %}
  , h.in_network_flag
  , h.data_source
  -- extension columns
  , h.patient_id                       as x_patient_id
  , h.source_claim_id                  as x_source_claim_id
  , h.final_claim_status               as x_final_claim_status
  , h.status_history                   as x_status_history
  , h.version_count                    as x_version_count
  , h.source_system                    as x_source_system
  , h.claim_filing_indicator           as x_claim_filing_indicator
  , h.carc                             as x_carc
  , h.header_paid                      as x_header_paid_amount
  , h.header_patient_resp              as x_header_patient_responsibility
  , h.encounter_id                     as x_encounter_id
  , l.line_origin                      as x_line_origin
  , l.line_record_id                   as x_line_record_id
  , case when upper(l.code_system) not in ('CPT', 'HCPCS', 'CPT/HCPCS') then l.code_system end as x_line_code_system
  , case when upper(l.code_system) not in ('CPT', 'HCPCS', 'CPT/HCPCS') then l.hcpcs_code end as x_line_local_code
  , h.last_arrival_date                as x_available_date
from hdr h
join {{ ref('int_medical_claim_lines') }} l on l.data_source = h.data_source and l.claim_id = h.claim_id
join {{ ref('int_person_match') }} m on m.patient_id = h.patient_id
left join dx d on d.data_source = h.data_source and d.claim_id = h.claim_id
left join {{ ref('discharge_disposition_map') }} dd on dd.source_value = h.discharge_status_raw
