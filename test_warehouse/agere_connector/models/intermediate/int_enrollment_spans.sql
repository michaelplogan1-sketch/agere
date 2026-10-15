-- Payer enrollment spans, current versions only. A span that another span
-- supersedes (retroactive termination, member-id reissue, corrections) is
-- dropped; the 9999-12-31 open-end placeholder becomes null (open coverage).
with s as (
    select * from {{ stream('enrollment_spans') }}
),
superseded as (
    select parent_record_id from {{ ref('stg_raw__supersession') }}
    where stream = 'enrollment_spans' and link_type = 'supersedes'
)
select
    s.record_id
  , s.patient_id
  , {{ js('member_id') }}                         as member_id
  , {{ js('payer_id') }}                          as payer_id
  , {{ js('plan_id') }}                           as plan_id
  , {{ js('product') }}                           as product
  , {{ jdate('coverage_start') }}                 as coverage_start
  , case when (j ->> 'coverage_end') like '9999%' then null else {{ jdate('coverage_end') }} end as coverage_end
  , {{ js('coverage_order') }}                    as coverage_order
  , {{ js('dual_status') }}                       as dual_status
  , {{ js('medicare_entitlement_reason') }}       as medicare_entitlement_reason
  , {{ js('enrollment_reason') }}                 as enrollment_reason
  , {{ js('termination_reason') }}                as termination_reason
  , {{ js('attribution_practice_id') }}           as attribution_practice_id
  , {{ js('source_system') }}                     as source_system
  , {{ jint('span_version') }}                    as span_version
  , s.arrival_date
from s
where s.record_id not in (select parent_record_id from superseded)
