-- Tuva practitioner: the clinician roster (raw_warehouse.source_entities).
select
    c.clinician_id                                         as practitioner_id
  , c.npi
  , case when c.display_name like 'Dr. %' then split_part(substr(c.display_name, 5), ' ', 1)
         else split_part(c.display_name, ' ', 1) end      as first_name
  , split_part(c.display_name, ' ', -1)                   as last_name
  , o.display_name                                        as practice_affiliation
  , c.specialty
  , 'provider_roster'                                     as data_source
  , c.organization_id                                     as x_organization_id
  , c.taxonomy_code                                       as x_taxonomy_code
from {{ ref('stg_raw__clinicians') }} c
left join {{ ref('stg_raw__organizations') }} o on o.organization_id = c.organization_id
