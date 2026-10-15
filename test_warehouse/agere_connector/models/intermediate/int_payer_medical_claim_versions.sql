-- Every payer_medical_claims row, flattened, assigned to its claim family and
-- ordered within it. This is the full adjudication history (kept for audit).
--
-- Family: a frequency-7 replacement claim (new payer_claim_id, carries
-- original_payer_claim_id) folds into the claim it replaces. Secondary-payer
-- copies carry an "-X" suffix; their replacements ("<id>-R-X") point
-- original_payer_claim_id (and supersedes) at the PRIMARY claim, so the target
-- of an -X replacement is original_payer_claim_id || '-X' (the secondary
-- claim), not the primary.
--
-- Order inside a family: original claim rows before replacement rows, then
-- adjudication_sequence, then arrival date, paid date, record id. Same-sequence
-- rows are corrections and the later arrival wins.
with r as (
    select
        record_id
      , patient_id
      , arrival_date
      , supersedes_record_id
      , {{ js('payer_claim_id') }}                 as payer_claim_id
      , {{ jint('adjudication_sequence') }}        as adjudication_sequence
      , {{ js('claim_status') }}                   as claim_status
      , {{ js('claim_frequency_code') }}           as claim_frequency_code
      , {{ js('original_payer_claim_id') }}        as original_payer_claim_id
      , {{ jnum('paid_amount') }}                  as paid_amount
      , {{ jnum('allowed_amount') }}               as allowed_amount
      , {{ jnum('total_charge') }}                 as total_charge
      , {{ jnum('patient_responsibility') }}       as patient_responsibility
      , {{ jdate('paid_date') }}                   as paid_date
      , {{ jdate('claim_received_date') }}         as claim_received_date
      , {{ jdate('service_from') }}                as service_from
      , {{ jdate('service_to') }}                  as service_to
      , {{ jdate('admission_date') }}              as admission_date
      , {{ jdate('discharge_date') }}              as discharge_date
      , {{ js('claim_type') }}                     as claim_type
      , {{ js('type_of_bill') }}                   as type_of_bill
      , {{ js('place_of_service') }}               as place_of_service
      , {{ js('ms_drg') }}                         as ms_drg
      , {{ js('admission_type') }}                 as admission_type
      , {{ js('admission_source') }}               as admission_source
      , {{ js('discharge_status') }}               as discharge_status
      , j -> 'diagnosis_codes'                     as diagnosis_codes
      , {{ js('principal_diagnosis') }}            as principal_diagnosis
      , {{ js('billing_provider_npi') }}           as billing_provider_npi
      , {{ js('rendering_provider_npi') }}         as rendering_provider_npi
      , {{ js('network_status') }}                 as network_status
      , {{ js('member_id') }}                      as member_id
      , {{ js('payer_id') }}                       as payer_id
      , {{ js('product') }}                        as product
      , {{ js('carc') }}                           as carc
      , {{ js('claim_filing_indicator') }}         as claim_filing_indicator
      , {{ js('source_system') }}                  as source_system
    from {{ stream('payer_medical_claims') }}
),
replacement_target as (
    select distinct
        payer_claim_id as replacement_claim_id
      , original_payer_claim_id || case when payer_claim_id like '%-X' then '-X' else '' end as target_claim_id
    from r
    where claim_frequency_code = '7' and original_payer_claim_id is not null
),
fam as (
    select
        r.*
      , coalesce(rt.target_claim_id, r.payer_claim_id) as claim_family_id
      , case when rt.replacement_claim_id is not null then 1 else 0 end as is_replacement
      , case
          when r.claim_status in ('paid', 'denied', 'denied_upheld') then 'restate'
          when r.claim_status = 'reversed' and r.paid_amount < 0 then 'delta'
          when r.claim_status = 'recouped' then 'delta'
          when r.claim_status = 'voided' then 'void'
          -- unsigned reversal: the record of an adjudication that was reversed
          -- and re-processed; the re-processed row carries the amounts.
          when r.claim_status = 'reversed' then 'info'
          else 'restate'
        end as ledger_role
    from r
    left join replacement_target rt on rt.replacement_claim_id = r.payer_claim_id
)
select
    *
  , row_number() over (
        partition by claim_family_id
        order by is_replacement, adjudication_sequence, arrival_date, paid_date, record_id
    ) as family_order
from fam
