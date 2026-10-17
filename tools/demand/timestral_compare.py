"""Compare Timestral candidates against demand.py's existing winner on the active warehouse.

Same path as forecast_series: build series -> screen -> usable regime -> rolling-origin backtest.
Per series: MASE of every model, the existing winner (lowest MASE among non-Timestral models), and sequential
split-conformal coverage (80/95) plus raw model coverage for the winner and each Timestral size.
"""
import json, os, sqlite3, sys, time
from collections import defaultdict
from pathlib import Path
import numpy as np

os.environ.setdefault('AGERE_DEMAND_TIMESTRAL', '28M,123M')
sys.path.insert(0, '/Users/mikel/clinical-insights-local')
from clinical_core.agere.clinical import demand, semantic_layer

active = json.loads(Path('/Users/mikel/clinical-insights-local/config/agere/active_warehouse.json').read_text())
path = Path(active['build']) / 'stack'
as_of = json.loads((path / 'run.json').read_text())['snapshot']['as_of']
HORIZON = {'week': 13, 'month': 6}
GROUPS = int(os.environ.get('GROUPS', '3'))  # top facility groups per measure in addition to 'all'

db = sqlite3.connect(f'file:{path / 'semantic.sqlite3'}?mode=ro', uri=True)
out = []
t0 = time.time()
for measure in demand.MEASURES:
    rows, _ = demand.events(db, measure, None, as_of)
    by = defaultdict(list)
    for r in rows:
        by['all'].append(r); by['facility:' + (r[2] or 'unknown')].append(r)
    names = ['all'] + sorted((g for g in by if g != 'all'), key=lambda g: -len(by[g]))[:GROUPS]
    for freq in demand.FREQUENCIES:
        for g in names:
            periods, y, _, _ = demand.build_series(by[g], freq, as_of)
            if not y: continue
            sc = demand.screen(y, freq)
            if sc['trailing_gap'] or sc['usable'] < demand.MIN_REGIME[freq]: continue
            yr = np.asarray(sc['cleaned'][sc['start']:], float)
            ts = time.time()
            records, origins, seasonal, baseline = demand.backtest(yr, freq, HORIZON[freq])
            mase = {k: float(np.mean([r['scaled_error'] for r in v])) for k, v in records.items()}
            classic = {k: v for k, v in mase.items() if not k.startswith('timestral')}
            winner = min(classic, key=classic.get)
            row = {'measure': measure, 'freq': freq, 'group': g, 'periods': len(yr), 'mean_count': round(float(yr.mean()), 1),
                   'origins': len(origins), 'winner': winner, 'mase': {k: round(v, 3) for k, v in mase.items()},
                   'baseline': baseline if baseline in mase else 'naive', 'coverage': {}, 'seconds': round(time.time() - ts, 1)}
            for k in [winner] + [k for k in mase if k.startswith('timestral')]:
                c80, n80 = demand.sequential_coverage(records[k], 0.8, 0.5)
                c95, _ = demand.sequential_coverage(records[k], 0.95, 0.5)
                row['coverage'][k] = {'conf80': c80, 'conf95': c95, 'n': n80,
                                      'model80': float(np.mean([r['model_cover'][0.8] for r in records[k]])),
                                      'model95': float(np.mean([r['model_cover'][0.95] for r in records[k]]))}
            # nested: select on early origins, score the selected model on late origins (targets after the split)
            split = origins[len(origins) // 2]
            def sel_score(names):
                early = {k: np.mean([r['scaled_error'] for r in records[k] if r['target'] < split] or [np.inf]) for k in names}
                pick = min(early, key=early.get)
                late = [r for r in records[pick] if r['origin'] >= split]
                return pick, float(np.mean([r['scaled_error'] for r in late])), sum(abs(r['actual'] - r['point']) <= 0 for r in late)
            cl = [k for k in records if not k.startswith('timestral')]
            row['nested'] = {'classic': sel_score(cl)[:2], 'with_timestral': sel_score(list(records))[:2],
                             **{k: float(np.mean([r['scaled_error'] for r in records[k] if r['origin'] >= split])) for k in records if k.startswith('timestral')}}
            out.append(row)
            print(f"{measure:22s} {freq:5s} {g[:28]:28s} n={len(yr):4d} win={winner:32s} {classic[winner]:.3f} "
                  + ' '.join(f"{k}={v:.3f}" for k, v in mase.items() if k.startswith('timestral')), flush=True)
db.close()
Path(sys.argv[1] if len(sys.argv) > 1 else 'timestral_compare.json').write_text(json.dumps(
    {'build': str(path), 'as_of': as_of, 'horizon': HORIZON, 'series': out, 'seconds': round(time.time() - t0, 1)}, indent=1))
