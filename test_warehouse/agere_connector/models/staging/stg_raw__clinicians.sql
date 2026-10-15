select
    e.id                                         as clinician_id
  , e.payload::json ->> 'organization_id'        as organization_id
  , e.payload::json ->> 'display_name'           as display_name
  , e.payload::json ->> 'specialty'              as specialty
  , nullif(e.payload::json ->> 'npi', '')        as npi
  , e.payload::json ->> 'taxonomy_code'          as taxonomy_code
from {{ source('raw_warehouse', 'source_entities') }} as e
where e.release = '{{ var("raw_release") }}' and e.kind = 'clinicians'
