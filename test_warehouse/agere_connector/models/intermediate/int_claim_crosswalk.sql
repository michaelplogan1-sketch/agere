-- Payer copy <-> provider claim crosswalk (no shared key exists).
-- Rule: same source patient, payer service_from = provider service date, and
-- the payer family's largest total_charge (in cents) = the provider billed_cents
-- on any submission of the claim (or the billed amount on its adjudication when
-- no submission was delivered). Only adjudicated provider claims take part: a
-- submitted claim with no adjudication in the release is not loaded to Tuva, so
-- the payer's adjudication of that service is kept instead.
-- Matched payer families are the payer's adjudication of a claim the provider
-- feed already carries; they are excluded from Tuva medical_claim so each
-- service is counted once (the provider claim is kept, data_source provider_billing).
with adjudicated as (
    select claim_id, patient_id, service_date, charge_amount
    from {{ ref('int_provider_medical_claims') }}
    where final_status <> 'submitted_not_adjudicated'
),
prov_keys as (
    -- billed amount on any submission (original or corrected) of an adjudicated claim
    select s.patient_id, {{ js('claim_id', j='s.j') }} as claim_id, {{ jdate('service_date', j='s.j') }} as service_date,
           {{ jint('billed_cents', j='s.j') }} as billed_cents
    from {{ stream('medical_claim_submissions') }} s
    where {{ js('claim_id', j='s.j') }} in (select claim_id from adjudicated)
    union
    -- claims known only from their adjudication (no submission delivered)
    select patient_id, claim_id, service_date, cast(round(charge_amount * 100) as bigint)
    from adjudicated
)
select
    p.claim_family_id
  , min(k.claim_id)              as provider_claim_id
  , count(distinct k.claim_id)   as provider_match_count
  , 'patient + service_from = service_date + max(total_charge) cents = billed_cents' as match_rule
from {{ ref('int_payer_medical_claims') }} p
join prov_keys k
  on k.patient_id = p.patient_id
 and k.service_date = p.service_from
 and k.billed_cents = cast(round(p.max_total_charge * 100) as bigint)
group by p.claim_family_id
