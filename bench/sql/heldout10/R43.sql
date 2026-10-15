WITH RECURSIVE months(m) AS (
  SELECT '2029-01-01' UNION ALL SELECT date(m, '+1 month') FROM months WHERE m < '2029-12-01'
), spans AS (
  SELECT patient, json_extract(payload,'$.coverage_start') AS s, COALESCE(json_extract(payload,'$.coverage_end'), '9999-12-31') AS e
  FROM (SELECT * FROM records WHERE stream='enrollment_spans' AND id NOT IN (SELECT supersedes FROM records WHERE stream='enrollment_spans' AND supersedes IS NOT NULL)) WHERE json_extract(payload,'$.product') = 'medicare_advantage'
), mm AS (
  SELECT COUNT(*) AS n FROM (SELECT DISTINCT spans.patient, months.m FROM spans JOIN months ON spans.s <= months.m AND spans.e >= months.m)
), paid AS (
  SELECT SUM(json_extract(payload,'$.paid_amount')) AS amt FROM (SELECT * FROM records WHERE stream='payer_medical_claims' AND id NOT IN (SELECT supersedes FROM records WHERE stream='payer_medical_claims' AND supersedes IS NOT NULL))
  WHERE json_extract(payload,'$.product') = 'medicare_advantage' AND json_extract(payload,'$.claim_status') = 'paid'
    AND json_extract(payload,'$.service_from') BETWEEN '2029-01-01' AND '2029-12-31'
)
SELECT ROUND(paid.amt / mm.n, 2) AS value FROM paid, mm
