-- Data quality: roster patients (patients view) with no race recorded (race null or empty string). Distinct patients.
SELECT COUNT(*) AS count FROM patients WHERE coalesce(json_extract(payload,'$.race'),'')='';
