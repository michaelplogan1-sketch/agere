-- Tuva eligibility from payer enrollment spans. data_source 'payer_feed' is the
-- same label the payer medical and pharmacy claims carry, so Tuva's member
-- months and claims-enrollment flags join claims to coverage. plan = product
-- (the line of business both enrollment and claims carry); the payer's plan_id
-- is kept as x_plan_id.
select
    m.person_id
  , e.member_id
  , e.member_id                                     as subscriber_id
  , e.coverage_start                                as enrollment_start_date
  , e.coverage_end                                  as enrollment_end_date
  , e.payer_id                                      as payer
  , pm.payer_type
  , e.product                                       as plan
  , pt.first_name
  , pt.last_name
  , pt.sex
  , pt.race
  , pt.ethnicity
  , pt.birth_date
  , pt.death_date
  , pt.death_flag
  , pt.address
  , pt.city
  , pt.state
  , pt.zip_code
  , pt.phone
  -- Medicare entitlement (only meaningful on Medicare lines of business):
  -- OREC 0 = old age, 1 = disability; MSC 10 = aged, 20 = disabled.
  , case when pm.medicare_flag = 1 then case e.medicare_entitlement_reason when 'age' then '0' when 'disability' then '1' end end as original_reason_entitlement_code
  , case when pm.medicare_flag = 1 then case e.medicare_entitlement_reason when 'age' then '10' when 'disability' then '20' end end as medicare_status_code
  -- CMS dual status: full-benefit duals -> 08 (other full dual), partial -> 06; non-duals on Medicare -> 00.
  , case when e.dual_status = 'full_benefit' then '08'
         when e.dual_status = 'partial_benefit' then '06'
         when pm.medicare_flag = 1 then '00' end    as dual_status_code
  , case when e.product = 'medicaid' or e.dual_status is not null then 1 else 0 end as medicaid_indicator
  , 'payer_feed'                                    as data_source
  , e.source_system                                 as x_source_system
  , e.plan_id                                       as x_plan_id
  , e.coverage_order                                as x_coverage_order
  , e.enrollment_reason                             as x_enrollment_reason
  , e.termination_reason                            as x_termination_reason
  , e.attribution_practice_id                       as x_attribution_practice_id
  , e.record_id                                     as x_source_record_id
  , e.patient_id                                    as x_patient_id
from {{ ref('int_enrollment_spans') }} e
join {{ ref('int_person_match') }} m on m.patient_id = e.patient_id
left join {{ ref('map_patient') }} pt on pt.patient_id = e.patient_id
left join {{ ref('payer_product_map') }} pm on pm.product = e.product
