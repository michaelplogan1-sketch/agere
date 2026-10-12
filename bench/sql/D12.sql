SELECT COUNT(DISTINCT patient) AS count FROM records WHERE stream='adt' AND (json_extract(payload,'$.event')='admission');
