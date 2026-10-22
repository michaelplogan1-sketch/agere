"""SQL-specialist base-model swap: size check / small-only fetch, held-out run, paired comparison against the incumbent.

  swap_base.py check                      registry + live size of each candidate's weights on the Hugging Face hub
  swap_base.py fetch <id>                 download ONLY if weights < max_download_gb (2 GB); else prints why and the swap path
  swap_base.py run <id> <test.jsonl> <stack> <out.jsonl> [k]   evaluate.run with that base (adapter-free unless one exists)
  swap_base.py compare <incumbent.jsonl> <candidate.jsonl> [margin]   paired verdict on the SAME questions

Swap path at runtime: AGERE_BOTTLED_BASE=<registry id|path> (bottled.base_dir / base_adapter_dir; identity in every receipt).
A candidate wins only when, on the questions both answered, its greedy execution-correct count beats the incumbent's by >= margin
(default 2 points) with an exact McNemar sign test p < 0.10, AND its accepted answers (k-candidate agreement rule) are at least as precise.
"""
import json, math, sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(Path(__file__).resolve().parent))
REGISTRY = ROOT / 'config' / 'agere' / 'sql_base_models.json'


def registry():
    return json.loads(REGISTRY.read_text())


def hub_size_gb(repo):
    from huggingface_hub import HfApi
    info = HfApi().model_info(repo, files_metadata=True)
    return sum((s.size or 0) for s in info.siblings if s.rfilename.endswith(('.safetensors', '.bin', '.gguf'))) / 1e9


def check():
    reg, out = registry(), {}
    for name, m in reg['models'].items():
        row = {'dir': m['dir'], 'present': (ROOT / m['dir']).exists(), 'registry_gb': m.get('weights_gb')}
        if m.get('hf'):
            try: row['hub_gb'] = round(hub_size_gb(m['hf']), 2)
            except Exception as e: row['hub_error'] = str(e)[:80]
            row['within_download_limit'] = (row.get('hub_gb') or m.get('weights_gb') or 99) < reg['max_download_gb']
        out[name] = row
    return out


def fetch(name):
    reg = registry(); m = reg['models'][name]
    if not m.get('hf'): return {'fetched': False, 'reason': 'no hub repo (local incumbent)'}
    gb = hub_size_gb(m['hf'])
    if gb >= reg['max_download_gb']:
        return {'fetched': False, 'reason': f'{gb:.1f} GB >= {reg["max_download_gb"]} GB download limit',
                'swap_path': f'place MLX weights at {m["dir"]} then AGERE_BOTTLED_BASE={name}; evaluate with `swap_base.py run`'}
    from huggingface_hub import snapshot_download
    snapshot_download(m['hf'], local_dir=str(ROOT / m['dir']))
    return {'fetched': True, 'dir': m['dir'], 'gb': round(gb, 2)}


def run(name, test, stack, out, k=5):
    import evaluate
    m = registry()['models'][name]
    adapter = ROOT / m['adapter_dir'] if m.get('adapter_dir') and (ROOT / m['adapter_dir'] / 'adapters.safetensors').exists() else 'none'
    if not (ROOT / m['dir']).exists(): raise SystemExit(f'{m["dir"]} is not present: run `swap_base.py fetch {name}` (or place MLX weights there)')
    evaluate.run(test, stack, str(ROOT / m['dir']), str(adapter), out, k)


def _load(path):
    return {r['question']: r for r in (json.loads(l) for l in open(path) if l.strip())}


def mcnemar_p(b, c):
    """Exact two-sided sign test on the discordant pairs (b = incumbent-only correct, c = candidate-only correct)."""
    n = b + c
    if n == 0: return 1.0
    k = min(b, c)
    return min(1.0, 2 * sum(math.comb(n, i) for i in range(k + 1)) / 2 ** n)


def compare_records(inc, cand, margin=0.02, k=5, agree=3):
    from clinical_core.agere.clinical import bottled
    common = sorted(set(inc) & set(cand))
    if not common: return {'verdict': 'no common questions', 'n': 0, 'swap': False}
    g_i = {q: bool(inc[q]['candidates'][0]['correct']) for q in common}
    g_c = {q: bool(cand[q]['candidates'][0]['correct']) for q in common}
    b = sum(g_i[q] and not g_c[q] for q in common); c = sum(g_c[q] and not g_i[q] for q in common)
    def accepted(recs):
        a = cor = 0
        for q in common:
            chosen, _ = bottled.decide([{**x} for x in recs[q]['candidates'][:k]], agree)
            if chosen is not None: a += 1; cor += bool(chosen['correct'])
        return a, cor
    ai, ci = accepted(inc); ac, cc = accepted(cand)
    pi, pc = (ci / ai if ai else None), (cc / ac if ac else None)
    gain = (sum(g_c.values()) - sum(g_i.values())) / len(common)
    p = mcnemar_p(b, c)
    swap = gain >= margin and p < 0.10 and (pc is not None) and (pi is None or pc >= pi)
    return {'n': len(common), 'incumbent_greedy_correct': sum(g_i.values()), 'candidate_greedy_correct': sum(g_c.values()),
            'gain': round(gain, 4), 'incumbent_only': b, 'candidate_only': c, 'mcnemar_p': round(p, 4),
            'incumbent_accepted': ai, 'incumbent_precision': pi, 'candidate_accepted': ac, 'candidate_precision': pc,
            'margin': margin, 'swap': bool(swap),
            'verdict': 'candidate beats the incumbent: set AGERE_BOTTLED_BASE' if swap else 'incumbent stays'}


def main(argv):
    cmd = argv[0] if argv else 'check'
    if cmd == 'check': print(json.dumps(check(), indent=1))
    elif cmd == 'fetch': print(json.dumps(fetch(argv[1]), indent=1))
    elif cmd == 'run': run(argv[1], argv[2], argv[3], argv[4], int(argv[5]) if len(argv) > 5 else 5)
    elif cmd == 'compare':
        print(json.dumps(compare_records(_load(argv[1]), _load(argv[2]), float(argv[3]) if len(argv) > 3 else 0.02), indent=1))
    else: raise SystemExit(__doc__)


if __name__ == '__main__':
    main(sys.argv[1:])
