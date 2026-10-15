SELECT patient AS patient_id FROM records
WHERE stream = 'appointments' AND json_extract(payload,'$.status') = 'no_show'
GROUP BY patient HAVING COUNT(DISTINCT json_extract(payload,'$.appointment_id')) >= 5
