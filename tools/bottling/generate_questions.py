"""Bottling step 1: a frontier model writes diverse analyst questions over Agere's semantic layer.

Questions are training material for the local plan model, so they must not reuse benchmark questions:
any generated question too similar to a bench question (token Jaccard >= 0.5) is dropped.
Usage: .venv/bin/python tools/bottling/generate_questions.py <out.jsonl> [per_area]
"""
import json, os, re, sys
from pathlib import Path
from concurrent.futures import ThreadPoolExecutor

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
for line in (Path(os.environ.get('AGERE_ENV_FILE', '/Users/mikel/insight-engine/.env')).read_text().splitlines()
             if Path(os.environ.get('AGERE_ENV_FILE', '/Users/mikel/insight-engine/.env')).exists() else []):
    if '=' in line and not line.startswith('#'):
        k, v = line.split('=', 1); os.environ.setdefault(k.strip(), v.strip().strip('"'))
from clinical_core.agere.gateway import BudgetLedger
from clinical_core.agere.clinical import nl2sql, semantic_layer

AREAS = {
    'cohorts': 'condition registries, lab thresholds (inclusive/exclusive), latest values, age/sex/payer filters, AND/OR/NOT, time windows',
    'medications': 'drug classes on/not on, dispensed vs ordered vs listed, therapy gaps, polypharmacy, adherence proxies',
    'utilization': 'ED, inpatient, observation, urgent care, primary care, specialist, telehealth volumes and rates by time grain, PCP, practice, payer',
    'transitions': 'follow-up after discharge or ED, readmissions, repeat utilizers, discharge plans',
    'value_based_care': 'PMPM, attribution, quality measures (A1c, BP control, statin use), care gaps, risk',
    'revenue_cycle': 'claims status sequences, denials and overturns, net paid after reversals, adjustments (deductible/coinsurance/copay), claim lag, days to payment, remittance, patient balances, payments and refunds',
    'operations': 'appointments (no-shows, cancellations, kept), scheduling lead time, referrals and loop closure, phone messages',
    'data_quality': 'undated rows, arrival lag, orphan orders/results, duplicate versions, feed gaps by source system and month',
    'social_needs': 'food, transport and cost-of-care needs across screening instruments, combined with conditions or utilization',
}
SYS = """You write realistic questions that healthcare buyers (CMO, CFO, VP population health, quality director, care manager,
revenue cycle lead, data engineer, practice manager) ask an analytics system. Vary phrasing: terse, chatty, abbreviations,
explicit definitions sometimes, ambiguity sometimes. Every question must be answerable from the data described.
Return a JSON list of strings only."""


def tokens(t): return set(re.findall(r'[a-z0-9]+', t.lower()))


def main(out, per_area=40):
    bench = [q['question'] for f in (ROOT / 'bench' / 'questions').glob('*.json') for q in json.load(open(f))]
    for prior in (ROOT / 'artifacts' / 'bottling').glob('questions-*.jsonl'):  # do not repeat earlier training questions
        bench += [json.loads(l)['question'] for l in prior.open() if l.strip()]
    bench_tokens = [tokens(b) for b in bench]
    doc = '\n'.join(f'{k}: {v}' for k, v in semantic_layer.SCHEMA_DOC.items())
    def ask(item):
        area, desc = item
        ledger = BudgetLedger(ROOT / 'artifacts/agere-full/provider-budget.sqlite3')
        m = nl2sql.HostedModel(ledger, ROOT / 'artifacts/agere-healthcare-v123/stack/provider-receipts', model='claude-opus-5-5')
        style = os.environ.get('BOTTLING_STYLE', '')
        text, _ = m(SYS, f'DATA\n{doc}\n\nEXAMPLES\nArea: {area} ({desc}). Write {per_area} distinct questions for this area.{style}', max_tokens=9000)
        mm = re.search(r'\[.*\]', text, re.S)
        qs = json.loads(mm.group(0)) if mm else []
        return area, qs, m.cost
    kept, dropped, cost = [], 0, 0.0
    with ThreadPoolExecutor(5) as ex:
        for area, qs, usd in ex.map(ask, AREAS.items()):
            cost += usd or 0
            for q in qs:
                t = tokens(q)
                if any(len(t & b) / max(len(t | b), 1) >= 0.5 for b in bench_tokens): dropped += 1; continue
                kept.append({'area': area, 'question': q})
    Path(out).write_text('\n'.join(json.dumps(k) for k in kept))
    print(f'{len(kept)} questions kept, {dropped} dropped as too close to benchmark questions, ${cost:.2f}')


if __name__ == '__main__':
    main(sys.argv[1], int(sys.argv[2]) if len(sys.argv) > 2 else 40)
