SELECT json_extract(payload,'$.payment_channel') AS payment_channel,SUM(json_extract(payload,'$.amount_cents')) AS total_cents
 FROM records WHERE stream='patient_payments' AND json_extract(payload,'$.status')='received'
 AND json_extract(payload,'$.currency')='USD' AND json_type(payload,'$.amount_cents')='integer'
 AND event BETWEEN '2029-01-01' AND :as_of AND json_extract(payload,'$.payment_channel') IS NOT NULL
 GROUP BY 1 ORDER BY 1;
