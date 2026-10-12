# Synthetic document integration fixture

These two fictional records exercise the source interface, not a clinical benchmark or model-training dataset. The imported text is a literal engineering fixture; no OCR model produced it. Do not report this fixture as OCR accuracy.

`documents-pending.json` records an unread outside document. `documents-ready.json` is a later extraction revision of the same document. Run both in the same namespace/database to preserve the earlier state. A snapshot before September 20 continues to see the pending document.

The source envelope's available_date is the date this specific revision/extraction became usable. It must not be backdated to the original document date. Patient IDs are exact supplied links. Document identity hashes here are fixture placeholders, not checksums of an attached real scan.
