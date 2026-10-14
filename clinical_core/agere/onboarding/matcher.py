"""Schema matching: propose source -> semantic-model mappings with scores, evidence and transforms.

Score of (target field, source column) =
    0.40 * name     (token overlap with the field name/aliases/description; blended with local MiniLM cosine when available)
  + 0.35 * value    (profiled value shape and detected code system vs the field's semantic type / code system)
  + 0.25 * structure(key uniqueness, inclusion-dependency joins to the mapped patient/organization/clinician tables)
Tables are assigned to entities by the summed field scores plus a table-name term; reference entities
(patient, organization, clinician) are mapped first so foreign keys can use join evidence.
The output is a reviewable mapping document. Nothing is applied: apply.py requires approval.
"""
from datetime import datetime, timezone
from pathlib import Path
import math

from . import codes, contract as contract_mod
from .profile import tokens

WEIGHTS = {'name': 0.40, 'value': 0.35, 'structure': 0.25}
ACCEPT_THRESHOLD = 0.60
FIELD_FLOOR = 0.42
TABLE_FLOOR = 0.45
GENERIC = {'source', 'value', 'concept', 'the', 'of', 'a', 'as', 'by', 'and', 'or', 'when'}
REFERENCE = ('patient', 'organization', 'clinician')
SEX_SYNONYMS = {'female': 'F', 'f': 'F', 'woman': 'F', 'w': 'F', '2': 'F', 'male': 'M', 'm': 'M', 'man': 'M', '1': 'M',
                'unknown': 'U', 'u': 'U', 'other': 'U', 'unk': 'U', 'not reported': 'U'}
HOSTED_MODEL = {'enabled': False, 'note': 'Optional hosted-model reviewer hook. OFF by default; when enabled a caller must pass '
                                          'a callable(prompt)->str. Never called from tests or the proof run.'}


def _phrases(field, spec, entity, use_aliases=True):
    out = [tokens(field)]
    if use_aliases:
        out += [tokens(a) for a in spec.get('aliases', [])]
    return [[t for t in p if t not in GENERIC] or p for p in out]


LOW_INFORMATION = {'id', 'date', 'name', 'code', 'number', 'type', 'time', 'key'}  # count half: they appear in most column names


def _skeleton(word):
    """First letter + remaining consonants with repeats collapsed: received -> rcvd, gender -> gndr, allowed -> alwd."""
    out = word[:1]
    for ch in word[1:]:
        if ch in 'aeiou' or ch == out[-1]: continue
        out += ch
    return out


def _token_match(a, b):
    """Exact, abbreviation-by-prefix (adjud ~ adjudication) or consonant-skeleton (rslt ~ result) token match."""
    if a == b or {a, b} == {'id', 'key'}: return True
    if not (a.isalpha() and b.isalpha()): return False
    short, long_ = sorted((a, b), key=len)
    if len(short) >= 3 and long_.startswith(short): return True
    return len(short) >= 2 and short[0] == long_[0] and short == _skeleton(short) and _skeleton(long_) == short


def _lexical(col_tokens, phrases, informative=False):
    ct = [t for t in col_tokens if t not in GENERIC] or col_tokens
    w = lambda t: 0.5 if t in LOW_INFORMATION else 1.0
    best = 0.0
    for p in phrases:
        ps = list(dict.fromkeys(p))
        if not ps: continue
        hit_p = [t for t in ps if any(_token_match(c, t) for c in ct)]
        hit_c = [c for c in dict.fromkeys(ct) if any(_token_match(c, t) for t in ps)]
        if not hit_p or (informative and not set(hit_p) - LOW_INFORMATION): continue
        recall = sum(map(w, hit_p)) / sum(map(w, ps))
        union = sum(map(w, ps)) + sum(w(c) for c in dict.fromkeys(ct) if c not in hit_c)
        jacc = sum(map(w, hit_p)) / union
        best = max(best, 0.65 * recall + 0.35 * jacc)
    return best


class Encoder:
    """Optional local sentence encoder (pinned MiniLM). Falls back to lexical-only when unavailable."""

    def __init__(self, enabled=True):
        self.model = None
        self.reason = 'disabled'
        if not enabled: return
        path = next((p / 'models' / 'minilm-l6-v2' for p in [contract_mod.ROOT, *contract_mod.ROOT.parents]
                     if (p / 'models' / 'minilm-l6-v2' / 'agere-model.json').is_file()), None)
        if path is None:
            self.reason = 'local encoder not installed'; return
        try:
            from ...local_encoders import LocalEncoder
            cache = contract_mod.onboarding_dir() / 'encoder-cache.sqlite3'
            cache.parent.mkdir(parents=True, exist_ok=True)
            self.model = LocalEncoder(path, pooling='mean', cache=cache, threads=2)
            self.reason = f'local MiniLM at {path}'
        except Exception as e:  # noqa: BLE001 - lexical fallback, never a broken match
            self.reason = f'encoder unavailable: {type(e).__name__}: {e}'
        self._cache = {}

    def vectors(self, texts):
        missing = [t for t in dict.fromkeys(texts) if t not in self._cache]
        if missing:
            for t, v in zip(missing, self.model.encode(missing, batch_size=16)): self._cache[t] = v
        return [self._cache[t] for t in texts]

    def similarity(self, a, b):
        va, vb = self.vectors([a, b])
        return float((va * vb).sum())


def _value_score(spec, col):
    sem, code = col['semantic'], col['code']['system']
    want = spec['semantic']
    if col['null_rate'] is not None and col['null_rate'] >= 0.999: return 0.35, 'column is empty in the sample (name evidence only)'
    if want == 'code':
        target = spec.get('code_system', '')
        if code and (code == target or (target == 'CPT/HCPCS' and code in ('CPT', 'HCPCS', 'CPT/HCPCS'))):
            return 1.0, f'values match {code} (valid {col["code"]["valid_share"]:.0%})'
        if code: return 0.05, f'values look like {code}, not {target}'
        if target in ('SNOMED CT', 'NUCC taxonomy', 'TIN') and sem in ('identifier', 'categorical', 'integer', 'code', 'empty'):
            return 0.55, f'{target} not pattern-detected; identifier-shaped values'
        return 0.25, f'no {target} pattern detected'
    if spec.get('code_system') and want != 'code':
        if code and code != spec['code_system'] and not (spec['code_system'] == 'ZIP'):
            return 0.1, f'values look like {code}'
    table = {
        'date': {'date': 1.0, 'datetime': 0.85},
        'datetime': {'datetime': 1.0, 'date': 0.5},
        'money_cents': {'money_cents': 1.0, 'money_dollars': 1.0, 'integer': 0.45, 'decimal': 0.45},
        'decimal': {'decimal': 1.0, 'integer': 0.85, 'money_dollars': 0.4, 'money_cents': 0.4},
        'integer': {'integer': 1.0, 'decimal': 0.4, 'boolean': 0.5},
        'categorical': {'categorical': 1.0, 'text': 0.55, 'identifier': 0.45, 'boolean': 0.5, 'code': 0.3, 'integer': 0.25},
        'identifier': {'identifier': 1.0, 'integer': 0.8, 'code': 0.3, 'categorical': 0.6, 'text': 0.3},
        'text': {'text': 1.0, 'categorical': 0.75, 'identifier': 0.35, 'integer': 0.4, 'date': 0.5},
    }.get(want, {})
    s = table.get(sem, 0.0)
    note = f'{sem} values for a {want} field'
    if want == 'identifier' and col['unique'] and col['storage'] == 'text' and not code and (
            sem in ('identifier', 'categorical') or col.get('digit_share', 0) >= 0.9):
        s, note = max(s, 0.95), 'unique text values for an identifier field'
    if spec.get('allowed_values') and col['top_values']:
        vals = [v for v, _ in col['top_values']]
        allowed = set(spec['allowed_values'])
        direct = sum(1 for v in vals if v in allowed or str(v).lower().replace(' ', '_') in allowed) / len(vals)
        mapped = sum(1 for v in vals if SEX_SYNONYMS.get(str(v).lower()) in allowed) / len(vals) if allowed <= {'F', 'M', 'U'} else 0
        if max(direct, mapped) >= 0.8:
            s = min(1.0, s + 0.25); note += '; values within the contract vocabulary' + (' after value map' if mapped > direct else '')
    if want in ('date', 'datetime') and col['semantic'] in ('date', 'datetime') and col.get('null_rate', 0) > 0:
        note += f'; {col["null_rate"]:.0%} null'
    return s, note


def _structure_score(field, spec, col, table, prof, ref_tables, joins_by_from):
    """Key and foreign-key evidence. ref_tables: entity -> (table, key column) already mapped."""
    if spec.get('key') and field != 'record_id':
        if not (col['unique'] and col['null_rate'] == 0): return 0.15, 'not unique'
        if col['storage'] == 'integer': return 0.7, 'unique integer surrogate (Agere keeps the source identifier when one exists)'
        return 1.0, 'unique, non-null text identifier in sample'
    if field == 'record_id':
        if col['unique'] and col['null_rate'] == 0 and col['semantic'] in ('identifier', 'code', 'text'):
            return 0.9, 'unique non-null text identifier (lineage key)'
        return 0.1, 'not a unique identifier'
    fk = spec.get('foreign_key')
    if fk:
        ent = fk.split('.')[0]
        if ent in ref_tables:
            rt, rk = ref_tables[ent]
            hits = [j for j in joins_by_from.get(f"{table}.{col['name']}", []) if j['to'].split('.')[0] == rt]
            if hits:
                best = max(hits, key=lambda j: (j['name_match'], j['to'] == f'{rt}.{rk}', j['inclusion_share']))
                s = 1.0 if best['name_match'] else 0.6
                return s, f"joins {best['to']} ({best['inclusion_share']:.0%} of values{', name agrees' if best['name_match'] else ''})"
            return 0.0, f'no join to {rt}'
        return 0.4, 'reference entity not mapped'
    if field in ('encounter_id', 'claim_id') and col['semantic'] in ('identifier', 'code', 'integer', 'categorical'):
        return (0.8, f"repeats across rows (distinct ratio {col['distinct_ratio']}) - version key") if not col['unique'] else (0.6, 'unique')
    return 0.5, ''


def _transforms(field, spec, col):
    out = []
    sem = col['semantic']
    if spec['semantic'] == 'money_cents' and sem == 'money_dollars':
        out.append({'op': 'dollars_to_cents', 'why': f"decimal values (max {col.get('numeric', {}).get('max_decimals')} decimals) for a cents field"})
    if spec['semantic'] == 'date' and sem == 'datetime':
        out.append({'op': 'to_date', 'why': 'datetime values for a date field'})
    elif spec['semantic'] == 'date' and sem == 'date' and any(not codes.ISO_DATE.match(str(v)) for v, _ in col['top_values']):
        out.append({'op': 'to_date', 'why': 'non-ISO date spelling'})
    if spec.get('code_system') == 'ICD-10-CM' and any('.' not in str(v) and len(str(v)) > 3 for v, _ in col['top_values']):
        out.append({'op': 'icd10_normalize', 'why': 'ICD-10 codes without the dot'})
    if spec.get('code_system') == 'NDC' and any('-' in str(v) for v, _ in col['top_values']):
        out.append({'op': 'ndc_normalize', 'why': 'hyphenated NDC'})
    if spec['type'] == 'text' and col['storage'] in ('integer', 'real') and spec['semantic'] in ('code', 'identifier', 'categorical'):
        out.append({'op': 'to_text', 'why': f"{col['storage']} storage for a text field"})
    vals = [str(v) for v, _ in col['top_values']]
    allowed = spec.get('allowed_values')
    if allowed and vals and not all(v in allowed for v in vals):
        if set(allowed) <= {'F', 'M', 'U'}:
            out.append({'op': 'value_map', 'map': {v: SEX_SYNONYMS.get(v.lower()) for v in vals}, 'why': 'source vocabulary differs'})
        elif all(v.lower().replace(' ', '_') in allowed for v in vals):
            out.append({'op': 'value_map', 'map': {v: v.lower().replace(' ', '_') for v in vals}, 'why': 'case/spacing differs from the contract vocabulary'})
        else:
            out.append({'op': 'review', 'why': 'values outside the contract vocabulary: ' + ', '.join(v for v in vals if v not in allowed)[:200]})
    return out


def _candidates(entity, espec, table, prof, ref_tables, joins_by_from, encoder, use_aliases):
    scores = {}
    for field, spec in espec['fields'].items():
        if spec['role'] != 'mapped': continue
        phrases = _phrases(field, spec, entity, use_aliases)
        tt = set(prof['tokens'])
        row = []
        for cname, col in prof['columns'].items():
            nr = bool(spec.get('name_required'))
            lex = max(_lexical(col['tokens'], phrases, nr), _lexical([t for t in col['tokens'] if t not in tt], phrases, nr))
            name = lex
            enc = None
            if encoder is not None and encoder.model is not None:
                a = f"{table} {' '.join(col['tokens'])}"
                b = f"{entity} {field.replace('_', ' ')}: {spec.get('description', '')}"
                enc = max(0.0, encoder.similarity(a, b))
                name = 0.6 * lex + 0.4 * enc
            v, vnote = _value_score(spec, col)
            st, snote = _structure_score(field, spec, col, table, prof, ref_tables, joins_by_from)
            if v <= 0.05: st = min(st, 0.2)
            s = WEIGHTS['name'] * name + WEIGHTS['value'] * v + WEIGHTS['structure'] * st
            if nr and lex < 0.5: s = min(s, FIELD_FLOOR - 0.01)  # receipt clocks / lineage ids are only mapped when named as such
            row.append((round(s, 4), cname, {'name': round(lex, 3), 'encoder': None if enc is None else round(enc, 3),
                                              'value': round(v, 3), 'value_evidence': vnote, 'structure': round(st, 3),
                                              'structure_evidence': snote}))
        row.sort(key=lambda x: -x[0])
        scores[field] = row
    return scores


def _assign(scores, espec):
    """Greedy one-to-one assignment of columns to fields by descending score."""
    pairs = sorted(((s, f, c, ev) for f, row in scores.items() for s, c, ev in row if s >= FIELD_FLOOR), key=lambda x: -x[0])
    used_f, used_c, out = set(), set(), {}
    for s, f, c, ev in pairs:
        if f in used_f or c in used_c: continue
        out[f] = (s, c, ev); used_f.add(f); used_c.add(c)
    return out


def _table_score(entity, espec, table, prof, assignment):
    req = [f for f, s in espec['fields'].items() if s['role'] == 'mapped' and s['required']]
    opt = [f for f, s in espec['fields'].items() if s['role'] == 'mapped' and not s['required']]
    r = sum(assignment[f][0] for f in req if f in assignment) / max(1, len(req))
    o = sum(assignment[f][0] for f in opt if f in assignment) / max(1, len(opt))
    name = _lexical(prof['tokens'], [tokens(entity)] + [tokens(w) for w in espec.get('grain', '').split() if len(w) > 4])
    missing = [f for f in req if f not in assignment]
    return round(0.5 * r + 0.35 * o + 0.15 * name - 0.2 * len(missing), 4), missing


def propose(model, profile, *, encoder=None, use_aliases=True, accept_threshold=ACCEPT_THRESHOLD, hosted_model=None):
    if hosted_model is not None and not HOSTED_MODEL['enabled']:
        raise RuntimeError('Hosted-model hook is OFF by default; enable it explicitly in matcher.HOSTED_MODEL first')
    targets = contract_mod.targets(model)
    tables = profile['tables']
    joins_by_from = {}
    for j in profile['joins']: joins_by_from.setdefault(j['from'], []).append(j)
    ref_tables, chosen, used_tables = {}, {}, set()
    order = [e for e in REFERENCE if e in targets] + [e for e in targets if e not in REFERENCE]
    # Phase 1 reference entities (no FK evidence yet), phase 2 the rest with join evidence to them. A reference entity
    # may only take a table that it fits better than any fact entity does (a claim header is not an organization table).
    best_fact = {}
    for entity in order[len(REFERENCE):]:
        for t, prof in tables.items():
            sc = _candidates(entity, targets[entity], t, prof, {}, joins_by_from, encoder, use_aliases)
            best_fact[t] = max(best_fact.get(t, 0.0), _table_score(entity, targets[entity], t, prof, _assign(sc, targets[entity]))[0])
    for phase in (order[:len(REFERENCE)], order[len(REFERENCE):]):
        options = []
        for entity in phase:
            espec = targets[entity]
            for t, prof in tables.items():
                if t in used_tables: continue
                sc = _candidates(entity, espec, t, prof, ref_tables, joins_by_from, encoder, use_aliases)
                asg = _assign(sc, espec)
                ts, missing = _table_score(entity, espec, t, prof, asg)
                options.append((ts, entity, t, asg, sc, missing))
        options.sort(key=lambda x: -x[0])
        alternatives = {}
        for ts, entity, t, asg, sc, missing in options:
            alternatives.setdefault(entity, []).append({'table': t, 'score': ts})
        for ts, entity, t, asg, sc, missing in options:
            if entity in chosen or t in used_tables or ts < TABLE_FLOOR: continue
            if entity in REFERENCE and best_fact.get(t, 0.0) > ts: continue
            chosen[entity] = (ts, t, asg, sc, missing, alternatives[entity][:3])
            used_tables.add(t)
            if entity in REFERENCE:
                key = next((f for f, s in targets[entity]['fields'].items() if s.get('key')), None)
                if key in asg: ref_tables[entity] = (t, asg[key][1])
        for entity in phase:
            if entity not in chosen: chosen[entity] = None
    entities = {}
    for entity in order:
        espec = targets[entity]
        if chosen.get(entity) is None:
            entities[entity] = {'source_table': None, 'confidence': 0.0, 'status': 'unmapped',
                                'evidence': ['no source table scored above the table floor'], 'fields': {}}
            continue
        ts, t, asg, sc, missing, alts = chosen[entity]
        prof = tables[t]
        fields = {}
        for field, spec in espec['fields'].items():
            if spec['role'] != 'mapped': continue
            if field not in asg:
                fields[field] = {'source': None, 'confidence': 0.0, 'accept': False, 'required': spec['required'],
                                 'alternatives': [{'column': c, 'score': s} for s, c, _ in sc[field][:3]]}
                continue
            s, c, ev = asg[field]
            col = prof['columns'][c]
            entry = {'source': c, 'confidence': s, 'accept': s >= accept_threshold, 'required': spec['required'],
                     'evidence': ev, 'transforms': _transforms(field, spec, col),
                     'alternatives': [{'column': cc, 'score': ss} for ss, cc, _ in sc[field][:3] if cc != c][:2]}
            fk = spec.get('foreign_key')
            if fk and fk.split('.')[0] in ref_tables:
                rt, rk = ref_tables[fk.split('.')[0]]
                to = [j for j in joins_by_from.get(f'{t}.{c}', []) if j['to'].split('.')[0] == rt]
                to = max(to, key=lambda j: (j['name_match'], j['inclusion_share'])) if to else None
                if to and to['to'] != f'{rt}.{rk}':
                    entry['transforms'].append({'op': 'lookup', 'table': rt, 'key': to['to'].split('.')[1], 'value': rk,
                                                'why': f"{c} holds {to['to']} surrogate keys; Agere keeps {rt}.{rk}"})
            fields[field] = entry
        ver = espec.get('versioning')
        rule = None
        if ver and ver['rule'] in ('latest_version', 'claim_settlement') and ver['key'] in fields and fields[ver['key']]['source']:
            kcol = prof['columns'][fields[ver['key']]['source']]
            rule = {**ver, 'evidence': f"{fields[ver['key']]['source']} distinct ratio {kcol['distinct_ratio']} "
                                      f"({'repeats: versions present' if kcol['distinct_ratio'] < 1 else 'no repeats in sample'})"}
        elif ver:
            rule = dict(ver)
        entities[entity] = {'source_table': t, 'confidence': ts, 'status': 'proposed', 'grain': espec['grain'],
                            'missing_required': missing, 'table_alternatives': alts, 'versioning': rule, 'fields': fields}
    mapped_tables = {e['source_table'] for e in entities.values() if e['source_table']}
    return {'schema': 'agere-onboarding-mapping/1', 'model': model['schema'],
            'generated_at': datetime.now(timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ'),
            'source': profile['connector'],
            'matcher': {'weights': WEIGHTS, 'accept_threshold': accept_threshold, 'field_floor': FIELD_FLOOR, 'table_floor': TABLE_FLOOR,
                        'encoder': None if encoder is None else encoder.reason, 'aliases': use_aliases, 'hosted_model': HOSTED_MODEL['enabled']},
            'approval': {'approved': False, 'reviewer': None, 'approved_at': None,
                         'instructions': 'Review every field. Set accept: true/false per field, fix source/transforms, then set approved: true.'},
            'entities': entities,
            'unmapped_source_tables': sorted(set(tables) - mapped_tables)}


def save(mapping, path):
    import json
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.suffix in ('.yaml', '.yml'):
        import yaml
        path.write_text(yaml.safe_dump(mapping, sort_keys=False, width=140))
    else:
        path.write_text(json.dumps(mapping, indent=1) + '\n')
    return path


def load(path):
    import json
    path = Path(path)
    if path.suffix in ('.yaml', '.yml'):
        import yaml
        return yaml.safe_load(path.read_text())
    return json.loads(path.read_text())


def score_against_truth(mapping, truth):
    """Field-level precision/recall of accepted proposals vs a known truth {entity: (table, {field: column})}."""
    tp = fp = 0
    wrong, missed = [], []
    for entity, ent in mapping['entities'].items():
        t_table, t_fields = truth.get(entity, (None, {}))
        for field, f in ent['fields'].items():
            if not f.get('accept') or not f.get('source'): continue
            if ent['source_table'] == t_table and t_fields.get(field) == f['source']:
                tp += 1
            else:
                fp += 1; wrong.append(f"{entity}.{field} <- {ent['source_table']}.{f['source']} (truth {t_table}.{t_fields.get(field)})")
    total = sum(len(v[1]) for v in truth.values())
    for entity, (t_table, t_fields) in truth.items():
        ent = mapping['entities'].get(entity, {'fields': {}, 'source_table': None})
        for field, col in t_fields.items():
            f = ent['fields'].get(field) or {}
            if not (f.get('accept') and ent['source_table'] == t_table and f.get('source') == col):
                missed.append(f'{entity}.{field} <- {t_table}.{col}')
    precision = tp / (tp + fp) if tp + fp else 0.0
    recall = tp / total if total else 0.0
    return {'precision': round(precision, 4), 'recall': round(recall, 4), 'true_positive': tp, 'false_positive': fp,
            'truth_pairs': total, 'wrong': wrong, 'missed': missed,
            'f1': round(2 * precision * recall / (precision + recall), 4) if precision + recall else 0.0}
