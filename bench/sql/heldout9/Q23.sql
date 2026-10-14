-- Patients with a screening mammogram resulted in 2029: distinct patients with cancer_screening_results test 'bilateral_screening_mammography', result_status 'final', performed_date 2029-01-01..2029-12-31 (orders and self-reported screening history excluded).
SELECT COUNT(DISTINCT patient) AS count FROM records WHERE stream='cancer_screening_results' AND json_extract(payload,'$.test')='bilateral_screening_mammography'
  AND json_extract(payload,'$.result_status')='final' AND json_extract(payload,'$.performed_date') BETWEEN '2029-01-01' AND '2029-12-31';
