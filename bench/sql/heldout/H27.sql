SELECT AVG(julianday(arrival)-julianday(event)) AS value FROM records
 WHERE stream='imaging_reports' AND date(event)=event AND date(arrival)=arrival;
