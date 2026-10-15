-- Reading B: distinct patients with any encounters row (any type/version) dated 2029-05-28..2030-05-27.
SELECT COUNT(DISTINCT patient) AS count FROM records WHERE stream='encounters' AND json_extract(payload,'$.event_date') BETWEEN '2029-05-28' AND '2030-05-27';
