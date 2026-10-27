"""Offline fitting/calibration/release from explicit approved classification data.

No discovery of benchmark files, legacy answer stores or factory labels. This
compares a classical lexical head and the existing pinned-encoder head on the
same separately authored families. Runtime native answer checks remain intact.
"""
import hashlib
import json
import os
import time
from pathlib import Path
import numpy as np
from clinical_core.contracts import digest


def load(manifest):
    from clinical_core.agere.clinical import component_learning as C
    path=C.checked_path(manifest);spec=json.loads(path.read_text())
    if spec.get('schema')!='approved-component-data/1':raise ValueError('Explicit approved-component-data manifest required')
    allowed=C.labels(spec['role']);rows=[];groups={};questions={};patients={};seen={}
    if not isinstance(spec.get('provenance'),str) or not spec['provenance'].strip():raise ValueError('Dataset provenance is required')
    for source in spec['files']:
        p=C.checked_path(path.parent/source['path'])
        raw=p.read_bytes()
        if hashlib.sha256(raw).hexdigest()!=source['sha256']:raise ValueError('Approved input content changed')
        for line in raw.decode().splitlines():
            if not line.strip():continue
            row=json.loads(line)
            if set(row)-{'question','accepted_labels','family','split','source_kind','id','patient_id','source_digest','review_id'}:
                raise ValueError('Classification inputs must not contain clinical answers or answer keys')
            if row.get('source_kind') not in ('user_review','authored_engineering','machine_review'):raise ValueError('Unapproved supervision source')
            if row['source_kind']=='machine_review' and spec.get('allow_machine_supervision') is not True:raise ValueError('Machine supervision needs an explicit manifest declaration')
            if not isinstance(row.get('question'),str) or not row['question'].strip() or not isinstance(row.get('family'),str) or not row['family'].strip():raise ValueError('Question and source family are required')
            if row.get('split') not in ('fit','calibration','verification'):raise ValueError('Use separate fit/calibration/verification families')
            acceptable=row.get('accepted_labels')
            if not isinstance(acceptable,list) or not acceptable or any(x not in allowed for x in acceptable):raise ValueError('Explicit acceptable component labels required')
            split=row['split'];q=' '.join(row['question'].split()).casefold().rstrip('?.!')
            for values,key in ((groups,row['family']),(questions,q),(patients,row.get('patient_id'))):
                if key is None:continue
                if key in values and values[key]!=split:raise ValueError('Source family, patient or wording crosses training/verification boundaries')
                values[key]=split
            if q in seen:
                if set(seen[q])!=set(acceptable):raise ValueError('Conflicting reviewed labels for the same wording')
                continue
            seen[q]=acceptable
            rows.append({**row,'label':acceptable[0]})
    by={split:[r for r in rows if r['split']==split] for split in ('fit','calibration','verification')}
    if any(not r for r in by.values()) or len({r['label'] for r in by['fit']})<2:raise ValueError('Need separate nonempty fit/calibration/verification data and two fit labels')
    return spec,by,digest({'manifest':spec,'rows':rows})


class Lexical:
    identity=None
    def encode(self,questions):return np.zeros((len(questions),0))


def matrix(rows,encoder,kind):
    from clinical_core.agere.clinical import learned_router as LR
    return LR.features([r['question'] for r in rows],Lexical() if kind=='lexical' else encoder)


def probabilities(model,rows,encoder):
    from clinical_core.agere.clinical.learned_router import softmax
    return softmax((matrix(rows,encoder,model['feature_kind'])@np.asarray(model['weights']).T+np.asarray(model['bias']))/model['temperature'])


def acceptable(label,row,*,shape=False):
    accepted=row['accepted_labels']
    # Native answer checking accepts either scalar representation. A numeric
    # quantity must not become a format error merely because it was labelled
    # count rather than value. Source units/arithmetic remain downstream checks.
    return label in accepted or bool(shape and label in ('count','value') and set(accepted)&{'count','value'})


def native_shape_baseline(rows):
    from clinical_core.agere.clinical.analysis_graph import requested_shape
    labels=[requested_shape(row['question']) for row in rows]
    correct=[acceptable(label,row,shape=True) for label,row in zip(labels,rows)]
    return {'kind':'native_shape_rules','cases':len(rows),'argmax_acceptability':sum(correct)/len(rows),
        'coverage':sum(label is not None for label in labels)/len(rows),
        'wrong_confident':sum(label is not None and not ok for label,ok in zip(labels,correct)),
        'encoder_calls_per_prediction':0}


def score(model,rows,encoder):
    p=probabilities(model,rows,encoder);best=p.argmax(1)
    shape=model.get('version')=='answer-shape-head/1'
    correct=np.array([acceptable(model['labels'][i],row,shape=shape) for i,row in zip(best,rows)])
    used=p.max(1)>=model['threshold']
    result={'cases':len(rows),'argmax_acceptability':float(correct.mean()),'coverage':float(used.mean()),
        'accepted_decision_acceptability':float(correct[used].mean()) if used.any() else None,
        'wrong_confident':int((used & ~correct).sum()),'encoder_calls_per_prediction':int(model['feature_kind']!='lexical')}
    if shape:
        from clinical_core.agere.clinical.analysis_graph import requested_shape
        native=np.array([acceptable(requested_shape(row['question']),row,shape=True) for row in rows])
        result['native_useful_improvements']=int((used & correct & ~native).sum())
    return result


def threshold(model,rows,encoder):
    """Calibrate only on calibration families; verification never tunes a threshold."""
    p=probabilities(model,rows,encoder);best=p.argmax(1);confidence=p.max(1)
    correct=np.array([acceptable(model['labels'][i],row,shape=model.get('version')=='answer-shape-head/1') for i,row in zip(best,rows)])
    candidates=[float(t) for t in sorted(set(confidence)) if correct[confidence>=t].all()]
    return min(candidates) if candidates else 1.01


def run(manifest,root,*,stack=None,encoder=None,install=False):
    from clinical_core.agere.clinical import component_learning as C,learned_router as LR,tier_router as TR
    from tools.training import train_all as T
    spec,by,dataset=load(manifest);root=C.checked_path(root);role=spec['role'];allowed=C.labels(role)
    # Force local-only model loading. Training never acquires provider credentials.
    os.environ['HF_HUB_OFFLINE']='1';os.environ['TRANSFORMERS_OFFLINE']='1'
    started=time.monotonic();prior=C.models(root);parent=next((m for m in reversed(prior) if m['role']==role),None)
    feedback=C.last_feedback(root,role)
    if parent and feedback and feedback['accepted'] is False and feedback['revision']>parent['qualification'].get('feedback_revision',0) and dataset==parent['qualification'].get('dataset'):
        return {'status':'review_required','revision':parent['id'],'provider_calls':0,
            'reason':'Rejected release cannot be retrained/reactivated from the unchanged supervision. Supply corrected reviewed data.'}
    trainer_identity=digest({str(p.name):hashlib.sha256(p.read_bytes()).hexdigest()
        for p in (Path(__file__),Path(T.__file__))})
    job=digest([dataset,C.contract(role),trainer_identity,C.feedback_revision(root)])
    if parent and parent['qualification'].get('job')==job:
        if install and parent['qualification']['selected'] and parent['state']=='candidate':
            C.rollback(root,role,parent['id'],'Explicit activation of unchanged qualified candidate')
        return {'status':'unchanged','revision':parent['id'],'dataset':dataset,'provider_calls':0}
    owned=[]
    if encoder is None and stack is not None:
        encoder,_=T.encoder_for(stack);owned.append(encoder)
    candidates=[('lexical',Lexical()),*([('encoder',encoder)] if encoder is not None else [])]
    try:
        for reference in spec.get('encoders') or []:
            extra=C.load_encoder(reference,root);owned.append(extra);candidates.append(('encoder',extra))
    except Exception:
        for own in owned:own.close()
        raise
    variants=[]
    try:
        for kind,enc in candidates:
            fitted=T.fit_head(by['fit'],by['calibration'],allowed,enc)
            model={'version':LR.VERSION if role=='router' else TR.VERSION if role=='tier_router' else 'answer-shape-head/1',
                'labels':allowed,'feature_kind':kind,'encoder_identity':getattr(enc,'identity',None),
                'encoder_reference':C.encoder_reference(enc) if kind=='encoder' else None,
                'temperature':float(fitted['temperature']),'weights':fitted['W'].tolist(),'bias':fitted['b'].tolist(),
                'cues':LR.cue_names(),'lexical_scale':LR.LEXICAL_SCALE,'threshold':1.01,'supervision':'classification feedback; not clinical qualification'}
            model['threshold']=threshold(model,by['calibration'],enc)
            variants.append((model,score(model,by['verification'],enc)))
        # Accuracy first, then reliable coverage, then avoiding an encoder call.
        quality=lambda item:(item[1]['argmax_acceptability'],-item[1]['wrong_confident'],item[1]['coverage'],-item[1]['encoder_calls_per_prediction'])
        # A more accurate argmax can still make unsafe accepted decisions.
        # Keep a useful calibrated alternative when another variant fails the
        # existing zero-confident-error qualification, rather than reject all.
        eligible=[item for item in variants if item[1]['coverage']>0 and item[1]['wrong_confident']==0 and
                  (role!='shape' or item[1]['native_useful_improvements']>0)]
        chosen,metric=max(eligible or variants,key=quality)
        majority=max(allowed,key=lambda label:sum(r['label']==label for r in by['fit']))
        majority_baseline={'kind':'majority','argmax_acceptability':sum(acceptable(majority,r,shape=role=='shape') for r in by['verification'])/len(by['verification']),
                  'coverage':1.0,'wrong_confident':sum(not acceptable(majority,r,shape=role=='shape') for r in by['verification']),'encoder_calls_per_prediction':0}
        baseline=native_shape_baseline(by['verification']) if role=='shape' else majority_baseline
        current=C.runtime_model(role,root)
        if current:
            old=json.loads(current.read_text())
            old_encoder=next((e for _,e in candidates if getattr(e,'identity',None)==old.get('encoder_identity')),None)
            if old.get('feature_kind')=='lexical' or old_encoder is not None:
                baseline={**score(old,by['verification'],old_encoder),'kind':'current_release'}
        # An adjunct shape hint must add safe decisions beyond the existing
        # rules. Its unserved low-confidence argmaxes need not replace rules.
        selected=bool(eligible) and (baseline['kind']=='native_shape_rules' or quality((chosen,metric))>quality((None,baseline)))
        qualification={'job':job,'dataset':dataset,'trainer_identity':trainer_identity,
            'feedback_revision':C.feedback_revision(root),
            'input_files':spec['files'],'provenance':spec['provenance'],
            'selected':selected,'baseline':baseline,'majority_baseline':majority_baseline,'chosen':metric,
            'selection_policy':'Require zero confident errors; shape hints must add accepted correct decisions beyond native rules. Compare eligible variants, preserve all comparisons and the majority reference.',
            'comparisons':[{'feature_kind':m['feature_kind'],'encoder_identity':m.get('encoder_identity'),**s} for m,s in variants],
            'family_counts':{k:len({r['family'] for r in v}) for k,v in by.items()},'threshold_fitted_on':'calibration only',
            'source_kinds':sorted({r['source_kind'] for rows in by.values() for r in rows}),
            'clinical_qualification':False,'cost_basis':'Native encoder invocation count; no API-dollar saving claimed',
            'round':1+(parent['qualification'].get('round',0) if parent else 0)}
        release=C.register(root,role,chosen,qualification,parent=parent['id'] if parent else None,activate=bool(install and selected))
        receipt={'status':'activated' if release['activated'] else 'qualified_candidate' if selected else 'not_selected',
            'revision':release['id'],'parent':release['parent'],'qualification':qualification,
            'model':release['model_path'],'seconds':time.monotonic()-started,'provider_calls':0}
        out=root/'runs';out.mkdir(parents=True,exist_ok=True);(out/(release['id']+'.json')).write_text(json.dumps(receipt,indent=2)+'\n')
        return receipt
    finally:
        for own in owned:own.close()
