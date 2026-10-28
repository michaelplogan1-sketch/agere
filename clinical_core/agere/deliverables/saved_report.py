"""Render an exact saved answer as a reusable, printable report, without a model.

This presents existing evidence; it neither reinterprets it nor authors a new
service design. Bespoke composition remains an explicit separate mode.
"""
from html import escape
import json
import re
from pathlib import Path
from . import answer_context as A
from .receipts import fingerprint

VERSION = 'saved-report/1'


def value(v):
    if v is None:
        return 'Unknown / not supplied'
    if isinstance(v, (dict, list)):
        return json.dumps(v, ensure_ascii=False, indent=2)
    return str(v)


def fields(data):
    return '<dl>' + ''.join('<dt>' + escape(str(k).replace('_', ' ')) + '</dt><dd><pre>' +
                           escape(value(v)) + '</pre></dd>' for k, v in data.items()) + '</dl>'


def sections(answer):
    """Keep compound outputs, including empty tables and nonpatient results."""
    yield answer
    for section in answer.get('sections') or []:
        if isinstance(section, dict) and isinstance(section.get('answer'), dict):
            yield from sections(section['answer'])


def create(path, request):
    if set(request) != {'context_id', 'template', 'request'}:
        raise ValueError('An exact saved answer, report layout and request are required')
    if request['template'] not in ('command', 'brief', 'explore'):
        raise ValueError('Choose command, brief or explore layout')
    if not isinstance(request['request'], str) or not 1 <= len(request['request']) <= 4000:
        raise ValueError('A report request up to 4000 characters is required')
    context, bundle = A.evidence_bundle(path, request['context_id'])
    validator = A.bundle_validator(path, bundle)
    validator()
    answer = context['answer']
    records = []
    outputs = []
    for index, part in enumerate(sections(answer)):
        rows = part.get('table')
        if isinstance(rows, list):
            records.extend(rows)
        # Do not truncate evidence, sources, missing facts or compound outputs.
        info = {k: v for k, v in part.items() if k not in ('table', 'sections')}
        outputs.append('<section class="panel"><h2>Saved output ' + str(index + 1) + '</h2>' + fields(info) + '</section>')
    cards = []
    for index, row in enumerate(records):
        row = row if isinstance(row, dict) else {'value': row}
        label = row.get('patient_id') or 'Result row ' + str(index + 1)
        cards.append('<details class="record"><summary>' + escape(str(label)) + '</summary>' + fields(row) + '</details>')
    payload = {'context_id': context['id'], 'question': context['question'], 'source': context['source'],
               'answer': answer, 'patient_ids': context['patient_ids'], 'workflow': context.get('workflow') or [],
               'review_work': A.work(path, context)}
    data = json.dumps(payload, ensure_ascii=False).replace('<', '\\u003c').replace('>', '\\u003e').replace('&', '\\u0026')
    title = request['request']
    status = answer.get('status') or 'Not recorded'
    replacements={'TITLE':escape(title),'QUESTION':escape(context['question']),
                  'STATUS':escape(status),'DATE':escape(str(context['source']['as_of'])),
                  'ROWS':str(len(records)),'OUTPUTS':''.join(outputs),
                  'RECORDS':''.join(cards) or '<p>No result rows were returned.</p>',
                  'WORK':fields({'declared_workflow':payload['workflow'],'saved_review_work':payload['review_work']}),
                  'DATA':data}
    body=re.sub(r'%%([A-Z]+)%%',lambda match:replacements[match.group(1)],TEMPLATE)
    key = 'DLV-' + fingerprint([VERSION, Path(__file__).read_text(), context['id'], request, payload])[:12].upper()
    spec = {'schema': 'agere-deliverable/1', 'id': key, 'title': title, 'status': 'delivered',
            'workspace': context['id'], 'request': title, 'template': request['template'],
            'created_at': A.now(), 'revision': 1, 'synthetic': True, 'mockup': False,
            'generation': 'saved_report', 'renderer_version': VERSION,
            'costs': {'total_usd': 0, 'known_api_estimate_usd': 0, 'api_cost_known': True,
                      'scope': 'No model requests for rendering; local compute and upstream analysis are excluded.'},
            'new_paid_api_calls': 0, 'model_calls': [],
            'verification': {'passed': True, 'basis': 'Deterministic presentation of the exact saved answer; no new clinical claims or acceptance.',
                             'analytical_status': status, 'answer_fingerprint': fingerprint(answer), 'rows': len(records)}}
    # Recheck immediately before saving, including changes to internal work.
    spec['dependency_validation'] = validator()
    from .compose import _save
    _save(A.workspace(path, context), key, doc=body, body=body, bundle=bundle, spec=spec)
    return {**spec, 'html': body}


TEMPLATE = '''<!doctype html><html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>%%TITLE%%</title>
<style>
:root{color-scheme:light;--ink:#15352e;--line:#d5dfda;--muted:#536b62}*{box-sizing:border-box}body{margin:0;background:#f3f5ef;color:var(--ink);font:15px/1.6 system-ui,sans-serif}header{padding:44px max(5vw,22px);background:#163f34;color:white}header small{letter-spacing:.15em;text-transform:uppercase;color:#c8e2cd}h1{font:normal clamp(30px,4vw,48px)/1.15 Georgia,serif;max-width:1000px;overflow-wrap:anywhere}header p{max-width:1000px}main{max-width:1280px;margin:auto;padding:28px 24px}.toolbar,.metrics{display:flex;flex-wrap:wrap;gap:12px;margin-bottom:22px}.metric,.panel,.record{background:white;border:1px solid var(--line);border-radius:13px;padding:20px;margin-bottom:18px}.metric{flex:1;min-width:180px;margin:0}.metric strong{display:block;font:28px Georgia,serif}.metric span,.muted{color:var(--muted)}.notice{border-left:4px solid #a06c20;background:#fff9ed;padding:17px;margin-bottom:24px}button,input{font:inherit;border:1px solid #9cb3a5;padding:10px 16px;border-radius:8px;background:white;color:var(--ink)}button{cursor:pointer}input{flex:1;min-width:200px}h2{font:24px Georgia,serif}dl{display:grid;grid-template-columns:minmax(140px,1fr) minmax(0,4fr);margin:0}dt,dd{border-bottom:1px solid var(--line);padding:10px 8px;margin:0}dt{color:var(--muted);overflow-wrap:anywhere}pre{font:13px/1.55 system-ui,sans-serif;white-space:pre-wrap;overflow-wrap:anywhere;margin:0}summary{font-weight:650;cursor:pointer}details[open]>summary{margin-bottom:12px}.record[hidden]{display:none}footer{padding:28px;border-top:1px solid var(--line);color:var(--muted)}@media(max-width:650px){dl{grid-template-columns:1fr}dt{border:0;padding-bottom:0}h1{font-size:30px}}@media print{@page{size:auto;margin:15mm}body{background:white;font-size:10pt}header{background:white;color:var(--ink);padding:0}header small{color:var(--muted)}main{padding:0;max-width:none}.toolbar{display:none}.panel,.record{border-radius:0;break-inside:auto}h1{font-size:24pt}.record[hidden]{display:block}.record>summary{display:block}.record>dl{display:grid!important}pre{font-size:9pt}dt,dd{break-inside:avoid}footer{padding:10px 0}}
</style></head><body><header><small>Agere / Evidence-linked report / Synthetic data</small><h1>%%TITLE%%</h1><p>%%QUESTION%%</p></header><main>
<div class="metrics"><div class="metric"><strong>%%STATUS%%</strong><span>Saved analytical status</span></div><div class="metric"><strong>%%DATE%%</strong><span>Source snapshot / simulated clinical clock</span></div><div class="metric"><strong>%%ROWS%%</strong><span>Result rows / not necessarily unique patients</span></div></div>
<p class="notice">This report presents the saved analysis and its recorded uncertainty. Rendering adds no clinical interpretation or acceptance. Proposed work and saved review tasks do not establish enrollment, clinical care, orders or shipments.</p>
<div class="toolbar"><button id="print">Print / save PDF</button><button id="download">Download evidence JSON</button><button id="expand">Expand all records</button><label for="search">Find a result</label><input id="search" type="search" placeholder="Search all saved row fields"></div>
%%OUTPUTS%%<section><h2>Complete result records</h2><p class="muted">All returned rows are retained. Search affects this view; the evidence download preserves the complete answer.</p>%%RECORDS%%</section>
<section class="panel"><h2>Declared workflow and saved review work</h2>%%WORK%%</section></main><footer>Reusable report template. No model request was used to render this document. Upstream analytical costs and local compute are separate.</footer>
<script id="evidence" type="application/json">%%DATA%%</script><script>
const evidence=JSON.parse(document.getElementById('evidence').textContent),records=[...document.querySelectorAll('.record')];
document.getElementById('search').addEventListener('input',e=>{const q=e.target.value.toLowerCase();records.forEach(r=>r.hidden=!r.textContent.toLowerCase().includes(q))});
document.getElementById('expand').addEventListener('click',()=>records.forEach(r=>r.open=true));
document.getElementById('print').addEventListener('click',()=>{records.forEach(r=>r.open=true);window.print()});
document.getElementById('download').addEventListener('click',()=>{const u=URL.createObjectURL(new Blob([JSON.stringify(evidence,null,2)],{type:'application/json'})),a=document.createElement('a');a.href=u;a.download='Agere-evidence.json';a.click();setTimeout(()=>URL.revokeObjectURL(u),0)});
</script></body></html>'''
