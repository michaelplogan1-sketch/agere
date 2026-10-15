-- Line grain for every medical claim kept in Tuva (payer families not matched
-- to a provider claim, plus every provider claim), with the header net paid and
-- allowed allocated to lines.
--
-- Payer lines: payer_medical_claim_lines of the final version's claim id, else
-- of the family's original claim id, else (secondary -X copies, which carry
-- no lines of their own) of the primary claim. Provider lines:
-- medical_claim_lines of the current submission, else of the claim's latest
-- submission. A claim without lines gets one header-only line.
-- Allocation: header net x line weight, weight = line allowed share (payer),
-- line charge share otherwise, equal split if both are zero; the rounding
-- remainder goes to the last line so lines sum exactly to the header.
with payer_claims as (
    select * from {{ ref('int_payer_medical_claims') }}
    where claim_family_id not in (select claim_family_id from {{ ref('int_claim_crosswalk') }})
),
pl as (
    select
        {{ js('payer_claim_id') }}         as payer_claim_id
      , {{ jint('line_number') }}          as line_number
      , {{ js('procedure_code') }}         as hcpcs_code
      , {{ js('procedure_code_system') }}  as code_system
      , {{ js('revenue_code') }}           as revenue_code
      , {{ jnum('units') }}                as units
      , {{ jnum('charge_amount') }}        as line_charge
      , {{ jnum('allowed_amount') }}       as line_allowed
      , {{ jdate('service_date') }}        as line_service_date
      , record_id                          as line_record_id
    from {{ stream('payer_medical_claim_lines') }}
),
pl_claims as (select distinct payer_claim_id from pl),
payer_line_source as (
    select c.claim_family_id,
           coalesce(
             (select payer_claim_id from pl_claims where payer_claim_id = c.final_payer_claim_id),
             (select payer_claim_id from pl_claims where payer_claim_id = c.claim_family_id),
             (select payer_claim_id from pl_claims where payer_claim_id = regexp_replace(c.claim_family_id, '-X$', ''))
           ) as line_claim_id
    from payer_claims c
),
payer_lines as (
    select
        'payer_feed' as data_source, c.claim_family_id as claim_id
      , coalesce(l.line_number, 1) as source_line_number
      , l.hcpcs_code, l.code_system, l.revenue_code, l.units
      , coalesce(l.line_charge, c.charge_amount) as line_charge
      , l.line_allowed
      , coalesce(l.line_service_date, c.service_from) as line_start_date
      , coalesce(l.line_service_date, c.service_to)   as line_end_date
      , l.line_record_id
      , case when ls.line_claim_id is null then 'header_only'
             when ls.line_claim_id = c.final_payer_claim_id then 'own'
             else 'borrowed:' || ls.line_claim_id end as line_origin
      , c.net_paid_amount as header_paid, c.net_allowed_amount as header_allowed
    from payer_claims c
    join payer_line_source ls on ls.claim_family_id = c.claim_family_id
    left join pl l on l.payer_claim_id = ls.line_claim_id
),
prl as (
    select
        {{ js('claim_id') }}               as claim_id
      , {{ js('submission_record_id') }}   as submission_record_id
      , {{ jint('line_number') }}          as line_number
      , {{ js('service_code') }}           as hcpcs_code
      , {{ js('service_code_system') }}    as code_system
      , {{ js('revenue_code') }}           as revenue_code
      , {{ jnum('units') }}                as units
      , {{ jint('charge_cents') }} / 100.0 as line_charge
      , {{ jdate('service_date') }}        as line_service_date
      , {{ js('local_charge_code') }}      as local_charge_code
      , record_id                          as line_record_id
      , arrival_date
    from {{ stream('medical_claim_lines') }}
),
prov_sub_pick as (
    select c.claim_id,
           coalesce(
             (select max(submission_record_id) from prl where prl.claim_id = c.claim_id and prl.submission_record_id = c.submission_record_id),
             (select arg_max(submission_record_id, arrival_date) from prl where prl.claim_id = c.claim_id)
           ) as line_submission_record_id
    from {{ ref('int_provider_medical_claims') }} c
),
provider_lines as (
    select
        'provider_billing' as data_source, c.claim_id
      , coalesce(l.line_number, 1) as source_line_number
      , coalesce(l.hcpcs_code, case when c.standalone_service_code_system in ('CPT', 'HCPCS') then c.standalone_service_code end) as hcpcs_code
      , coalesce(l.code_system, c.standalone_service_code_system) as code_system
      , coalesce(l.revenue_code, c.standalone_revenue_code) as revenue_code
      , l.units
      , coalesce(l.line_charge, c.charge_amount) as line_charge
      , cast(null as double) as line_allowed
      , coalesce(l.line_service_date, c.service_date) as line_start_date
      , coalesce(l.line_service_date, c.service_date) as line_end_date
      , l.line_record_id
      , case when l.claim_id is null then 'header_only' else 'own' end as line_origin
      , c.net_paid_amount as header_paid, c.net_allowed_amount as header_allowed
    from {{ ref('int_provider_medical_claims') }} c
    join prov_sub_pick ps on ps.claim_id = c.claim_id
    left join prl l on l.claim_id = c.claim_id and l.submission_record_id = ps.line_submission_record_id
    where c.final_status <> 'submitted_not_adjudicated'
),
all_lines as (
    select * from payer_lines
    union all
    select * from provider_lines
),
weighted as (
    select *
      , row_number() over (partition by data_source, claim_id order by source_line_number, line_record_id) as claim_line_number
      , count(*) over (partition by data_source, claim_id) as n_lines
      , sum(coalesce(line_allowed, 0)) over (partition by data_source, claim_id) as sum_allowed
      , sum(coalesce(line_charge, 0)) over (partition by data_source, claim_id) as sum_charge
    from all_lines
),
alloc as (
    select *
      , case when sum_allowed > 0 then coalesce(line_allowed, 0) / sum_allowed
             when sum_charge > 0 then coalesce(line_charge, 0) / sum_charge
             else 1.0 / n_lines end as w
    from weighted
),
rounded as (
    select *
      , round(header_paid * w, 2)    as paid_r
      , round(header_allowed * w, 2) as allowed_r
    from alloc
)
select
    data_source, claim_id, claim_line_number, source_line_number
  , hcpcs_code, code_system, revenue_code, units, line_charge
  , line_start_date, line_end_date, line_record_id, line_origin
  , case when claim_line_number = n_lines
         then round(header_paid - (sum(paid_r) over (partition by data_source, claim_id) - paid_r), 2)
         else paid_r end as line_paid_amount
  , case when claim_line_number = n_lines
         then round(header_allowed - (sum(allowed_r) over (partition by data_source, claim_id) - allowed_r), 2)
         else allowed_r end as line_allowed_amount
from rounded
