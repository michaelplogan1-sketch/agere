-- Fails (returns rows) when a stream present in raw_warehouse.records is not
-- accounted for in seeds/stream_map.csv, when a map row has an invalid status,
-- or when an unused stream has no reason.
select s.stream, 'stream missing from stream_map' as problem
from {{ ref('stg_raw__stream_inventory') }} s
left join {{ ref('stream_map') }} m on m.stream = s.stream
where m.stream is null
union all
select stream, 'invalid status ' || coalesce(status, 'null')
from {{ ref('stream_map') }}
where status not in ('mapped', 'unused')
union all
select stream, 'unused without a reason'
from {{ ref('stream_map') }}
where status = 'unused' and coalesce(trim(reason), '') = ''
union all
select stream, 'mapped without an input table'
from {{ ref('stream_map') }}
where status = 'mapped' and coalesce(trim(input_tables), '') = ''
