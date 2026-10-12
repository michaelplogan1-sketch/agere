SELECT COUNT(*) AS count FROM records WHERE stream='patient_payments' AND (json_extract(payload,'$.status')='received' AND date(event)=event AND substr(arrival,1,4)>substr(event,1,4));
