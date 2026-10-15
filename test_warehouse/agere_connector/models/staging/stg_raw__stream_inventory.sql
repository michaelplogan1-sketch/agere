-- Every stream present in the raw warehouse with its row count. Feeds the
-- stream_map coverage test (tests/assert_every_stream_is_mapped.sql).
select stream, count(*) as record_count, count(distinct patient) as patient_count
from {{ source('raw_warehouse', 'records') }}
where release = '{{ var("raw_release") }}'
group by stream
