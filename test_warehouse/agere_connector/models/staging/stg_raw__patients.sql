select
    p.id                                         as patient_id
  , p.payload::json ->> 'display_name'           as display_name
  , try_cast(p.payload::json ->> 'birth_date' as date) as birth_date
  , p.payload::json ->> 'sex_reported'           as sex_reported
  , p.payload::json ->> 'race'                   as race
  , p.payload::json ->> 'ethnicity'              as ethnicity
  , p.payload::json ->> 'preferred_language'     as preferred_language
  , p.payload::json ->> 'marital_status'         as marital_status
  , p.payload::json ->> 'address_line'           as address_line
  , p.payload::json ->> 'city'                   as city
  , p.payload::json ->> 'state'                  as state
  , p.payload::json ->> 'postal_code'            as postal_code
  , p.payload::json ->> 'county_fips'            as county_fips
  , p.payload::json ->> 'phone'                  as phone
  , p.payload::json ->> 'data_segment'           as data_segment
from {{ source('raw_warehouse', 'patients') }} as p
where p.release = '{{ var("raw_release") }}'
