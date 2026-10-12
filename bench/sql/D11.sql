SELECT COUNT(*) AS count FROM records WHERE stream='adt' AND (json_extract(payload,'$.event')='admission');
