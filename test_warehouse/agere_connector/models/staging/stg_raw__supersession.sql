{{ config(materialized='table') }}
-- Version links among mapped records. The indexed envelope column `supersedes`
-- mirrors payload.supersedes_record_id, but other correction pointers live only
-- in the payload (labs replaces_result_record_id, medication orders
-- replaces_order_record_id, demographic corrects_age_record_id), so all are unioned.
with links as (
    select record_id as child_record_id, stream, supersedes_record_id as parent_record_id, 'supersedes' as link_type
    from {{ ref('stg_raw__records') }} where supersedes_record_id is not null
    union
    select record_id, stream, j ->> 'supersedes_record_id', 'supersedes'
    from {{ ref('stg_raw__records') }} where (j ->> 'supersedes_record_id') is not null
    union
    select record_id, stream, j ->> 'replaces_result_record_id', 'replaces_result'
    from {{ ref('stg_raw__records') }} where (j ->> 'replaces_result_record_id') is not null
    union
    select record_id, stream, j ->> 'replaces_order_record_id', 'replaces_order'
    from {{ ref('stg_raw__records') }} where (j ->> 'replaces_order_record_id') is not null
    union
    select record_id, stream, j ->> 'corrects_age_record_id', 'corrects_age'
    from {{ ref('stg_raw__records') }} where (j ->> 'corrects_age_record_id') is not null
)
select * from links
