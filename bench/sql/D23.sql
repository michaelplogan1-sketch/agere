SELECT COUNT(*) AS count FROM records WHERE stream='appointments' AND (json_extract(payload,'$.status')='fulfilled' AND json_extract(payload,'$.completed_encounter_id') IS NULL);
