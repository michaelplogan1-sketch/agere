"""Complete source-window features with a target-focused view; shadow use only.

Reuses pinned offline encoder windows and the existing head/calibration math.
The fixed-length baseline, failed fits, teacher labels and splits are untouched.
"""
from pathlib import Path
import json
import time
import numpy as np
from sklearn.linear_model import LogisticRegression
from threadpoolctl import threadpool_limits
from ...contracts import digest
from ... import local_encoders
from . import learning_cohorts as cohorts, semantic_student as student, semantic_reader as reader

VERSION='windowed-source-student/1'
DIRECTORY='windowed-minilm-v1'
WINDOW={'size':256,'overlap':32,'pooling':'mean',
        'aggregation':'concatenate normalized means of all windows and target-intersecting windows; weight by newly covered token counts'}


def identity():
    return digest([VERSION,student.sha(__file__),student.code_identity(),cohorts.identity()])


def features(encoder, packets):
    vectors=[];coverage=[]
    for packet in packets:
        text=student.model_text(packet);windows=encoder.windows(text,size=WINDOW['size'],overlap=WINDOW['overlap'])
        if not windows or windows[0]['start']!=0 or windows[-1]['end']!=len(text):
            raise ValueError('Complete source character coverage required')
        source_start=len('Concept: '+packet['concept']+'\nSource: ')
        target_start=text.index('[TARGET]',source_start);target_end=text.index('[/TARGET]',target_start)+len('[/TARGET]')
        weights=[];focused=[];end=0;char_end=0
        for window in windows:
            if (window['token_start']>end or window['token_end']<=end or window['start']>char_end or
                window['text']!=text[window['start']:window['end']]):raise ValueError('Gap or invalid explicit source window')
            weights.append(window['token_end']-end);end=window['token_end'];char_end=window['end']
            focused.append(window['start']<target_end and window['end']>target_start)
        if not any(focused):raise ValueError('Target absent from explicit source windows')
        encoded=encoder.encode([w['text'] for w in windows],batch_size=8)
        if encoded.shape[0]!=len(windows) or not np.isfinite(encoded).all():raise ValueError('Invalid window representations')
        def mean(mask):
            value=np.average(encoded[mask],axis=0,weights=np.asarray(weights)[mask]);norm=np.linalg.norm(value)
            if not np.isfinite(norm) or norm<=0:raise ValueError('Invalid aggregated window representation')
            return value/norm
        vectors.append(np.concatenate([mean(np.ones(len(windows),dtype=bool)),mean(np.asarray(focused))]))
        coverage.append({'input_digest':digest(text),'input_characters':len(text),'unique_input_tokens':end,
            'windows':[{k:w[k] for k in ('start','end','token_start','token_end')} for w in windows],
            'target_start':target_start,'target_end':target_end,'target_windows':sum(focused),'complete_source_coverage':True,'truncated':False})
    return np.asarray(vectors,dtype=np.float32),coverage


def fit(stack, key):
    output,plan,rows,_=cohorts.dataset(stack,key);path=output/DIRECTORY
    if path.exists():raise ValueError('This representation was already attempted; preserve its result')
    path.mkdir();attempt={'cohort_id':key,'implementation':identity(),'status':'running','window_policy':WINDOW,
        'reason':'Fixed-length MiniLM rejected overlength inputs; this additive representation keeps all source windows. No label/split changes.',
        'development_only':True,'automatic_acceptance':False}
    cohorts.write(path/'attempt.json',attempt);started=time.perf_counter();encoder=None
    try:
        student.validate_rows(rows)
        parts={p:[r for r in rows if r['partition']==p] for p in student.PARTITIONS}
        encoder=local_encoders.LocalEncoder(reader.ROOT/'models/minilm-l6-v2',pooling='mean',cache=path/'encoder-cache.sqlite3')
        representations={};coverage={};arrays={};heads={}
        for part in student.PARTITIONS:
            representations[part],coverage[part]=features(encoder,[r['input'] for r in parts[part]])
        with threadpool_limits(limits=4):
            for axis in reader.AXES:
                head=LogisticRegression(C=1.0,class_weight='balanced',max_iter=2000,random_state=812)
                head.fit(representations['train'],[r['labels'][axis] for r in parts['train']])
                classes=list(head.classes_);arrays[axis+'_coef']=head.coef_;arrays[axis+'_intercept']=head.intercept_
                z=student.logits(representations['calibration'],head.coef_,head.intercept_,classes)
                heads[axis]={'classes':classes,'temperature':student.temperature(z,[r['labels'][axis] for r in parts['calibration']],classes)}
        cp=student.predictions(representations['calibration'],heads,arrays);selection=student.calibrate(parts['calibration'],cp)
        vp=student.predictions(representations['validation'],heads,arrays);validation=student.metrics(parts['validation'],vp,selection['threshold'])
        missing={a:sorted(set(reader.AXES[a])-set(heads[a]['classes'])) for a in reader.AXES}
        reasons=['Unreviewed machine imitation is not clinical qualification; no automatic deployment.',
            'The same development cohort was inspected for prior representations; this is not independent confirmatory validation.',
            'Window aggregation preserves coverage, not full cross-window attention or guaranteed clinical meaning.']
        if any(missing.values()):reasons.append('Some source-language classes are absent from fitting data.')
        if selection['threshold'] is None:reasons.append('Calibration does not support selective local acceptance.')
        if validation['selected_coverage']<student.POLICY['minimum_coverage'] or validation['selected_error_upper_95']>student.POLICY['maximum_empirical_error_upper_95']:
            reasons.append('Development validation fails selective accuracy/coverage requirements.')
        np.savez_compressed(path/'weights.npz',**arrays)
        cohorts.write(path/'window-coverage.json',coverage)
        (path/'validation-predictions.jsonl').write_text(''.join(json.dumps({'id':r['id'],'expected':r['labels'],**p},sort_keys=True)+'\n' for r,p in zip(parts['validation'],vp)))
        manifest={'schema':VERSION,'implementation':identity(),'cohort_id':key,'kind':'windowed_minilm','synthetic':True,
            'supervision':'machine_teacher_snapshot','reader_identity':reader.identity(),'dataset_digest':digest(rows),
            'provenance':{'teacher_snapshot_sha256':student.sha(output/'dataset/manifest.json')},'heads':heads,
            'encoder_path':str(encoder.path),'encoder_identity':encoder.identity,'window_policy':WINDOW,
            'counts':{p:len(parts[p]) for p in parts},'policy':student.POLICY,'calibration':selection,
            'calibration_metrics':student.metrics(parts['calibration'],cp,selection['threshold']),'validation':validation,
            'unrepresented_training_classes':missing,'encoder_stats':dict(encoder.stats),'training_seconds':time.perf_counter()-started,
            'model_artifact_bytes':(path/'weights.npz').stat().st_size,'foundation_weights_updated':False,
            'release':{'status':'shadow_only','automatic_acceptance':False,'reasons':reasons},
            'files':{name:student.sha(path/name) for name in ('weights.npz','window-coverage.json','validation-predictions.jsonl')},
            'new_paid_api_calls':0}
        cohorts.write(path/'manifest.json',manifest);cohorts.dataset(stack,key)
        attempt.update(status='complete_shadow_only',manifest_sha256=student.sha(path/'manifest.json'))
        return manifest
    except (ValueError,RuntimeError,OSError,ImportError) as exc:
        attempt.update(status='failed',error=str(exc)[:500]);raise
    finally:
        if encoder:encoder.close()
        cohorts.write(path/'attempt.json',attempt)


def model(stack, key):
    output,plan,rows,_=cohorts.dataset(stack,key);path=output/DIRECTORY
    attempt=json.loads((path/'attempt.json').read_text());m=json.loads((path/'manifest.json').read_text())
    if attempt['status']!='complete_shadow_only' or student.sha(path/'manifest.json')!=attempt['manifest_sha256']:
        raise ValueError('Complete unchanged windowed model required')
    if (m['schema']!=VERSION or m['implementation']!=identity() or m['reader_identity']!=reader.identity() or
        m['cohort_id']!=key or m['dataset_digest']!=digest(rows) or m['window_policy']!=WINDOW or
        m['provenance']['teacher_snapshot_sha256']!=student.sha(output/'dataset/manifest.json')):
        raise ValueError('Windowed source model or supervision changed')
    if set(m['files'])!={'weights.npz','window-coverage.json','validation-predictions.jsonl'}:
        raise ValueError('Invalid windowed model artifacts')
    for name,h in m['files'].items():
        if student.sha(path/name)!=h:raise ValueError('Windowed model artifact changed')
    return path,m,rows


def status(stack, key):
    output,_,_=cohorts.connected(stack,key);path=output/DIRECTORY
    if not (path/'attempt.json').exists():return {'status':'not_attempted'}
    attempt=json.loads((path/'attempt.json').read_text())
    if attempt['status']!='complete_shadow_only':return attempt
    path,m,_=model(stack,key)
    return {'status':'complete_shadow_only','manifest_sha256':student.sha(path/'manifest.json'),
        **{k:m[k] for k in ('counts','calibration','validation','release','training_seconds','model_artifact_bytes','window_policy','encoder_stats')}}


def read(stack, request):
    if not isinstance(request,dict) or set(request)!={'cohort_id','kind','target'} or request['kind']!='windowed_minilm':
        raise ValueError('Exact cohort, windowed reader kind and source target required')
    path,m,_=model(stack,request['cohort_id']);target=request['target'];record,text,meta=reader.source(stack,target)
    packet=reader.packet(record,text,target,meta['snapshot']['as_of']);student.model_text(packet)
    source_identity={k:meta[k] for k in ('source_digest','snapshot')};model_id=student.sha(path/'manifest.json')
    key=digest([identity(),request['cohort_id'],source_identity,target,digest(record),model_id,packet])
    db=reader.db(stack)
    try:
        saved=db.execute('SELECT payload FROM readings WHERE id=?',(key,)).fetchone()
        if saved:return {**json.loads(saved[0]),'cache_hit':True,'new_paid_api_calls':0}
        started=time.perf_counter();encoder=local_encoders.LocalEncoder(m['encoder_path'],pooling='mean',cache=path/'encoder-cache.sqlite3')
        try:
            if encoder.identity!=m['encoder_identity']:raise ValueError('Installed encoder identity changed')
            matrix,coverage=features(encoder,[packet])
            with np.load(path/'weights.npz',allow_pickle=False) as arrays:result=student.predictions(matrix,m['heads'],arrays)[0]
        finally:encoder.close()
        threshold=m['calibration']['threshold']
        receipt={'schema':reader.VERSION,'id':key,'synthetic':True,'target':target,'source_hash':digest(record),
            'source_identity':source_identity,'reader_identity':reader.identity(),'model_identity':model_id,'provider':'cohort_windowed_minilm','context':packet,
            'reading':{'labels':result['labels'],'distributions':{a:{'choice':result['labels'][a],'probabilities':result['probabilities'][a]} for a in reader.AXES},
                'semantic_validation':'pending','clinical_authority':False,'automatic_acceptance':False},
            'requirement_bridge':reader.implications(result['labels'],target['concept']),
            'student':{'cohort_id':request['cohort_id'],'supervision':m['supervision'],'release':m['release'],'confidence':result['confidence'],
                'passes_development_threshold':threshold is not None and student.selectable(result) and result['confidence']>=threshold,'window_coverage':coverage[0]},
            'usage':{'local_seconds_including_load':time.perf_counter()-started,'encoder':encoder.stats,'paid_api_calls':0,'hosted_tokens':0},
            'cache_hit':False,'new_paid_api_calls':0,'current_clinical_state_established':False,'training_label_created':False}
        with db:db.execute('INSERT OR IGNORE INTO readings VALUES (?,?,?)',(key,target['patient_id'],json.dumps(receipt,allow_nan=False)))
        return receipt
    finally:db.close()
