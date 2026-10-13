-- With a 2028-12-31 cutoff: medical_claims records with service_date in 2028 that had arrived by the cutoff.
SELECT COUNT(*) AS count FROM records WHERE stream='medical_claims'
  AND json_extract(payload,'$.service_date') BETWEEN '2028-01-01' AND '2028-12-31';
