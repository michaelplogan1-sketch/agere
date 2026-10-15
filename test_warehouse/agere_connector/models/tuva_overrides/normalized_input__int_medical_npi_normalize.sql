{{ config(
     enabled = the_tuva_project.tuva_boolean_var('claims_enabled', false),
     schema = 'claims_preprocessing',
     alias = '_int_normalized_input_medical_npi_normalize',
     materialized = 'table',
     tags = ['normalized_input', 'claims_preprocessing']
   )
}}
{#- Override of the_tuva_project/models/normalized_layer/intermediate/
    normalized_input__int_medical_npi_normalize.sql (Tuva Core v1.0.0); the
    package model is disabled in dbt_project.yml. Only the facility join
    predicate differs. -#}

with base as (
select distinct
    med.claim_id
  , med.claim_line_number
  , med.claim_type
  , med.data_source
  , rend_prov.npi as normalized_rendering_npi
  , case
      when rend_prov.entity_type_code = '1' then
        cast({{ the_tuva_project.concat_custom(["rend_prov.provider_last_name", "', '", "rend_prov.provider_first_name"]) }} as {{ dbt.type_string() }})
      else
        cast(rend_prov.provider_organization_name as {{ dbt.type_string() }})
    end as normalized_rendering_name
  , bill_prov.npi as normalized_billing_npi
  , case
      when bill_prov.entity_type_code = '1' then
        cast({{ the_tuva_project.concat_custom(["bill_prov.provider_last_name", "', '", "bill_prov.provider_first_name"]) }} as {{ dbt.type_string() }})
      else
        cast(bill_prov.provider_organization_name as {{ dbt.type_string() }})
    end as normalized_billing_name
  , fac_prov.npi as normalized_facility_npi
  , case
      when fac_prov.entity_type_code = '1' then
        cast({{ the_tuva_project.concat_custom(["fac_prov.provider_last_name", "', '", "fac_prov.provider_first_name"]) }} as {{ dbt.type_string() }})
      else
        cast(fac_prov.provider_organization_name as {{ dbt.type_string() }})
    end as normalized_facility_name
  , cast('{{ var('tuva_last_run') }}' as {{ dbt.type_timestamp() }}) as tuva_last_run
from {{ ref('normalized_input__stg_medical_claim') }} as med
left outer join {{ ref('provider_data__provider') }} as rend_prov
  on med.rendering_npi = rend_prov.npi
left outer join {{ ref('provider_data__provider') }} as bill_prov
  on med.billing_npi = bill_prov.npi
left outer join {{ ref('provider_data__provider') }} as fac_prov
  -- connector override: same predicate as Tuva Core v1.0.0, written so DuckDB
  -- 1.5 plans a hash join (a left-side-only predicate in the ON clause made it
  -- fall back to a blockwise nested-loop join over 2M organization NPIs)
  on fac_prov.npi = case when med.claim_type = 'institutional' then med.facility_npi end
  and fac_prov.entity_type_description = 'Organization'
)

, facility_npi_ranked as (
select
    data_source
  , claim_id
  , normalized_facility_npi
  , normalized_facility_name
  , row_number() over (partition by data_source, claim_id order by claim_line_number) as facility_npi_rank
from base
where normalized_facility_npi is not null
)

select 
    base.claim_id
  , base.claim_line_number
  , base.claim_type
  , base.data_source
  , base.normalized_rendering_npi
  , base.normalized_rendering_name
  , base.normalized_billing_npi
  , base.normalized_billing_name
  , fac.normalized_facility_npi
  , fac.normalized_facility_name
  , base.tuva_last_run
from base
left join facility_npi_ranked as fac
  on base.claim_id = fac.claim_id
  and base.data_source = fac.data_source
  and facility_npi_rank = 1