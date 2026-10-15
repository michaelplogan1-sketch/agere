"""Apply an APPROVED mapping: read the source through its connector and build an onboarded semantic layer.

Refuses unless mapping.approval.approved is true; uses only fields with accept: true; applies only the
transforms written in the mapping (plus the contract's derivations: versioning/dedup, concepts, unit
conversion, drug classes, claim settlement). Output table and column names follow semantic_layer.py so
the same SQL runs against a native build and an onboarded build. Every row keeps its source row id and
`onboarding_lineage` records which source column fed which target field through which transforms.
"""
from collections import Counter, defaultdict
from pathlib import Path
import json
import re
import sqlite3

from ...contracts import digest
from ..clinical import concepts, semantic_layer
from ..clinical.drug_classes import classify
from . import codes
from . import contract as contract_mod

VERSION = 'agere-onboarding-apply/1'


class NotApproved(RuntimeError):
    pass


def _to_text(v):
    if v is None: return None
    if isinstance(v, float) and v.is_integer(): return str(int(v))
    return str(v)


def _transform(op, v, stats, key, lookups=None):
    name = op['op']
    if v is None and name != 'value_map': return None
    if name == 'dollars_to_cents':
        if not codes.is_number(v): stats[key + ':unparseable'] += 1; return None
        return int(round(float(v) * 100))
    if name == 'to_date':
        d = codes.parse_date(v)
        if d is None: stats[key + ':unparseable'] += 1
        return d
    if name == 'icd10_normalize': return codes.icd10_normalize(v)
    if name == 'ndc_normalize': return codes.ndc_normalize(v)
    if name == 'to_text': return _to_text(v)
    if name == 'value_map':
        if v is None: return None
        out = op['map'].get(str(v))
        if out is None: stats[key + ':unmapped_value'] += 1; return v
        return out
    if name == 'review': return v  # reviewer note only; values pass through unchanged
    if name == 'lookup':
        out = lookups[(op['table'], op['key'], op['value'])].get(_to_text(v))
        if out is None: stats[key + ':lookup_miss'] += 1
        return out
    raise ValueError(f'Unknown transform {name}')


def _coerce(spec, v):
    if v is None: return None
    t = spec['type']
    if t == 'text': return _to_text(v)
    if t == 'integer':
        return int(round(float(v))) if codes.is_number(v) else None
    if t == 'real':
        return float(v) if codes.is_number(v) else None
    return v


def check_approved(mapping):
    a = mapping.get('approval') or {}
    if a.get('approved') is not True:
        raise NotApproved('Mapping is not approved; a reviewer must set approval.approved: true (nothing is applied silently)')
    if not a.get('reviewer'):
        raise NotApproved('Approval needs a reviewer name')


def _grain(conn, entity, ent, espec, stats):
    """Rows of the entity at its source grain: list of dicts with target field names."""
    fields = {f: e for f, e in ent['fields'].items() if e.get('accept') and e.get('source')}
    cols = sorted({e['source'] for e in fields.values()})
    lookups = {}
    for f, e in fields.items():
        for op in e.get('transforms', []):
            k = (op.get('table'), op.get('key'), op.get('value'))
            if op['op'] == 'lookup' and k not in lookups:
                lookups[k] = {_to_text(a): b for a, b in conn.rows(op['table'], [op['key'], op['value']])}
    rows = []
    synth_rid = 'record_id' in espec['fields'] and 'record_id' not in fields
    for i, raw in enumerate(conn.rows(ent['source_table'], cols), 1):
        src = dict(zip(cols, raw))
        out = {}
        if synth_rid:
            out['record_id'] = f"{ent['source_table']}#{i}"; stats[f'{entity}:record_id_synthesized'] += 1
        for f, e in fields.items():
            v = src[e['source']]
            for op in e.get('transforms', []):
                v = _transform(op, v, stats, f'{entity}.{f}', lookups)
            out[f] = _coerce(espec['fields'][f], v)
        rows.append(out)
    stats[f'{entity}:source_rows'] = len(rows)
    return rows, fields


SEMANTIC_DDL = """
CREATE TABLE meta(key TEXT PRIMARY KEY, value TEXT);
CREATE TABLE onboarding_lineage(entity TEXT, target_field TEXT, source_table TEXT, source_column TEXT, transforms TEXT, confidence REAL);
CREATE TABLE patient(patient_id TEXT PRIMARY KEY, age_reported INTEGER, sex_reported TEXT, coverage_type TEXT, coverage_status TEXT,
  birth_date TEXT, race TEXT, ethnicity TEXT, preferred_language TEXT, zip TEXT, state TEXT, county TEXT);
CREATE TABLE visit_event(record_id TEXT, encounter_id TEXT, patient_id TEXT, type TEXT, status TEXT, event_date TEXT, end_date TEXT,
  available_date TEXT, organization_id TEXT, attending_clinician_id TEXT, disposition TEXT, place_of_service TEXT);
CREATE TABLE visit(encounter_id TEXT, patient_id TEXT, type TEXT, type_concept TEXT, status TEXT, event_date TEXT, end_date TEXT,
  available_date TEXT, organization_id TEXT, attending_clinician_id TEXT, disposition TEXT, versions INTEGER, place_of_service TEXT, record_id TEXT);
CREATE TABLE condition(record_id TEXT, patient_id TEXT, source_table TEXT, concept TEXT, label TEXT, icd10 TEXT, snomed TEXT, onset TEXT,
  event_date TEXT, recorded_date TEXT, available_date TEXT, organization_id TEXT);
CREATE TABLE measurement(record_id TEXT, patient_id TEXT, source_table TEXT, concept TEXT, name TEXT, value REAL, unit TEXT,
  value_canonical REAL, unit_canonical TEXT, result_status TEXT, event_date TEXT, available_date TEXT, organization_id TEXT, loinc TEXT,
  reference_range TEXT, abnormal_flag TEXT);
CREATE TABLE measurement_latest(patient_id TEXT, concept TEXT, event_date TEXT, value_canonical REAL, unit_canonical TEXT,
  same_day_values INTEGER, same_day_min REAL, record_id TEXT, PRIMARY KEY(patient_id, concept));
CREATE TABLE medication(record_id TEXT, patient_id TEXT, source_stream TEXT, evidence TEXT, drug_name TEXT, drug_class TEXT, status TEXT,
  event_date TEXT, available_date TEXT, rxnorm TEXT, ndc TEXT);
CREATE TABLE claim_event(record_id TEXT, claim_id TEXT, patient_id TEXT, payer TEXT, service_date TEXT, status TEXT, payer_paid_cents INTEGER,
  billed_cents INTEGER, allowed_cents INTEGER, patient_responsibility_cents INTEGER, event_date TEXT, available_date TEXT, available_time TEXT,
  service_organization_id TEXT, reverses_record_id TEXT);
CREATE TABLE claim(claim_id TEXT PRIMARY KEY, patient_id TEXT, payer TEXT, service_date TEXT, benefit_year TEXT, first_status TEXT,
  final_status TEXT, ever_denied INTEGER, denied_then_paid INTEGER, net_payer_paid_cents INTEGER, billed_cents INTEGER,
  allowed_cents INTEGER, first_available_date TEXT, last_available_date TEXT, service_organization_id TEXT, versions INTEGER,
  received_final_status TEXT, live_payer_paid_cents INTEGER);
CREATE TABLE claim_line(record_id TEXT, claim_id TEXT, patient_id TEXT, line_number INTEGER, service_date TEXT, code_system TEXT,
  cpt TEXT, hcpcs TEXT, local_charge_code TEXT, revenue_code TEXT, units REAL, charge_cents INTEGER, service_organization_id TEXT);
CREATE TABLE clinician(clinician_id TEXT PRIMARY KEY, organization_id TEXT, specialty TEXT, npi TEXT, taxonomy_code TEXT);
CREATE TABLE organization(organization_id TEXT PRIMARY KEY, organization_type TEXT, display_name TEXT, npi TEXT, taxonomy_code TEXT,
  tin TEXT, city TEXT, state TEXT, postal_code TEXT);
"""


def _insert(out, table, rows):
    if not rows: return 0
    cols = [r[1] for r in out.execute(f'PRAGMA table_info({table})')]
    out.executemany(f'INSERT INTO {table}({",".join(cols)}) VALUES ({",".join("?" * len(cols))})', [tuple(r.get(c) for c in cols) for r in rows])
    return len(rows)


def _encounter_concepts():
    return {c: re.compile(p, re.I) for c, d in concepts.ENCOUNTERS.items() for s, f in d['fields'].items() if s == 'encounters'
            for p in f.values()}


def _measurement_concept(name, loinc, labs_dictionary, by_loinc):
    if name is not None:
        c = concepts.concept_for_label(name)
        if c: return c, True
        v = labs_dictionary.get(name)
        if v and v.get('agere_concept') and (v.get('confidence') or 0) >= 0.8: return v['agere_concept'], False
    if loinc:
        cs = by_loinc.get(loinc)
        if cs and len(cs) == 1: return next(iter(cs)), False
        c = concepts.concept_for_code(loinc)
        if c in concepts.MEASUREMENTS: return c, False
    return None, False


def _label_fallback(label, kind, memo, stats):
    """Second-encoder terminology fallback for a label that code, alias and dictionary left unresolved (one encoding per
    distinct label); accepted proposals are counted in stats, below-threshold candidates only counted as proposed."""
    if label is None or not str(label).strip(): return None
    if (kind, label) not in memo:
        m = concepts.match_label(label, kind)
        memo[(kind, label)] = m
        if m and m['method'] == 'encoder':
            stats[f'{kind}:encoder_fallback_' + ('accepted' if m['accepted'] else 'below_threshold')] += 1
    m = memo[(kind, label)]
    return m.get('concept') if m else None


def apply(mapping, conn, out_path, model=None):
    check_approved(mapping)
    model = model or contract_mod.load_model()
    targets = contract_mod.targets(model)
    out_path = Path(out_path)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    tmp = out_path.with_suffix('.tmp')
    tmp.unlink(missing_ok=True)
    out = sqlite3.connect(tmp)
    out.executescript(SEMANTIC_DDL)
    stats = Counter()
    grains, lineage = {}, []
    for entity, ent in mapping['entities'].items():
        if not ent.get('source_table') or entity not in targets: continue
        rows, fields = _grain(conn, entity, ent, targets[entity], stats)
        grains[entity] = (rows, fields)
        for f, e in fields.items():
            lineage.append((entity, f, ent['source_table'], e['source'],
                            json.dumps(e.get('transforms', [])), e['confidence']))
    out.executemany('INSERT INTO onboarding_lineage VALUES (?,?,?,?,?,?)', lineage)
    g = lambda e: grains.get(e, ([], {}))[0]

    # Reference entities.
    _insert(out, 'organization', list({r['organization_id']: r for r in g('organization') if r.get('organization_id')}.values()))
    _insert(out, 'clinician', list({r['clinician_id']: r for r in g('clinician') if r.get('clinician_id')}.values()))
    # Patient + latest coverage (contract coverage.versioning.feeds).
    cov = defaultdict(list)
    for r in g('coverage'): cov[r.get('patient_id')].append(r)
    patients = {}
    for r in g('patient'):
        c = max(cov.get(r['patient_id'], []), key=lambda x: x.get('event_date') or '', default={})
        patients[r['patient_id']] = {**r, 'coverage_type': c.get('coverage_type'), 'coverage_status': c.get('status')}
    _insert(out, 'patient', list(patients.values()))

    # Visits: received encounter rows, then the contract's latest-version rule.
    _insert(out, 'visit_event', g('visit'))
    ver = targets['visit']['versioning']
    have = {f for f in grains.get('visit', ([], {}))[1]}
    order = ', '.join(f'{e} {d.upper()}' for e, d in ver['order'] if set(re.findall(r'\b(?!desc|asc)[a-z_]+\b', e)) - {'completed'} <= have) or 'rowid'
    tc = _encounter_concepts()
    out.execute(f"""INSERT INTO visit(encounter_id, patient_id, type, status, event_date, end_date, available_date, organization_id,
          attending_clinician_id, disposition, versions, place_of_service, record_id)
        SELECT encounter_id, patient_id, type, status, event_date, end_date, available_date, organization_id, attending_clinician_id,
          disposition, n, place_of_service, record_id FROM (
          SELECT v.*, count(*) OVER (PARTITION BY coalesce(v.encounter_id, v.record_id)) AS n,
                 row_number() OVER (PARTITION BY coalesce(v.encounter_id, v.record_id) ORDER BY {order}) AS rn
          FROM visit_event v) WHERE rn = 1""")
    out.executemany('UPDATE visit SET type_concept = ? WHERE type = ?',
                    [(next((c for c, rx in tc.items() if rx.search(t.strip())), None), t)
                     for (t,) in out.execute('SELECT DISTINCT type FROM visit WHERE type IS NOT NULL').fetchall()])

    # Conditions with library concepts (ICD-10 prefix, then label pattern).
    rows = []
    term_memo = {}
    for r in g('condition'):
        c = concepts.concept_for_code(r['icd10']) if r.get('icd10') else None
        if c not in concepts.CONDITIONS:
            c = None
        if c is None and not r.get('icd10'):  # never second-guess a received ICD-10 code: label alias, then encoder fallback
            c = _label_fallback(r.get('label'), 'condition', term_memo, stats)
        rows.append({**r, 'source_table': 'conditions', 'concept': c})
    _insert(out, 'condition', rows)

    # Measurements: concept by name pattern / dictionary / LOINC; canonical unit conversion from the concept library.
    labs = semantic_layer._dictionary('labs')
    by_loinc = defaultdict(set)
    for v in labs.values():
        if v.get('loinc') and v.get('agere_concept') and (v.get('confidence') or 0) >= 0.8: by_loinc[v['loinc']].add(v['agere_concept'])
    rows = []
    for r in g('measurement'):
        c, _ = _measurement_concept(r.get('name'), r.get('loinc'), labs, by_loinc)
        # a received LOINC outside the library, or a confident dictionary entry mapping the name to no concept, is an exact
        # "not one of ours": no fallback
        entry = labs.get(r.get('name')) or {}
        known = max([entry.get('confidence') or 0] + [v.get('confidence') or 0 for v in (entry.get('variants_by_unit') or {}).values()]) >= 0.8
        if c is None and not r.get('loinc') and not known:
            c = _label_fallback(r.get('name'), 'lab', term_memo, stats)
        canon = cunit = None
        if c:
            factor = concepts.MEASUREMENTS[c]['units'].get(r.get('unit'))
            cunit = concepts.MEASUREMENTS[c]['unit']
            if r.get('value') is not None and factor is not None:
                canon = r['value'] * factor
                if factor != 1.0: stats['measurement:unit_converted'] += 1
            elif r.get('value') is not None:
                stats['measurement:unit_unconvertible'] += 1
        rows.append({**r, 'source_table': 'measurement', 'concept': c, 'value_canonical': canon, 'unit_canonical': cunit})
    _insert(out, 'measurement', rows)
    out.execute("""INSERT INTO measurement_latest
        SELECT patient_id, concept, event_date, value_canonical, unit_canonical, n, mn, record_id FROM (
          SELECT m.*, count(*) OVER d AS n, min(m.value_canonical) OVER d AS mn,
                 row_number() OVER (PARTITION BY m.patient_id, m.concept ORDER BY m.event_date DESC, m.available_date DESC, m.record_id DESC) AS rn
          FROM measurement m WHERE m.concept IS NOT NULL AND m.value_canonical IS NOT NULL AND m.event_date IS NOT NULL
          WINDOW d AS (PARTITION BY m.patient_id, m.concept, m.event_date)) WHERE rn = 1""")

    # Medications: one row per (record, drug class); classes from the ingredient rules plus the normalization dictionary.
    meds = semantic_layer._dictionary('medications')
    rows = []
    for r in g('medication'):
        name = r.get('drug_name')
        if not (name or r.get('rxnorm') or r.get('ndc')): continue
        cls = list(classify(name))
        d = meds.get(name) or {}
        if (d.get('confidence') or 0) >= 0.8: cls += [c for c in (d.get('drug_classes') or []) if c not in cls]
        for c in cls or [None]:
            rows.append({**r, 'source_stream': r.get('evidence'), 'drug_class': c})
    _insert(out, 'medication', rows)

    # Claims: adjudication events -> claim entity (semantic_layer._derived claim aggregate + settle_history).
    _insert(out, 'claim_event', g('claim'))
    out.execute("""INSERT INTO claim(claim_id, patient_id, payer, service_date, benefit_year, first_status, final_status, ever_denied,
          denied_then_paid, net_payer_paid_cents, billed_cents, allowed_cents, first_available_date, last_available_date,
          service_organization_id, versions)
        SELECT claim_id, max(patient_id), max(payer), max(service_date), max(substr(service_date,1,4)),
          max(CASE WHEN rn_first = 1 THEN status END), max(CASE WHEN rn_last = 1 THEN status END), max(status = 'denied'),
          coalesce(max(CASE WHEN status = 'paid' THEN ord END) > min(CASE WHEN status = 'denied' THEN ord END), 0),
          sum(CASE status WHEN 'paid' THEN coalesce(payer_paid_cents,0) WHEN 'reversed' THEN -abs(coalesce(payer_paid_cents,0)) ELSE 0 END),
          max(billed_cents), max(allowed_cents), min(available_date), max(available_date), max(service_organization_id), count(*)
        FROM (SELECT *, coalesce(available_date,'') || coalesce(available_time,'') || record_id AS ord,
                     row_number() OVER (PARTITION BY claim_id ORDER BY available_date, available_time, record_id) AS rn_first,
                     row_number() OVER (PARTITION BY claim_id ORDER BY available_date DESC, available_time DESC, record_id DESC) AS rn_last
              FROM claim_event WHERE claim_id IS NOT NULL)
        GROUP BY claim_id""")
    out.execute('UPDATE claim SET received_final_status = final_status')
    hist = defaultdict(list)
    for cid, rid, st, paid, orig in out.execute(
            """SELECT claim_id, record_id, status, payer_paid_cents, reverses_record_id FROM claim_event WHERE claim_id IS NOT NULL
               ORDER BY event_date, available_date, available_time, record_id"""):
        hist[cid].append({'record_id': rid, 'status': st, 'payer_paid_cents': paid, 'reverses': orig})
    out.executemany('UPDATE claim SET final_status = ?, live_payer_paid_cents = ? WHERE claim_id = ?',
                    [(*semantic_layer.settle_history(h), cid) for cid, h in hist.items()])

    rows = []
    claim_patient = dict(out.execute('SELECT claim_id, max(patient_id) FROM claim_event WHERE claim_id IS NOT NULL GROUP BY claim_id').fetchall())
    for r in g('claim_line'):
        if r.get('patient_id') is None and r.get('claim_id') in claim_patient:
            r = {**r, 'patient_id': claim_patient[r['claim_id']]}; stats['claim_line:patient_id_via_claim'] += 1
        code, system = r.get('service_code'), (r.get('code_system') or '')
        if not system and code: system = 'CPT' if codes.CPT.match(str(code)) else ('HCPCS' if codes.HCPCS.match(str(code)) else '')
        rows.append({**r, 'code_system': system or None, 'cpt': code if system == 'CPT' else None, 'hcpcs': code if system == 'HCPCS' else None,
                     'local_charge_code': code if system not in ('CPT', 'HCPCS') else None})
    _insert(out, 'claim_line', rows)
    out.executescript("""
      CREATE INDEX visit_patient ON visit(patient_id); CREATE INDEX condition_patient ON condition(patient_id, icd10);
      CREATE INDEX measurement_concept ON measurement(concept, patient_id); CREATE INDEX medication_class ON medication(drug_class, patient_id);
      CREATE INDEX claim_patient ON claim(patient_id); CREATE INDEX claim_line_claim ON claim_line(claim_id);""")
    receipt = {'version': VERSION, 'mapping_digest': digest({k: v for k, v in mapping.items() if k != 'generated_at'}),
               'model': model['schema'], 'source': mapping.get('source'), 'approval': mapping['approval'],
               'transform_stats': dict(stats),
               'rows': {t: out.execute(f'SELECT count(*) FROM {t}').fetchone()[0] for t in
                        ('patient', 'visit_event', 'visit', 'condition', 'measurement', 'measurement_latest', 'medication', 'claim_event',
                         'claim', 'claim_line', 'clinician', 'organization')}}
    out.executemany('INSERT INTO meta VALUES (?,?)', [('receipt', json.dumps(receipt, default=str)), ('version', VERSION)])
    out.commit(); out.close()
    tmp.replace(out_path)
    return receipt
