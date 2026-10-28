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

VERSION = 'saved-report/2'


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
    from .patient_dashboard import render as dashboard
    from .patient_evidence import projection
    from .patient_report_pdf import render as pdf_report
    import hashlib
    import tempfile
    from urllib.parse import urlencode
    answer = context['answer']
    records = [row for part in sections(answer) for row in part.get('table') or []]
    evidence = projection(context)
    title = request['request']
    status = answer.get('status') or 'Not recorded'
    payload = {'context_id': context['id'], 'question': context['question'], 'source': context['source'],
               'answer': answer, 'patient_ids': context['patient_ids'], 'workflow': context.get('workflow') or [],
               'review_work': A.work(path, context), 'evidence': evidence}
    renderers = {name: Path(__file__).with_name(name).read_text() for name in
                 ('saved_report.py', 'patient_dashboard.py', 'patient_evidence.py', 'patient_report_pdf.py')}
    key = 'DLV-' + fingerprint([VERSION, renderers, context['id'], request, payload])[:12].upper()
    wanted = ' '.join([title, context['question']] + [str(s.get('quote', '')) for s in context.get('workflow') or []])
    want_pdf = bool(re.search(r'\bpdf\b', wanted, re.I))
    pdf_url = '/api/stack/action-deliverable?' + urlencode({'context': context['id'], 'id': key, 'format': 'pdf'}) if want_pdf else None
    body = dashboard(context, title=title, pdf_url=pdf_url, evidence=evidence)
    pdf_bytes = None
    pdf_receipt = None
    if want_pdf:
        with tempfile.TemporaryDirectory(prefix='agere-report-') as tmp:
            output = Path(tmp) / 'artifact.pdf'
            pdf_receipt = pdf_report(context, output, evidence=evidence)
            pdf_bytes = output.read_bytes()
            pdf_receipt.pop('output', None)
    formats = {'html': {'filename': 'artifact.html', 'mime': 'text/html; charset=utf-8',
                        'sha256': hashlib.sha256(body.encode()).hexdigest()}}
    if pdf_bytes is not None:
        formats['pdf'] = {'filename': 'artifact.pdf', 'mime': 'application/pdf',
                          'sha256': hashlib.sha256(pdf_bytes).hexdigest(), **pdf_receipt}
    spec = {'schema': 'agere-deliverable/1', 'id': key, 'title': title, 'status': 'delivered',
            'workspace': context['id'], 'request': title, 'template': request['template'],
            'created_at': A.now(), 'revision': 1, 'synthetic': True, 'mockup': False,
            'generation': 'saved_report', 'renderer_version': VERSION, 'formats': formats,
            'costs': {'total_usd': 0, 'known_api_estimate_usd': 0, 'api_cost_known': True,
                      'scope': 'No model requests for rendering; local compute and upstream analysis are excluded.'},
            'new_paid_api_calls': 0, 'model_calls': [],
            'verification': {'passed': True, 'basis': 'Deterministic presentation of the exact saved answer and executed supporting evidence; no new clinical claims or acceptance.',
                             'analytical_status': status, 'answer_fingerprint': fingerprint(answer), 'rows': len(records),
                             'note_patients': len(evidence['notes']), 'supporting_tables': len(evidence['supporting_tables'])}}
    # Recheck after local rendering, before publishing either artifact.
    spec['dependency_validation'] = validator()
    from .compose import _save, _dir
    ws = A.workspace(path, context)
    if pdf_bytes is not None:
        folder = _dir(ws) / key
        folder.mkdir(parents=True, exist_ok=True)
        (folder / 'artifact.pdf').write_bytes(pdf_bytes)
    _save(ws, key, doc=body, body=body, bundle=bundle, spec=spec)
    return {**spec, 'html': body}


def read_format(path, context_id, deliverable_id, format_name):
    """Download only a source-bound, hash-verified recorded report format."""
    import hashlib
    from .compose import load
    context = A.load(path, context_id)
    ws = A.workspace(path, context)
    spec, bundle, _, _ = load(ws, deliverable_id)
    if spec.get('status') != 'delivered' or spec.get('generation') != 'saved_report':
        raise ValueError('No saved report format is available')
    A.bundle_validator(path, bundle)()
    if format_name not in ('html', 'pdf'):
        raise ValueError('Choose a recorded HTML or PDF format')
    item = (spec.get('formats') or {}).get(format_name)
    if not item or item.get('filename') != 'artifact.' + format_name:
        raise ValueError('Requested format has not been generated')
    data = (Path(ws.dir) / 'deliverables' / deliverable_id / item['filename']).read_bytes()
    if hashlib.sha256(data).hexdigest() != item.get('sha256'):
        raise ValueError('Report bytes differ from their recorded rendering')
    return data, item['mime'], 'Agere-report.' + format_name

