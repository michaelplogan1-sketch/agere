"""HL7 C-CDA R2.1 clinical documents (CCD, discharge summary, referral note, ...) in a raw delivery -> landing tables.

detect: an XML file whose root element is ``ClinicalDocument`` in the ``urn:hl7-org:v3`` namespace. parse yields, per file:

  ccda_document      one row: document id, type (LOINC), title, effective time, patient (id root/extension, name, sex, birth
                     date), author, custodian, encounter, template ids, counts of what the sections held
  ccda_section       one row per section: LOINC code, title, kind, narrative text (the human-readable block, whitespace
                     flattened), entry count, nullFlavor / "no known" statement
  ccda_problem       ccda_medication   ccda_allergy   ccda_result   ccda_vital   ccda_immunization   ccda_procedure
  ccda_encounter     ccda_social       one row per structured entry, with the coded concept (code / code system / display
                     and every translation), status, onset-or-effective low/high, value + unit where the entry has one,
                     negation (negationInd), and ``resource`` = the entry rendered as a FHIR R4-shaped JSON resource
                     (HL7 C-CDA-on-FHIR concept maps), so downstream adapters can read one shape for XML and ndjson
  document_text      the document's narrative as one text (status ``text_layer``, engine ``ccda_xml``), the same table the
                     scanned-document reader fills, so passage indexes and outside-document questions read C-CDA
                     narrative without a second path
  ccda_parse_issue   a document that cannot be read still lands (code + detail); nothing a sender delivered is silently dropped

Safety: a DOCTYPE or ENTITY declaration is refused (billion-laughs / external entity), the parser never resolves external
references, and file size is bounded. Nothing is interpreted clinically: a concept with a nullFlavor keeps it; an
allergy/problem entry with negationInd="true" lands with ``negated`` = 1 so "no known allergies" never reads as an allergy.
Synthetic data only; no model calls.
"""
from __future__ import annotations

import hashlib
import json
import re
from pathlib import Path
import xml.etree.ElementTree as ET

VERSION = 'ccda-ingest/1'
NS = '{urn:hl7-org:v3}'
MAX_BYTES = 64 * 1024 * 1024

SECTION_KIND = {
    '11450-4': 'problem', '10160-0': 'medication', '48765-2': 'allergy', '30954-2': 'result', '8716-3': 'vital',
    '11369-6': 'immunization', '47519-4': 'procedure', '46240-8': 'encounter', '29762-2': 'social', '18776-5': 'plan_of_care',
    '10157-6': 'family', '11348-0': 'past_medical', '10164-2': 'hpi', '29545-1': 'physical_exam', '10187-3': 'review_of_meds',
    '34109-9': 'note', '42349-1': 'reason_for_referral', '8648-8': 'hospital_course', '8653-8': 'discharge_instructions',
    '11535-2': 'discharge_diagnosis', '10183-2': 'discharge_medication', '8650-4': 'discharge_summary', '51847-2': 'assessment_plan',
    '10154-3': 'chief_complaint', '10155-0': 'allergy', '18748-4': 'diagnostic_imaging',
}
KIND_TABLE = {'problem': 'ccda_problem', 'past_medical': 'ccda_problem', 'discharge_diagnosis': 'ccda_problem',
              'medication': 'ccda_medication', 'discharge_medication': 'ccda_medication', 'review_of_meds': 'ccda_medication',
              'allergy': 'ccda_allergy', 'result': 'ccda_result', 'vital': 'ccda_vital', 'immunization': 'ccda_immunization',
              'procedure': 'ccda_procedure', 'encounter': 'ccda_encounter', 'social': 'ccda_social'}
SYSTEMS = {  # code-system OID -> (short name, FHIR system URI)
    '2.16.840.1.113883.6.1': ('loinc', 'http://loinc.org'), '2.16.840.1.113883.6.96': ('snomed', 'http://snomed.info/sct'),
    '2.16.840.1.113883.6.88': ('rxnorm', 'http://www.nlm.nih.gov/research/umls/rxnorm'),
    '2.16.840.1.113883.6.90': ('icd10cm', 'http://hl7.org/fhir/sid/icd-10-cm'), '2.16.840.1.113883.6.103': ('icd9cm', 'http://hl7.org/fhir/sid/icd-9-cm'),
    '2.16.840.1.113883.12.292': ('cvx', 'http://hl7.org/fhir/sid/cvx'), '2.16.840.1.113883.6.69': ('ndc', 'http://hl7.org/fhir/sid/ndc'),
    '2.16.840.1.113883.6.12': ('cpt', 'http://www.ama-assn.org/go/cpt'), '2.16.840.1.113883.4.6': ('npi', 'http://hl7.org/fhir/sid/us-npi'),
    '2.16.840.1.113883.6.8': ('ucum', 'http://unitsofmeasure.org'), '2.16.840.1.113883.5.4': ('actcode', 'http://terminology.hl7.org/CodeSystem/v3-ActCode'),
}
ADMIN_SEX = {'F': 'female', 'M': 'male', 'UN': 'other'}
STATUS = {'completed': 'completed', 'active': 'active', 'aborted': 'stopped', 'suspended': 'on-hold', 'held': 'on-hold',
          'cancelled': 'cancelled', 'new': 'draft', 'nullified': 'entered-in-error'}


def _t(tag):
    return NS + tag


def detect(path: Path) -> bool:
    p = Path(path)
    if p.suffix.lower() not in ('.xml', '.ccda', '.cda', '.ccd'):
        return False
    try:
        with open(p, 'rb') as fh:
            head = fh.read(8192)
    except OSError:
        return False
    return b'ClinicalDocument' in head and b'urn:hl7-org:v3' in head


# ---- element helpers ------------------------------------------------------------------------------------------------
def _flat(el):
    return re.sub(r'\s+', ' ', ''.join(el.itertext())).strip() if el is not None else ''


def _first(el, *tags):
    for tag in tags:
        found = el.find(_t(tag)) if el is not None else None
        if found is not None:
            return found
    return None


def day(value):
    """HL7 TS (YYYY[MM[DD[HHMM[SS]]]][+-ZZZZ]) -> ISO date / datetime text; partial dates stay partial."""
    s = (value or '').strip()
    m = re.match(r'^(\d{4})(\d{2})?(\d{2})?(\d{2})?(\d{2})?(\d{2})?', s)
    if not m: return None
    y, mo, d, hh, mm, ss = m.groups()
    out = y + (f'-{mo}' if mo else '') + (f'-{d}' if d else '')
    if hh and mm: out += f'T{hh}:{mm}' + (f':{ss}' if ss else '')
    return out


def _time(el):
    """effectiveTime-like element -> (low, high, point)."""
    if el is None: return None, None, None
    low, high = el.find(_t('low')), el.find(_t('high'))
    return (day(low.get('value')) if low is not None else None, day(high.get('value')) if high is not None else None, day(el.get('value')))


def concept(el):
    """A coded element (code / value xsi:type=CD / translation) -> {code, system, system_oid, display, original_text, null_flavor,
    translations:[...]}."""
    if el is None: return {}
    oid = el.get('codeSystem')
    name, uri = SYSTEMS.get(oid, (el.get('codeSystemName'), oid))
    original = el.find(_t('originalText'))
    out = {'code': el.get('code'), 'system': name, 'system_uri': uri, 'system_oid': oid,
           'display': el.get('displayName') or _flat(original) or None, 'null_flavor': el.get('nullFlavor'),
           'translations': []}
    for tr in el.findall(_t('translation')):
        t = concept(tr); t.pop('translations', None); out['translations'].append(t)
    return out


def _codes(c, system):
    """Code of a given system on a concept or its translations."""
    for x in [c, *(c.get('translations') or [])]:
        if x.get('system') == system and x.get('code'): return x['code']
    return None


def _coding(c):
    return [{'system': x.get('system_uri'), 'code': x.get('code'), 'display': x.get('display')}
            for x in [c, *(c.get('translations') or [])] if x.get('code')]


def _id(el):
    i = el.find(_t('id')) if el is not None else None
    if i is None: return None
    return f"{i.get('root') or ''}|{i.get('extension')}" if i.get('extension') else i.get('root')


def _val(el):
    """observation value -> (number, unit, text, concept)."""
    v = el.find(_t('value')) if el is not None else None
    if v is None: return None, None, None, {}
    typ = (v.get('{http://www.w3.org/2001/XMLSchema-instance}type') or '').upper()
    if typ in ('PQ', 'REAL', 'INT') or (v.get('value') and typ == ''):
        try: num = float(v.get('value'))
        except (TypeError, ValueError): num = None
        return num, v.get('unit'), v.get('value'), {}
    if typ in ('CD', 'CE', 'CO') or v.get('code'): return None, None, None, concept(v)
    return None, None, (_flat(v) or v.get('value')), {}


# ---- entries --------------------------------------------------------------------------------------------------------
def _obs_base(obs):
    code = concept(obs.find(_t('code')))
    status = obs.find(_t('statusCode'))
    low, high, point = _time(obs.find(_t('effectiveTime')))
    return {'entry_id': _id(obs), 'code': code.get('code'), 'code_system': code.get('system'), 'display': code.get('display'),
            'code_null_flavor': code.get('null_flavor'), 'status': (status.get('code') if status is not None else None),
            'effective_low': low or point, 'effective_high': high, 'negated': 1 if obs.get('negationInd') == 'true' else 0,
            'translations': json.dumps(code.get('translations') or []), '_concept': code}


def _problem(act):
    obs = None
    for rel in act.iter(_t('observation')):
        obs = rel; break
    base = _obs_base(act)
    row = {'entry_id': base['entry_id']}
    if obs is not None:
        vnum, vunit, vtext, vconcept = _val(obs)
        c = vconcept or concept(obs.find(_t('code')))
        low, high, point = _time(obs.find(_t('effectiveTime')))
        st = None
        for sc in obs.iter(_t('observation')):
            if concept(sc.find(_t('code'))).get('code') == '33999-4':  # problem status
                st = concept(sc.find(_t('value'))).get('display')
        negated = 1 if obs.get('negationInd') == 'true' else 0
        row.update({'entry_id': _id(obs) or base['entry_id'], 'code': c.get('code'), 'code_system': c.get('system'), 'display': c.get('display'),
                    'icd10': _codes(c, 'icd10cm'), 'snomed': _codes(c, 'snomed'), 'onset': low or point, 'resolved': high,
                    'status': (st or (act.find(_t('statusCode')).get('code') if act.find(_t('statusCode')) is not None else None)),
                    'negated': negated, 'null_flavor': c.get('null_flavor'), 'translations': json.dumps(c.get('translations') or [])})
        row['resource'] = {'resourceType': 'Condition', 'id': row['entry_id'],
                           'clinicalStatus': {'text': row['status']}, 'code': {'coding': _coding(c), 'text': c.get('display')},
                           'onsetDateTime': row['onset'], 'abatementDateTime': row['resolved'],
                           'verificationStatus': {'text': 'refuted' if negated else 'confirmed'}}
    return row


def _medication(sa):
    cons = sa.find(f'.//{_t("consumable")}')
    prod = cons.find(f'.//{_t("manufacturedMaterial")}') if cons is not None else None
    c = concept(prod.find(_t('code'))) if prod is not None else {}
    low, high, point = _time(next((e for e in sa.findall(_t('effectiveTime')) if e.get('{http://www.w3.org/2001/XMLSchema-instance}type') == 'IVL_TS'), None))
    dose = sa.find(_t('doseQuantity'))
    period = next((e for e in sa.findall(_t('effectiveTime')) if e.find(_t('period')) is not None), None)
    per = period.find(_t('period')) if period is not None else None
    route = concept(sa.find(_t('routeCode')))
    sc = sa.find(_t('statusCode'))
    negated = 1 if sa.get('negationInd') == 'true' else 0
    row = {'entry_id': _id(sa), 'rxnorm': _codes(c, 'rxnorm'), 'ndc': _codes(c, 'ndc'), 'code': c.get('code'), 'code_system': c.get('system'),
           'display': c.get('display') or (_flat(prod.find(_t('name'))) if prod is not None and prod.find(_t('name')) is not None else None),
           'status': sc.get('code') if sc is not None else None, 'mood': sa.get('moodCode'), 'start': low or point, 'stop': high,
           'dose_value': float(dose.get('value')) if dose is not None and dose.get('value') not in (None, '') and re.match(r'^-?\d+(\.\d+)?$', dose.get('value')) else None,
           'dose_unit': dose.get('unit') if dose is not None else None, 'route': route.get('display') or route.get('code'),
           'period_value': per.get('value') if per is not None else None, 'period_unit': per.get('unit') if per is not None else None,
           'negated': negated, 'translations': json.dumps(c.get('translations') or [])}
    row['resource'] = {'resourceType': 'MedicationStatement', 'id': row['entry_id'], 'status': 'not-taken' if negated else STATUS.get(row['status'], row['status']),
                       'medicationCodeableConcept': {'coding': _coding(c), 'text': row['display']},
                       'effectivePeriod': {'start': row['start'], 'end': row['stop']},
                       'dosage': [{'doseAndRate': [{'doseQuantity': {'value': row['dose_value'], 'unit': row['dose_unit']}}], 'route': {'text': row['route']}}]}
    return row


def _allergy(act):
    obs = next(act.iter(_t('observation')), None)
    if obs is None: return {'entry_id': _id(act)}
    base = _obs_base(obs)
    vnum, vunit, vtext, v = _val(obs)
    sub = None
    part = obs.find(f'.//{_t("participant")}')
    if part is not None:
        sub = concept(part.find(f'.//{_t("playingEntity")}/{_t("code")}'))
    reaction = None
    for r in obs.iter(_t('observation')):
        if r is not obs and r.find(_t('value')) is not None and concept(r.find(_t('code'))).get('code') == 'ASSERTION':
            reaction = concept(r.find(_t('value'))).get('display')
    for sc in obs.findall(f'.//{_t("observation")}'):
        cv = concept(sc.find(_t('value')))
        if cv and cv.get('display') and not reaction and sc is not obs: reaction = cv.get('display')
    negated = 1 if obs.get('negationInd') == 'true' else 0
    sub = sub or {}
    row = {'entry_id': base['entry_id'], 'allergen_code': sub.get('code'), 'allergen_system': sub.get('system'), 'allergen': sub.get('display'),
           'category': v.get('display') or base['display'], 'reaction': reaction, 'status': base['status'], 'onset': base['effective_low'],
           'negated': negated or (1 if v.get('code') in ('716186003',) else 0), 'null_flavor': sub.get('null_flavor') or v.get('null_flavor')}
    row['resource'] = {'resourceType': 'AllergyIntolerance', 'id': row['entry_id'], 'code': {'coding': _coding(sub), 'text': row['allergen']},
                       'reaction': [{'manifestation': [{'text': reaction}]}] if reaction else [], 'onsetDateTime': row['onset'],
                       'verificationStatus': {'text': 'refuted' if row['negated'] else 'confirmed'}}
    return row


def _result_rows(org):
    rows = []
    organizer_code = concept(org.find(_t('code')))
    for comp in org.findall(_t('component')):
        obs = comp.find(_t('observation'))
        if obs is None: continue
        base = _obs_base(obs)
        num, unit, text, vc = _val(obs)
        rng = obs.find(f'.//{_t("referenceRange")}//{_t("observationRange")}')
        interp = concept(obs.find(_t('interpretationCode')))
        row = {'entry_id': base['entry_id'], 'panel_code': organizer_code.get('code'), 'panel': organizer_code.get('display'),
               'code': base['code'], 'code_system': base['code_system'], 'loinc': _codes(base['_concept'], 'loinc'),
               'display': base['display'], 'value': num, 'unit': unit, 'value_text': text or vc.get('display'),
               'reference_range': _flat(rng.find(_t('text'))) if rng is not None and rng.find(_t('text')) is not None else (_flat(rng) if rng is not None else None),
               'interpretation': interp.get('code'), 'status': base['status'], 'effective': base['effective_low'], 'negated': base['negated'],
               'translations': base['translations']}
        row['resource'] = {'resourceType': 'Observation', 'id': row['entry_id'], 'status': 'final' if row['status'] in ('completed', None) else row['status'],
                           'code': {'coding': _coding(base['_concept']), 'text': row['display']}, 'effectiveDateTime': row['effective'],
                           'valueQuantity': {'value': num, 'unit': unit} if num is not None else None,
                           'valueString': row['value_text'] if num is None else None,
                           'interpretation': [{'coding': [{'code': interp.get('code')}]}] if interp.get('code') else []}
        rows.append(row)
    return rows


def _immunization(sa):
    cons = sa.find(f'.//{_t("manufacturedMaterial")}')
    c = concept(cons.find(_t('code'))) if cons is not None else {}
    low, high, point = _time(sa.find(_t('effectiveTime')))
    sc = sa.find(_t('statusCode'))
    negated = 1 if sa.get('negationInd') == 'true' else 0
    lot = cons.find(_t('lotNumberText')) if cons is not None else None
    row = {'entry_id': _id(sa), 'cvx': _codes(c, 'cvx') or (c.get('code') if c.get('system') == 'cvx' else None), 'code': c.get('code'),
           'display': c.get('display'), 'status': sc.get('code') if sc is not None else None, 'administered': low or point,
           'lot_number': _flat(lot) if lot is not None else None, 'negated': negated}
    row['resource'] = {'resourceType': 'Immunization', 'id': row['entry_id'], 'status': 'not-done' if negated else 'completed',
                       'vaccineCode': {'coding': _coding(c), 'text': row['display']}, 'occurrenceDateTime': row['administered']}
    return row


def _procedure(proc):
    c = concept(proc.find(_t('code')))
    low, high, point = _time(proc.find(_t('effectiveTime')))
    sc = proc.find(_t('statusCode'))
    row = {'entry_id': _id(proc), 'kind': proc.tag.replace(NS, ''), 'code': c.get('code'), 'code_system': c.get('system'), 'display': c.get('display'),
           'cpt': _codes(c, 'cpt'), 'snomed': _codes(c, 'snomed'), 'status': sc.get('code') if sc is not None else None,
           'performed': low or point, 'performed_end': high, 'negated': 1 if proc.get('negationInd') == 'true' else 0}
    row['resource'] = {'resourceType': 'Procedure', 'id': row['entry_id'], 'status': STATUS.get(row['status'], row['status']),
                       'code': {'coding': _coding(c), 'text': row['display']}, 'performedDateTime': row['performed']}
    return row


def _encounter(enc):
    c = concept(enc.find(_t('code')))
    low, high, point = _time(enc.find(_t('effectiveTime')))
    dx = [concept(v) for v in (x.find(_t('value')) for x in enc.iter(_t('observation'))) if v is not None and v.get('code')]
    row = {'entry_id': _id(enc), 'code': c.get('code'), 'code_system': c.get('system'), 'display': c.get('display'),
           'start': low or point, 'end': high, 'icd10': ';'.join(sorted({_codes(d, 'icd10cm') for d in dx if _codes(d, 'icd10cm')})) or None}
    row['resource'] = {'resourceType': 'Encounter', 'id': row['entry_id'], 'status': 'finished',
                       'type': [{'coding': _coding(c), 'text': row['display']}], 'period': {'start': row['start'], 'end': row['end']}}
    return row


def _social(obs):
    base = _obs_base(obs)
    num, unit, text, vc = _val(obs)
    row = {'entry_id': base['entry_id'], 'code': base['code'], 'code_system': base['code_system'], 'display': base['display'],
           'value': num, 'unit': unit, 'value_text': text or vc.get('display'), 'value_code': vc.get('code'),
           'effective': base['effective_low'], 'status': base['status']}
    row['resource'] = {'resourceType': 'Observation', 'id': row['entry_id'], 'category': [{'text': 'social-history'}],
                       'code': {'coding': _coding(base['_concept']), 'text': row['display']}, 'effectiveDateTime': row['effective'],
                       'valueString': row['value_text']}
    return row


def _vital_rows(org):
    out = []
    when = _time(org.find(_t('effectiveTime')))
    for r in _result_rows(org):
        r['effective'] = r.get('effective') or when[0] or when[2]
        r['resource']['category'] = [{'text': 'vital-signs'}]
        r['resource']['effectiveDateTime'] = r['effective']
        out.append(r)
    return out


def _entries(section, kind):
    """(table, row) for each structured entry of one section."""
    for entry in section.findall(_t('entry')):
        el = next((c for c in entry), None)
        if el is None: continue
        tag = el.tag.replace(NS, '')
        try:
            if kind in ('problem', 'past_medical', 'discharge_diagnosis') and tag in ('act', 'observation'):
                row = _problem(el) if tag == 'act' else _problem(_wrap(el))
                yield 'ccda_problem', row
            elif kind in ('medication', 'discharge_medication', 'review_of_meds') and tag == 'substanceAdministration':
                yield 'ccda_medication', _medication(el)
            elif kind == 'allergy' and tag == 'act':
                yield 'ccda_allergy', _allergy(el)
            elif kind == 'result' and tag == 'organizer':
                for r in _result_rows(el): yield 'ccda_result', r
            elif kind == 'vital' and tag == 'organizer':
                for r in _vital_rows(el): yield 'ccda_vital', r
            elif kind == 'immunization' and tag == 'substanceAdministration':
                yield 'ccda_immunization', _immunization(el)
            elif kind == 'procedure' and tag in ('procedure', 'observation', 'act'):
                yield 'ccda_procedure', _procedure(el)
            elif kind == 'encounter' and tag == 'encounter':
                yield 'ccda_encounter', _encounter(el)
            elif kind == 'social' and tag == 'observation':
                yield 'ccda_social', _social(el)
        except (AttributeError, ValueError, TypeError):
            yield 'ccda_parse_issue', {'code': 'entry_unreadable', 'detail': f'{kind}/{tag}'}


def _wrap(obs):
    act = ET.Element(_t('act')); rel = ET.SubElement(act, _t('entryRelationship')); rel.append(obs)
    return act


# ---- document -------------------------------------------------------------------------------------------------------
def _patient(root):
    rt = root.find(_t('recordTarget'))
    role = rt.find(_t('patientRole')) if rt is not None else None
    pat = role.find(_t('patient')) if role is not None else None
    ids = [(i.get('root'), i.get('extension')) for i in role.findall(_t('id'))] if role is not None else []
    name = pat.find(_t('name')) if pat is not None else None
    given = ' '.join(_flat(g) for g in name.findall(_t('given'))) if name is not None else ''
    family = _flat(name.find(_t('family'))) if name is not None and name.find(_t('family')) is not None else ''
    sex = pat.find(_t('administrativeGenderCode')) if pat is not None else None
    birth = pat.find(_t('birthTime')) if pat is not None else None
    addr = role.find(_t('addr')) if role is not None else None
    return {'patient_ids': json.dumps([f'{r}|{e}' if e else r for r, e in ids]), 'patient_id_root': ids[0][0] if ids else None,
            'patient_id_extension': ids[0][1] if ids else None,
            'mrn': next((e for r, e in ids if e and r and r != '2.16.840.1.113883.4.1'), None),
            'patient_name': (given + ' ' + family).strip() or None, 'sex': ADMIN_SEX.get(sex.get('code'), sex.get('code')) if sex is not None else None,
            'birth_date': day(birth.get('value')) if birth is not None else None,
            'postal_code': _flat(addr.find(_t('postalCode'))) if addr is not None and addr.find(_t('postalCode')) is not None else None}


def parse(path: Path, context: dict):
    path = Path(path)
    size = path.stat().st_size
    rel = context.get('relpath') or path.name
    if size > MAX_BYTES:
        yield 'ccda_parse_issue', {'code': 'file_too_large', 'detail': str(size), 'relpath': rel}
        return
    raw = path.read_bytes()
    sha = hashlib.sha256(raw).hexdigest()
    head = raw[:4096].lower()
    if b'<!doctype' in raw[:100000].lower() or b'<!entity' in raw[:100000].lower():
        yield 'ccda_parse_issue', {'code': 'doctype_refused', 'detail': 'DOCTYPE/ENTITY declarations are not parsed', 'relpath': rel, 'sha256': sha}
        return
    try:
        root = ET.fromstring(raw)
    except ET.ParseError as exc:
        yield 'ccda_parse_issue', {'code': 'xml_parse_error', 'detail': str(exc)[:200], 'relpath': rel, 'sha256': sha}
        return
    if root.tag != _t('ClinicalDocument'):
        yield 'ccda_parse_issue', {'code': 'not_clinical_document', 'detail': root.tag, 'relpath': rel, 'sha256': sha}
        return
    doc_id = _id(root)
    typ = concept(root.find(_t('code')))
    title = _flat(root.find(_t('title'))) if root.find(_t('title')) is not None else None
    eff = root.find(_t('effectiveTime'))
    templates = [t.get('root') + (':' + t.get('extension') if t.get('extension') else '') for t in root.findall(_t('templateId'))]
    author = root.find(f'{_t("author")}/{_t("assignedAuthor")}')
    author_npi = next((i.get('extension') for i in author.findall(_t('id')) if i.get('root') == '2.16.840.1.113883.4.6'), None) if author is not None else None
    custodian = root.find(f'.//{_t("custodian")}//{_t("name")}')
    enc = root.find(f'{_t("componentOf")}/{_t("encompassingEncounter")}')
    enc_id = _id(enc) if enc is not None else None
    pat = _patient(root)
    counts, narrative = {}, []
    base = {'document_id': doc_id, 'relpath': rel, 'sha256': sha}
    for sec in root.iter(_t('section')):
        code = concept(sec.find(_t('code')))
        loinc = code.get('code')
        kind = SECTION_KIND.get(loinc, 'other')
        text = _flat(sec.find(_t('text')))
        nf = sec.get('nullFlavor')
        entries = sec.findall(_t('entry'))
        nostatement = bool(re.search(r'\b(no known|none|not on file|no (?:active|current|significant))\b', text, re.I)) and not entries
        yield 'ccda_section', {**base, 'section_code': loinc, 'section_title': _flat(sec.find(_t('title'))) or code.get('display'), 'kind': kind,
                               'narrative': text, 'entry_count': len(entries), 'null_flavor': nf, 'states_none': 1 if nostatement or nf else 0}
        if text: narrative.append(f"{_flat(sec.find(_t('title'))) or kind}: {text}")
        table = KIND_TABLE.get(kind)
        if not table: continue
        for t, row in _entries(sec, kind):
            row = dict(row)
            resource = row.pop('resource', None)
            row.pop('_concept', None)
            if t == 'ccda_parse_issue':
                yield t, {**base, **row}; continue
            counts[t] = counts.get(t, 0) + 1
            if resource is not None:
                resource['subject'] = {'identifier': {'value': pat['patient_id_extension'] or pat['patient_id_root']}}
                if enc_id: resource['encounter'] = {'identifier': {'value': enc_id}}
            yield t, {**base, **{k: v for k, v in pat.items() if k in ('patient_id_root', 'patient_id_extension', 'mrn')},
                      'section_code': loinc, 'section_kind': kind, **row,
                      'resource': json.dumps({k: v for k, v in (resource or {}).items() if v not in (None, [], {})}) if resource else None}
    full_text = '\n'.join(narrative)
    effective = day(eff.get('value')) if eff is not None else None
    yield 'ccda_document', {**base, 'doc_type_code': typ.get('code'), 'doc_type': typ.get('display'), 'title': title, 'effective': effective,
                            'templates': json.dumps(templates), 'author_npi': author_npi,
                            'custodian': _flat(custodian) if custodian is not None else None, 'encounter_id': enc_id,
                            'sections': sum(1 for _ in root.iter(_t('section'))), 'entries': json.dumps(counts), **pat, 'reader': VERSION}
    yield 'document_text', {'relpath': rel, 'file_name': path.name, 'sha256': sha, 'status': 'text_layer', 'engine': 'ccda_xml',
                            'text': full_text, 'text_chars': len(full_text), 'page_count': 1, 'date_of_service': effective,
                            'pages_json': json.dumps([full_text]), 'attempts_json': json.dumps([{'engine': 'ccda_xml', 'status': 'ok'}]),
                            'reader': VERSION, 'patient_ref': pat['mrn'] or pat['patient_id_extension']}
