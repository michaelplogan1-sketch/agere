SELECT COUNT(*) AS prerequisite_available FROM records
WHERE lower(stream) LIKE '%genom%' OR lower(stream) LIKE '%genetic%' OR lower(stream) LIKE '%genotyp%'
   OR (stream IN ('labs','notes','imported_documents','hie_documents','scanned_documents','conditions','received_specialty_records','pathology_reports','diagnostic_test_reports','received_diagnostic_reports','medication_review_decisions')
       AND (lower(payload) LIKE '%cyp2c19%' OR lower(payload) LIKE '%genotyp%' OR lower(payload) LIKE '%pharmacogen%' OR lower(payload) LIKE '%metabolizer%'
            OR lower(payload) LIKE '%germline%' OR lower(payload) LIKE '%hla-b%'))
