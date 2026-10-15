"""Measure the second pinned encoder (UltraBERT-Embed-150M) through the INTEGRATED code paths, not the bake-off script.

  .venv/bin/python tools/second_encoder/measure_integrated.py catalog      # answer_guard.probe + raw_catalog_slice, lexical vs fused
  .venv/bin/python tools/second_encoder/measure_integrated.py terminology  # concepts.match_label (code -> alias -> encoder; brands)
  .venv/bin/python tools/second_encoder/measure_integrated.py calibrate    # threshold table on the normalization dictionary

Cases: catalog_cases.json / terminology_cases.json are the bake-off's (branch worktree-agent-ad91a50a110313238, commit efcf4a3,
tools/encoder_bakeoff/), written before any encoder ran. Catalog = raw streams of
artifacts/agere-healthcare-v28-sample-0.30/stack/source-profile.json (read-only). Offline; CPU; 4 threads; no API calls.
Vectors go to a throwaway cache (--cache) so cold embedding cost is measured. Results: tools/second_encoder/results/<task>.json.
"""
import os

os.environ['HF_HUB_OFFLINE'] = '1'
os.environ['TRANSFORMERS_OFFLINE'] = '1'
os.environ.setdefault('TOKENIZERS_PARALLELISM', 'false')

import argparse, json, resource, sys, tempfile, time
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]
sys.path.insert(0, str(ROOT))
PROFILE = Path('/Users/mikel/clinical-insights-local/artifacts/agere-healthcare-v28-sample-0.30/stack/source-profile.json')


def rss_mb():
    """Current resident set size (MB) via ps; peak via getrusage."""
    import subprocess
    cur = int(subprocess.run(['ps', '-o', 'rss=', '-p', str(os.getpid())], capture_output=True, text=True).stdout.strip() or 0) / 1024
    r = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
    peak = r / (1024 * 1024) if sys.platform == 'darwin' else r / 1024
    return round(cur, 1), round(peak, 1)


def load_encoder():
    from clinical_core import local_encoders as L
    before = rss_mb()
    t = time.perf_counter()
    enc = L.second_encoder()
    load_s = time.perf_counter() - t
    after = rss_mb()
    if enc is None: raise SystemExit('second encoder unavailable: ' + json.dumps(L.second_encoder_status()))
    return enc, {'load_seconds': round(load_s, 2), 'rss_before_mb': before[0], 'rss_after_load_mb': after[0],
                 'added_rss_mb': round(after[0] - before[0], 1), 'identity': enc.identity}


def ranks(order, expect):
    hit = [order.index(e) + 1 for e in expect if e in order]
    return min(hit) if hit else None


def summarize(rs, n):
    def at(k): return round(sum(1 for r in rs if r is not None and r <= k) / n, 4)
    return {'top1': at(1), 'top3': at(3), 'top5': at(5), 'mrr': round(sum(1 / r for r in rs if r) / n, 4)}


def catalog(args):
    import numpy as np
    from clinical_core.agere.clinical import answer_guard as G
    cases = json.loads((HERE / 'catalog_cases.json').read_text())['cases']
    profile = json.loads(PROFILE.read_text())
    n = len(cases)
    out = {'cases': n, 'streams': len(profile['streams']), 'profile': str(PROFILE), 'path': 'answer_guard.probe(k=4) / raw_catalog_slice(k=4)'}
    lex, lex_slice, lex_t = [], [], []
    for c in cases:  # lexical baseline: the probe with the dense stage disabled (the shipped behaviour without the model)
        t = time.perf_counter(); p = G.probe(c['q'], profile, k=4, dense=None); lex_t.append(time.perf_counter() - t)
        lex.append(ranks([x['stream'] for x in p['candidates']], c['expect']))
        _, rec = G.raw_catalog_slice(c['q'], profile, k=4, dense=None)
        lex_slice.append(any(e in (rec or {}).get('streams', []) for e in c['expect']))
    enc, mem = load_encoder()
    t = time.perf_counter(); G.probe('warm up the catalog', profile, k=4); cold = time.perf_counter() - t  # embeds the catalog once
    fused, fused_slice, fused_t, per = [], [], [], []
    for c in cases:
        t = time.perf_counter(); p = G.probe(c['q'], profile, k=4); fused_t.append(time.perf_counter() - t)
        order = [x['stream'] for x in p['candidates']]
        fused.append(ranks(order, c['expect']))
        _, rec = G.raw_catalog_slice(c['q'], profile, k=4)
        fused_slice.append(any(e in (rec or {}).get('streams', []) for e in c['expect']))
        per.append({'q': c['q'], 'expect': c['expect'], 'lexical_rank': lex[len(per)], 'fused_rank': fused[-1], 'top5': order,
                    'slice_streams': (rec or {}).get('streams')})
    after = rss_mb()
    out.update({
        'lexical': {**summarize(lex, n), 'slice_recall': round(sum(lex_slice) / n, 4),
                    'ms_per_question_median': round(1000 * float(np.median(lex_t)), 1)},
        'fused_ultrabert': {**summarize(fused, n), 'slice_recall': round(sum(fused_slice) / n, 4),
                            'ms_per_question_median': round(1000 * float(np.median(fused_t)), 1),
                            'ms_per_question_p90': round(1000 * float(np.percentile(fused_t, 90)), 1),
                            'catalog_embed_seconds_cold': round(cold, 2)},
        'memory': {**mem, 'rss_after_catalog_mb': after[0], 'peak_rss_mb': after[1]},
        'bakeoff_reference': {'Bq_dense_only': {'top1': 0.8438, 'top5': 1.0}, 'A_minilm': {'top1': 0.75, 'top5': 0.875}},
        'per_case': per})
    save('catalog', out)


def terminology(args):
    from clinical_core.agere.clinical import concepts as C
    cases = json.loads((HERE / 'terminology_cases.json').read_text())['cases']
    enc, mem = load_encoder()
    rows = []
    t0 = time.perf_counter()
    for c in cases:
        ok = {c['expect'], *c.get('also', [])}
        if c['kind'] == 'medication':
            m = C.match_label(c['label'], 'medication')
            got = (m or {}).get('ingredients') or []
            rows.append({'kind': 'medication', 'label': c['label'], 'brand_only': c.get('brand_only', False), 'method': (m or {}).get('method'),
                         'correct': bool(ok & set(got)), 'got': got})
            continue
        alias = C.match_label(c['label'], c['kind'], encoder=None)
        full = C.match_label(c['label'], c['kind'], encoder=enc, threshold=0.0)
        prod = C.match_label(c['label'], c['kind'], encoder=enc)
        enc_only = C.encoder_candidates([c['label']], c['kind'], enc, top=1)[0][0]
        top1 = full.get('candidate') or full['concept']
        rows.append({'kind': c['kind'], 'label': c['label'], 'expect': c['expect'], 'alias': (alias or {}).get('concept'),
                     'alias_correct': (alias or {}).get('concept') in ok, 'method': full['method'], 'top1': top1, 'top1_correct': top1 in ok,
                     'score': full['score'], 'accepted': prod.get('concept'), 'accepted_correct': prod.get('concept') in ok,
                     'accepted_wrong': prod.get('concept') is not None and prod.get('concept') not in ok,
                     'encoder_only_top1': enc_only[0], 'encoder_only_correct': enc_only[0] in ok})
    per_label_ms = 1000 * (time.perf_counter() - t0) / len(cases)

    def agg(sub, key): return round(sum(r[key] for r in sub) / max(1, len(sub)), 4)
    out = {'path': 'concepts.match_label (code -> alias -> encoder fallback; medication: ingredient -> brand table)', 'memory': mem,
           'ms_per_label_mean': round(per_label_ms, 1)}
    for kind in ('lab', 'condition'):
        sub = [r for r in rows if r['kind'] == kind]
        out[kind] = {'n': len(sub), 'alias_only_resolved_correct': agg(sub, 'alias_correct'),
                     'integrated_top1_threshold0': agg(sub, 'top1_correct'),
                     'integrated_accepted_correct': agg(sub, 'accepted_correct'), 'integrated_accepted_wrong': sum(r['accepted_wrong'] for r in sub),
                     'reached_encoder': sum(r['method'] == 'encoder' for r in sub),
                     'encoder_only_top1 (bake-off Bq comparable)': agg(sub, 'encoder_only_correct'), 'threshold': C.TERMINOLOGY_THRESHOLDS[kind]}
    both = [r for r in rows if r['kind'] in ('lab', 'condition')]
    out['labs_and_conditions'] = {'n': len(both), 'integrated_top1_threshold0': agg(both, 'top1_correct'),
                                  'encoder_only_top1': agg(both, 'encoder_only_correct'), 'alias_only': agg(both, 'alias_correct'),
                                  'bakeoff_reference': {'Bq': 0.975, 'A_minilm': 0.80}}
    meds = [r for r in rows if r['kind'] == 'medication']
    out['medication'] = {'n': len(meds), 'correct': agg(meds, 'correct'),
                         'brand_only': {'n': sum(r['brand_only'] for r in meds), 'correct': agg([r for r in meds if r['brand_only']], 'correct')},
                         'full_label': agg([r for r in meds if not r['brand_only']], 'correct'),
                         'bakeoff_reference': {'Bq_brand_only': 0.1, 'A_brand_only': 0.2}}
    out['per_case'] = rows
    save('terminology', out)


def calibrate(args):
    """Threshold table: the normalization dictionary's confident (>= 0.8) single-concept entries (bake-off labels excluded),
    each scored by the encoder alone. Entries mapped to a library concept give the encoder's score distribution for right
    answers (most of them are alias-resolvable in production, so they never reach the fallback - they are used here only
    because the labels that do reach it are nearly all mapped-to-none); entries mapped to none are the negatives. An
    accepted proposal is wrong if the dictionary maps the label to another concept or to none."""
    from clinical_core.agere.clinical import concepts as C
    enc, _ = load_encoder()
    bake = {c['label'] for c in json.loads((HERE / 'terminology_cases.json').read_text())['cases']}
    out = {}
    for kind, fn in (('lab', 'labs'), ('condition', 'conditions')):
        fam = C.MEASUREMENTS if kind == 'lab' else C.CONDITIONS
        labels, truth, reach = [], [], []
        for label, v in json.loads((ROOT / 'config' / 'agere' / 'normalization' / f'{fn}.json').read_text())['entries'].items():
            if label in bake: continue
            vs = list(v['variants_by_unit'].values()) if 'variants_by_unit' in v else [v]
            if any((x.get('confidence') or 0) < 0.8 for x in vs): continue
            cs = {x.get('agere_concept') for x in vs}
            if len(cs) != 1: continue
            t = cs.pop()
            if t is not None and t not in fam: continue
            labels.append(label); truth.append(t); reach.append(C.match_label(label, kind, encoder=None) is None)
        cands = C.encoder_candidates(labels, kind, enc, top=1)
        rows = [(c[0][1], c[0][0], t, r) for c, t, r in zip(cands, truth, reach)]
        table = []
        for i in range(0, 12):
            th = round(0.05 * i + 0.30, 2)
            acc = [r for r in rows if r[0] >= th]
            table.append({'threshold': th, 'accepted': len(acc), 'correct': sum(r[1] == r[2] for r in acc),
                          'wrong_or_none': sum(r[1] != r[2] for r in acc),
                          'of_which_reach_fallback': {'correct': sum(r[1] == r[2] for r in acc if r[3]),
                                                      'wrong_or_none': sum(r[1] != r[2] for r in acc if r[3])}})
        out[kind] = {'labels': len(rows), 'mapped': sum(t is not None for t in truth), 'mapped_to_none': sum(t is None for t in truth),
                     'reach_fallback': sum(reach), 'shipped_threshold': C.TERMINOLOGY_THRESHOLDS[kind], 'table': table}
    save('calibrate', out)


def save(task, payload):
    (HERE / 'results').mkdir(exist_ok=True)
    (HERE / 'results' / f'{task}.json').write_text(json.dumps(payload, indent=1) + '\n')
    print(json.dumps({k: v for k, v in payload.items() if k != 'per_case'}, indent=1))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('task', choices=['catalog', 'terminology', 'calibrate'])
    ap.add_argument('--cache', default=None, help='vector cache path (default: a fresh temporary file, so costs are cold)')
    a = ap.parse_args()
    tmp = tempfile.TemporaryDirectory()
    os.environ['AGERE_SECOND_ENCODER_CACHE'] = a.cache or str(Path(tmp.name) / 'vectors.sqlite3')
    {'catalog': catalog, 'terminology': terminology, 'calibrate': calibrate}[a.task](a)


if __name__ == '__main__':
    main()
