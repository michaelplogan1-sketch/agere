"""Classify each failed benchmark answer: engine_error | format_only | defensible_definition | reference_questionable.

Usage: .venv/bin/python bench/adjudicate.py <results.jsonl> <questions.json>...
Evaluation only (sees the reference definition); never used to train or tune the engine.
"""
import json, sys, os, re
from pathlib import Path
for line in (Path(os.environ.get('AGERE_ENV_FILE','/Users/mikel/insight-engine/.env')).read_text().splitlines() if Path(os.environ.get('AGERE_ENV_FILE','/Users/mikel/insight-engine/.env')).exists() else []):
    if '=' in line and not line.startswith('#'):
        k,v=line.split('=',1); os.environ.setdefault(k.strip(), v.strip().strip('"'))
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from clinical_core.agere.gateway import BudgetLedger
from clinical_core.agere.clinical import nl2sql
from concurrent.futures import ThreadPoolExecutor
ROOT=Path(__file__).resolve().parents[1]
P=ROOT/'artifacts/agere-healthcare-v123/stack'
qs={q['id']:q for f in sys.argv[2:] for q in json.load(open(f))}
SYS="""You adjudicate a disagreement between an analytics engine's answer and a reference answer for the same question.
Classify the engine's answer into exactly one category:
- "engine_error": the engine's query clearly misreads the question or the data (wrong entity, wrong table, counts versions
  instead of entities, wrong population, ignores an explicit instruction in the question, wrong units, empty/null result).
- "defensible_definition": the question is ambiguous and the engine used a reasonable, standard interpretation that differs
  from the reference's chosen definition (status filter, date field, inclusive window, instrument, rounding).
- "format_only": same numbers, different column names, ordering, rounding or shape.
- "reference_questionable": the engine's reading is better than the reference's.
Reply JSON only: {"category": "...", "reason": "one sentence"}"""
def judge(item):
    i,q,ref_interp,exp,sql,got=item
    ledger=BudgetLedger(ROOT/'artifacts/agere-full/provider-budget.sqlite3')
    m=nl2sql.HostedModel(ledger,P/'provider-receipts',model='claude-opus-5-5')
    text,_=m(SYS, f"QUESTION: {q}\n\nEXAMPLES\nREFERENCE DEFINITION: {ref_interp}\nREFERENCE ANSWER: {exp}\n\nENGINE SQL: {sql}\nENGINE ANSWER: {got}", max_tokens=700)
    mm=re.search(r'\{.*\}',text,re.S)
    try: return i, json.loads(mm.group(0)), m.cost
    except Exception:
        cat=next((c for c in ('engine_error','format_only','defensible_definition','reference_questionable') if c in text), 'unparsed')
        return i, {'category':cat,'reason':text[:200]}, m.cost
items=[]
for l in open(sys.argv[1]):
    r=json.loads(l); s=r['score']
    if s.get('correct'): continue
    a=r.get('answer') or {}; e=r['expected']
    pick=lambda x: json.dumps(next((x[k] for k in ('count','value') if x.get(k) is not None), None) if any(x.get(k) is not None for k in ('count','value')) else (x.get('table') or x.get('patient_ids') or [])[:10] or x.get('missing'), default=str)[:900]
    items.append((r['id'], r['question'], qs[r['id']]['interpretation'][:1200], pick(e), (a.get('query') or 'n/a')[:1500], pick(a)))
with ThreadPoolExecutor(4) as ex: res=list(ex.map(judge, items))
import collections; c=collections.Counter(); cost=0
for i,v,usd in res: c[v.get('category')]+=1; cost+=usd or 0; print(i, v.get('category'), '|', (v.get('reason') or '')[:170])
print(dict(c), '$%.2f'%cost)
