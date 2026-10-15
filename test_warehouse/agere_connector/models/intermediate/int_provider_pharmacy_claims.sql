-- Owned-pharmacy billing, one row per billed fill.
--  * pharmacy_claim_transactions: claim_id = dispense fill_id; amounts are signed
--    cents deltas (paid, reversal, rejection), summed per fill. Final status =
--    last transaction.
--  * pharmacy_claims: a second owned-pharmacy billing feed, one paid claim per
--    pharmacy_prescription_id (cents), status dispensed / paid.
with t as (
    select
        record_id, patient_id, arrival_date, event_date
      , {{ jts('event_time') }}                  as event_ts
      , {{ js('claim_id') }}                     as fill_id
      , {{ js('transaction_status') }}           as transaction_status
      , {{ jint('payer_paid_delta_cents') }}     as payer_cents
      , {{ jint('allowed_delta_cents') }}        as allowed_cents
      , {{ jint('patient_responsibility_delta_cents') }} as patient_cents
      , {{ js('ndc') }}                          as ndc
      , {{ jnum('quantity') }}                   as quantity
      , {{ jnum('days_supply_reported') }}       as days_supply
      , {{ jint('authorized_refill_number') }}   as refill_number
      , {{ js('prescription_id') }}              as prescription_id
      , {{ js('reject_code') }}                  as reject_code
      , {{ js('organization_id') }}              as payer_id
      , {{ js('source_system') }}                as source_system
    from {{ stream('pharmacy_claim_transactions') }}
),
disp as (
    select {{ js('fill_id') }} as fill_id, any_value({{ js('organization_id') }}) as pharmacy_org_id, any_value({{ js('rxcui') }}) as rxcui
    from {{ stream('pharmacy_dispenses') }} where (j ->> 'fill_id') is not null group by 1
),
txn as (
    select
        'pct:' || fill_id                                   as claim_id
      , fill_id
      , arg_max(patient_id, arrival_date)                   as patient_id
      , arg_max(transaction_status, coalesce(event_ts, arrival_date::timestamp)) as final_status
      , min(event_date) filter (where transaction_status = 'paid') as dispensing_date
      , max(event_date)                                     as paid_date
      , sum(payer_cents) / 100.0                            as paid_amount
      , sum(allowed_cents) / 100.0                          as allowed_amount
      , sum(patient_cents) / 100.0                          as patient_amount
      , arg_max(ndc, arrival_date)                          as ndc
      , max(quantity)                                       as quantity
      , max(days_supply)                                    as days_supply
      , max(refill_number)                                  as refills
      , arg_max(payer_id, arrival_date)                     as payer_id
      , arg_max(prescription_id, arrival_date)              as prescription_id
      , arg_max(reject_code, arrival_date)                  as reject_code
      , arg_max(source_system, arrival_date)                as source_system
      , list(transaction_status order by coalesce(event_ts, arrival_date::timestamp)) as status_history
      , max(arrival_date)                                   as last_arrival_date
      , 'pharmacy_claim_transactions'                       as source_stream
    from t group by fill_id
),
pc as (
    select
        'pc:' || {{ js('pharmacy_prescription_id') }}      as claim_id
      , cast(null as varchar)                               as fill_id
      , patient_id
      , {{ js('transaction_status') }}                      as final_status
      , event_date                                          as dispensing_date
      , event_date                                          as paid_date
      , {{ jint('payer_paid_cents') }} / 100.0              as paid_amount
      , ({{ jint('payer_paid_cents') }} + coalesce({{ jint('patient_paid_cents') }}, 0)) / 100.0 as allowed_amount
      , {{ jint('patient_paid_cents') }} / 100.0            as patient_amount
      , {{ js('ndc') }}                                     as ndc
      , {{ jnum('quantity') }}                              as quantity
      , {{ jnum('days_supply_reported') }}                  as days_supply
      , cast(null as bigint)                                as refills
      , {{ js('payer_id') }}                                as payer_id
      , {{ js('pharmacy_prescription_id') }}                as prescription_id
      , cast(null as varchar)                               as reject_code
      , {{ js('source_system') }}                           as source_system
      , [{{ js('transaction_status') }}]                    as status_history
      , arrival_date                                        as last_arrival_date
      , 'pharmacy_claims'                                   as source_stream
    from {{ stream('pharmacy_claims') }}
),
u as (select * from txn union all select * from pc)
select u.*, d.pharmacy_org_id, o.npi as dispensing_provider_npi, d.rxcui
from u
left join disp d on d.fill_id = u.fill_id
left join {{ ref('stg_raw__organizations') }} o on o.organization_id = d.pharmacy_org_id
