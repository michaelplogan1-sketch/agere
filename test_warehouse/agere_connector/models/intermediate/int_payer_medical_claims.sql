-- One row per payer claim family: the plan's final adjudicated claim.
--
-- Ledger (applied in family order):
--   restate rows (paid / denied / denied_upheld)  -> net = this row's amount
--   delta rows (signed reversal, recoupment)      -> net = net + amount
--   void rows (frequency 8 / voided)              -> net = 0
--   info rows (unsigned reversal records)         -> no effect
-- A frequency-7 replacement is a restate row of the family, so it replaces the
-- original's payment. Final status = status of the last non-info row.
with v as (
    select * from {{ ref('int_payer_medical_claim_versions') }}
),
last_restate as (
    select claim_family_id, max(family_order) as restate_order
    from v where ledger_role = 'restate' group by 1
),
agg as (
    select
        v.claim_family_id
      , lr.restate_order
      , max(case when v.ledger_role = 'void' and v.family_order > coalesce(lr.restate_order, 0) then 1 else 0 end) as voided_after
      , sum(case when v.ledger_role = 'delta' and v.family_order > coalesce(lr.restate_order, 0) then v.paid_amount else 0 end)     as delta_paid
      , sum(case when v.ledger_role = 'delta' and v.family_order > coalesce(lr.restate_order, 0) then v.allowed_amount else 0 end)  as delta_allowed
      , sum(case when v.ledger_role = 'delta' and v.family_order > coalesce(lr.restate_order, 0) then v.patient_responsibility else 0 end) as delta_patient_resp
      , max(case when v.ledger_role <> 'info' then v.family_order end) as last_order
      , count(*)                                    as version_count
      , max(v.total_charge)                         as max_total_charge
      , list(v.record_id order by v.family_order)   as source_record_ids
      , list(v.claim_status order by v.family_order) as status_history
      , max(v.arrival_date)                         as last_arrival_date
    from v
    left join last_restate lr on lr.claim_family_id = v.claim_family_id
    group by v.claim_family_id, lr.restate_order
)
select
    a.claim_family_id
  , h.payer_claim_id                         as final_payer_claim_id
  , coalesce(rs.record_id, h.record_id)      as header_record_id
  , h.patient_id
  , l.claim_status                           as final_claim_status
  , l.paid_date                              as final_paid_date
  , case when a.voided_after = 1 then 0 else coalesce(rs.paid_amount, 0) + a.delta_paid end           as net_paid_amount
  , case when a.voided_after = 1 then 0 else coalesce(rs.allowed_amount, 0) + a.delta_allowed end     as net_allowed_amount
  , case when a.voided_after = 1 then 0 else coalesce(rs.patient_responsibility, 0) + a.delta_patient_resp end as net_patient_responsibility
  , coalesce(rs.total_charge, a.max_total_charge) as charge_amount
  , a.max_total_charge
  , a.version_count
  , a.source_record_ids
  , a.status_history
  , a.last_arrival_date
  , case when rs.record_id is null then 1 else 0 end as no_restate_version_flag
  -- header attributes from the final restating version (else the last version)
  , h.member_id, h.payer_id, h.product, h.claim_type, h.type_of_bill, h.place_of_service
  , h.service_from, h.service_to, h.admission_date, h.discharge_date
  , h.ms_drg, h.admission_type, h.admission_source, h.discharge_status
  , h.diagnosis_codes, h.principal_diagnosis
  , h.billing_provider_npi, h.rendering_provider_npi, h.network_status
  , h.claim_received_date, h.carc, h.claim_filing_indicator, h.source_system
  , h.adjudication_sequence as final_adjudication_sequence
from agg a
left join v rs on rs.claim_family_id = a.claim_family_id and rs.family_order = a.restate_order
left join v l  on l.claim_family_id = a.claim_family_id and l.family_order = a.last_order
left join v h  on h.claim_family_id = a.claim_family_id and h.family_order = coalesce(a.restate_order, a.last_order)
