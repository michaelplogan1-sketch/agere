SELECT DISTINCT patient AS patient_id FROM records WHERE stream='adt' AND json_extract(payload,'$.event')='discharge' ORDER BY patient;
