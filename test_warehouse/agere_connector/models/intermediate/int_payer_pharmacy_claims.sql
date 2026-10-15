-- One row per payer pharmacy claim (rx_claim_id) with its final status and net
-- plan paid. Versions are ordered by adjudication_sequence then arrival:
--   paid / rejected          -> net = plan_paid (rejected = 0)
--   reversed (NCPDP B2)      -> net = 0 (the fill was returned to stock / backed out)
--   recouped (CO-27 etc.)    -> net = net + plan_paid (negative)
-- If a claim has two "current" rows, the latest arrival wins.
with r as (
    select
        record_id, patient_id, arrival_date, event_date
      , {{ js('rx_claim_id') }}           as rx_claim_id
      , coalesce({{ jint('adjudication_sequence') }}, 1) as adjudication_sequence
      , {{ js('claim_status') }}          as claim_status
      , {{ jnum('plan_paid') }}           as plan_paid
      , {{ jnum('patient_pay') }}         as patient_pay
      , {{ jnum('ingredient_cost') }}     as ingredient_cost
      , {{ jnum('dispensing_fee') }}      as dispensing_fee
      , {{ js('member_id') }}             as member_id
      , {{ js('payer_id') }}              as payer_id
      , {{ js('product') }}               as product
      , {{ js('ndc') }}                   as ndc
      , {{ js('rxcui') }}                 as rxcui
      , {{ js('drug_name') }}             as drug_name
      , {{ jnum('days_supply') }}         as days_supply
      , {{ jnum('quantity_dispensed') }}  as quantity
      , {{ jint('fill_number') }}         as fill_number
      , {{ js('prescription_number') }}   as prescription_number
      , {{ js('pharmacy_npi') }}          as pharmacy_npi
      , {{ js('pharmacy_name') }}         as pharmacy_name
      , {{ js('pharmacy_type') }}         as pharmacy_type
      , {{ js('prescriber_npi') }}        as prescriber_npi
      , {{ js('reject_code') }}           as reject_code
      , {{ js('reversal_reason') }}       as reversal_reason
      , {{ js('formulary_tier') }}        as formulary_tier
      , {{ js('bin') }}                   as bin
      , {{ js('source_system') }}         as source_system
    from {{ stream('payer_pharmacy_claims') }}
),
o as (
    select *, row_number() over (partition by rx_claim_id order by adjudication_sequence, arrival_date, record_id) as ord
    from r
),
lr as (
    select rx_claim_id, max(ord) as restate_ord from o where claim_status in ('paid', 'rejected') group by 1
),
agg as (
    select o.rx_claim_id
      , max(lr.restate_ord) as restate_ord
      , max(o.ord) as last_ord
      , max(case when o.claim_status = 'reversed' and o.ord > coalesce(lr.restate_ord, 0) then 1 else 0 end) as reversed_after
      , sum(case when o.claim_status = 'recouped' and o.ord > coalesce(lr.restate_ord, 0) then o.plan_paid else 0 end) as recouped_amount
      , bool_and(o.plan_paid is null) as no_amounts
      , count(*) as version_count
      , list(o.claim_status order by o.ord) as status_history
      , max(o.arrival_date) as last_arrival_date
    from o left join lr on lr.rx_claim_id = o.rx_claim_id
    group by o.rx_claim_id
)
select
    h.*
  , l.claim_status as final_claim_status
  , l.event_date   as final_event_date
  , case when a.no_amounts then null
         when a.reversed_after = 1 then 0
         else coalesce(h.plan_paid, 0) + a.recouped_amount end as net_plan_paid
  , a.version_count
  , a.status_history
  , a.last_arrival_date
  , a.no_amounts
  , case when a.restate_ord is null then 1 else 0 end as no_paid_version_flag
from agg a
join o h on h.rx_claim_id = a.rx_claim_id and h.ord = coalesce(a.restate_ord, a.last_ord)
join o l on l.rx_claim_id = a.rx_claim_id and l.ord = a.last_ord
