SELECT COUNT(DISTINCT patient) AS count FROM records WHERE stream='reported_medication_entries' AND (json_extract(payload,'$.use_statement')='reports_not_taking');
