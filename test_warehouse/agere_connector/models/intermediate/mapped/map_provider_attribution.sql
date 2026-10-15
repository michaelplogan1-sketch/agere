-- Payer attribution: enrollment_spans.attribution_practice_id expanded to one
-- row per covered month (Tuva provider_attribution grain person/member/month).
-- Open spans run to the release as-of date.
with spans as (
    select * from {{ ref('map_eligibility') }} where x_attribution_practice_id is not null
),
months as (
    select s.*, unnest(generate_series(date_trunc('month', s.enrollment_start_date),
                                       date_trunc('month', coalesce(s.enrollment_end_date, cast('{{ var("tuva_last_run") }}' as date))),
                                       interval 1 month)) as month_start
    from spans s
    where s.enrollment_start_date <= coalesce(s.enrollment_end_date, cast('{{ var("tuva_last_run") }}' as date))
)
select
    person_id
  , member_id
  , strftime(month_start, '%Y%m')    as year_month
  , payer
  , plan
  , cast(null as varchar)            as payer_attributed_provider
  , x_attribution_practice_id        as payer_attributed_provider_practice
  , x_attribution_practice_id        as payer_attributed_provider_organization
  , plan                             as payer_attributed_provider_lob
  , 'payer_feed'                     as data_source
from months
qualify row_number() over (partition by person_id, member_id, payer, plan, month_start order by enrollment_start_date desc) = 1
