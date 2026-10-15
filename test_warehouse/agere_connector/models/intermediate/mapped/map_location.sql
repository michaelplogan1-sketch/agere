-- Tuva location: the organization roster (practices, hospitals, labs, pharmacies, payers).
select
    o.organization_id                                     as location_id
  , o.npi
  , o.display_name                                        as name
  , o.organization_type                                   as facility_type
  , o.address_line                                        as address
  , o.city
  , o.state
  , o.postal_code                                         as zip_code
  , 'provider_roster'                                     as data_source
  , o.tin                                                 as x_tin
  , o.taxonomy_code                                       as x_taxonomy_code
  , o.owned_practice_flag                                 as x_owned_practice
from {{ ref('stg_raw__organizations') }} o
