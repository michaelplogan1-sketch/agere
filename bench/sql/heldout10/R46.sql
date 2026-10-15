SELECT ROUND(AVG(CAST(json_extract(payload,'$.svi_overall_percentile') AS REAL)), 2) AS value FROM records
WHERE stream = 'sdoh_vendor_attributes'
  AND json_extract(payload,'$.delivery_date') = (SELECT MAX(json_extract(payload,'$.delivery_date')) FROM records WHERE stream = 'sdoh_vendor_attributes')
  AND json_extract(payload,'$.member_match_status') = 'exact'
  AND json_extract(payload,'$.svi_overall_percentile') IS NOT NULL
