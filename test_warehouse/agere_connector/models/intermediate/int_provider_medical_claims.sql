-- One row per provider (owned practice / lab / hospital) billing claim.
--
-- Header: the current submission per claim_id. A corrected submission
-- (submission_kind = corrected) names the submission it replaces in
-- original_submission_record_id; replaced submissions are dropped, then the
-- latest arrival wins.
-- Money: sum over the claim's adjudication records (medical_claims) of
-- financial_deltas_cents (payer, allowed, deductible, copay, coinsurance).
-- Reversals carry negative deltas and replacement adjudications carry the
-- re-priced amount, so the sum is the final net. Facility DRG adjudications
-- without deltas use payer_paid_cents / allowed_cents.
-- Adjudication rows without a claim_id (a second 835 feed keyed only by
-- record and encounter) are their own claims (claim_id = record_id) and carry
-- their own service code.
with sub as (
    select
        record_id, patient_id, arrival_date
      , {{ js('claim_id') }}                      as claim_id
      , {{ js('submission_kind') }}               as submission_kind
      , {{ js('original_submission_record_id') }} as original_submission_record_id
      , {{ js('claim_type') }}                    as claim_type
      , {{ js('type_of_bill') }}                  as type_of_bill
      , {{ js('place_of_service') }}              as place_of_service
      , {{ jdate('service_date') }}               as service_date
      , {{ jdate('admission_date') }}             as admission_date
      , {{ jdate('discharge_date') }}             as discharge_date
      , {{ js('ms_drg') }}                        as ms_drg
      , j -> 'diagnosis_codes'                    as diagnosis_codes
      , {{ js('principal_diagnosis') }}           as principal_diagnosis
      , {{ js('billing_provider_npi') }}          as billing_provider_npi
      , {{ js('rendering_provider_npi') }}        as rendering_provider_npi
      , {{ jint('billed_cents') }}                as billed_cents
      , {{ js('member_identifier_submitted') }}   as member_identifier_submitted
      , {{ js('organization_id') }}               as organization_id
      , {{ js('encounter_id') }}                  as encounter_id
      , {{ js('account_id') }}                    as account_id
      , {{ js('source_system') }}                 as source_system
    from {{ stream('medical_claim_submissions') }}
),
current_sub as (
    select *
    from sub
    where record_id not in (select original_submission_record_id from sub where original_submission_record_id is not null)
    qualify row_number() over (partition by claim_id order by arrival_date desc, record_id desc) = 1
),
adj as (
    select
        record_id, patient_id, arrival_date, event_date
      , coalesce({{ js('claim_id') }}, record_id)  as claim_id
      , case when (j ->> 'claim_id') is null then 1 else 0 end as standalone_flag
      , {{ js('status') }}                         as status
      , {{ js('organization_id') }}                as payer_id
      , {{ js('submission_record_id') }}           as submission_record_id
      , coalesce(try_cast((j -> 'financial_deltas_cents' ->> 'payer') as bigint), {{ jint('payer_paid_cents') }})   as payer_cents
      , coalesce(try_cast((j -> 'financial_deltas_cents' ->> 'allowed') as bigint), {{ jint('allowed_cents') }})    as allowed_cents
      , coalesce(try_cast((j -> 'financial_deltas_cents' ->> 'billed') as bigint), {{ jint('billed_cents') }})      as billed_cents
      , coalesce(try_cast((j -> 'financial_deltas_cents' ->> 'deductible') as bigint), 0)   as deductible_cents
      , coalesce(try_cast((j -> 'financial_deltas_cents' ->> 'copay') as bigint), 0)        as copay_cents
      , coalesce(try_cast((j -> 'financial_deltas_cents' ->> 'coinsurance') as bigint), 0)  as coinsurance_cents
      , coalesce(try_cast((j -> 'financial_deltas_cents' ->> 'responsibility') as bigint), {{ jint('patient_responsibility_cents') }}) as patient_resp_cents
      -- standalone-feed header fields
      -- the standalone 835 feed carries no service date; its event date (the
      -- service date the remittance reports against) stands in
      , coalesce({{ jdate('service_date') }}, case when (j ->> 'claim_id') is null then event_date end) as service_date
      , {{ js('service_code') }}                   as service_code
      , {{ js('service_code_system') }}            as service_code_system
      , {{ js('revenue_code') }}                   as revenue_code
      , {{ js('place_of_service') }}               as place_of_service
      , {{ js('service_organization_id') }}        as service_organization_id
      , {{ js('encounter_id') }}                   as encounter_id
      , {{ js('source_system') }}                  as source_system
      , j -> 'diagnosis_codes'                     as diagnosis_codes
      , {{ js('principal_diagnosis') }}            as principal_diagnosis
      , {{ js('billing_provider_npi') }}           as billing_provider_npi
      , {{ js('rendering_provider_npi') }}         as rendering_provider_npi
      , {{ js('type_of_bill') }}                   as type_of_bill
      , {{ js('ms_drg') }}                         as ms_drg
    from {{ stream('medical_claims') }}
),
remit as (
    select {{ js('claim_id') }} as claim_id, max(event_date) as remit_date, arg_max({{ js('status') }}, arrival_date) as remit_status,
           arg_max({{ js('payer_organization_id') }}, arrival_date) as remit_payer_id
    from {{ stream('provider_remittance_advice') }}
    group by 1
),
adj_agg as (
    select
        claim_id
      , max(standalone_flag)                              as standalone_flag
      , arg_max(status, arrival_date)                     as final_status
      , arg_max(payer_id, arrival_date)                   as payer_id
      , max(event_date)                                   as last_adjudication_date
      , sum(payer_cents) / 100.0                          as net_paid_amount
      , sum(allowed_cents) / 100.0                        as net_allowed_amount
      , sum(deductible_cents) / 100.0                     as deductible_amount
      , sum(copay_cents) / 100.0                          as copayment_amount
      , sum(coinsurance_cents) / 100.0                    as coinsurance_amount
      , sum(patient_resp_cents) / 100.0                   as patient_responsibility
      , count(*)                                          as adjudication_count
      , list(record_id order by arrival_date)             as adjudication_record_ids
      , list(status order by arrival_date)                as status_history
      , max(arrival_date)                                 as last_arrival_date
      -- standalone header (rows without claim_id)
      , arg_max(patient_id, arrival_date)                 as patient_id
      , min(service_date)                                 as service_date
      , arg_max(service_code, arrival_date)               as service_code
      , arg_max(service_code_system, arrival_date)        as service_code_system
      , arg_max(revenue_code, arrival_date)               as revenue_code
      , arg_max(place_of_service, arrival_date)           as place_of_service
      , arg_max(service_organization_id, arrival_date)    as service_organization_id
      , arg_max(encounter_id, arrival_date)               as encounter_id
      , arg_max(source_system, arrival_date)              as source_system
      , arg_max(diagnosis_codes, arrival_date)            as diagnosis_codes
      , arg_max(principal_diagnosis, arrival_date)        as principal_diagnosis
      , arg_max(billing_provider_npi, arrival_date)       as billing_provider_npi
      , arg_max(rendering_provider_npi, arrival_date)     as rendering_provider_npi
      , arg_max(type_of_bill, arrival_date)               as type_of_bill
      , arg_max(ms_drg, arrival_date)                     as ms_drg
      , max(billed_cents) filter (where billed_cents > 0) as billed_cents
    from adj
    group by claim_id
)
select
    coalesce(s.claim_id, a.claim_id)                     as claim_id
  , coalesce(s.patient_id, a.patient_id)                 as patient_id
  , s.record_id                                          as submission_record_id
  , coalesce(a.standalone_flag, 0)                       as standalone_flag
  , case when a.claim_id is null then 'submitted_not_adjudicated' else a.final_status end as final_status
  , coalesce(a.payer_id, r.remit_payer_id)               as payer_id
  , coalesce(r.remit_date, a.last_adjudication_date)     as paid_date
  , r.remit_status
  , coalesce(a.net_paid_amount, 0)                       as net_paid_amount
  , coalesce(a.net_allowed_amount, 0)                    as net_allowed_amount
  , a.deductible_amount, a.copayment_amount, a.coinsurance_amount, a.patient_responsibility
  , coalesce(s.billed_cents, a.billed_cents) / 100.0     as charge_amount
  , a.adjudication_count, a.adjudication_record_ids, a.status_history
  , coalesce(s.claim_type, case when a.type_of_bill is not null then 'institutional' else 'professional' end) as claim_type
  , coalesce(s.type_of_bill, a.type_of_bill)             as type_of_bill
  , coalesce(s.place_of_service, a.place_of_service)     as place_of_service
  , coalesce(s.service_date, a.service_date)             as service_date
  , s.admission_date, s.discharge_date
  , coalesce(s.ms_drg, a.ms_drg)                         as ms_drg
  , coalesce(s.diagnosis_codes, a.diagnosis_codes)       as diagnosis_codes
  , coalesce(s.principal_diagnosis, a.principal_diagnosis) as principal_diagnosis
  , coalesce(s.billing_provider_npi, a.billing_provider_npi) as billing_provider_npi
  , coalesce(s.rendering_provider_npi, a.rendering_provider_npi) as rendering_provider_npi
  , s.member_identifier_submitted
  , coalesce(s.organization_id, a.service_organization_id) as billing_organization_id
  , coalesce(s.encounter_id, a.encounter_id)             as encounter_id
  , s.account_id
  , coalesce(s.source_system, a.source_system)           as source_system
  , a.service_code as standalone_service_code, a.service_code_system as standalone_service_code_system, a.revenue_code as standalone_revenue_code
  , greatest(s.arrival_date, a.last_arrival_date)        as last_arrival_date
from current_sub s
full outer join adj_agg a on a.claim_id = s.claim_id
left join remit r on r.claim_id = coalesce(s.claim_id, a.claim_id)
