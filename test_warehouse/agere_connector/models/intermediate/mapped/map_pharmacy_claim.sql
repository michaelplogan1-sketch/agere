-- Tuva pharmacy_claim.
--   payer_feed       : payer pharmacy claims, final status paid or recouped, net
--                      plan paid. Rejected and reversed (B2 return-to-stock) claims
--                      are not fills and are excluded. Amount-less payer copies of
--                      owned-pharmacy fills (BIN 990099) are dropped when the
--                      owned-pharmacy billing record exists (same patient, NDC,
--                      fill date within 3 days); unmatched copies stay with null paid.
--   pharmacy_billing : owned-pharmacy billing (int_provider_pharmacy_claims), final
--                      status paid.
with payer as (
    select * from {{ ref('int_payer_pharmacy_claims') }}
    where final_claim_status in ('paid', 'recouped')
),
prov as (
    select * from {{ ref('int_provider_pharmacy_claims') }}
    where final_status = 'paid'
),
copy_matched as (
    select p.rx_claim_id
    from payer p
    join {{ ref('int_provider_pharmacy_claims') }} b
      on b.patient_id = p.patient_id and b.ndc = p.ndc
     and abs(datediff('day', b.dispensing_date, p.event_date)) <= 3
    where p.no_amounts
    group by 1
),
payer_rows as (
    select
        p.rx_claim_id                     as claim_id
      , 1                                 as claim_line_number
      , p.patient_id
      , p.member_id
      , p.payer_id                        as payer
      , coalesce(p.product, e.product)    as plan
      , p.prescriber_npi                  as prescribing_provider_npi
      , p.pharmacy_npi                    as dispensing_provider_npi
      , p.event_date                      as dispensing_date
      , p.ndc                             as ndc_code
      , p.quantity
      , p.days_supply
      , p.fill_number                     as refills
      , p.final_event_date                as paid_date
      , p.net_plan_paid                   as paid_amount
      , case when p.no_amounts then null else coalesce(p.net_plan_paid, 0) + coalesce(p.patient_pay, 0) end as allowed_amount
      , case when p.no_amounts then null else coalesce(p.ingredient_cost, 0) + coalesce(p.dispensing_fee, 0) end as charge_amount
      , p.patient_pay                     as copayment_amount
      , 'payer_feed'                      as data_source
      , p.final_claim_status              as x_final_claim_status
      , array_to_string(p.status_history, '>') as x_status_history
      , p.rxcui                           as x_rxcui
      , p.drug_name                       as x_drug_name
      , p.pharmacy_name                   as x_pharmacy_name
      , p.prescription_number             as x_prescription_number
      , p.source_system                   as x_source_system
      , p.no_paid_version_flag            as x_no_paid_version
      , p.last_arrival_date               as x_available_date
    from payer p
    -- amount-less copies do not carry product; take it from the member's enrollment
    left join (select member_id, payer_id, any_value(product) as product from {{ ref('int_enrollment_spans') }} group by 1, 2) e
      on e.member_id = p.member_id and e.payer_id = p.payer_id
    where p.rx_claim_id not in (select rx_claim_id from copy_matched)
),
prov_rows as (
    select
        b.claim_id
      , 1
      , b.patient_id
      , cast(null as varchar)
      , b.payer_id
      , cast(null as varchar)
      , cast(null as varchar)
      , b.dispensing_provider_npi
      , b.dispensing_date
      , b.ndc
      , b.quantity
      , b.days_supply
      , b.refills
      , b.paid_date
      , b.paid_amount
      , b.allowed_amount
      , b.allowed_amount
      , b.patient_amount
      , 'pharmacy_billing'
      , b.final_status
      , array_to_string(b.status_history, '>')
      , b.rxcui
      , cast(null as varchar)
      , b.pharmacy_org_id
      , b.prescription_id
      , b.source_system
      , 0
      , b.last_arrival_date
    from prov b
)
select m.person_id, u.*, u.patient_id as x_patient_id
from (select * from payer_rows union all select * from prov_rows) u
join {{ ref('int_person_match') }} m on m.patient_id = u.patient_id
