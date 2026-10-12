SELECT COUNT(*) AS count FROM records WHERE stream='medical_claims'
 AND json_extract(payload,'$.service_date') BETWEEN '2028-01-01' AND '2028-12-31'
 AND date(json_extract(payload,'$.service_date'))=json_extract(payload,'$.service_date')
 AND julianday(arrival)-julianday(json_extract(payload,'$.service_date'))>60;
