-- % of diabetics (roster age 40-75) with any statin record across medication streams (nystatin excluded).
WITH dmc AS MATERIALIZED (SELECT patient, lower(coalesce(json_extract(payload,'$.description'),'')) d, coalesce(json_extract(payload,'$.icd10'),'') i FROM records WHERE stream='conditions'),
dm AS (SELECT DISTINCT patient FROM dmc WHERE ((d LIKE '%diabet%' OR d LIKE 't1dm%' OR d LIKE 't2dm%' OR i LIKE 'E10%' OR i LIKE 'E11%')
  AND d NOT LIKE '%prediabet%' AND d NOT LIKE '%family history%' AND d NOT LIKE 'gdm%' AND i NOT LIKE 'O24%' AND i NOT LIKE 'Z83%')),
den AS (SELECT dm.patient FROM dm JOIN patients p ON p.id=dm.patient WHERE json_extract(p.payload,'$.age_reported') BETWEEN 40 AND 75),
m AS (SELECT patient, lower(coalesce(json_extract(payload,'$.medication_name'), json_extract(payload,'$.name'),'')) n FROM records
      WHERE stream IN ('medication_lists','medication_orders','pharmacy_dispenses','pharmacy_claims','pharmacy_history') AND patient IN (SELECT patient FROM den))
SELECT round(100.0*(SELECT count(DISTINCT patient) FROM m WHERE (n LIKE '%statin%' AND n NOT LIKE '%nystatin%'))/(SELECT count(*) FROM den),1) AS value;
