"""Shared paths, provider access and the fact-label contract for the note_fact bottling tools.

Data paths default to the main checkout's artifacts (gitignored, so absent from worktrees); override with AGERE_ARTIFACTS.
"""
import json, os, re, sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
ART = Path(os.environ.get('AGERE_ARTIFACTS') or (ROOT / 'artifacts' if (ROOT / 'artifacts').exists()
                                                 else Path('/Users/mikel/clinical-insights-local/artifacts')))
STACK = ART / 'agere-healthcare-v123/stack'
OUT = ART / 'notes-extraction'
LEDGER = ART / 'agere-full/provider-budget.sqlite3'
_env = Path(os.environ.get('AGERE_ENV_FILE', '/Users/mikel/insight-engine/.env'))
for line in (_env.read_text().splitlines() if _env.exists() else []):
    if '=' in line and not line.startswith('#'):
        k, v = line.split('=', 1); os.environ.setdefault(k.strip(), v.strip().strip('"'))

from clinical_core.agere.clinical import note_facts  # noqa: E402

FACT_TYPES = list(note_facts.FACT_VALUES)

LABEL_SYSTEM = """You extract clinical facts from synthetic EHR text for a population-analytics warehouse. Precision matters
more than recall, but list every fact that is plainly stated. For each input unit, return the facts it STATES about the
patient (or, with experiencer=family, about a relative).

Fact types and allowed values:
""" + '\n'.join(f'- {t}: {d}' for t, d in note_facts.FACT_DEFINITIONS.items()) + """

Rules:
- quote: the shortest EXACT substring of the unit text (copy characters verbatim) that supports the fact.
- negated=true when the text documents the finding as absent/denied (e.g. "no resting breathlessness", "edema none",
  "No acute fracture", "Transport difficulty reported: False"). Negated facts are wanted.
- SKIP conditional, hypothetical or instructional mentions: return precautions ("seek urgent assessment for new breathing
  difficulty"), "call if ...", things to ask about or review ("ask whether cost or transport has prevented collection",
  "review the pharmacy balance, cost and daily routine", "tell us about ... cost"), and general counselling. These are not facts.
- temporality: current (true at this encounter/report), historical (past, prior record, "earlier explanation on file",
  history list, prior report quoted), planned is NOT allowed (follow_up already encodes plans).
- experiencer: patient unless the text says the finding belongs to a family member.
- One fact per (fact_type, value, qualifier) per unit; do not repeat the same fact for the same quote.
- value must be one of the allowed values (numbers as plain numerals). qualifier per the definition, else null.
- If the unit contains no qualifying facts, return an empty list for it."""


def label_schema():
    fact = {'type': 'object', 'additionalProperties': False,
            'required': ['fact_type', 'value', 'qualifier', 'negated', 'temporality', 'experiencer', 'quote'],
            'properties': {'fact_type': {'type': 'string', 'enum': FACT_TYPES}, 'value': {'type': 'string'},
                           'qualifier': {'type': ['string', 'null']}, 'negated': {'type': 'boolean'},
                           'temporality': {'type': 'string', 'enum': ['current', 'historical']},
                           'experiencer': {'type': 'string', 'enum': ['patient', 'family']}, 'quote': {'type': 'string'}}}
    return {'type': 'object', 'additionalProperties': False, 'required': ['units'],
            'properties': {'units': {'type': 'array', 'items': {'type': 'object', 'additionalProperties': False,
                                                                'required': ['unit_id', 'facts'],
                                                                'properties': {'unit_id': {'type': 'string'},
                                                                               'facts': {'type': 'array', 'items': fact}}}}}}


def call(model, system, user, *, effort='medium', max_tokens=16000, schema=None):
    """One cached, ledgered structured-output call (dedupe by request hash: reruns are free)."""
    from clinical_core.agere.gateway import BudgetLedger
    from clinical_core.agere.clinical.context_student import send
    receipts = OUT / 'provider-receipts'; receipts.mkdir(parents=True, exist_ok=True)
    body = {'model': model, 'max_tokens': max_tokens,
            'system': [{'type': 'text', 'text': system, 'cache_control': {'type': 'ephemeral'}}],
            'messages': [{'role': 'user', 'content': user}],
            'output_config': {'effort': effort, 'format': {'type': 'json_schema', 'schema': schema or label_schema()}}}

    def parse(raw):
        if raw.get('stop_reason') != 'end_turn': raise ValueError('Incomplete: ' + str(raw.get('stop_reason')))
        return json.loads(''.join(b.get('text', '') for b in raw['content'] if b.get('type') == 'text'))
    ledger = BudgetLedger(LEDGER)
    try:
        value, receipt = send(body, 'claude', ledger, receipts, parse=parse, timeout=300)
    finally:
        ledger.db.close()
    usd = 0.0 if receipt.get('cache_hit') else ((receipt.get('cost_estimate') or {}).get('estimated_usd') or 0.0)
    return value, usd, bool(receipt.get('cache_hit'))


def units_payload(units):
    return json.dumps([{'unit_id': u['unit_id'], 'stream': u['stream'], 'field': u['field'], 'text': u['text']} for u in units],
                      ensure_ascii=False)


def read_jsonl(p):
    return [json.loads(l) for l in open(p) if l.strip()] if Path(p).exists() else []


def norm_value(fact_type, v):
    v = str(v).strip().lower()
    if fact_type in note_facts.NUMERIC_FACTS:
        m = re.search(r'-?\d+(?:\.\d+)?', v)
        if m:
            x = float(m.group(0)); return str(int(x)) if x == int(x) else str(x)
    return v.replace(' ', '_')
