{{ config(materialized='table') }}
-- One scan of the native warehouse: every record of every stream the connector maps
-- (seeds/stream_map.csv status = mapped), with the JSON payload parsed once.
-- Ordered by stream so DuckDB zone maps prune per-stream reads downstream.
select
    r.id                          as record_id
  , r.patient                     as patient_id
  , r.stream
  , try_cast(r.event as date)     as event_date
  , try_cast(r.arrival as date)   as arrival_date
  , nullif(r.supersedes, '')      as supersedes_record_id
  , r.payload::json               as j
from {{ source('raw_warehouse', 'records') }} as r
where r.release = '{{ var("raw_release") }}'
  and r.stream in (select stream from {{ ref('stream_map') }} where status = 'mapped')
order by r.stream, r.patient
