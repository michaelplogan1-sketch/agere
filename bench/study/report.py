"""Render a run's analysis.json as a self-contained HTML report and a Markdown version.

    .venv/bin/python bench/study/report.py --run-dir artifacts/study/<run_id>

Reads analysis.json only. Every number links to the run file it was computed from (paths relative to the run folder, so
the folder can be moved or zipped). Headline claims are only those analyze.py found with an interval excluding no
difference. The HTML loads nothing external; it follows the reader's light/dark preference and prints cleanly.
"""
from __future__ import annotations

import argparse
import html
import json
from pathlib import Path
import sys

if __package__ in (None, ''):
    sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

CSS = """
:root{--bg:#fbfaf7;--fg:#1d1d1b;--muted:#5f5e5a;--line:#dedcd5;--accent:#1f5f8b;--card:#ffffff;--good:#1d6b3a;--bad:#9b2c2c}
@media (prefers-color-scheme:dark){:root:not([data-theme="light"]){--bg:#151614;--fg:#e8e6df;--muted:#a3a19a;--line:#33342f;--accent:#7fb6e0;--card:#1d1e1b;--good:#7fcf98;--bad:#f0a0a0}}
:root[data-theme="dark"]{--bg:#151614;--fg:#e8e6df;--muted:#a3a19a;--line:#33342f;--accent:#7fb6e0;--card:#1d1e1b;--good:#7fcf98;--bad:#f0a0a0}
*{box-sizing:border-box}
body{margin:0;background:var(--bg);color:var(--fg);font:15px/1.5 -apple-system,BlinkMacSystemFont,"Segoe UI",Helvetica,Arial,sans-serif}
main{max-width:1100px;margin:0 auto;padding:24px 16px 64px}
h1{font-size:1.6rem;margin:0 0 4px}h2{font-size:1.2rem;margin:32px 0 8px;border-bottom:1px solid var(--line);padding-bottom:4px}
h3{font-size:1rem;margin:20px 0 6px}
p.meta{color:var(--muted);margin:0 0 16px;font-size:.9rem}
a{color:var(--accent);text-decoration:none}a:hover{text-decoration:underline}
.wrap{overflow-x:auto;border:1px solid var(--line);border-radius:6px;background:var(--card)}
table{border-collapse:collapse;width:100%;font-variant-numeric:tabular-nums;font-size:.88rem}
th,td{padding:6px 10px;border-bottom:1px solid var(--line);text-align:right;white-space:nowrap}
th:first-child,td:first-child,td.l,th.l{text-align:left}
th{color:var(--muted);font-weight:600}
tr:last-child td{border-bottom:none}
.ci{color:var(--muted);font-size:.8rem}
ul.claims li{margin:4px 0}
.none{color:var(--muted)}
.good{color:var(--good)}.bad{color:var(--bad)}
@media print{body{background:#fff;color:#000}a{color:#000}.wrap{border:none;overflow:visible}main{max-width:none;padding:0}
h2{break-after:avoid}table{page-break-inside:auto}tr{page-break-inside:avoid}}
"""


def esc(x):
    return html.escape(str(x))


def fmt(v, kind):
    if v is None:
        return '–'
    if kind == 'pct':
        return f'{v * 100:.1f}%'
    if kind == 'usd':
        sign = '-' if v < 0 else ''
        return f'{sign}${abs(v):,.4f}' if abs(v) < 100 else f'{sign}${abs(v):,.0f}'
    if kind == 's':
        return f'{v:.1f}s'
    if kind == 'p':
        return f'{v:.3g}'
    return str(v)


class Out:
    """Writes the same content as HTML and Markdown."""

    def __init__(self):
        self.h, self.m = [], []

    def link(self, text, source):
        return (f'<a href="{esc(source)}">{esc(text)}</a>', f'[{text}]({source})') if source else (esc(text), text)

    def num(self, v, kind, source, ci=None):
        t = fmt(v, kind)
        h, m = self.link(t, source)
        if ci:
            c = f'[{fmt(ci[0], kind)}, {fmt(ci[1], kind)}]'
            h += f' <span class="ci">{esc(c)}</span>'
            m += f' {c}'
        return h, m

    def heading(self, level, text):
        self.h.append(f'<h{level}>{esc(text)}</h{level}>')
        self.m.append('#' * level + ' ' + text + '\n')

    def para(self, h, m=None):
        self.h.append(f'<p>{h}</p>')
        self.m.append((m if m is not None else h) + '\n')

    def table(self, header, rows):
        """rows: list of lists of (html, md) cells or plain strings."""
        def cell(c):
            return c if isinstance(c, tuple) else (esc(c), str(c))
        th = ''.join(f'<th{" class=l" if i == 0 else ""}>{esc(x)}</th>' for i, x in enumerate(header))
        body = ''.join('<tr>' + ''.join(f'<td>{cell(c)[0]}</td>' for c in r) + '</tr>' for r in rows)
        self.h.append(f'<div class="wrap"><table><thead><tr>{th}</tr></thead><tbody>{body}</tbody></table></div>')
        self.m.append('| ' + ' | '.join(header) + ' |\n|' + '---|' * len(header) + '\n'
                      + ''.join('| ' + ' | '.join(cell(c)[1].replace('|', '/') for c in r) + ' |\n' for r in rows))


def render(run_dir):
    run_dir = Path(run_dir)
    a = json.loads((run_dir / 'analysis.json').read_text())
    o = Out()
    title = f"Agere study run {a['run_id']}"
    git = a.get('git') or {}
    o.heading(1, title)
    meta_h = (f"Generated {esc(a['generated_at'])} from <a href=\"analysis.json\">analysis.json</a>; run record "
              f"<a href=\"{esc(a['run_source'])}\">run.json</a>. Commit {esc((git.get('commit') or '?')[:12])}"
              f"{' (working tree had uncommitted changes)' if git.get('dirty') else ''}; pricing file sha256 "
              f"{esc((a.get('pricing_sha256') or '?')[:12])}. Bootstrap: {a['bootstrap']['resamples']} paired resamples, seed "
              f"{a['bootstrap']['seed']}. Primary comparisons use each arm's first (cold) pass.")
    meta_m = (f"Generated {a['generated_at']} from [analysis.json](analysis.json); run record [run.json]({a['run_source']}). "
              f"Commit {(git.get('commit') or '?')[:12]}{' (working tree had uncommitted changes)' if git.get('dirty') else ''}; "
              f"pricing file sha256 {(a.get('pricing_sha256') or '?')[:12]}. Bootstrap: {a['bootstrap']['resamples']} paired "
              f"resamples, seed {a['bootstrap']['seed']}. Primary comparisons use each arm's first (cold) pass.")
    o.h.append(f'<p class="meta">{meta_h}</p>')
    o.m.append(meta_m + '\n')
    qs = (a.get('questions') or {}).get('fingerprint') or {}
    if qs:
        o.para('Question sets: ' + '; '.join(f"{esc(k)} n={v['n']} (sha256 {esc(v['sha256'][:12])})" for k, v in qs.items()),
               'Question sets: ' + '; '.join(f"{k} n={v['n']} (sha256 {v['sha256'][:12]})" for k, v in qs.items()))

    o.heading(2, 'Headline findings')
    if a['headlines']:
        items_h = ''.join(f"<li>{esc(h['text'])} <span class=\"ci\">({' · '.join(f'<a href="{esc(s)}">{esc(s)}</a>' for s in h['sources'])})</span></li>"
                          for h in a['headlines'])
        o.h.append(f'<ul class="claims">{items_h}</ul>')
        o.m.append(''.join(f"- {h['text']} ({', '.join(f'[{s}]({s})' for s in h['sources'])})\n" for h in a['headlines']))
    else:
        o.h.append('<p class="none">No difference between arm A and another arm cleared its 95% interval (or McNemar test) in this run, '
                   'so no headline claim is made.</p>')
        o.m.append('No difference between arm A and another arm cleared its 95% interval (or McNemar test) in this run, so no '
                   'headline claim is made.\n')

    for set_name, blk in a['sets'].items():
        o.heading(2, f'Question set: {set_name}')
        o.heading(3, 'Arms (pass 1, cold)')
        rows = []
        for arm, ab in blk['arms'].items():
            s = ab['source']
            rows.append([o.link(arm, s), ((a['arms'].get(arm) or {}).get('label') or ''),
                         o.link(f"{ab['correct']}/{ab['n']}", s), o.num(ab['accuracy']['value'], 'pct', s, ab['accuracy']['ci95']),
                         o.num(ab['cost_per_question']['value'], 'usd', s, ab['cost_per_question']['ci95']),
                         o.num(ab['cost_per_correct']['value'], 'usd', s, ab['cost_per_correct']['ci95']),
                         o.num(ab['latency_median']['value'], 's', s, ab['latency_median']['ci95']),
                         o.num(ab['latency_p90']['value'], 's', s, ab['latency_p90']['ci95']),
                         o.num(ab['free_share']['value'], 'pct', s, ab['free_share']['ci95'])])
        o.table(['Arm', 'What answers', 'Correct', 'Accuracy', 'Cost / question', 'Cost / correct', 'Median latency',
                 'p90 latency', 'Free tier'], rows)
        for title_, key in (('Arm A against the other arms (paired)', 'comparisons'), ('Ablations: A minus each F arm (paired)', 'ablations')):
            comps = blk.get(key) or {}
            if not comps:
                continue
            o.heading(3, title_)
            rows = []
            for arm, c in comps.items():
                s = c['sources'][1]
                d = c['differences']
                rows.append([o.link(arm, s), (c.get('component') or (a['arms'].get(arm) or {}).get('label') or ''),
                             o.link(str(c['n_paired']), s), o.link(str(c['a_only_correct']), s), o.link(str(c['x_only_correct']), s),
                             o.num(c['mcnemar_p'], 'p', s),
                             o.num(d['accuracy']['a_minus_x'], 'pct', s, d['accuracy']['ci95']),
                             o.num(d['cost_per_question']['a_minus_x'], 'usd', s, d['cost_per_question']['ci95']),
                             o.num(d['latency_median']['a_minus_x'], 's', s, d['latency_median']['ci95']),
                             o.num(d['latency_p90']['a_minus_x'], 's', s, d['latency_p90']['ci95']),
                             o.num(d['free_share']['a_minus_x'], 'pct', s, d['free_share']['ci95'])])
            o.table(['Arm', 'Arm / component', 'Paired', 'Only A right', 'Only arm right', 'McNemar p', 'Δ accuracy (A−arm)',
                     'Δ cost / question', 'Δ median latency', 'Δ p90 latency', 'Δ free share'], rows)
        if blk.get('warm'):
            w = blk['warm']
            d = w['differences']
            s = w['sources'][0]
            o.heading(3, 'Arm A warm (pass 2) minus cold (pass 1)')
            o.table(['Paired', 'Δ accuracy', 'Δ cost / question', 'Δ median latency', 'Δ free share'],
                    [[o.link(str(w['n_paired']), s), o.num(d['accuracy']['a_minus_x'], 'pct', s, d['accuracy']['ci95']),
                      o.num(d['cost_per_question']['a_minus_x'], 'usd', s, d['cost_per_question']['ci95']),
                      o.num(d['latency_median']['a_minus_x'], 's', s, d['latency_median']['ci95']),
                      o.num(d['free_share']['a_minus_x'], 'pct', s, d['free_share']['ci95'])]])
        o.heading(3, 'Monthly cost projection (measured cost per question × volume)')
        rows = []
        for arm, p in blk['projection'].items():
            s = p['source']
            rows.append([o.link(arm, s), o.num(p['cost_per_question'], 'usd', s)]
                        + [o.num(p['monthly_usd'][v]['value'], 'usd', s, p['monthly_usd'][v]['ci95']) for v in ('1000', '10000', '100000')])
        o.table(['Arm', 'Cost / question', '1,000 / month', '10,000 / month', '100,000 / month'], rows)

    o.heading(2, 'Certificate validity')
    if a['certificates']:
        rows = [[o.link(f"{c['arm']} p{c['pass']}", c['source']), c['tier'], o.link(str(c['certified_answers']), c['source']),
                 o.link(str(c['errors']), c['source']), o.num(c['observed_error'], 'pct', c['source']),
                 o.num(c['observed_upper'], 'pct', c['source']), o.link(fmt(c['bound'], 'pct'), c['source']),
                 ('yes' if c['within_bound'] else 'no' if c['within_bound'] is not None else '–'), c.get('note') or '']
                for c in a['certificates']]
        o.table(['Arm-pass', 'Tier', 'Certified answers', 'Errors', 'Observed error', 'Upper limit', 'Certified bound', 'Within bound', 'Note'], rows)
    else:
        o.para('No answer in this run carried a certificate.', 'No answer in this run carried a certificate.')

    o.heading(2, 'Repeatability (pass 1 vs pass 2)')
    if a['repeatability']:
        rows = [[o.link(r['arm'], r['sources'][1]), o.link(str(r['n_paired']), r['sources'][1]),
                 o.link(str(r['identical_answers']), r['sources'][1]), o.num(r['identical_share'], 'pct', r['sources'][1]),
                 o.link(str(r['same_correctness']), r['sources'][1]), 'warm by design' if r['warm_second_pass'] else 'cold']
                for r in a['repeatability']]
        o.table(['Arm', 'Paired', 'Identical answers', 'Share', 'Same correctness', 'Pass 2'], rows)
    else:
        o.para('Single pass: repeatability needs --repeat 2.', 'Single pass: repeatability needs --repeat 2.')

    o.heading(2, 'Arm-passes and ledger spend')
    rows = [[o.link(f"{p['arm']} p{p['pass']}", p['source']), o.link(f"{p['correct']}/{p['n']}", p['source']),
             o.num(p['cost_usd'], 'usd', p['source']), o.num(p['ledger_cost_usd'], 'usd', p['source'].replace('score.json', 'ledger.jsonl')),
             o.link(str(p['ledger_calls']), p['source'].replace('score.json', 'ledger.jsonl')),
             o.num(p['median_seconds'], 's', p['source'].replace('score.json', 'timings.json'))] for p in a['passes']]
    o.table(['Arm-pass', 'Correct', 'Per-question cost sum', 'Ledger cost', 'Ledger calls', 'Median latency'], rows)
    o.para(f"Total ledger spend for the run: {o.num(a['total_spend_usd'], 'usd', 'analysis.json')[0]}.",
           f"Total ledger spend for the run: {o.num(a['total_spend_usd'], 'usd', 'analysis.json')[1]}.")

    page = (f'<!doctype html><html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">'
            f'<title>Agere study report</title><style>{CSS}</style></head><body><main>{"".join(o.h)}</main></body></html>')
    (run_dir / 'report.html').write_text(page)
    (run_dir / 'report.md').write_text('\n'.join(o.m))
    return run_dir / 'report.html'


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--run-dir', type=Path, required=True)
    a = ap.parse_args()
    print(render(a.run_dir))


if __name__ == '__main__':
    main()
