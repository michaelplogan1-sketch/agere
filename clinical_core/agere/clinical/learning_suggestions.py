"""Display acquisition attempts separately from successful interpretation receipts."""
from collections import Counter,defaultdict
import json
from . import learning_queue as queue


def failure_kind(error):
    error=error.casefold()
    if any(x in error for x in ('overlength','context bound','complete received text','no truncation')):return 'context_limit'
    if any(x in error for x in ('distribution','probability','unexpected model','typed answer','choices required')):return 'provider_response_validation'
    if any(x in error for x in ('source','target','span','quote')):return 'source_binding'
    if any(x in error for x in ('http','timeout','urlerror')):return 'provider_transport'
    return 'other_execution_failure'


def attempts(stack,item_id=None):
    root,_,_,_=queue.connected(stack);run_hash=queue.sha(root/'run.json');rows=[]
    for file in sorted(root.glob('suggestions-*.json')):
        batch=json.loads(file.read_text())
        if batch.get('queue_run_sha256')!=run_hash:continue
        for r in batch['rows']:
            if item_id is None or r['item_id']==item_id:
                rows.append({**r,'batch_file':file.name,'failure_kind':failure_kind(r['error']) if r.get('error') else None})
    return rows


def summary(stack):
    rows=attempts(stack);by_item=defaultdict(list)
    for r in rows:
        if r.get('reading_id'):by_item[r['item_id']].append(r)
    paired=[]
    for key,items in by_item.items():
        teachers={r['provider']:r for r in items if r['provider'] in ('jev','perplexity')}
        if set(teachers)=={'jev','perplexity'}:
            paired.append({'item_id':key,'disagreement_axes':[a for a in queue.reader.AXES if teachers['jev']['labels'][a]!=teachers['perplexity']['labels'][a]]})
    return {'synthetic':True,'attempt_rows':len(rows),'source_items':len({r['item_id'] for r in rows}),
        'successful_readings':len({r['reading_id'] for r in rows if r.get('reading_id')}),
        'errors_by_kind':dict(Counter(r['failure_kind'] for r in rows if r.get('error'))),
        'successful_attempts_by_provider':dict(Counter(r['provider'] for r in rows if r.get('reading_id'))),
        'paired_teacher_comparisons':paired,'reviewed_labels_created':0,
        'scope':'Execution and disagreement accounting only. Labels-created count refers to this acquisition runner; explicit user reviews are audited separately. No retrieval-recall, accuracy or consensus-truth score.'}
