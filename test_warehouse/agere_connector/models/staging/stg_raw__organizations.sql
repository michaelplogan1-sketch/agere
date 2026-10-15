select
    e.id                                         as organization_id
  , e.payload::json ->> 'organization_type'      as organization_type
  , e.payload::json ->> 'display_name'           as display_name
  , nullif(e.payload::json ->> 'npi', '')        as npi
  , e.payload::json ->> 'taxonomy_code'          as taxonomy_code
  , e.payload::json ->> 'tin'                    as tin
  , e.payload::json ->> 'address_line'           as address_line
  , e.payload::json ->> 'city'                   as city
  , e.payload::json ->> 'state'                  as state
  , e.payload::json ->> 'postal_code'            as postal_code
  , case when e.id like 'WH-PRACTICE-%' then 1 else 0 end as owned_practice_flag
from {{ source('raw_warehouse', 'source_entities') }} as e
where e.release = '{{ var("raw_release") }}' and e.kind = 'organizations'
