"""Read-only inspection of frozen development predictions, never clinical truth."""
import json
from collections import Counter
from . import learning_cohorts as cohorts, semantic_student as student
from ...contracts import digest


def coverage(stack, key):
    output,plan,targets=cohorts.connected(stack,key)
    items=json.loads((output/'queue/items.json').read_text());by_id={r['id']:r for r in items}
    categories=dict(Counter(by_id[r['item_id']]['stream'] for r in targets))
    concepts=dict(Counter(r['target']['concept'] for r in targets))
    return {'sampled_fields':len(items),'sampled_categories':len({r['stream'] for r in items}),
        'fitted_target_categories':len(categories),'category_counts':categories,'fitted_target_concepts':len(concepts),
        'concept_counts':concepts,'unmarked_fields':sum(r['target'] is None for r in items),
        'meaning':'Acquisition breadth is not learned semantic coverage. Targets remain selected, unreviewed development examples.'}


def inspect(stack, key, kind, *, outcome='disagreement', offset=0, limit=12):
    if kind not in ('lexical','minilm','windowed_minilm') or outcome not in ('all','agreement','disagreement'):
        raise ValueError('Known model kind and development outcome filter required')
    if type(offset) is not int or offset<0 or type(limit) is not int or not 1<=limit<=30:
        raise ValueError('Bounded development result page required')
    if kind=='windowed_minilm':
        from .windowed_student import model
        path,manifest,rows=model(stack,key)
    else:
        output,plan,rows,_=cohorts.dataset(stack,key);path=output/'models'/kind
        report=json.loads((output/'models/run.json').read_text())
        if report['status'] not in ('complete_shadow_only','partial_shadow_only') or kind not in report['models']:
            raise ValueError('Completed shadow model required')
        manifest=student.verify(path)
        if (cohorts.queue.sha(path/'manifest.json')!=report['models'][kind]['manifest_sha256'] or
            manifest['kind']!=kind or manifest['dataset_digest']!=digest(rows) or
            manifest['provenance']['teacher_snapshot_sha256']!=cohorts.queue.sha(output/'dataset/manifest.json')):
            raise ValueError('Cohort model or supervision binding changed')
    validation=[r for r in rows if r['partition']=='validation']
    scored=[json.loads(line) for line in (path/'validation-predictions.jsonl').read_text().splitlines()]
    if len(scored)!=len(validation) or any(p['id']!=r['id'] or p['expected']!=r['labels'] for p,r in zip(scored,validation)):
        raise ValueError('Saved development predictions do not match frozen supervision')
    selected=[];agreement=0
    threshold=manifest['calibration']['threshold']
    for source,pred in zip(validation,scored):
        matches=source['labels']==pred['labels'];agreement+=int(matches)
        if outcome=='agreement' and not matches or outcome=='disagreement' and matches:continue
        selected.append({'id':source['id'],'partition':'validation','target':source['target'],'context':source['input'],
            'teacher_labels':source['labels'],'teacher_reading_id':source['teacher']['reading_id'],
            'model_labels':pred['labels'],'disagreement_axes':[a for a in source['labels'] if source['labels'][a]!=pred['labels'][a]],
            'confidence':pred['confidence'],'passes_development_threshold':threshold is not None and student.selectable(pred) and pred['confidence']>=threshold,
            'automatic_acceptance':False,'clinical_accuracy_label':False})
    return {'cohort_id':key,'kind':kind,'model_identity':cohorts.queue.sha(path/'manifest.json'),
        'score_reference':'unreviewed_machine_teacher','validation_rows':len(validation),'agreement_rows':agreement,
        'disagreement_rows':len(validation)-agreement,'outcome':outcome,'offset':offset,'limit':limit,'total':len(selected),
        'rows':selected[offset:offset+limit],'calibration':manifest['calibration'],'release':manifest['release'],
        'limits':['Disagreement identifies a model/teacher difference, not which interpretation is correct.',
            'Development validation is neither clinical truth nor an independent clinical evaluation.',
            'Inspecting or reviewing these sources does not move them into fitting or change saved model results.'],
        'new_model_calls':0,'reviews_created':0}
