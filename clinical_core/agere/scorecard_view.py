"""Agere error-rate scorecard (/scorecard): blind-set results, adjudicated error rates, defect Pareto, cost by route.

Server-rendered, self-contained HTML with hand-written SVG (same tokens as ask_view). Every number comes from
bench/results (scored runs, saved adjudications, acceptance runs) through tools/quality/defects.py, plus the stack's
learning summary when a stack is connected. Nothing is invented: a section without data says so.
"""
from __future__ import annotations

import datetime as dt
import html
import importlib.util
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
RESULTS = ROOT / 'bench' / 'results'


def _defects():
    spec = importlib.util.spec_from_file_location('agere_quality_defects', ROOT / 'tools' / 'quality' / 'defects.py')
    module = importlib.util.module_from_spec(spec); spec.loader.exec_module(module)
    return module


def build(results=RESULTS, fmea=None):
    """The defect report for the page, or {'empty': reason} when there is nothing scored."""
    module = _defects()
    results = Path(results)
    if not results.exists() or not any(results.glob('*/*.jsonl')):
        acceptance = None
        try: acceptance = module.report(results, fmea or module.load_fmea())['acceptance'] if results.exists() else None
        except Exception: acceptance = None  # noqa: BLE001
        return {'empty': 'No scored benchmark runs were found in bench/results.', 'acceptance': acceptance}
    return module.report(results, fmea or module.load_fmea())


e = lambda v: html.escape(str(v), quote=True)


def _pct(x, digits=0): return '–' if x is None else f'{100 * x:.{digits}f}%'


def _ci(ci): return '' if not ci else f'{_pct(ci[0])}–{_pct(ci[1])}'


def _num(x, spec=',.0f'): return '–' if x is None else format(x, spec)


def _usd(x): return '–' if x is None else (f'${x:,.4f}' if x < 0.1 else f'${x:,.2f}')


CSS = """
:root{--bg:#f6f6f4;--fg:#1c1917;--muted:#57534e;--line:#e2e0dc;--card:#fff;--soft:#f1f0ec;--accent:#0f766e;--warn:#92400e;--warn-bg:#fef3c7;
--bad:#b91c1c;--bad-bg:#fee2e2;--good:#166534;--good-bg:#dcfce7;--s1:#0f766e;--s2:#b45309;--s3:#4f46e5;--grid:#e7e5e4;--band:rgba(15,118,110,.10)}
@media (prefers-color-scheme:dark){:root:not([data-theme="light"]){--bg:#0e0d0c;--fg:#f5f5f4;--muted:#b5afa9;--line:#34302c;--card:#1a1816;--soft:#24211e;--accent:#2dd4bf;
--warn:#fcd34d;--warn-bg:#3b2a07;--bad:#fca5a5;--bad-bg:#3f1414;--good:#86efac;--good-bg:#0f2e1a;--s1:#2dd4bf;--s2:#fbbf24;--s3:#a5b4fc;--grid:#2c2926;--band:rgba(45,212,191,.12)}}
:root[data-theme="dark"]{--bg:#0e0d0c;--fg:#f5f5f4;--muted:#b5afa9;--line:#34302c;--card:#1a1816;--soft:#24211e;--accent:#2dd4bf;
--warn:#fcd34d;--warn-bg:#3b2a07;--bad:#fca5a5;--bad-bg:#3f1414;--good:#86efac;--good-bg:#0f2e1a;--s1:#2dd4bf;--s2:#fbbf24;--s3:#a5b4fc;--grid:#2c2926;--band:rgba(45,212,191,.12)}
*{box-sizing:border-box}html{-webkit-text-size-adjust:100%}
body{margin:0;background:var(--bg);color:var(--fg);font:15px/1.5 system-ui,-apple-system,"Segoe UI",sans-serif;overflow-x:hidden}
a{color:var(--accent)}
header.top{display:flex;flex-wrap:wrap;align-items:baseline;gap:8px 16px;padding:12px 16px;border-bottom:1px solid var(--line);background:var(--card)}
header.top h1{font-size:18px;margin:0}header.top .tag{color:var(--muted);font-size:13px}
main{max-width:1200px;margin:0 auto;padding:16px;display:grid;gap:16px}
.panel{background:var(--card);border:1px solid var(--line);border-radius:12px;padding:14px 16px;min-width:0}
.panel>h2{font-size:13px;text-transform:uppercase;letter-spacing:.06em;color:var(--muted);margin:0 0 10px}
.grid2{display:grid;grid-template-columns:repeat(auto-fit,minmax(min(100%,460px),1fr));gap:16px}
.kpis{display:grid;grid-template-columns:repeat(auto-fit,minmax(150px,1fr));gap:8px}
.kpi{background:var(--soft);border-radius:10px;padding:8px 10px}.kpi .l{font-size:12px;color:var(--muted)}
.kpi .v{font-size:22px;font-weight:650;font-variant-numeric:tabular-nums}.kpi .s{font-size:12px;color:var(--muted)}
.empty{color:var(--muted);background:var(--soft);border-radius:8px;padding:10px 12px;font-size:14px}
.note{color:var(--muted);font-size:13px;margin:8px 0 0}
.tablewrap{overflow-x:auto;border:1px solid var(--line);border-radius:8px}
table{border-collapse:collapse;width:100%;font-size:13px}th,td{text-align:left;padding:6px 8px;border-bottom:1px solid var(--line);vertical-align:top}
th{background:var(--soft);color:var(--muted);font-weight:600;white-space:nowrap}td.n,th.n{text-align:right;font-variant-numeric:tabular-nums;white-space:nowrap}
.badge{display:inline-block;border-radius:6px;padding:1px 8px;font-size:12px}.badge.good{color:var(--good);background:var(--good-bg)}
.badge.bad{color:var(--bad);background:var(--bad-bg)}.badge.warn{color:var(--warn);background:var(--warn-bg)}
.chart{width:100%;max-width:760px;height:auto;display:block;margin:4px 0}
.pareto{display:grid;grid-template-columns:minmax(0,1.3fr) minmax(0,1fr) auto;gap:4px 10px;align-items:center;font-size:13px}
.pareto .lab{overflow-wrap:anywhere}.pareto .bar{height:12px;border-radius:3px;background:var(--s1)}.pareto .cnt{font-variant-numeric:tabular-nums;color:var(--muted);white-space:nowrap}
.chips{display:flex;flex-wrap:wrap;gap:6px;margin-top:8px}.chip{border:1px solid var(--line);border-radius:999px;padding:2px 10px;font-size:12px;background:var(--soft)}
.chip.bad{border-color:var(--bad);color:var(--bad)}
h3.sub{font-size:14px;margin:14px 0 6px}.chart text{fill:var(--muted);font-size:11px}.chart .axis{stroke:var(--line)}.chart .gridl{stroke:var(--grid)}
.legend{display:flex;flex-wrap:wrap;gap:4px 14px;font-size:12px;color:var(--muted);margin:6px 0 0}
.sw{display:inline-block;width:12px;height:3px;vertical-align:middle;margin-right:5px;border-radius:2px}
dl.defs{margin:0;display:grid;grid-template-columns:minmax(0,190px) minmax(0,1fr);gap:6px 14px;font-size:14px}dl.defs dt{font-weight:600}dl.defs dd{margin:0;color:var(--muted)}
@media (max-width:600px){dl.defs{grid-template-columns:minmax(0,1fr)}dl.defs dd{margin-bottom:6px}}
"""


def _table(head, rows, numeric=()):
    th = ''.join(f'<th class="{"n" if i in numeric else ""}">{e(h)}</th>' for i, h in enumerate(head))
    body = ''.join('<tr>' + ''.join(f'<td class="{"n" if i in numeric else ""}">{c}</td>' for i, c in enumerate(r)) + '</tr>' for r in rows)
    return f'<div class="tablewrap"><table><thead><tr>{th}</tr></thead><tbody>{body}</tbody></table></div>'


def _rate_chart(series, labels, title):
    """Rates (0-1) per category with optional 95% interval whiskers. series = [(name, colour var, [(value, (lo, hi)) | None])]."""
    if not labels: return '<p class="empty">No blind runs to chart.</p>'
    w, h, left, bottom, top = 640, 230, 40, 26, 10
    step = (w - left - 10) / len(labels)
    y = lambda v: top + (1 - v) * (h - top - bottom)
    parts = [f'<svg class="chart" viewBox="0 0 {w} {h}" role="img" aria-label="{e(title)}">']
    for g in (0, .25, .5, .75, 1):
        parts.append(f'<line class="gridl" x1="{left}" x2="{w - 10}" y1="{y(g):.1f}" y2="{y(g):.1f}"/><text x="{left - 6}" y="{y(g) + 4:.1f}" text-anchor="end">{int(g * 100)}%</text>')
    n = len(series)
    for si, (name, colour, values) in enumerate(series):
        for i, v in enumerate(values):
            if not v or v[0] is None: continue
            cx = left + step * i + step * (si + 1) / (n + 1)
            if v[1]:
                parts.append(f'<line x1="{cx:.1f}" x2="{cx:.1f}" y1="{y(v[1][1]):.1f}" y2="{y(v[1][0]):.1f}" stroke="var({colour})" stroke-width="1.5" opacity=".6"/>')
            parts.append(f'<circle cx="{cx:.1f}" cy="{y(v[0]):.1f}" r="4.5" fill="var({colour})"><title>{e(name)} {e(labels[i])}: {_pct(v[0], 1)} '
                         f'{("(95% CI " + _ci(v[1]) + ")") if v[1] else ""}</title></circle>')
    for i, label in enumerate(labels):
        parts.append(f'<text x="{left + step * (i + .5):.1f}" y="{h - 8}" text-anchor="middle">{e(label)}</text>')
    parts.append(f'<line class="axis" x1="{left}" x2="{w - 10}" y1="{y(0):.1f}" y2="{y(0):.1f}"/></svg>')
    legend = ''.join(f'<span><span class="sw" style="background:var({c})"></span>{e(nm)}</span>' for nm, c, _ in series)
    return ''.join(parts) + f'<div class="legend">{legend}<span>Whiskers: 95% Wilson interval</span></div>'


def _p_chart(chart, title):
    pts = [p for p in (chart or {}).get('points') or [] if p.get('rate') is not None]
    if len(pts) < 2: return '<p class="empty">Fewer than two runs with this measure; no control chart yet.</p>'
    w, h, left, bottom, top = 640, 220, 40, 26, 10
    step = (w - left - 10) / max(1, len(pts) - 1) if len(pts) > 1 else 0
    x = lambda i: left + 14 + (w - left - 38) * (i / max(1, len(pts) - 1))
    y = lambda v: top + (1 - v) * (h - top - bottom)
    parts = [f'<svg class="chart" viewBox="0 0 {w} {h}" role="img" aria-label="{e(title)}">']
    for g in (0, .25, .5, .75, 1):
        parts.append(f'<line class="gridl" x1="{left}" x2="{w - 10}" y1="{y(g):.1f}" y2="{y(g):.1f}"/><text x="{left - 6}" y="{y(g) + 4:.1f}" text-anchor="end">{int(g * 100)}%</text>')
    band = ' '.join(f'{x(i):.1f},{y(p["ucl"]):.1f}' for i, p in enumerate(pts)) + ' ' + ' '.join(f'{x(i):.1f},{y(p["lcl"]):.1f}' for i, p in reversed(list(enumerate(pts))))
    parts.append(f'<polygon points="{band}" fill="var(--band)"/>')
    if chart.get('center') is not None:
        parts.append(f'<line x1="{left}" x2="{w - 10}" y1="{y(chart["center"]):.1f}" y2="{y(chart["center"]):.1f}" stroke="var(--muted)" stroke-dasharray="4 3"/>')
    parts.append('<polyline fill="none" stroke="var(--s1)" stroke-width="2" points="' + ' '.join(f'{x(i):.1f},{y(p["rate"]):.1f}' for i, p in enumerate(pts)) + '"/>')
    for i, p in enumerate(pts):
        colour = 'var(--bad)' if p.get('signal') == 'above' else 'var(--good)' if p.get('signal') == 'below' else 'var(--s1)'
        parts.append(f'<circle cx="{x(i):.1f}" cy="{y(p["rate"]):.1f}" r="4.5" fill="{colour}"><title>{e(p["label"])}: {p["defects"]}/{p["opportunities"]} = {_pct(p["rate"], 1)}; '
                     f'limits {_pct(p["lcl"], 1)}–{_pct(p["ucl"], 1)}{"; signal " + p["signal"] if p.get("signal") else ""}</title></circle>'
                     f'<text x="{x(i):.1f}" y="{h - 8}" text-anchor="middle">{e(p["label"])}</text>')
    parts.append('</svg>')
    return ''.join(parts) + (f'<div class="legend"><span>Centre line (dashed): pooled defect rate {_pct(chart.get("center"), 1)}</span>'
                             '<span>Shaded: 3-sigma binomial limits per run size</span><span>Red point: above the upper limit; green: below the lower limit</span></div>')


def _pareto(items, title):
    items = [i for i in items if i['count']]
    if not items: return '<p class="empty">No classified failures yet.</p>'
    top = max(i['count'] for i in items)
    rows = []
    for i in items:
        label = f"{i['mode']} {i['label']}" if i['mode'].startswith('FM-') else i['label']
        rows.append(f'<div class="lab">{e(label)}</div><div><div class="bar" style="width:{100 * i["count"] / top:.1f}%"></div></div>'
                    f'<div class="cnt">{i["count"]} · {_pct(i["cumulative"])}</div>')
    return f'<div class="pareto" role="table" aria-label="{e(title)}">' + ''.join(rows) + '</div>'



def _empty(text): return f'<p class="empty">{e(text)}</p>'


def page(report=None, learning=None, results=RESULTS, now=None):
    if report is None:
        try: report = build(results)
        except Exception as exc:  # noqa: BLE001 - the page must render and say why
            report = {'empty': f'The defect report could not be computed: {type(exc).__name__}: {exc}'[:300]}
    now = now or dt.datetime.now(dt.timezone.utc).strftime('%Y-%m-%d %H:%M UTC')
    out = ['<!doctype html><html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">',
           '<title>Agere Error Scorecard</title>', f'<style>{CSS}</style></head><body>',
           f'<header class="top"><h1>Agere error-rate scorecard</h1><span class="tag">Synthetic data only. Generated {e(now)} from bench/results'
           f'{" and the stack learning store" if learning else ""}. <a href="/ask">Workspace</a></span></header><main>']
    if report.get('empty'):
        out.append(f'<section class="panel"><h2>Benchmark results</h2>{_empty(report["empty"])}</section>')
        out.append(_acceptance_panel(report.get('acceptance'), None))
    else:
        out += [_latest_panel(report), _trend_panel(report), _dpmo_panel(report), _pareto_panel(report), _routes_panel(report),
                _acceptance_panel(report.get('acceptance'), report.get('smoke')), _controls_panel(report)]
    out.append(_learning_panel(learning))
    out.append(_definitions_panel())
    out.append('</main></body></html>')
    return ''.join(out)


def _latest_panel(r):
    sets = r.get('blind_sets') or []
    if not sets: return f'<section class="panel"><h2>Latest blind set</h2>{_empty("No full blind-set runs recorded.")}</section>'
    s = sets[-1]; m = s['latest']
    kpis = [('Answerable questions', f"{m['answerable']}", f"{m['unanswerable']} unanswerable on purpose"),
            ('Strict pass', f"{m['correct_answerable']}/{m['answerable']}", f"failure rate {_pct(m['strict_rate'], 1)} (95% CI {_ci(m['strict_ci'])})"),
            ('Correct refusals', f"{m['correct_abstentions']}/{m['unanswerable']}", 'unanswerable questions declined with the right reason'),
            ('False refusals', f"{m['mechanical'].get('false_refusal', 0)}", 'answerable questions declined')]
    adj = s.get('adjudicated')
    if adj:
        kpis.insert(2, ('Adjudicated engine errors', f"{adj['engine_errors']}/{adj['answerable']}",
                        f"{_pct(adj['engine_rate'], 1)} (95% CI {_ci(adj['engine_ci'])})" + (f"; up to {adj['engine_errors_upper']} counting unparsed" if adj['engine_errors_upper'] != adj['engine_errors'] else '')))
    body = '<div class="kpis">' + ''.join(f'<div class="kpi"><div class="l">{e(a)}</div><div class="v">{e(b)}</div><div class="s">{e(c)}</div></div>' for a, b, c in kpis) + '</div>'
    if not adj:
        prior = next((x for x in reversed(sets) if x.get('adjudicated')), None)
        body += _empty(f"Set {s['set']} has not been adjudicated, so its real engine-error rate is unknown; the strict rate above also counts "
                       f"format differences and defensible definitions." + (f" Latest adjudicated set: {prior['set']}, {prior['adjudicated']['engine_errors']} engine errors in "
                       f"{prior['adjudicated']['answerable']} answerable questions ({_pct(prior['adjudicated']['engine_rate'], 1)}, 95% CI {_ci(prior['adjudicated']['engine_ci'])})." if prior else ''))
    f = s.get('frontier')
    if f:
        errors = f['mechanical'].get('execution_error', 0)
        body += (f'<p class="note">Frontier model with SQL tools on the same set: {f["correct_answerable"]}/{f["answerable"]} strict passes'
                 f'{f", {errors} execution errors (no answer)" if errors else ""}' +
                 (f', {f["engine_errors"]} adjudicated engine errors ({_pct(f["engine_rate"], 1)})' if f.get('adjudicated') else ', not adjudicated') +
                 f'; known API cost {_usd(f["known_cost_usd"])} vs Agere {_usd(m["known_cost_usd"])} for the set.</p>')
    else:
        body += _empty(f"No frontier comparison run on set {s['set']}.")
    body += f'<p class="note">Run {e(m["file"])}, started {e(m["started"][:16])} UTC; {s["runs"]} full run(s) of this set recorded.</p>'
    return f'<section class="panel"><h2>Latest blind set: {e(s["set"])}</h2>{body}</section>'


def _trend_panel(r):
    sets = r.get('blind_sets') or []
    if not sets: return ''
    labels = [s['set'] for s in sets]
    series = [('Strict failure rate, first attempt', '--s2', [(s['first']['strict_rate'], s['first']['strict_ci']) for s in sets]),
              ('Strict failure rate, latest run', '--s3', [(s['latest']['strict_rate'], s['latest']['strict_ci']) for s in sets]),
              ('Adjudicated engine-error rate', '--s1', [((s['adjudicated'] or {}).get('engine_rate'), (s['adjudicated'] or {}).get('engine_ci')) if s.get('adjudicated') else None for s in sets])]
    rows = []
    for s in sets:
        a = s.get('adjudicated')
        rows.append([e(s['set']), str(s['runs']), f"{s['first']['strict_defects']}/{s['first']['answerable']} = {_pct(s['first']['strict_rate'])} ({_ci(s['first']['strict_ci'])})",
                     f"{s['latest']['strict_defects']}/{s['latest']['answerable']} = {_pct(s['latest']['strict_rate'])} ({_ci(s['latest']['strict_ci'])})",
                     (f"{a['engine_errors']}/{a['answerable']} = {_pct(a['engine_rate'])} ({_ci(a['engine_ci'])})" if a else '<span class="badge warn">not adjudicated</span>'),
                     (f"{s['frontier']['correct_answerable']}/{s['frontier']['answerable']}" if s.get('frontier') else '–')])
    table = _table(['Set', 'Runs', 'Strict failures, first attempt (95% CI)', 'Strict failures, latest run', 'Adjudicated engine errors', 'Frontier strict passes'], rows)
    return (f'<section class="panel"><h2>Trend across blind sets</h2>{_rate_chart(series, labels, "Failure rates by blind set")}{table}'
            '<p class="note">First attempt is the blind measurement: the set had not been seen. Later runs of the same set follow fixes made after seeing it, '
            'so they are no longer blind. Sets are in the order they were first run.</p></section>')


def _dpmo_panel(r):
    t = r.get('totals') or {}
    rows = []
    names = {'strict_first_attempt': 'Strict, first attempt of each blind set', 'strict_all_runs': 'Strict, every full blind run',
             'adjudicated': 'Adjudicated engine errors, latest adjudication per set'}
    for k, label in names.items():
        x = t.get(k) or {}
        if not x.get('opportunities'): rows.append([e(label), '–', '–', '–', '–', 'no data']); continue
        rows.append([e(label), f"{x['defects']}/{x['opportunities']}", _num(x['dpmo']), _num(x['sigma'], '.2f'), _ci(x['ci']), e(', '.join(x.get('sets') or []))])
    charts = (f'<div class="grid2"><div><h3 class="sub">p-chart: strict failures, first attempt per set</h3>{_p_chart(r.get("p_chart_strict"), "p-chart strict")}</div>'
              f'<div><h3 class="sub">p-chart: adjudicated engine errors</h3>{_p_chart(r.get("p_chart_adjudicated"), "p-chart adjudicated")}</div></div>')
    return (f'<section class="panel"><h2>Defects per million opportunities</h2>'
            f'{_table(["Measure", "Defects / opportunities", "DPMO", "Sigma level", "95% CI", "Sets"], rows, numeric=(1, 2, 3))}{charts}'
            '<p class="note">An opportunity is one answerable question. Sigma level uses the conventional 1.5-sigma shift (3.4 DPMO = 6 sigma). '
            'The under-3% engine-error target is 30,000 DPMO, about 3.4 sigma.</p></section>')


def _pareto_panel(r):
    adj = r.get('pareto_adjudicated') or []
    mech = r.get('pareto_mechanical') or []
    runs = (r.get('evidence') or {}).get('adjudicated_runs') or []
    left = _pareto(adj, 'Pareto of adjudicated failures') if adj else _empty('No saved adjudications; run bench/adjudicate.py on a blind run.')
    sets = {m['file']: m['set'] for m in r.get('runs') or []}
    right = _table(['Failure class (scorer only)', 'Count', 'Cumulative'], [[e(f"{i['mode']} {i['label']}" if i['mode'].startswith('FM-') else i['label']),
                                                                             str(i['count']), _pct(i['cumulative'])] for i in mech], numeric=(1, 2)) if mech else _empty('No scored failures.')
    return (f'<section class="panel"><h2>Defect Pareto by failure mode</h2><div class="grid2"><div><h3 style="font-size:14px;margin:0 0 6px">Adjudicated failures '
            f'(sets {e(", ".join(sets.get(x, "?") for x in runs)) or "none"}; bar: count · cumulative share)</h3>{left}</div><div><h3 style="font-size:14px;margin:0 0 6px">All full blind runs, scorer reasons</h3>{right}</div></div>'
            '<p class="note">FM ids refer to docs/agere/fmea.json. Engine errors are assigned to one failure mode by the adjudicator\'s stated reason. '
            '"Wrong value unadjudicated" is a failed exact match nobody has classified yet.</p></section>')


def _routes_panel(r):
    routes = r.get('routes') or {}
    if not routes: return f'<section class="panel"><h2>Cost per question by route</h2>{_empty("No routed answers recorded.")}</section>'
    rows = [[e(k), str(v['questions']), _usd(v['usd_per_question']), _usd(v['known_usd']), str(v['unknown_cost']),
             (f"{v['correct_answerable']}/{v['answerable']} = {_pct(v['strict_accuracy'])}" if v['answerable'] else '–')]
            for k, v in sorted(routes.items(), key=lambda kv: -kv[1]['questions'])]
    return (f'<section class="panel"><h2>Cost per question by route</h2>'
            f'{_table(["Route", "Questions", "USD per priced question", "Known USD", "Unpriced", "Strict accuracy (answerable)"], rows, numeric=(1, 2, 3, 4))}'
            '<p class="note">Latest full run of each blind set (the current pipeline). Costs are product API estimates at dated rates, not invoices; local hardware and review are excluded. '
            'Unpriced questions are not counted as free.</p></section>')


def _acceptance_panel(acc, smoke):
    parts = []
    if smoke:
        m = smoke['latest']
        parts.append(f'<p><span class="badge {"good" if smoke["passed"] else "bad"}">{"pass" if smoke["passed"] else "fail"}</span> '
                     f'Development + challenge regression: {m["correct"]}/{m["questions"]} (threshold {smoke["threshold"]}), run {e(m["file"])}.</p>'
                     f'<p class="note">{e(smoke["note"])}</p>')
    else:
        parts.append(_empty('No full development + challenge run recorded.'))
    if acc:
        chips = ''.join(f'<span class="chip{"" if v["passed"] == v["total"] else " bad"}">{e(k)} {v["passed"]}/{v["total"]}</span>' for k, v in acc['by_operation'].items())
        parts.append(f'<p><span class="badge {"good" if acc["passed"] == acc["total"] else "bad"}">{acc["passed"]}/{acc["total"]}</span> '
                     f'capability acceptance phrasings reached the right engine ({e(acc["file"])}; {acc["runs_recorded"]} runs recorded).</p>'
                     f'<div class="chips">{chips}</div>')
    else:
        parts.append(_empty('No acceptance run saved in bench/results/acceptance.'))
    return f'<section class="panel"><h2>Smoke and acceptance</h2>{"".join(parts)}</section>'


def _controls_panel(r):
    replay = (r.get('evidence') or {}).get('replay') or {}
    if not replay: return ''
    rows = [[e(k), str(v['fired']), str(v['on_wrong']), str(v['on_correct']), 'warning' if v['warnings'] else 'info'] for k, v in sorted(replay.items())]
    return (f'<section class="panel"><h2>Answer-level checks, replayed on past answers</h2>'
            f'{_table(["Check", "Fired", "On failed answers", "On passed answers", "Shown as"], rows, numeric=(1, 2, 3))}'
            '<p class="note">The checks in clinical_core/agere/clinical/answer_checks.py re-run on every preserved blind answer. They annotate answers with warnings; '
            'they never change a number or refuse an answer. Firing on a failed answer is an association, not proof of the cause.</p></section>')


def _learning_panel(learning):
    if not learning:
        return f'<section class="panel"><h2>Learning store</h2>{_empty("No stack connected, so the learning summary is not shown.")}</section>'
    statuses = learning.get('statuses') or {}; routes = learning.get('routes') or {}
    kpis = [('Verified training pairs', learning.get('verified_training_pairs')), ('User corrections', learning.get('corrections')),
            ('Questions recorded', sum(statuses.values()) if statuses else 0)]
    body = '<div class="kpis">' + ''.join(f'<div class="kpi"><div class="l">{e(a)}</div><div class="v">{e(_num(b, ",") if isinstance(b, int) else b)}</div></div>' for a, b in kpis) + '</div>'
    if routes: body += _table(['Route', 'Questions'], [[e(k), str(v)] for k, v in sorted(routes.items(), key=lambda kv: -kv[1])], numeric=(1,))
    gaps = learning.get('top_gaps') or []
    if gaps: body += '<p class="note">Most frequent decline reasons:</p>' + _table(['Reason', 'Count'], [[e(g['reason'][:160]), str(g['count'])] for g in gaps[:8]], numeric=(1,))
    return f'<section class="panel"><h2>Learning store</h2>{body}<p class="note">Live product usage, separate from the benchmark. Verified pairs can include wrong answers that passed verification (FMEA FM-23).</p></section>'


def _definitions_panel():
    items = [
        ('Blind set', 'Fifty questions (some unanswerable on purpose) written by a separate agent that never saw Agere\'s code, each with an inspectable reference SQL answer. Sets are lettered in the order written.'),
        ('Strict failure', 'The answer does not exactly match the reference: wrong number, wrong shape (a count where a rate was asked), a refusal, an execution error, or the same number formatted differently. Strict failures overstate real errors.'),
        ('Engine error', 'An independent adjudicator (a frontier model given the question, the reference definition and both answers) judged that Agere misread the question or the data: wrong population, wrong table, versions counted as entities, an ignored instruction, wrong units, or a refusal when the data exists.'),
        ('Not an error', 'Format only (same numbers, different column names, rounding or layout) and defensible definition (the question was ambiguous and Agere used a reasonable standard reading). These are counted, but not as engine errors.'),
        ('Correct refusal', 'For an unanswerable question, Agere declines and names the missing data.'),
        ('95% interval', 'Wilson score interval for the error rate given the number of questions; with 44 answerable questions a set cannot resolve differences smaller than about 10 points.'),
        ('DPMO / sigma', 'Defects per million opportunities, one opportunity per answerable question; sigma level is the conventional Six Sigma conversion with a 1.5-sigma shift.'),
        ('Data', 'All figures are on synthetic patients. Adjudications are saved in bench/results/adjudications; the 2026-10-13 ones were recovered from the session log because they had not been saved at the time.'),
    ]
    return '<section class="panel"><h2>What counts as an error</h2><dl class="defs">' + ''.join(f'<dt>{e(a)}</dt><dd>{e(b)}</dd>' for a, b in items) + '</dl></section>'
