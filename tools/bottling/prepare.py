"""Bottling step 3: verified (question -> SQL) pairs -> MLX chat fine-tuning data for the local plan model.

Only pairs that passed the verified ladder (hosted_verified_sql) or the deterministic compiler's SQL are used; nothing
from benchmark question files. The prompt mirrors the hosted ladder (rules + retrieved schema slice) so the bottled model
is a drop-in rung. Usage: .venv/bin/python tools/bottling/prepare.py <out_dir> <answers.jsonl>...
"""
import json, random, re, sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
from clinical_core.agere.clinical import nl2sql, source_profile

STACK = ROOT / 'artifacts/agere-healthcare-v123/stack'


def main(out_dir, files):
    profile = source_profile.load(STACK)
    rows = [json.loads(l) for f in files for l in open(f) if l.strip()]
    bench = {q['question'].strip().lower() for f in (ROOT / 'bench/questions').glob('*.json') for q in json.load(open(f))}
    pairs = []
    for r in rows:
        sql = r.get('query')
        if r.get('route') not in ('hosted_verified_sql', 'local_compiler') or not sql or not re.search(r'(?i)\bselect\b', sql): continue
        if r['question'].strip().lower() in bench: continue
        _, schema = nl2sql.schema_for(r['question'], profile)
        pairs.append({'messages': [{'role': 'system', 'content': nl2sql.RULES + nl2sql.VERIFY_RULES},
                                   {'role': 'user', 'content': f"{schema}\n\nQ: {r['question']}"},
                                   {'role': 'assistant', 'content': f"```sql\n{sql}\n```"}]})
    random.Random(7).shuffle(pairs)
    n_valid = max(1, len(pairs) // 10)
    out = Path(out_dir); out.mkdir(parents=True, exist_ok=True)
    (out / 'valid.jsonl').write_text('\n'.join(json.dumps(p) for p in pairs[:n_valid]))
    (out / 'train.jsonl').write_text('\n'.join(json.dumps(p) for p in pairs[n_valid:]))
    lens = sorted(len(json.dumps(p)) // 4 for p in pairs)
    print(f'{len(pairs)} verified pairs ({len(pairs) - n_valid} train / {n_valid} valid); ~tokens p50 {lens[len(lens)//2] if lens else 0}, max {lens[-1] if lens else 0}')


if __name__ == '__main__':
    main(sys.argv[1], sys.argv[2:])
