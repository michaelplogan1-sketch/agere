"""Documentation context for the semantic analyst: the semantic model a customer would publish.

Shared by the product's semantic-analyst rung (together.py) and the bench's frontier_semantic baseline
(bench/semantic_context.py re-exports this module), so both send the same documentation.

Commercial text-to-SQL products (Databricks Genie spaces, Snowflake Cortex Analyst semantic models) give the model
documented, modeled tables, column descriptions, defined measures and verified example queries. This module assembles
the equivalent from Agere's DATA MODEL, read as text only:

- table/column documentation: SCHEMA_DOC, parsed from the source files with `ast` (no product module is imported);
- the concept library (condition / measurement / encounter code sets), parsed the same way from concepts.py;
- governed metric definitions (config/agere/metrics/*.json), measure specs (config/agere/measures/*.json) and
  definition defaults (config/agere/definitions.json), summarised as human-readable text;
- verified example question -> SQL pairs (config/agere/semantic_examples.json), written against the semantic layer and
  checked against development/challenge reference answers. Heldout and capability questions are never used.

No planner, compiler, router, engine, verifier or cache is called. The result is deterministic for a given repository
and semantic database, so the prompt prefix caches across questions.
"""
from __future__ import annotations

import ast
import json
from pathlib import Path

REPO = Path(__file__).resolve().parents[3]
CLINICAL = REPO / 'clinical_core/agere/clinical'
CONFIG = REPO / 'config/agere'
EXAMPLES_PATH = CONFIG / 'semantic_examples.json'
HELDOUT_PREFIXES = ('heldout',)
# Splits an example may cite: development and challenge are visible development artifacts. Heldout sets (heldout*),
# bench/capability and any other scored benchmark split are never a source.
ALLOWED_SPLITS = ('development', 'challenge')


class _Literal:
    """Evaluate a module's literal assignments (dicts, lists, strings, numbers, names of earlier literals, arithmetic)."""

    def __init__(self, path):
        self.path = Path(path)
        self.tree = ast.parse(self.path.read_text())
        self.names = {}
        for node in self.tree.body:
            if isinstance(node, ast.Assign) and len(node.targets) == 1 and isinstance(node.targets[0], ast.Name):
                try:
                    self.names[node.targets[0].id] = self.eval(node.value)
                except (ValueError, TypeError, ZeroDivisionError):
                    pass

    def eval(self, node):
        if isinstance(node, ast.Constant):
            return node.value
        if isinstance(node, ast.Tuple):
            return tuple(self.eval(e) for e in node.elts)
        if isinstance(node, (ast.List, ast.Set)):
            return [self.eval(e) for e in node.elts]
        if isinstance(node, ast.Name) and node.id in self.names:
            return self.names[node.id]
        if isinstance(node, ast.BinOp) and isinstance(node.op, (ast.Add, ast.Mult, ast.Div, ast.Sub)):
            left, right = self.eval(node.left), self.eval(node.right)
            ops = {ast.Add: lambda a, b: a + b, ast.Mult: lambda a, b: a * b,
                   ast.Div: lambda a, b: a / b, ast.Sub: lambda a, b: a - b}
            return ops[type(node.op)](left, right)
        if isinstance(node, ast.UnaryOp) and isinstance(node.op, ast.USub):
            return -self.eval(node.operand)
        if isinstance(node, ast.Dict):
            out = {}
            for key, value in zip(node.keys, node.values):
                if key is None:  # **module.SCHEMA_DOC: another module's literal of the same name
                    if (isinstance(value, ast.Attribute) and isinstance(value.value, ast.Name)
                            and (self.path.parent / f'{value.value.id}.py').is_file()):
                        out.update(_Literal(self.path.parent / f'{value.value.id}.py').names.get(value.attr, {}))
                        continue
                    raise ValueError('Unsupported dict expansion')
                out[self.eval(key)] = self.eval(value)
            return out
        raise ValueError(f'Non-literal expression in {self.path.name}: {ast.dump(node)[:80]}')


def schema_doc():
    return _Literal(CLINICAL / 'semantic_layer.py').names['SCHEMA_DOC']


def concept_library():
    names = _Literal(CLINICAL / 'concepts.py').names
    return {k: names[k] for k in ('CONDITIONS', 'MEASUREMENTS', 'ENCOUNTERS', 'CONDITION_STREAMS', 'MEASUREMENT_STREAMS')}


def _tables_section(columns, docs):
    lines = ['## Tables (semantic layer). Columns are exact; descriptions are the data model documentation.']
    for name in sorted(columns):
        lines.append(f'### {name}({", ".join(columns[name])})')
        if docs.get(name):
            lines.append(docs[name])
    topics = sorted(k for k in docs if k not in columns)
    if topics:
        lines.append('## Cross-table documentation topics')
        for k in topics:
            lines.append(f'- {k}: {docs[k]}')
    return '\n'.join(lines)


def _concepts_section(library, drug_classes):
    out = ['## Concept library (concept ids used in condition.concept, measurement.concept, encounter/visit.type_concept, '
           'concept_patient.concept). Documentation-level phenotypes, not certified value sets.',
           '### Condition concepts: icd10 = code prefixes; text = case-insensitive regex over received labels; '
           'exclude = labels removed even if text matches']
    for k, v in library['CONDITIONS'].items():
        out.append(f"- {k}: {v['label']}; icd10 {','.join(v.get('icd10', []))}; text /{v.get('text', '')}/"
                   + (f"; exclude /{v['exclude']}/" if v.get('exclude') else ''))
    out.append('### Measurement concepts: canonical unit; LOINC; name regex over labs.test|name / vitals.measurement; '
               'accepted unit -> factor to canonical')
    for k, v in library['MEASUREMENTS'].items():
        units = ', '.join(f'{u}->{round(f, 6)}' for u, f in v.get('units', {}).items())
        out.append(f"- {k}: {v['label']} [{v['unit']}]; loinc {','.join(v.get('loinc', [])) or '-'}; "
                   f"names /{v.get('names', '')}/; units {units}" + (f"; vitals columns {','.join(v['wide'])}" if v.get('wide') else ''))
    out.append('### Encounter concepts: per-stream field regexes over received values')
    for k, v in library['ENCOUNTERS'].items():
        fields = '; '.join(f'{s}.{f} /{p}/' for s, fs in v['fields'].items() for f, p in fs.items())
        out.append(f"- {k}: {v['label']}; {fields}")
    out.append('### Source fields bound to concepts: ' + json.dumps({'conditions': library['CONDITION_STREAMS'],
                                                                      'measurements': library['MEASUREMENT_STREAMS']}))
    if drug_classes:
        groups = {}
        for drug_class, group in drug_classes:
            groups.setdefault(drug_class, [])
            if group:
                groups[drug_class].append(group)
        out.append('### medication.drug_class ids (groups): ' +
                   '; '.join(f"{c} ({', '.join(g)})" if g else c for c, g in sorted(groups.items())))
    return '\n'.join(out)


def _compact(value, limit=600):
    text = value if isinstance(value, str) else json.dumps(value, sort_keys=True)
    return text if len(text) <= limit else text[:limit] + ' ...'


def _metrics_section():
    out = ['## Governed metric definitions (config/agere/metrics). Definitions and SQL templates are documentation: '
           "'{period_start}'/'{period_end}' are placeholders you fill (default period = the as-of calendar year unless "
           'the question states one). Use them when a question asks for that metric; otherwise answer the question as asked.']
    for path in sorted((CONFIG / 'metrics').glob('*.json')):
        data = json.loads(path.read_text())
        out.append(f"### Domain {data.get('domain', path.stem)}")
        for m in data.get('metrics', []):
            parts = [f"- {m['id']}: {m.get('name', '')}. {m.get('summary', '')}"]
            for key in ('population', 'denominator', 'numerator'):
                if isinstance(m.get(key), dict) and m[key].get('summary'):
                    parts.append(f"  {key}: {m[key]['summary']}")
            if m.get('grain'):
                parts.append(f"  grain: {_compact(m['grain'])}; unit: {m.get('unit')}; returns: {(m.get('output') or {}).get('returns')}")
            if m.get('time_anchor'):
                parts.append(f"  time anchor: {_compact(m['time_anchor'])}")
            if m.get('exclusions'):
                parts.append(f"  exclusions: {_compact(m['exclusions'], 400)}")
            if m.get('value_sets'):
                parts.append(f"  value sets: {_compact({k: {f: v[f] for f in v if f != 'provenance'} for k, v in m['value_sets'].items()}, 700)}")
            if m.get('variants'):
                parts.append('  variants: ' + '; '.join(f"{k}: {v.get('summary', '')}" for k, v in m['variants'].items()))
            if m.get('sql_template'):
                parts.append(f"  SQL template: {m['sql_template']}")
            out.append('\n'.join(parts))
    return '\n'.join(out)


def _measures_section():
    out = ['## Quality measure specifications (config/agere/measures; approximations of public specs, not certified)']
    for path in sorted((CONFIG / 'measures').glob('*.json')):
        m = json.loads(path.read_text())
        if not m.get('id'):
            continue
        parts = [f"- {m['id']}: {m.get('name')} ({m.get('status')}; {m.get('direction', '')}; unit {m.get('unit')})"]
        for key in ('denominator', 'numerator'):
            spec = m.get(key) or {}
            crit = '; '.join(c.get('label', '') + (f" [{_compact({k: c[k] for k in c if k not in ('label', 'type')}, 260)}]")
                             for c in spec.get('criteria', []))
            parts.append(f"  {key}: {spec.get('summary', '')}" + (f" Criteria: {crit}" if crit else ''))
        if m.get('exclusions'):
            parts.append('  exclusions: ' + '; '.join(e.get('label', '') for e in m['exclusions']))
        for note in m.get('notes', []):
            parts.append(f'  note: {note}')
        out.append('\n'.join(parts))
    return '\n'.join(out)


def _definitions_section():
    data = json.loads((CONFIG / 'definitions.json').read_text())
    out = ['## Definition defaults (config/agere/definitions.json: the organisation\'s adopted definition per term)']
    for k, v in data.get('definitions', {}).items():
        out.append(f"- {k}: {v.get('label', '')}: {v.get('agere_default', '')}")
    return '\n'.join(out)


def load_examples(path=EXAMPLES_PATH):
    data = json.loads(Path(path).read_text())
    for e in data['examples']:
        split = str(e.get('source_split', ''))
        if not e.get('source_id') or split.startswith(HELDOUT_PREFIXES) or split not in ALLOWED_SPLITS:
            raise ValueError('Examples must cite a development/challenge source and never a heldout or capability split')
    return data['examples']


def _examples_section(examples):
    out = ['## Verified example queries (question -> SQL over this semantic layer; each was checked against an '
           'independent reference answer). Reuse their patterns; adapt the definitions to what each new question asks.']
    for e in examples:
        out.append(f"### Q: {e['question']}\nanswer_type: {e['answer_type']}"
                   + (f"\nnote: {e['note']}" if e.get('note') else '')
                   + (f"\nSQL:\n{e['sql']}" if e.get('sql') else f"\nanswer:\n{e['answer']}"))
    return '\n'.join(out)


# Canonical analytic conventions (docs/agere/CANONICAL_MODEL.md; build log step 7). Standard healthcare-data rules the
# analyst applies unless the question states its own definition, which always wins.
CONVENTIONS = """## Canonical conventions (apply unless the question defines otherwise; the question's own definition wins)
- Scope: plain questions mean every patient in the warehouse (deep-chart panel + population), duplicate charts counted
  once as their person.
- Payer medical claims (payer_claim, payer_claim_line, claim_adjudication): a claim's status is its LAST adjudication
  (final_status; order adjudications by sequence, not arrival). A frequency-7 replacement supersedes the original; a
  frequency-8 void ends it; reversals and recoupments carry negative paid amounts. Use rows with counts_in_metrics = 1.
  - Paid dollars in a period: sum paid amounts by paid date, reversals/recoupments netting out.
  - Initial denial rate: FIRST adjudication of original claims (first_status). Denials "dated" in a year: the denial
    adjudication's date. An upheld appeal repeats the CARC and is not a second denial; count distinct claims.
  - Days to payment: final paid adjudication's paid_date minus service_from.
  - Adjudication HISTORY lives in payer_adjudication: every received adjudication version, superseded ones included
    (sequence, claim_status, carc, adjudication_date, paid_date, signed paid_cents, is_first, is_last, superseded).
    payer_claim, claim and record hold only the CURRENT state, so a denial later reworked and paid is visible only in
    payer_adjudication. Use it for denials or CARCs dated in a period, initial denials (is_first) and overturned denials.
  - Facility settings come from claim lines: revenue 045x = ED, 0762 or HCPCS G0378/G0379 = observation; TOB 011x =
    inpatient, 013x = hospital outpatient; POS 23 ED, 21 inpatient, 22 outpatient, 11 office, 02/10 telehealth.
- Visits: one real visit = one person + setting + calendar date across EHR (visit), ADT (adt_visit) and claims; stays span
  admission..discharge (stay). An ED visit that leads to admission still counts as an ED visit.
- Pharmacy fills: a fill counts only when picked up (medication.picked_up = 1, payer_pharmacy_claim.counts_as_fill = 1):
  paid and not later reversed/recouped, or dispensed and not returned to stock (returned_fill). Class from NDC/RxNorm
  (medication.drug_class), falling back to the ingredient name. Filter classes by drug_class / therapeutic_category,
  never by drug-name substrings ('%statin%' also matches nystatin).
  - New user of a class: a fill with no fill of that class in the prior 180 days AND at least 180 days of data before it.
  - Restart/gap: days between the end of one fill's supply (fill_date + days_supply - 1) and the next fill of the class.
  - Switch: class A's last fill followed within 90 days by class B's first-ever fill in the same therapeutic group, no
    later A. Add-on: a new class's first fill within 90 days of an existing class fill.
  - PDC: >= 2 fills of the class in the year; covered days from the first fill to period end, overlaps de-duplicated.
- Conditions (condition, payer_claim_diagnosis, encounter_diagnosis, problem_list_entry): default = any documentation
  (paid or denied claim, problem list, encounter diagnosis, ADT diagnosis); name another rule if the question asks.
- Labs and vitals: corrected results replace originals; latest value = latest specimen date (measurement_latest; use the
  question's tie-break, else the highest); censored strings keep their comparator (value_censor).
- Corrections and restated records: record_correction has every received record that a later record supersedes, across
  all streams (stream, superseded_by, link_field = supersedes_record_id or, for lab corrections, replaces_result_record_id, dates). record holds current versions only; current_record.versions counts
  versions per business id.
"""


def _conventions_section():
    return CONVENTIONS


def build_context(columns, streams, drug_classes, snapshot, examples):
    """The documentation block of the system prompt (stable across questions for one build)."""
    stream_list = ', '.join(f'{s} ({n})' for s, n in streams)
    return '\n\n'.join([
        '# Semantic model documentation',
        f"Snapshot: semantic layer {snapshot.get('version')} built from the received store as of {snapshot.get('as_of')}.",
        _tables_section(columns, schema_doc()),
        '## record.stream values (received records per stream in this snapshot)\n' + stream_list,
        _concepts_section(concept_library(), drug_classes),
        _definitions_section(),
        _conventions_section(),
        _metrics_section(),
        _measures_section(),
        _examples_section(examples),
    ])
