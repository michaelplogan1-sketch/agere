"""Live single-patient questions: one patient's current record read from the EMR's FHIR API, answered through the core.

A question about one patient ("what were this patient's creatinine results during the last admission?") should reflect the
record as it is now, not as of the last population export. This module reads that patient's resources with FHIR searches
(fhir_client.search), builds a one-patient semantic layer from them with the same importer the Bulk export uses, and answers
the question through the router on that layer, so the governed definitions, checks and recipes apply unchanged. No model sees
the patient's resources; the SQL writer sees only the schema, as for any question.

    answer(conn, patient_id, question)   -> the router's outcome, plus what was read
"""
import json
import os
import re
import shutil
import tempfile
import time
from pathlib import Path

from . import fhir_client, fhir_import

KINDS = ('Encounter', 'Condition', 'Observation', 'MedicationAdministration', 'Procedure')


def snapshot(conn, patient_id, folder):
    """Write one patient's current FHIR record as an export folder. -> counts by resource type."""
    folder = Path(folder); folder.mkdir(parents=True, exist_ok=True)
    patient = fhir_client.read(conn, 'Patient', patient_id)
    if patient is None: raise LookupError(f'Patient {patient_id} is not in the EMR')
    resources = {'Patient': [patient], 'Location': fhir_client.search(conn, 'Location')}
    for kind in KINDS: resources[kind] = fhir_client.search(conn, kind, patient=patient_id)
    out = []
    for kind, rows in resources.items():
        (folder / f'{kind}.ndjson').write_text(''.join(json.dumps(r) + '\n' for r in rows))
        out.append({'type': kind, 'url': f'{kind}.ndjson', 'count': len(rows)})
    (folder / 'manifest.json').write_text(json.dumps({'transactionTime': time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime()), 'request': f'live read of Patient/{patient_id}',
                                                      'requiresAccessToken': True, 'output': out, 'error': []}) + '\n')
    return {o['type']: o['count'] for o in out}


def answer(conn, patient_id, question, tiers=None):
    """-> the router's outcome for a question about one patient, on that patient's live record, with 'read' (resource counts)."""
    from . import router
    work = Path(tempfile.mkdtemp(prefix='agere-live-'))
    try:
        counts = snapshot(conn, patient_id, work / 'export')
        db = work / 'patient.sqlite3'
        fhir_import.build(work / 'export', db)
        old = os.environ.get('AGERE_TIERED_DB'); os.environ['AGERE_TIERED_DB'] = str(db)
        # The layer holds this one patient; the question is made to say so, so "this patient" or "their" has a referent.
        asked = re.sub(r'(?i)\b(?:this|the) patient(?:\'s)?\b', lambda m: f'patient {patient_id}' + ("'s" if m.group(0).lower().endswith("'s") else ''), question)
        # A question that does not refer to the patient is left as worded: the layer holds only them, and an exact wording keeps
        # the built-in and saved recipes, which answer at no model cost.
        try:
            outcome = router.answer(asked, **({'tiers': tiers} if tiers else {}))
        finally:
            if old is None: os.environ.pop('AGERE_TIERED_DB', None)
            else: os.environ['AGERE_TIERED_DB'] = old
        return {**outcome, 'read': counts, 'patient_id': patient_id, 'asked': asked}
    finally:
        shutil.rmtree(work, ignore_errors=True)
