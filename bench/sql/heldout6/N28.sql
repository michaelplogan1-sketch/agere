-- Net PR adjustments by reason code (1 deductible, 2 coinsurance, 3 copay) over all adjudications (reversals negative) for 2028 service dates.
WITH r AS MATERIALIZED (SELECT payload FROM records WHERE stream='medical_claims'),
a AS (SELECT json_extract(j.value,'$.reason_code') rc, json_extract(j.value,'$.amount_cents') c FROM r, json_each(json_extract(r.payload,'$.adjustments')) j
      WHERE json_extract(r.payload,'$.service_date') BETWEEN '2028-01-01' AND '2028-12-31' AND json_extract(j.value,'$.group')='PR')
SELECT CASE rc WHEN '1' THEN 'deductible' WHEN '2' THEN 'coinsurance' WHEN '3' THEN 'copay' END AS component, round(sum(c)/100.0,2) AS dollars
FROM a WHERE rc IN ('1','2','3') GROUP BY 1 ORDER BY 1;
