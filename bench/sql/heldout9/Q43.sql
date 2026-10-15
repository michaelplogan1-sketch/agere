-- Data quality: corrected lab results dated in 2029: labs rows with result_status 'corrected' (each replaces an earlier result via replaces_result_record_id) and event_date 2029-01-01..2029-12-31.
SELECT COUNT(*) AS count FROM records WHERE stream='labs' AND json_extract(payload,'$.result_status')='corrected' AND json_extract(payload,'$.event_date') BETWEEN '2029-01-01' AND '2029-12-31';
