"""Read-only creation library over the existing answer-bound artifact store.

Catalog entries describe recorded files, not current clinical authority. Opening
an artifact checks its recorded evidence and bytes, and reports current source
status separately. No generation, work creation or provider call occurs here.
"""
import hashlib
import json
import re
from pathlib import Path
from types import SimpleNamespace

from . import answer_context as A
from .receipts import fingerprint

CONTEXT = re.compile(r'ANS-[0-9a-f]{24}')
ARTIFACT = re.compile(r'DLV-[0-9A-F]{12}')
GENERIC = {'Evidence and next steps', 'Evidence review brief', 'Explore the saved evidence'}


def presentation_experience(context_id, title, request):
    """Choose a screen for an explicitly requested program, never clinical fit."""
    text = ' '.join(str(s or '') for s in (title, request)).lower()
    if re.search(r'\bckd\b', text) and re.search(r'program|nephrolog', text):
        return {'kind': 'ckd', 'availability': 'population_workspace',
                'url': '/ask?app=ckd&context='+context_id+'#program',
                'agent_execution_available': False}
    return None


def _folder(path, context_id, artifact_id):
    if not CONTEXT.fullmatch(context_id or '') or not ARTIFACT.fullmatch(artifact_id or ''):
        raise ValueError('Choose an exact saved creation')
    root = A.root(path).resolve()
    folder = root / context_id / 'deliverables' / artifact_id
    if not folder.resolve().is_relative_to(root) or any(p.is_symlink() for p in (root/context_id, folder.parent, folder)):
        raise ValueError('Creation is outside this workspace')
    return folder


def _read(file):
    if file.is_symlink():
        raise ValueError('Linked creation files are unavailable')
    return json.loads(file.read_text())


def catalog(path):
    """Read small metadata only; never load every analytical investigation."""
    root = A.root(path)
    items = []
    for file in root.glob('ANS-*/deliverables/DLV-*/spec.json'):
        context_id, artifact_id = file.parents[2].name, file.parent.name
        try:
            folder = _folder(path, context_id, artifact_id)
            spec = _read(folder/'spec.json')
            if spec.get('id') != artifact_id or spec.get('workspace') != context_id:
                continue
            if not A.snapshot_path(root/context_id/'answer.json').is_file():
                continue
            title = spec.get('title')
            workflow = root/context_id/'workflow.json'
            if (not title or title in GENERIC) and workflow.is_file():
                stage = next((s for s in _read(workflow).get('steps', []) if s.get('deliverable_id') == artifact_id), {})
                title = stage.get('title') or title
            title = title or spec.get('request') or 'Untitled creation'
            listing = root/context_id/'listing.json'
            origin = _read(listing).get('row', {}) if listing.is_file() else {}
            saved = spec.get('status') == 'delivered' and (folder/'artifact.html').is_file()
            generation = spec.get('generation') or spec.get('mode')
            kind = 'page' if generation == 'frontier' else 'brief' if spec.get('template') == 'brief' else 'explorer' if spec.get('template') == 'explore' else 'report'
            items.append({'id': artifact_id, 'context_id': context_id,
                'title': title, 'request': spec.get('request') or '',
                'question': origin.get('question') or '', 'created_at': spec.get('created_at'),
                'kind': kind, 'generation': generation, 'template': spec.get('template'),
                'status': 'saved' if saved else 'failed', 'recorded_status': spec.get('status'),
                'rows': (spec.get('verification') or {}).get('rows'),
                'failure': spec.get('failure') if not saved else None,
                'formats': list((spec.get('formats') or {}).keys()) if saved else [],
                'answer_url': '/ask?context='+context_id,
                'url': '/ask?context='+context_id+'&artifact='+artifact_id+'#gallery',
                'experience': presentation_experience(context_id, title, spec.get('request'))})
        except (OSError, ValueError, TypeError, KeyError):
            continue  # A malformed attempt cannot hide other saved creations.
    items.sort(key=lambda s: (s.get('created_at') or '', s['id']), reverse=True)
    return {'schema': 'creation-gallery/1', 'items': items,
            'saved': sum(s['status'] == 'saved' for s in items),
            'failed': sum(s['status'] == 'failed' for s in items),
            'source_status_checked_on_open': True, 'new_paid_api_calls': 0}


def preview(path, context_id, artifact_id):
    """Open an immutable saved version for inspection, with current state visible.

    Historical viewing grants no ability to create work or reuse a cohort. The
    browser embeds the returned document in a network-disabled, opaque sandbox.
    """
    folder = _folder(path, context_id, artifact_id)
    spec = _read(folder/'spec.json')
    if spec.get('id') != artifact_id or spec.get('workspace') != context_id:
        raise ValueError('Creation belongs to another answer')
    if spec.get('status') != 'delivered' or not (folder/'artifact.html').is_file():
        return {'id': artifact_id, 'context_id': context_id, 'status': 'failed',
                'failure': spec.get('failure') or 'This attempt did not produce a saved page.', 'html': None}
    from .compose import load
    for name in ('artifact.html', 'body.html', 'bundle.json', 'bundle.json.gz'):
        if (folder/name).is_symlink():
            raise ValueError('Linked creation files are unavailable')
    if A.snapshot_path(A.root(path)/context_id/'answer.json').is_symlink():
        raise ValueError('Linked answer snapshots are unavailable')
    context = A.load(path, context_id)
    _, bundle, _, doc = load(SimpleNamespace(dir=A.root(path)/context_id, slug=context_id), artifact_id)
    bound = [bundle.results[rid] for rid, r in bundle.receipts.items() if r.get('tool') == 'saved_answer']
    if (len(bound) != 1 or bound[0].get('id') != context_id or bundle.meta.get('workspace') != context_id
            or bound[0].get('source') != context['source']
            or not (bound[0].get('dependencies') or {}).get('derivation')
            or bound[0]['dependencies']['derivation'] != context['dependencies']['derivation']):
        raise ValueError('Saved creation has no matching original answer binding')
    raw = (folder/'artifact.html').read_bytes()
    if spec.get('generation') == 'saved_report':
        recorded = (spec.get('formats') or {}).get('html') or {}
        if recorded.get('filename') != 'artifact.html' or hashlib.sha256(raw).hexdigest() != recorded.get('sha256'):
            raise ValueError('Saved page differs from its recorded version')
    elif spec.get('bundle_fingerprint') != fingerprint(bundle.to_json()['results'])[:16]:
        raise ValueError('Saved creation evidence changed')
    if not doc:
        raise ValueError('Saved creation has no rendered page')
    historical = bool(context.get('invalidated') or context.get('stale') or context.get('source_review_required'))
    return {'id': artifact_id, 'context_id': context_id, 'status': 'saved', 'html': raw.decode(),
            'historical': historical, 'invalidated': context.get('invalidated', False),
            'stale': context.get('stale', False), 'source_review_required': context.get('source_review_required', False),
            'source': context['source'], 'clinical_execution_authorized': False,
            'new_paid_api_calls': 0}
