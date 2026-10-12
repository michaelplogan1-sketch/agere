"""Pinned, offline bounded decisions. No chat generation or hosted fallback.

Kev uses the audited upstream MLX backbone/LoRA merge and an equivalent fp32
MLX pointer head. Liquid uses its audited native MPS answer-slot readout.
"""
from pathlib import Path
import hashlib,importlib.metadata,json,math,os,sys,time,threading,resource
import numpy as np
from ...contracts import digest

ROOT=Path(__file__).resolve().parents[3]
VERSION='agere-local-decisions/1'
MAX_CONTEXT=8192


def sha256(path):
    h=hashlib.sha256()
    with Path(path).open('rb') as f:
        for block in iter(lambda:f.read(8*1024*1024),b''):h.update(block)
    return h.hexdigest()


def verify(path):
    path=Path(path);manifest=json.loads(path.read_text())
    if manifest['schema']!='agere-decision-model/1':raise ValueError('Unknown manifest')
    for component in manifest['components']:
        root=Path(component['path']).resolve(strict=True)
        if component.get('complete'):
            actual={str(p.relative_to(root)) for p in root.rglob('*') if p.is_file()
                    and '.cache' not in p.parts and '__pycache__' not in p.parts
                    and '.git' not in p.parts and p.name!='agere-model.json'}
            if actual!=set(component['files']):raise ValueError('Model file inventory changed')
        for name,expected in component['files'].items():
            p=root/name
            if not p.resolve().is_relative_to(root):raise ValueError('Unsafe model manifest path')
            if p.stat().st_size!=expected['bytes'] or sha256(p)!=expected['sha256']:
                raise ValueError('Model/code integrity mismatch: '+name)
    for package,version in manifest['runtime'].items():
        if importlib.metadata.version(package)!=version:raise ValueError('Runtime version changed: '+package)
    return manifest


def offline():
    os.environ.update(HF_HUB_OFFLINE='1',TRANSFORMERS_OFFLINE='1',HF_HUB_DISABLE_TELEMETRY='1',HF_HUB_DISABLE_IMPLICIT_TOKEN='1')


def validate_request(state,questions):
    if not isinstance(state,str) or not state or len(state)>100000:raise ValueError('Bounded text state required')
    if not isinstance(questions,dict) or not 1<=len(questions)<=3:raise ValueError('One to three bounded questions required')
    for q in questions.values():
        if set(q)!={'type','instructions','criteria'} or q['type']!='choice':raise ValueError('Choice contract required')
        if not 2<=len(q['criteria'])<=12 or 'unknown' not in q['criteria']:raise ValueError('Explicit unknown choice required')
        if any(not isinstance(v,str) or not v for v in q['criteria'].values()):raise ValueError('Named choice descriptions required')


def validate_answer(answers,questions):
    if set(answers)!=set(questions):raise ValueError('Question mismatch')
    result={}
    for key,q in questions.items():
        a=answers[key];p=a['probabilities'];choice=a['choice']
        if set(p)!=set(q['criteria']) or choice not in p:raise ValueError('Option mismatch')
        if any(type(v) not in (float,int) or not math.isfinite(v) or not 0<=v<=1 for v in p.values()):raise ValueError('Invalid probability')
        if abs(sum(p.values())-1)>1e-4 or p[choice]<max(p.values()):raise ValueError('Invalid distribution')
        result[key]={'choice':choice,'probabilities':p,'confidence':p[choice]}
    return result


class MLXPointerHead:
    """Exact checkpoint q/k affine projections and scaled dot product, fp32 CPU.

    CPU stream avoids Metal's reduced-precision fp32 matrix path for this tiny
    head; the expensive backbone remains on Metal. No randomly initialized head.
    """
    def __init__(self,state,temperature):
        import mlx.core as mx
        if set(state)!={'q.weight','q.bias','k.weight','k.bias'}:raise ValueError('Unsupported pointer head')
        self.weights={k:mx.array(v.detach().float().cpu().numpy()) for k,v in state.items()}
        self.scale=1/math.sqrt(self.weights['q.weight'].shape[0]);self.temperature=float(temperature)
        if not 0<self.temperature<100:raise ValueError('Invalid head temperature')
    def __call__(self,decide,options):
        import mlx.core as mx
        with mx.stream(mx.cpu):
            w=self.weights;decide=decide.astype(mx.float32);options=options.astype(mx.float32)
            q=decide@w['q.weight'].T+w['q.bias'];k=options@w['k.weight'].T+w['k.bias']
            logits=(k@q)*self.scale/self.temperature;mx.eval(logits)
        return logits


class KevMLX:
    def __init__(self,manifest=ROOT/'models/kev-4b/agere-model.json'):
        start=time.perf_counter();self.manifest=verify(manifest);offline()
        import torch,mlx.core as mx
        from transformers import AutoTokenizer
        code=self.manifest['upstream_code'];sys.path.insert(0,code['path'])
        from kev.model import PointerHead,pad_id
        from kev.mlx_model import MLXDecisionModel,load_base,merge_lora
        # Inspect and hash before importing any checkpoint-owned executable code.
        import kev.model,kev.mlx_model
        for module in [kev.model,kev.mlx_model]:
            if not Path(module.__file__).resolve().is_relative_to(Path(code['path']).resolve()):raise ValueError('Wrong Kev module imported')
        adapter=ROOT/'models/kev-4b';base=ROOT/'models/kev-qwen35-base'
        meta=torch.load(adapter/'head.pt',map_location='cpu',weights_only=True)
        if meta['base_revision']!=self.manifest['base_revision']:raise ValueError('Wrong Kev base revision')
        if meta['option_isolation'] or meta['special_embeddings']:raise ValueError('Unsupported Kev architecture')
        self.tokenizer=AutoTokenizer.from_pretrained(base,local_files_only=True,trust_remote_code=False)
        lm=load_base(base);self.merged_tensors=merge_lora(lm,adapter)
        self.model=MLXDecisionModel(lm,pad_id(self.tokenizer),head_dim=meta['head_dim'])
        self.model.head.load_state_dict(meta['head'],strict=True);self.model.head.temperature=meta['temperature'];self.model.eval()
        self.pointer=MLXPointerHead(meta['head'],meta['temperature'])
        self.original_logits=self.model._logits
        def pointer_logits(h,decide,opts):
            picked=h[mx.array([decide,*opts],dtype=mx.int32)]
            z=self.pointer(picked[0],picked[1:])
            return torch.from_numpy(np.asarray(z).copy())
        self.model._logits=pointer_logits
        self.load_seconds=time.perf_counter()-start
        self.identity=digest([VERSION,self.manifest,sha256(__file__)])
    def encode(self,state,questions):
        validate_request(state,questions)
        record={'state':state,'questions':[{'instr':q['instructions'],'options':list(q['criteria'].values()),'label':0} for q in questions.values()]}
        return self.model.encode(self.tokenizer,record,max_state=MAX_CONTEXT,max_branch=MAX_CONTEXT,strict=True)
    def __call__(self,state,questions):
        import mlx.core as mx
        enc=self.encode(state,questions);mx.reset_peak_memory();start=time.perf_counter()
        probabilities=[p.tolist() for p in self.model.probs(enc)];mx.synchronize()
        answers={}
        for (key,q),p in zip(questions.items(),probabilities):
            names=list(q['criteria']);answers[key]={'choice':names[int(np.argmax(p))],'probabilities':dict(zip(names,p))}
        return {'answers':validate_answer(answers,questions),'stats':{'seconds':time.perf_counter()-start,
            'input_tokens':len(enc['ids']),'output_tokens':0,'peak_mlx_memory_bytes':mx.get_peak_memory(),
            'memory_metric':'MLX peak allocated bytes since request reset, including resident weights; not whole-process RAM',
            'process_lifetime_peak_rss_bytes':resource.getrusage(resource.RUSAGE_SELF).ru_maxrss,
            'backend':'mlx_bf16_backbone_fp32_pointer','load_seconds':self.load_seconds}}
    def parity(self,state,questions):
        """Our head vs upstream torch head, then prefix vs row-form MLX path."""
        import mlx.core as mx
        enc=self.encode(state,questions);ours=[p.tolist() for p in self.model.probs(enc)]
        override=self.model._logits
        self.model._logits=self.original_logits
        try:reference=[p.tolist() for p in self.model.probs(enc)]
        finally:self.model._logits=override
        import torch
        rows=[torch.softmax(z,-1).tolist() for z in self.model.forward_rows(enc)]
        delta=max(abs(x-y) for a,b in zip(ours,reference) for x,y in zip(a,b))
        row_delta=max(abs(x-y) for a,b in zip(ours,rows) for x,y in zip(a,b))
        flips=sum(np.argmax(a)!=np.argmax(b) for a,b in zip(ours,reference))
        row_flips=sum(np.argmax(a)!=np.argmax(b) for a,b in zip(ours,rows))
        if delta>1e-5 or flips or row_delta>.03 or row_flips:raise ValueError('Kev MLX parity failed')
        return {'head_max_probability_delta':delta,'head_argmax_flips':int(flips),'prefix_vs_row_max_probability_delta':row_delta,
                'prefix_vs_row_argmax_flips':int(row_flips),'scope':'Same pinned MLX backbone; head matches upstream fp32 torch. Not an independent full fp32-backbone reproduction.'}


class LiquidD1:
    def __init__(self,manifest=ROOT/'models/d1-3b/agere-model.json'):
        start=time.perf_counter();self.manifest=verify(manifest);offline()
        import torch
        from transformers import AutoModel,AutoTokenizer
        path=ROOT/'models/d1-3b'
        if not torch.backends.mps.is_available():raise RuntimeError('Native MPS runtime unavailable')
        # trust_remote_code runs only the hash-verified local official files offline.
        self.model=AutoModel.from_pretrained(path,local_files_only=True,trust_remote_code=True,dtype=torch.bfloat16).to('mps').eval()
        self.engine=self.model.engine;self.tokenizer=self.engine.tokenizer
        self.identity=digest([VERSION,self.manifest,sha256(__file__)]);self.load_seconds=time.perf_counter()-start
    def __call__(self,state,questions):
        import torch
        validate_request(state,questions)
        # Inspect exact native render lengths before forward; no silent truncation.
        import importlib
        prompt=importlib.import_module(self.engine.__class__.__module__.rsplit('.',1)[0]+'.prompt')
        lengths=[len(self.tokenizer.encode(self.engine.render(state,prompt.as_question(q)),add_special_tokens=False)) for q in questions.values()]
        if max(lengths)>MAX_CONTEXT:raise ValueError('Shared context budget exceeded')
        torch.mps.synchronize();start=time.perf_counter();stop=threading.Event();peak=[0,0]
        def sample():
            while not stop.is_set():
                peak[0]=max(peak[0],torch.mps.current_allocated_memory())
                peak[1]=max(peak[1],torch.mps.driver_allocated_memory())
                stop.wait(.01)
        sampler=threading.Thread(target=sample,daemon=True);sampler.start()
        try:
            out=self.model.system_one(state,questions);torch.mps.synchronize()
        finally:
            stop.set();sampler.join()
        return {'answers':validate_answer(out['answers'],questions),'stats':{
            'seconds':time.perf_counter()-start,**out['usage'],'peak_mlx_memory_bytes':None,
            'mps_current_allocated_bytes':torch.mps.current_allocated_memory(),
            'mps_driver_allocated_bytes':torch.mps.driver_allocated_memory(),
            'sampled_peak_mps_allocated_bytes':peak[0],'sampled_peak_mps_driver_bytes':peak[1],
            'process_lifetime_peak_rss_bytes':resource.getrusage(resource.RUSAGE_SELF).ru_maxrss,
            'memory_metric':'MPS sampled every 10 ms plus after-request allocations; sampled peak is a lower bound, not an exact allocator peak or MLX metric. RSS is macOS process lifetime including load.',
            'backend':'native_torch_mps_bf16','load_seconds':self.load_seconds}}
