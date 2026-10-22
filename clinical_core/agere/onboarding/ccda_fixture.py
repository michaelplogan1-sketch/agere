"""Synthetic C-CDA R2.1 documents for the C-CDA ingest tests and the onboarding round trip.

    render(patient)                       patient dict -> a Continuity of Care Document (XML text)
    from_semantic(db, patient_id, ...)    the same dict built from one patient of the semantic layer (problems, medications,
                                          allergy-free, results, vitals), so a delivery of C-CDA files for known patients can
                                          be ingested and compared with the warehouse they came from
    write_delivery(db, out_dir, ids)      one <patient>.xml per patient under out_dir/ccda/<YYYY-MM>/
Every value is generated from the synthetic warehouse; no real person is implied. Entities and DOCTYPEs are never emitted.
"""
from __future__ import annotations

from pathlib import Path
from xml.sax.saxutils import escape, quoteattr

VERSION = 'ccda-fixture/1'
OID = {'loinc': '2.16.840.1.113883.6.1', 'snomed': '2.16.840.1.113883.6.96', 'rxnorm': '2.16.840.1.113883.6.88',
       'icd10cm': '2.16.840.1.113883.6.90', 'cvx': '2.16.840.1.113883.12.292'}


def ts(day):
    return (day or '').replace('-', '')[:8]


def _a(**kw):
    return ' '.join(f'{k}={quoteattr(str(v))}' for k, v in kw.items() if v is not None)


def _code(code, system, display):
    return f'<code {_a(code=code, codeSystem=OID.get(system), displayName=display)}/>' if code else f'<code nullFlavor="UNK"/>'


def _problem(i, p):
    neg = ' negationInd="true"' if p.get('negated') else ''
    return (f'<entry><act classCode="ACT" moodCode="EVN"><templateId root="2.16.840.1.113883.10.20.22.4.3"/><id root="2.16.840.1.113883.19.5" extension="prob-{i}"/>'
            f'<code code="CONC" codeSystem="2.16.840.1.113883.5.6"/><statusCode code="active"/><effectiveTime><low value="{ts(p.get("onset"))}"/></effectiveTime>'
            f'<entryRelationship typeCode="SUBJ"><observation classCode="OBS" moodCode="EVN"{neg}><id root="2.16.840.1.113883.19.5" extension="probobs-{i}"/>'
            f'<code code="55607006" codeSystem="2.16.840.1.113883.6.96" displayName="Problem"/><statusCode code="completed"/>'
            f'<effectiveTime><low value="{ts(p.get("onset"))}"/>{"<high value=" + quoteattr(ts(p["resolved"])) + "/>" if p.get("resolved") else ""}</effectiveTime>'
            f'<value xsi:type="CD" {_a(code=(p.get("snomed") or p.get("icd10")), codeSystem=OID["snomed"] if p.get("snomed") else OID["icd10cm"], displayName=p.get("label"))}>'
            f'{"<translation " + _a(code=p["icd10"], codeSystem=OID["icd10cm"], displayName=p.get("label")) + "/>" if p.get("icd10") and p.get("snomed") else ""}</value>'
            f'</observation></entryRelationship></act></entry>')


def _med(i, m):
    return (f'<entry><substanceAdministration classCode="SBADM" moodCode="EVN"><templateId root="2.16.840.1.113883.10.20.22.4.16"/>'
            f'<id root="2.16.840.1.113883.19.5" extension="med-{i}"/><statusCode code="{m.get("status") or "active"}"/>'
            f'<effectiveTime xsi:type="IVL_TS"><low value="{ts(m.get("start"))}"/>{"<high value=" + quoteattr(ts(m["stop"])) + "/>" if m.get("stop") else ""}</effectiveTime>'
            f'<doseQuantity {_a(value=m.get("dose"), unit=m.get("dose_unit"))}/>'
            f'<consumable><manufacturedProduct><manufacturedMaterial>{_code(m.get("rxnorm"), "rxnorm", m.get("name"))}'
            f'<name>{escape(m.get("name") or "")}</name></manufacturedMaterial></manufacturedProduct></consumable></substanceAdministration></entry>')


def _result(i, r):
    return (f'<entry><organizer classCode="BATTERY" moodCode="EVN"><templateId root="2.16.840.1.113883.10.20.22.4.1"/>'
            f'<id root="2.16.840.1.113883.19.5" extension="org-{i}"/>{_code(r.get("panel_loinc"), "loinc", r.get("panel"))}<statusCode code="completed"/>'
            f'<component><observation classCode="OBS" moodCode="EVN"><templateId root="2.16.840.1.113883.10.20.22.4.2"/>'
            f'<id root="2.16.840.1.113883.19.5" extension="res-{i}"/>{_code(r.get("loinc"), "loinc", r.get("name"))}<statusCode code="completed"/>'
            f'<effectiveTime value="{ts(r.get("date"))}"/><value xsi:type="PQ" {_a(value=r.get("value"), unit=r.get("unit"))}/>'
            f'{"<referenceRange><observationRange><text>" + escape(r["range"]) + "</text></observationRange></referenceRange>" if r.get("range") else ""}'
            f'</observation></component></organizer></entry>')


def _allergy(i, a):
    neg = ' negationInd="true"' if a.get('none') else ''
    who = '' if a.get('none') else (f'<participant typeCode="CSM"><participantRole classCode="MANU"><playingEntity classCode="MMAT">'
                                    f'{_code(a.get("rxnorm"), "rxnorm", a.get("substance"))}</playingEntity></participantRole></participant>')
    return (f'<entry><act classCode="ACT" moodCode="EVN"><templateId root="2.16.840.1.113883.10.20.22.4.30"/><id root="2.16.840.1.113883.19.5" extension="alg-{i}"/>'
            f'<code code="CONC" codeSystem="2.16.840.1.113883.5.6"/><statusCode code="active"/><entryRelationship typeCode="SUBJ">'
            f'<observation classCode="OBS" moodCode="EVN"{neg}><id root="2.16.840.1.113883.19.5" extension="algobs-{i}"/><code code="ASSERTION" codeSystem="2.16.840.1.113883.5.4"/>'
            f'<statusCode code="completed"/><effectiveTime><low value="{ts(a.get("onset"))}"/></effectiveTime>'
            f'<value xsi:type="CD" code="419511003" codeSystem="2.16.840.1.113883.6.96" displayName="Propensity to adverse reaction to drug"/>{who}'
            f'</observation></entryRelationship></act></entry>')


def _section(code, title, narrative, entries, template):
    body = f'<text>{narrative}</text>' if narrative else '<text>None</text>'
    return (f'<component><section><templateId root="{template}"/><code code="{code}" codeSystem="2.16.840.1.113883.6.1" displayName="{escape(title)}"/>'
            f'<title>{escape(title)}</title>{body}{"".join(entries)}</section></component>')


def _table(headers, rows):
    head = ''.join(f'<th>{escape(h)}</th>' for h in headers)
    body = ''.join('<tr>' + ''.join(f'<td>{escape(str(c if c is not None else ""))}</td>' for c in r) + '</tr>' for r in rows)
    return f'<table><thead><tr>{head}</tr></thead><tbody>{body}</tbody></table>'


def render(patient):
    """patient: {id, mrn, name, sex, birth_date, date, title, problems:[{label, icd10, snomed, onset, resolved, negated}],
    medications:[{name, rxnorm, start, stop, dose, dose_unit, status}], allergies:[{substance, rxnorm, onset, none}],
    results:[{name, loinc, value, unit, date, range, panel, panel_loinc}], vitals:[same as results]}"""
    pr, md, al, rs, vt = (patient.get(k) or [] for k in ('problems', 'medications', 'allergies', 'results', 'vitals'))
    name_given, _, name_family = (patient.get('name') or 'Test Patient').partition(' ')
    sections = [
        _section('11450-4', 'Problem List', _table(['Problem', 'ICD-10', 'Onset'], [(p.get('label'), p.get('icd10'), p.get('onset')) for p in pr]),
                 [_problem(i, p) for i, p in enumerate(pr)], '2.16.840.1.113883.10.20.22.2.5.1'),
        _section('10160-0', 'Medications', _table(['Medication', 'Start', 'Status'], [(m.get('name'), m.get('start'), m.get('status')) for m in md]),
                 [_med(i, m) for i, m in enumerate(md)], '2.16.840.1.113883.10.20.22.2.1.1'),
        _section('48765-2', 'Allergies and Adverse Reactions',
                 _table(['Substance', 'Onset'], [(a.get('substance') or 'No known drug allergies', a.get('onset')) for a in al]) if al else 'No known allergies',
                 [_allergy(i, a) for i, a in enumerate(al)], '2.16.840.1.113883.10.20.22.2.6.1'),
        _section('30954-2', 'Results', _table(['Test', 'Value', 'Unit', 'Date'], [(r.get('name'), r.get('value'), r.get('unit'), r.get('date')) for r in rs]),
                 [_result(i, r) for i, r in enumerate(rs)], '2.16.840.1.113883.10.20.22.2.3.1'),
        _section('8716-3', 'Vital Signs', _table(['Vital', 'Value', 'Unit', 'Date'], [(r.get('name'), r.get('value'), r.get('unit'), r.get('date')) for r in vt]),
                 [_result(1000 + i, r) for i, r in enumerate(vt)], '2.16.840.1.113883.10.20.22.2.4.1'),
    ]
    return ('<?xml version="1.0" encoding="UTF-8"?>\n'
            '<ClinicalDocument xmlns="urn:hl7-org:v3" xmlns:xsi="http://www.w3.org/2001/XMLSchema-instance">'
            '<templateId root="2.16.840.1.113883.10.20.22.1.1"/><templateId root="2.16.840.1.113883.10.20.22.1.2"/>'
            f'<id root="2.16.840.1.113883.19.5.99999.1" extension="doc-{escape(str(patient["id"]))}"/>'
            '<code code="34133-9" codeSystem="2.16.840.1.113883.6.1" displayName="Summarization of Episode Note"/>'
            f'<title>{escape(patient.get("title") or "Continuity of Care Document")}</title><effectiveTime value="{ts(patient.get("date"))}"/>'
            '<recordTarget><patientRole>'
            f'<id root="2.16.840.1.113883.19.5" extension={quoteattr(str(patient.get("mrn") or patient["id"]))}/>'
            f'<patient><name><given>{escape(name_given)}</given><family>{escape(name_family or "Patient")}</family></name>'
            f'<administrativeGenderCode code="{"F" if str(patient.get("sex", "")).lower().startswith("f") else "M"}" codeSystem="2.16.840.1.113883.5.1"/>'
            f'<birthTime value="{ts(patient.get("birth_date"))}"/></patient></patientRole></recordTarget>'
            '<author><time value="' + ts(patient.get('date')) + '"/><assignedAuthor><id root="2.16.840.1.113883.4.6" extension="1234567893"/></assignedAuthor></author>'
            '<custodian><assignedCustodian><representedCustodianOrganization><name>Synthetic Health System</name></representedCustodianOrganization></assignedCustodian></custodian>'
            '<component><structuredBody>' + ''.join(sections) + '</structuredBody></component></ClinicalDocument>')


def from_semantic(db, patient_id, as_of=None):
    """One patient of the semantic layer (sqlite3 connection) -> the dict render() takes."""
    pat = db.execute('SELECT patient_id, sex_reported, birth_date FROM patient WHERE patient_id=?', (patient_id,)).fetchone()
    if pat is None: raise KeyError(patient_id)
    problems = [{'label': l, 'icd10': i, 'onset': o} for l, i, o in db.execute(
        'SELECT DISTINCT coalesce(label, concept), icd10, coalesce(onset, event_date) FROM condition WHERE patient_id=? AND is_diagnosis=1 '
        'ORDER BY 3 LIMIT 25', (patient_id,))]
    meds = [{'name': n, 'rxnorm': r, 'start': d, 'status': s} for n, r, d, s in db.execute(
        'SELECT drug_name, rxnorm, event_date, status FROM medication WHERE patient_id=? ORDER BY event_date LIMIT 25', (patient_id,))]
    results = [{'name': n, 'loinc': l, 'value': v, 'unit': u, 'date': d, 'range': rr} for n, l, v, u, d, rr in db.execute(
        "SELECT name, loinc, value, unit, event_date, reference_range FROM measurement WHERE patient_id=? AND value IS NOT NULL "
        "AND concept NOT IN ('systolic_bp','diastolic_bp','weight','heart_rate') ORDER BY event_date LIMIT 25", (patient_id,))]
    vitals = [{'name': n, 'loinc': l, 'value': v, 'unit': u, 'date': d} for n, l, v, u, d in db.execute(
        "SELECT name, loinc, value, unit, event_date FROM measurement WHERE patient_id=? AND value IS NOT NULL "
        "AND concept IN ('systolic_bp','diastolic_bp','weight','heart_rate') ORDER BY event_date LIMIT 25", (patient_id,))]
    return {'id': patient_id, 'mrn': patient_id, 'name': f'Synthetic {patient_id[-6:]}', 'sex': pat[1], 'birth_date': pat[2],
            'date': as_of or '2030-05-27', 'problems': problems, 'medications': meds, 'allergies': [{'none': True}],
            'results': results, 'vitals': vitals}


def write_delivery(db, out_dir, patient_ids, as_of='2030-05-27'):
    folder = Path(out_dir) / 'ccda' / as_of[:7]
    folder.mkdir(parents=True, exist_ok=True)
    paths = []
    for pid in patient_ids:
        p = folder / f'{pid}.xml'
        p.write_text(render(from_semantic(db, pid, as_of)), encoding='utf-8')
        paths.append(p)
    return paths
