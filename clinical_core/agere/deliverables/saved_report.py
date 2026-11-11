"""Render an exact saved answer as a reusable, printable report, without a model.

This presents existing evidence; it neither reinterprets it nor authors a new
service design. Bespoke composition remains an explicit separate mode.
"""
from html import escape
import json
import re
from pathlib import Path
from . import answer_context as A
from .receipts import Bundle, fingerprint

VERSION = 'saved-report/6'


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


def create(path, request, *, context_url=None):
    if set(request)-{'context_id','template','request','retained_inspection'} or not {'context_id','template','request'}<=set(request):
        raise ValueError('An exact saved answer, report layout and request are required')
    if request['template'] not in ('command', 'brief', 'explore'):
        raise ValueError('Choose command, brief or explore layout')
    if not isinstance(request['request'], str) or not 1 <= len(request['request']) <= 4000:
        raise ValueError('A report request up to 4000 characters is required')
    retained=request.get('retained_inspection',False)
    if type(retained)is not bool:raise ValueError('retained_inspection must be explicit boolean')
    # Load and validate the original once. A report consumes its answer and
    # displayed evidence, not a second copy of every analyst attempt/SQL result.
    # The immutable native context remains the full audit record; its sealed
    # derivation and live source/work state are rechecked before publication.
    context = A.load(path, request['context_id'], current=not retained)
    if retained:context={**context,'retained_inspection':True}
    from .patient_dashboard import render as dashboard, exports
    from .patient_evidence import projection
    import hashlib
    import tempfile
    from urllib.parse import urlencode
    answer = context['answer']
    records = [row for part in sections(answer) for row in part.get('table') or []]
    evidence = projection(context)
    review_work = A.work(path, context)
    bundle = Bundle({'workspace': context['id'], 'as_of': context['source']['as_of'],
                     'synthetic': True, 'retained_inspection': retained,
                     'clinical_execution_authorized': False,
                     'scope': 'Exact saved report evidence with a sealed reference to the complete native investigation.'})
    reference = {k: context[k] for k in (
        'id', 'question', 'original_question', 'source', 'answer', 'patient_ids',
        'input_patient_ids', 'workflow', 'dependencies', 'context_policy_identity',
        'source_disposition', 'dependency_validation', 'stale', 'invalidated') if k in context}
    reference['native_context_reference'] = {
        'schema': 'saved-report-native-reference/1', 'context_id': context['id'],
        'derivation': context['dependencies']['derivation'],
        'url': context_url or '/api/stack/action-context?' + urlencode({'context': context['id'], 'view': 'native'}),
        'complete_investigation_retained': True}
    bundle.add('saved_answer', {'context_id': context['id']}, reference,
               definition=context['question'], as_of=context['source']['as_of'],
               data_fp=fingerprint(context['source']), source='server-saved /ask execution')
    from collections import Counter
    bundle.add('review_work', {'context_id': context['id']},
               {'items': review_work, 'counts': dict(Counter(i['state'] for i in review_work))},
               definition='Internal review states, not outreach, enrollment or clinical completion',
               data_fp=fingerprint(review_work))
    bundle.add('saved_report_evidence', {'context_id': context['id']}, evidence,
               definition='Complete supporting evidence projected by the saved report renderer',
               data_fp=fingerprint(evidence), source=reference['native_context_reference']['url'])
    validator = A.bundle_validator(path, bundle)
    title = {'brief': 'Evidence review brief', 'command': 'Evidence and next steps',
             'explore': 'Explore the saved evidence'}[request['template']]
    status = answer.get('status') or 'Not recorded'
    payload = {'context_id': context['id'], 'question': context['question'], 'source': context['source'],
               'answer': answer, 'patient_ids': context['patient_ids'], 'workflow': context.get('workflow') or [],
               'review_work': review_work, 'evidence': evidence}
    renderers = {name: Path(__file__).with_name(name).read_text() for name in
                 ('saved_report.py', 'patient_dashboard.py', 'patient_evidence.py', 'patient_report_pdf.py')}
    key = 'DLV-' + fingerprint([VERSION, renderers, context['id'], request, payload, context_url])[:12].upper()
    # Export intent belongs to this request, not a prior question or report.
    want_pdf = bool(re.search(r'\bpdf\b', request['request'], re.I))
    pdf_url = '/api/stack/action-deliverable?' + urlencode({'context': context['id'], 'id': key, 'format': 'pdf'}) if want_pdf else None
    body = dashboard(context, title=title, pdf_url=pdf_url, evidence=evidence, layout=request['template'])
    pdf_bytes = None
    pdf_receipt = None
    if want_pdf:
        from .patient_report_pdf import render as pdf_report
        with tempfile.TemporaryDirectory(prefix='agere-report-') as tmp:
            output = Path(tmp) / 'artifact.pdf'
            pdf_receipt = pdf_report(context, output, evidence=evidence)
            pdf_bytes = output.read_bytes()
            pdf_receipt.pop('output', None)
    formats = {'html': {'filename': 'artifact.html', 'mime': 'text/html; charset=utf-8',
                        'sha256': hashlib.sha256(body.encode()).hexdigest()}}
    downloads = exports(context, title=title, evidence=evidence, layout=request['template'])
    for name, data in downloads.items():
        formats[name] = {'filename':'artifact.'+name,
                         'mime':'application/json; charset=utf-8' if name=='json' else 'text/csv; charset=utf-8',
                         'sha256':hashlib.sha256(data).hexdigest()}
    if pdf_bytes is not None:
        formats['pdf'] = {'filename': 'artifact.pdf', 'mime': 'application/pdf',
                          'sha256': hashlib.sha256(pdf_bytes).hexdigest(), **pdf_receipt}
    spec = {'schema': 'agere-deliverable/1', 'id': key, 'title': title, 'status': 'delivered',
            'workspace': context['id'], 'request': request['request'], 'template': request['template'],
            'created_at': A.now(), 'revision': 1, 'synthetic': True, 'mockup': False,
            'generation': 'saved_report','retained_inspection':retained,'clinical_execution_authorized':False, 'renderer_version': VERSION, 'formats': formats,
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
    folder = _dir(ws) / key
    folder.mkdir(parents=True, exist_ok=True)
    for name, data in downloads.items():
        (folder / ('artifact.'+name)).write_bytes(data)
    if pdf_bytes is not None:
        (folder / 'artifact.pdf').write_bytes(pdf_bytes)
    _save(ws, key, doc=body, body=body, bundle=bundle, spec=spec)
    return {**spec, 'html': body}


def read_format(path, context_id, deliverable_id, format_name):
    """Download only a source-bound, hash-verified recorded report format."""
    import hashlib
    from types import SimpleNamespace
    from .compose import load
    if not isinstance(context_id,str) or not re.fullmatch(r'ANS-[0-9a-f]{24}',context_id):
        raise ValueError('A saved answer context is required')
    if not A.snapshot_path(A.root(path)/context_id/'answer.json').is_file():
        raise ValueError('Saved answer not found in this warehouse')
    ws=SimpleNamespace(dir=A.root(path)/context_id,slug=context_id)
    spec, bundle, _, _ = load(ws, deliverable_id, documents=False)
    if spec.get('status') != 'delivered' or spec.get('generation') != 'saved_report':
        raise ValueError('No saved report format is available')
    saved=[rid for rid,r in bundle.receipts.items()if r.get('tool')=='saved_answer']
    if(len(saved)!=1 or bundle.meta.get('workspace')!=context_id
       or bundle.receipts[saved[0]].get('args',{}).get('context_id')!=context_id
       or bundle.results.get(saved[0],{}).get('id')!=context_id):
        raise ValueError('Report bundle belongs to a different saved context')
    # Verify recorded historical bytes without rewriting their original source
    # projection. Creation still rechecks the live projection after rendering.
    # This performs the full current-context read once, after the bundle is
    # loaded; avoid a duplicate inspection read before these stricter checks.
    A.bundle_validator(path, bundle, source_projection=False)()
    if format_name not in ('html', 'pdf', 'json', 'csv'):
        raise ValueError('Choose a recorded HTML, PDF, JSON or CSV format')
    item = (spec.get('formats') or {}).get(format_name)
    if not item or item.get('filename') != 'artifact.' + format_name:
        raise ValueError('Requested format has not been generated')
    data = (Path(ws.dir) / 'deliverables' / deliverable_id / item['filename']).read_bytes()
    if hashlib.sha256(data).hexdigest() != item.get('sha256'):
        raise ValueError('Report bytes differ from their recorded rendering')
    return data, item['mime'], 'Agere-report.' + format_name

