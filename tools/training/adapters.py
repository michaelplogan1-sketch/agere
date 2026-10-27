"""Explicit offline SLM learning with native task acceptance and pinned releases.

Uses the installed MLX LoRA trainer. It never discovers old datasets, reads legacy
answer stores, calls a provider or downloads a model. Verification data never enter
gradient fitting or training-time loss evaluation.
"""
import argparse
from contextlib import contextmanager
import hashlib
import json
import os
from pathlib import Path
import time
from types import SimpleNamespace
from unittest.mock import patch
from clinical_core.contracts import digest
from clinical_core.agere.clinical import component_learning as C,adapter_learning as A
from tools.training import adapter_tasks as T

VERSION='offline-adapter-learning/1'


@contextmanager
def offline():
    guard={'blocked_attempts':[]}
    def deny(*args,**kw):
        guard['blocked_attempts'].append('network_or_child_process')
        raise RuntimeError('Local adapter learning forbids network and child-process execution')
    env={'HF_HUB_OFFLINE':'1','TRANSFORMERS_OFFLINE':'1','HF_DATASETS_OFFLINE':'1','HF_HUB_DISABLE_TELEMETRY':'1',
        'HF_HUB_DISABLE_IMPLICIT_TOKEN':'1','AGERE_CLAUDE':'off','WANDB_MODE':'disabled'}
    with patch.dict(os.environ,env),patch('socket.socket.connect',side_effect=deny),patch('socket.create_connection',side_effect=deny),\
            patch('urllib.request.urlopen',side_effect=deny),patch('subprocess.Popen',side_effect=deny):yield guard


def sequence_length(lengths,explicit=None):
    maximum=max(lengths)
    length=explicit if explicit is not None else 32*((maximum+31)//32)
    if maximum>length:raise ValueError(f'Complete training sequence needs {maximum} tokens; explicit limit {length} would truncate it')
    return maximum,length


def configuration(value):
    allowed={'iterations','batch_size','learning_rate','num_layers','rank','scale','dropout','keys','seed','max_seq_length','max_tokens'}
    if not isinstance(value,dict) or set(value)-allowed or type(value.get('iterations')) is not int or value['iterations']<1:
        raise ValueError('Explicit positive training iterations and known local optimization parameters required')
    cfg={'batch_size':1,'learning_rate':2e-4,'num_layers':-1,'rank':16,'scale':20.0,'dropout':0.0,
         'keys':['self_attn.q_proj','self_attn.k_proj','self_attn.v_proj','self_attn.o_proj','mlp.gate_proj','mlp.up_proj','mlp.down_proj'],
         'seed':7,'max_tokens':700,**value}
    for key in ('batch_size','rank','max_tokens'):
        if type(cfg[key]) is not int or cfg[key]<1:raise ValueError('Positive '+key+' required')
    if type(cfg['num_layers']) is not int or cfg['num_layers']==0 or cfg['num_layers']< -1:raise ValueError('Use -1 or a positive number of layers')
    if not cfg['keys'] or not all(isinstance(k,str) for k in cfg['keys']):raise ValueError('Explicit LoRA projection names required')
    import math
    if any(type(cfg[k]) not in (int,float) or not math.isfinite(cfg[k]) or cfg[k]<=0 for k in ('learning_rate','scale')):raise ValueError('Positive finite optimization rates/scales required')
    if not 0<=cfg['dropout']<1:raise ValueError('LoRA dropout must be in [0,1)')
    if 'max_seq_length' in cfg and (type(cfg['max_seq_length']) is not int or cfg['max_seq_length']<1):raise ValueError('Positive explicit sequence length required')
    return cfg


class Mlx:
    def __init__(self,base):self.base=base

    def train(self,data,output,cfg,resume=None):
        import mlx.core as mx
        from mlx_lm import lora
        from mlx_lm.tuner.datasets import ChatDataset
        from mlx_lm.utils import load
        model,tok=load(str(self.base),trust_remote_code=False)
        class ServingTokenizer:
            def __getattr__(self,name):return getattr(tok,name)
            def apply_chat_template(self,*a,**kw):return tok.apply_chat_template(*a,**{**kw,'enable_thinking':False})
        datasets=[];maximum=0;counts={}
        for split in ('train','valid'):
            rows=[json.loads(line) for line in (data/(split+'.jsonl')).read_text().splitlines()]
            counts[split]=len(rows)
            ds=ChatDataset(rows,ServingTokenizer(),mask_prompt=True)
            for row in rows:maximum=max(maximum,len(ds.process(row)[0]))
            datasets.append(ds)
        maximum,length=sequence_length([maximum],cfg.get('max_seq_length'))
        args={**lora.CONFIG_DEFAULTS,'model':str(self.base),'train':True,'adapter_path':str(output),'data':str(data),
            'iters':cfg['iterations'],'batch_size':cfg['batch_size'],'learning_rate':cfg['learning_rate'],
            'num_layers':cfg['num_layers'],'lora_parameters':{k:cfg[k] for k in ('rank','scale','dropout','keys')},
            'seed':cfg['seed'],'mask_prompt':True,'grad_checkpoint':True,'max_seq_length':length,
            'steps_per_report':1,'steps_per_eval':cfg['iterations'],'val_batches':-1,'save_every':cfg['iterations'],
            'resume_adapter_file':str(resume/'adapters.safetensors') if resume else None,'trust_remote_code':False}
        import numpy as np
        np.random.seed(cfg['seed']);mx.random.seed(cfg['seed'])
        try:lora.train_model(SimpleNamespace(**args),model,*datasets,None)
        finally:
            del model,tok,datasets
            import gc
            gc.collect();mx.clear_cache()
        return {'maximum_complete_sequence_tokens':maximum,'max_seq_length':length,'training_rows':counts,'truncated':False}

    @contextmanager
    def generator(self,learner,adapter,ctx,cfg):
        import mlx.core as mx
        from mlx_lm import load,generate
        from mlx_lm.sample_utils import make_sampler
        from clinical_core.agere.clinical import constrained_decoding as G,bottled as B
        model,tok=load(str(self.base),adapter_path=str(adapter) if adapter else None,trust_remote_code=False)
        def call(row):
            messages=ctx.messages(learner,row)
            prompt=tok.apply_chat_template(messages,add_generation_prompt=True,tokenize=False,enable_thinking=False)
            grammar=G.JsonGrammar(root='object') if learner=='typed' else G.LineGrammar() if learner=='normalizer' else B.slot_grammar() if learner=='narrow' else B.sql_grammar(ctx.path)
            try:return generate(model,tok,prompt=prompt,max_tokens=cfg['max_tokens'],sampler=make_sampler(temp=0.0),verbose=False,
                logits_processors=G.processors(tok,grammar))
            finally:mx.clear_cache()
        try:yield call
        finally:
            del model,tok
            import gc
            gc.collect();mx.clear_cache()


def evaluate(ctx,learner,rows,generate,variants=()):
    details=[];started=time.monotonic()
    for row in rows:
        # Infrastructure failures invalidate the run. Only actual model output
        # reaches the task grader; a broken generator is not model abstention.
        text=generate(row);verdict=ctx.assess(learner,row,text)
        checked=[variant.assess(learner,row,text) for variant in variants]
        unsafe=any(v['guarded'] and not v['acceptable'] for v in [verdict,*checked])
        if checked:
            verdict={**verdict,'source_variants':checked,'guarded':verdict['guarded'] and all(v['guarded'] for v in checked),
                'acceptable':verdict['acceptable'] and all(v['acceptable'] for v in checked)}
        details.append({'task':digest([row['question'],row['family']]),'family':row['family'],'question':row['question'],'guarded_wrong':unsafe,
            'output':text,**verdict})
    return {'cases':len(rows),'acceptable':sum(r['acceptable'] for r in details),
        'guarded_wrong':sum(r['guarded_wrong'] for r in details),
        'declined':sum(not r['guarded'] for r in details),'seconds':time.monotonic()-started,'details':details}


def cached_generator(generate,ctx,learner,root,model_identity,cfg,stats):
    """Reuse only exact local decoding requests; native grading always runs fresh."""
    directory=root/'local-decodes';directory.mkdir(exist_ok=True)
    from clinical_core.agere.clinical import constrained_decoding
    policy=digest([A.file_hash(Path(__file__)),A.file_hash(Path(constrained_decoding.__file__)),cfg['max_tokens']])
    def call(row):
        identity={'model':model_identity,'messages':digest(ctx.messages(learner,row)),'policy':policy,'learner':learner}
        ident=digest(identity);path=directory/(ident+'.json')
        reused=path.exists()
        if reused:
            record=json.loads(path.read_text())
            if record.get('request')!=identity or record.get('output_sha256')!=digest(record.get('output')):raise ValueError('Local decode cache integrity changed')
            stats['reused']+=1;text=record['output']
        else:
            text=generate(row)
            if not isinstance(text,str):raise ValueError('Local generator did not return text')
            record={'request':identity,'output':text,'output_sha256':digest(text),'clinical_truth':False}
            path.write_text(json.dumps(record,indent=2)+'\n');stats['generated']+=1
        print(f"Local adapter check {stats['reused']+stats['generated']}: {'reused' if reused else 'decoded'}",flush=True)
        return text
    return call


def _run(manifest,root,*,stack,base,train=False,install=False,backend=None,candidate_adapter=None):
    started=time.monotonic();root=C.checked_path(root);spec,by,dataset=T.load(manifest);learner=spec['learner'];role=A.role(learner)
    ctx=T.Context(stack);pinned=A.pin_base(base);cfg=configuration(spec.get('training'))
    if spec.get('source_identity')!=ctx.identity:raise ValueError('Task manifest and source snapshot differ')
    variants=[]
    for entry in spec.get('source_variants') or []:
        variant=T.Context(C.checked_path(Path(manifest).resolve().parent/entry['path']))
        if variant.identity!=entry['source_identity'] or variant.as_of!=ctx.as_of or variant.catalog.tables!=ctx.catalog.tables:raise ValueError('Explicit source-variant snapshot/schema differs')
        variants.append(variant)
    contract=A.contract(learner);feedback=C.last_feedback(root,role);prior=[m for m in C.models(root) if m['role']==role];parent=prior[-1] if prior else None
    if parent and feedback and feedback['accepted'] is False and dataset==parent['qualification'].get('dataset') and feedback['revision']>parent['qualification'].get('feedback_revision',0):
        return {'status':'review_required','provider_calls':0,'reason':'Rejected supervision must be corrected before retraining'}
    job=digest([dataset,ctx.identity,pinned['identity'],cfg,contract,C.feedback_revision(root)])
    if parent and parent['qualification'].get('job')==job:
        if install and parent['qualification'].get('selected') and parent['state']=='candidate':
            model=json.loads(Path(parent['model_path']).read_text())
            for name,entry in model['files'].items():
                if A.file_hash(A.checked_child(Path(model['adapter']),name))!=entry['sha256']:raise ValueError('Qualified candidate bytes changed')
            C.rollback(root,role,parent['id'],'Explicit activation of unchanged qualified adapter candidate')
        return {'status':'unchanged','revision':parent['id'],'provider_calls':0}
    directory=root/'adapter-jobs'/job;data=directory/'data';data.mkdir(parents=True,exist_ok=True)
    # Validate the supplied targets against received source calculations. An
    # invalid target is a dataset problem, never a reason to train a wrong label.
    for split,rows in by.items():
        for row in rows:
            text=row['target'] if isinstance(row['target'],str) else json.dumps(row['target'])
            verdict=ctx.assess(learner,row,text)
            if not verdict['acceptable']:raise ValueError('Reviewed target fails native task acceptance: '+row['question']+'; '+verdict['reason'])
            if any(not variant.assess(learner,row,text)['acceptable'] for variant in variants):raise ValueError('Reviewed target fails source-variant acceptance')
        if split!='verification':
            (data/('train.jsonl' if split=='fit' else 'valid.jsonl')).write_text(''.join(json.dumps({'messages':ctx.messages(learner,row,target=True)})+'\n' for row in rows))
    prepared={'schema':VERSION,'status':'prepared','job':job,'learner':learner,'dataset':dataset,'source_identity':ctx.identity,
        'base_identity':pinned['identity'],'configuration':cfg,'task_counts':{s:len(r) for s,r in by.items()},
        'source_variants':[{'path':str(v.path),'identity':v.identity} for v in variants],
        'family_counts':{s:len({r['family'] for r in rows}) for s,rows in by.items()},'provider_calls':0,'clinical_qualification':False}
    (directory/'prepared.json').write_text(json.dumps(prepared,indent=2)+'\n')
    if not train and candidate_adapter is None:return prepared
    backend=backend or Mlx(pinned['path']);candidate=C.checked_path(candidate_adapter) if candidate_adapter else directory/'candidate'
    released=A.release_identity(learner,root=root,base=base)
    current=Path(released['adapter']) if released else None
    with offline() as guard:
        fit=backend.train(data,candidate,cfg,resume=current) if train and candidate_adapter is None else {
            'existing_explicit_candidate':str(candidate),'adapter_config':json.loads((candidate/'adapter_config.json').read_text()),
            'weights_sha256':A.file_hash(candidate/'adapters.safetensors')}
        comparisons={};local_cache={'generated':0,'reused':0}
        for name,adapter in (('baseline',current),('candidate',candidate)):
            with backend.generator(learner,adapter,ctx,cfg) as generate:
                model_identity=digest([pinned['identity'],A.file_hash(adapter/'adapters.safetensors') if adapter else None])
                cached=cached_generator(generate,ctx,learner,root,model_identity,cfg,local_cache)
                comparisons[name]={s:evaluate(ctx,learner,by[s],cached,variants) for s in ('calibration','verification')}
                (directory/(name+'-comparison.json')).write_text(json.dumps(comparisons[name],indent=2)+'\n')
        deterministic=[ctx.deterministic(row) and all(v.deterministic(row) for v in variants) for row in by['verification']]
        base_results=comparisons['baseline']['verification'];candidate_results=comparisons['candidate']['verification']
        useful=[i for i,done in enumerate(deterministic) if not done]
        gained=sum(candidate_results['details'][i]['acceptable'] and not base_results['details'][i]['acceptable'] for i in useful)
        regressions=sum(b['acceptable'] and not c['acceptable'] for split in ('calibration','verification')
            for b,c in zip(comparisons['baseline'][split]['details'],comparisons['candidate'][split]['details']))
        selected=gained>0 and regressions==0 and all(comparisons['candidate'][s]['guarded_wrong']==0 for s in ('calibration','verification'))
        if T.Context(stack).identity!=ctx.identity:raise ValueError('Source changed during adapter qualification')
        if any(T.Context(v.path).identity!=v.identity for v in variants):raise ValueError('Source variant changed during qualification')
        if A.contract(learner)!=contract:raise ValueError('Adapter implementation changed during qualification')
        if guard['blocked_attempts']:raise RuntimeError('A blocked network/child-process attempt invalidated offline qualification')
        qualification={**prepared,'selected':selected,'job':job,'feedback_revision':C.feedback_revision(root),'training':fit,
            'comparisons':comparisons,'deterministic_already_acceptable':sum(deterministic),'useful_improvements':gained,
            'local_decodes':local_cache,
            'regressions':regressions,'offline_guard':guard,'reason':'Candidate must improve tasks beyond deterministic/current coverage without a measured regression or guarded wrong answer',
            'provenance':spec['provenance'],'input_files':spec['files'],'contract':contract,
            'round':1+(parent['qualification'].get('round',0) if parent else 0)}
        release=A.capture(root,learner,candidate,base,qualification,parent=parent['id'] if parent else None,activate=bool(install and selected))
    receipt={**prepared,'status':'activated' if release['activated'] else 'qualified_candidate' if selected else 'not_selected',
        'revision':release['id'],'parent':release['parent'],'qualification':qualification,'model':release['model_path'],
        'seconds':time.monotonic()-started}
    (directory/'receipt.json').write_text(json.dumps(receipt,indent=2)+'\n');return receipt


def run(manifest,root,**options):
    """Serialize heavy local jobs and retain failures, without retrying them."""
    import fcntl
    import traceback
    root=C.checked_path(root);root.mkdir(parents=True,exist_ok=True);started=time.monotonic()
    with (root/'adapter-training.lock').open('a') as lock:
        try:fcntl.flock(lock,fcntl.LOCK_EX|fcntl.LOCK_NB)
        except BlockingIOError:return {'status':'busy','provider_calls':0}
        try:return _run(manifest,root,**options)
        except Exception as exc:
            failed=root/'adapter-failures';failed.mkdir(exist_ok=True)
            record={'schema':VERSION,'status':'failed','manifest':str(manifest),'error':str(exc),'type':type(exc).__name__,
                'seconds':time.monotonic()-started,'traceback':traceback.format_exc(),'provider_calls':0}
            (failed/(str(time.time_ns())+'.json')).write_text(json.dumps(record,indent=2)+'\n')
            raise


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    for arg in ('manifest','root','stack','base'):parser.add_argument('--'+arg,required=True,type=Path)
    parser.add_argument('--train',action='store_true');parser.add_argument('--install',action='store_true')
    parser.add_argument('--candidate-adapter',type=Path)
    args=parser.parse_args();print(json.dumps(run(args.manifest,args.root,stack=args.stack,base=args.base,train=args.train,install=args.install,candidate_adapter=args.candidate_adapter),indent=2))


if __name__=='__main__':main()
