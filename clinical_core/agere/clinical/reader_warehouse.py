"""Execute the factorized reader over mapped observable warehouse text.

Produces a queryable annotation sidecar; never mutates clinical workflow results.
"""
from pathlib import Path
from collections import Counter
import hashlib
import json
import sqlite3
import time

from ...contracts import digest
from ..warehouse import Warehouse
from .evidence_reader import AXES, EvidenceReader, candidates
from .relation_learning import write


def annotate(root, model_path, clinical_run, output, *, limit=None, progress=None, warehouse_path=None, document_extractions=None):
    root, output, clinical_run = Path(root), Path(output), Path(clinical_run)
    output.mkdir(parents=True, exist_ok=True)
    if (output/'run.json').exists() or (output/'annotations.sqlite3').exists():
        raise ValueError('Fresh output directory required; use a shared prediction cache for reuse')
    source_run = json.loads((clinical_run/'run.json').read_text())
    if source_run.get('synthetic') is not True:
        raise ValueError('Synthetic source run required')
    model_card = json.loads((Path(model_path)/'model-card.json').read_text())
    from .reader_factory import load_reader
    reader = load_reader(model_path, Path(model_path)/'warehouse-predictions.sqlite3')
    warehouse = Warehouse(warehouse_path or root/'artifacts/agere-full/warehouse.sqlite3')
    db = sqlite3.connect(output/'annotations.sqlite3')
    db.executescript('''CREATE TABLE annotations
        (id TEXT PRIMARY KEY, patient TEXT, concept TEXT, assertion TEXT, subject TEXT,
         time TEXT, relation TEXT, priority TEXT, payload TEXT);
        CREATE INDEX priority_annotations ON annotations(priority);
        CREATE INDEX patient_annotations ON annotations(patient);
        CREATE INDEX relation_annotations ON annotations(relation, assertion, subject, time);''')
    started = time.perf_counter(); counts = Counter(); axes = {a: Counter() for a in AXES}
    examples = {}; batch = []; routing_counts = Counter(); reason_counts = Counter()
    def flush():
        if not batch:
            return
        predictions = reader.predict([r['input'] for r in batch])
        with db:
            for binding, prediction in zip(batch, predictions):
                value = {**binding, 'annotation': prediction, 'decision_authority': False,
                         'scope_applies_to_axes': model_card.get('scope_applies_to_axes', [])}
                labels = {a: prediction['axes'][a]['label'] for a in AXES}
                for a, label in labels.items():
                    axes[a][label] += 1
                key = digest([reader.identity, binding])
                routing = prediction.get('review_routing', {})
                priority = routing.get('priority', 'routine_annotation_review')
                routing_counts[priority] += 1
                reason_counts.update(r['code'] for r in routing.get('reasons', []))
                db.execute('INSERT INTO annotations VALUES (?,?,?,?,?,?,?,?,?)',
                    (key, binding['patient_id'], binding['input']['concept'], labels['assertion'], labels['subject'],
                     labels['time'], labels['relation'], priority, json.dumps(value)))
                examples.setdefault(labels['relation'], value)
        counts['annotation_bindings'] += len(batch); batch.clear()
    try:
        patients, snapshot = warehouse.snapshot(source_run['snapshot']['release'], source_run['snapshot']['as_of'])
        if snapshot != source_run['snapshot']:
            raise ValueError('Visible snapshot changed')
        ocr_path = Path(document_extractions) if document_extractions else (root/'artifacts/agere-full/document-extractions.json' if warehouse_path is None else None)
        ocr = {r['record_id']:r for r in json.loads(ocr_path.read_text())['records']} if ocr_path else {}
        for pid, patient in patients.items():
            if limit is not None and counts['patients'] >= limit:
                break
            records = patient['records']
            for r in records:
                if r['record_id'] in ocr:
                    extracted = ocr[r['record_id']]
                    if r['source_table'] != 'documents' or extracted['patient_id'] != pid:
                        raise ValueError('OCR binding mismatch')
                    r.update(extracted_text=extracted['text'], extraction_method=extracted['method'])
            for binding in candidates(pid, records, snapshot['as_of']):
                batch.append(binding)
                if len(batch) >= 1024:
                    flush()
            counts['patients'] += 1; counts['visible_records_processed'] += len(records)
            if progress is not None and counts['patients'] % 1000 == 0:
                progress({'patients': counts['patients'], 'annotations_written': counts['annotation_bindings'],
                          'elapsed_seconds': time.perf_counter()-started})
        flush()
        result = {'schema': 'factorized-reader-run/2', 'synthetic': True, 'counts': dict(counts),
            'axes': {k: dict(v) for k, v in axes.items()}, 'cache': dict(reader.stats),
            'elapsed_seconds': time.perf_counter()-started, 'new_paid_api_calls': 0,
            'clinical_decisions_changed': 0, 'source_run': source_run['cache_key_digest'], 'snapshot': snapshot,
            'model_identity': reader.identity, 'reader_schema': model_card['schema'],
            'reader_axes': model_card.get('runtime_axes'), 'patient_limit': limit,
            'review_routing': {'priorities': dict(routing_counts), 'reasons': dict(reason_counts),
                'scope': 'Engineering review triage, not clinical severity; all annotations still require review.'},
            'scope': 'Mapped narrative fields with an explicit candidate vocabulary; not all clinical concepts or all unstructured meaning.',
            'decision_authority': False, 'missing_documentation_implies_missing_care': False,
            'clinical_time_basis': 'Current means current relative to source account, not the warehouse cutoff.'}
        write(output/'run.json', result); write(output/'examples.json', examples)
        return result
    finally:
        db.close(); warehouse.close(); reader.close()


def search(path, *, patient=None, relation=None, assertion=None, subject=None, time=None, priority=None, limit=20, offset=0):
    path = Path(path)
    if not path.is_file():
        raise ValueError('Annotation database unavailable')
    if type(limit) is not int or not 1 <= limit <= 100 or type(offset) is not int or not 0 <= offset <= 1000000:
        raise ValueError('Invalid pagination')
    filters = {'relation': relation, 'assertion': assertion, 'subject': subject, 'time': time}
    where, params = [], []
    for axis, value in filters.items():
        if value is not None:
            if value not in AXES[axis]:
                raise ValueError('Unknown '+axis+' label')
            where.append(axis+'=?'); params.append(value)
    if priority is not None:
        if priority not in {'context_conflict', 'model_disagreement', 'routine_annotation_review'}:
            raise ValueError('Unknown review priority')
        where.append('priority=?'); params.append(priority)
    if patient is not None:
        if not isinstance(patient, str) or not 1 <= len(patient) <= 200:
            raise ValueError('Invalid patient ID')
        where.append('patient=?'); params.append(patient)
    clause = ' WHERE '+' AND '.join(where) if where else ''
    db = sqlite3.connect(path.resolve().as_uri()+'?mode=ro', uri=True)
    try:
        columns = {r[1] for r in db.execute('PRAGMA table_info(annotations)')}
        if priority is not None and 'priority' not in columns:
            raise ValueError('Review priority unavailable for this historical run')
        count = db.execute('SELECT COUNT(*) FROM annotations'+clause, params).fetchone()[0]
        rows = [json.loads(r[0]) for r in db.execute('SELECT payload FROM annotations'+clause+' ORDER BY patient,id LIMIT ? OFFSET ?', [*params, limit, offset])]
        return {'synthetic': True, 'total_bindings': count, 'offset': offset, 'limit': limit, 'rows': rows,
                'decision_authority': False, 'scope': 'Filter machine annotation proposals; counts are not clinical population prevalence.'}
    finally:
        db.close()
