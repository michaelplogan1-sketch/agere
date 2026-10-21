"""Governed clinical definitions chosen by the clinical owner, exposed to analyses as macros.

Each definition is versioned, carries its decision basis and a disclosure sentence that is attached to every
answer using it. Definitions select evidence; they do not establish clinical appropriateness.
"""

DECIDED_BY = 'Mike (clinical owner), 2026-10-21'


def _hfref_sql(tables):
    """Coded HFrEF (EHR or counted claim), or HF (EHR or claim) with a documented EF of 40% or less in notes."""
    from .concepts import condition_evidence_sql
    coded = condition_evidence_sql(['heart_failure_reduced_ef'], tables)
    hf = condition_evidence_sql(['heart_failure', 'heart_failure_reduced_ef'], tables)
    return ("(SELECT patient_id, MAX(coded) AS coded_hfref, MAX(note_ef) AS note_ef_le_40, "
            "MIN(lowest_ef) AS lowest_documented_ef, MAX(ef_date) AS latest_low_ef_date, "
            "CASE WHEN MAX(coded) = 1 AND MAX(note_ef) = 1 THEN 'coded_and_note_ef' WHEN MAX(coded) = 1 THEN 'coded_hfref' "
            "ELSE 'note_ef_le_40' END AS hfref_basis FROM ("
            "SELECT patient_id, 1 AS coded, 0 AS note_ef, NULL AS lowest_ef, NULL AS ef_date FROM " + coded + " "
            "UNION ALL SELECT f.patient_id, 0, 1, CAST(f.value AS REAL), f.event_date FROM note_fact f "
            "WHERE f.fact_type = 'ejection_fraction' AND COALESCE(f.negated, 0) = 0 AND COALESCE(f.experiencer, 'patient') = 'patient' "
            "AND typeof(f.value) IN ('integer', 'real') AND f.value <= 40 "
            "AND f.patient_id IN (SELECT patient_id FROM " + hf + ")) GROUP BY patient_id)")

PHENOTYPES = {
    'hfref': {
        'version': 1,
        'label': 'Heart failure with reduced ejection fraction',
        'decision': DECIDED_BY,
        'disclosure': ('Reduced EF = a coded HFrEF diagnosis (EHR or counted payer claim), or a heart failure diagnosis with a '
                       'documented ejection fraction of 40% or less in notes (non-negated, about the patient, any date).'),
        # Returns patient_id plus the basis for each qualifying patient.
        'build': lambda tables: _hfref_sql(tables),
    },
}

CURRENT_GDMT = {
    'version': 1,
    'decision': 'Mike (clinical owner), 2026-10-20',
    'disclosure': ('Current GDMT pillar = a picked-up fill of that pillar within the 12 months before the snapshot date; '
                   'no picked-up fill in that window means not currently on that pillar (reported as 0, not unknown).'),
}

TOLERANCE = {
    'version': 1,
    'decision': DECIDED_BY,
    'disclosure': ('Maximum tolerated dose = below the guideline target with a documented intolerance, therapy barrier '
                   '(any type, affirmed or historical) or dose reduction for that drug or its class.'),
    # One row per documented tolerance signal, with its source record.
    'sql': """(SELECT patient_id, drug_key, drug_class, gdmt_pillar, 'barrier:' || barrier_type AS evidence_type,
   assertion, event_date, record_id FROM therapy_barrier WHERE COALESCE(negated, 0) = 0
 UNION ALL
 SELECT patient_id, drug_key, drug_class, NULL, 'dose_decrease', 'affirmed', event_date, record_id
   FROM note_therapy_change WHERE action = 'dose_decrease')""",
}


def describe():
    """Prompt-facing summary of the governed clinical definitions."""
    return {'phenotypes': {k: {'version': v['version'], 'label': v['label'], 'definition': v['disclosure'],
                               'decision': v['decision'], 'macro': '{{phenotype:' + k + '}}',
                               'columns': ['patient_id', 'coded_hfref', 'note_ef_le_40', 'lowest_documented_ef',
                                           'latest_low_ef_date', 'hfref_basis']}
                           for k, v in PHENOTYPES.items()},
            'current_gdmt': {'version': CURRENT_GDMT['version'], 'definition': CURRENT_GDMT['disclosure'],
                             'decision': CURRENT_GDMT['decision'],
                             'evidence': 'fill / {{drug_fill:...}} picked-up fills by the catalog GDMT pillar fill classes'},
            'precedence': ('For the terms these definitions cover, they override general observation-vs-state caution: '
                           'apply them, report definite values (0/1, at/below target, maximum tolerated) and disclose them.'),
            'maximum_tolerated_dose': {'version': TOLERANCE['version'], 'definition': TOLERANCE['disclosure'],
                                       'decision': TOLERANCE['decision'], 'macro': '{{tolerance_evidence}}',
                                       'columns': ['patient_id', 'drug_key', 'drug_class', 'gdmt_pillar', 'evidence_type',
                                                   'assertion', 'event_date', 'record_id']}}


# Owner decisions that retire saved interpretations built on the earlier reading.
MEDICAL_CLAIMS = {
    'version': 1, 'decision': DECIDED_BY,
    'disclosure': ('Medical-claim finance questions (denial rate, paid, PMPM) use payer-adjudicated claims '
                   '(payer_claim, counted rows only); provider billing claims are used only when asked for.'),
}
_PROVIDER_CLAIM = __import__('re').compile(r'(?:FROM|JOIN)\s+\\?"?claim\\?"?(?=[\s)]|$)|"table":\s*"claim"', __import__('re').I)


def superseded(question, plan, tables):
    """True when a saved plan reads the provider claim table for a question that did not ask for provider claims,
    in a warehouse that has counted payer claims."""
    import json, re
    if 'counts_in_metrics' not in tables.get('payer_claim', []): return False
    if re.search(r'\bprovider\b', str(question), re.I): return False
    return bool(_PROVIDER_CLAIM.search(json.dumps(plan, default=str)))


CARE_GAP_LIVING = {
    'version': 1, 'decision': DECIDED_BY,
    'disclosure': ('Care gaps (did not get, had no, missing) count living patients only; patients with a death record '
                   'are excluded unless the question names living or deceased patients.'),
}


AGE = {
    'version': 1, 'decision': DECIDED_BY,
    'disclosure': ('Age is calculated from birth_date: at the warehouse as-of date for living patients, and at the '
                   'date of death for patients with a death record. Missing or invalid birth dates remain unknown.'),
}
