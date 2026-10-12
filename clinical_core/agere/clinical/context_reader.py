"""Versioned local reader with a second cheap view and explicit review routing."""
from pathlib import Path
from collections import Counter
import hashlib,json,sqlite3
from ...contracts import digest
from . import context_features,scoped_reader,evidence_reader
from .context_features import context_view
from .evidence_reader import AXES,validate_input,input_feature,interpretation
from .scoped_reader import consistency_issues

VERSION='context-evidence-reader/4'


def review_reasons(x,axes,alternate):
    view=context_view(x);flags=view['flags'];labels={a:v['label'] for a,v in axes.items()}
    reasons=[]
    changed=[a for a in AXES if labels[a]!=alternate[a]['label']]
    if changed:reasons.append({'code':'cross_view_disagreement','axes':changed,'detail':'Target-context and whole-window models disagree; neither is clinical truth.'})
    issues=consistency_issues(axes)
    if issues:reasons.append({'code':'metadata_inconsistency','axes':['assertion','subject','relation'],'detail':issues[0]})
    if flags['window:prefix_omitted'] or flags['window:suffix_omitted']:
        reasons.append({'code':'bounded_context','axes':list(AXES),'detail':'Input window omits source text; section or qualifier may be outside the window.'})
    if flags['historical:copy_heading'] and labels['time']=='current':
        reasons.append({'code':'copied_history_current','axes':['time'],'detail':'Copy-history cue and current label need reconciliation; no date inference applied.'})
    if (flags['negation:left'] or flags['negation:coordinated']) and not flags['negation:pseudo'] and not flags['uncertainty:local'] and labels['assertion']=='affirmed':
        reasons.append({'code':'polarity_cue_conflict','axes':['assertion'],'detail':'Affirmed proposal coexists with a local denial cue; inspect its actual scope.'})
    if any(labels[a]=='unknown' for a in AXES) or labels['assertion']=='uncertain':
        reasons.append({'code':'unresolved_proposal','axes':[a for a in AXES if labels[a] in ('unknown','uncertain')],'detail':'Reader explicitly leaves one or more axes unresolved.'})
    return reasons


class ContextReader:
    def __init__(self,model_dir,cache_path):
        import joblib
        self.path=Path(model_dir);self.card=json.loads((self.path/'model-card.json').read_text())
        if self.card['schema']!=VERSION or self.card['decision_authority'] is not False:raise ValueError('Unexpected context reader contract')
        modules=(context_features,scoped_reader)
        for module in modules:
            path=Path(module.__file__)
            if hashlib.sha256(path.read_bytes()).hexdigest()!=self.card['runtime_hashes'][path.name]:raise ValueError('Context features changed after training')
        self.models={};self.alternates={};selected={}
        for axis in AXES:
            for branch,collection in [('typed_context',self.models),('lexical_retrained',self.alternates)]:
                path=self.path/f'{axis}-{branch}.joblib';checksum=hashlib.sha256(path.read_bytes()).hexdigest()
                if checksum!=self.card['weight_hashes'][path.name]:raise ValueError('Context model weights changed')
                collection[axis]=joblib.load(path);selected[path.name]=checksum
        self.identity=digest({'schema':VERSION,'weights':selected,'runtime':{Path(m.__file__).name:hashlib.sha256(Path(m.__file__).read_bytes()).hexdigest() for m in (*modules,evidence_reader)},'reader_runtime':hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),'axes':AXES})
        self.db=sqlite3.connect(cache_path);self.db.execute('CREATE TABLE IF NOT EXISTS predictions (id TEXT PRIMARY KEY,payload TEXT NOT NULL)');self.stats=Counter()
    def close(self):self.db.close()
    def predict(self,inputs):
        keys=[digest([self.identity,validate_input(x)]) for x in inputs];found={};pending={}
        for k,x in zip(keys,inputs):
            if k in found or k in pending:self.stats['batch_reuses']+=1;continue
            old=self.db.execute('SELECT payload FROM predictions WHERE id=?',(k,)).fetchone()
            if old:found[k]=json.loads(old[0]);self.stats['persistent_cache_hits']+=1
            else:pending[k]=x
        if pending:
            rows=list(pending.values());texts=[input_feature(x) for x in rows];primary={};alternate={}
            for axis in AXES:
                for models,data,result in [(self.models,rows,primary),(self.alternates,texts,alternate)]:
                    model=models[axis];probs=model.predict_proba(data)
                    result[axis]=[{'label':str(model.classes_[p.argmax()]),'probabilities':dict(zip(model.classes_,map(float,p))),'clinically_calibrated':False} for p in probs]
            with self.db:
                for i,(k,x) in enumerate(pending.items()):
                    axes={a:primary[a][i] for a in AXES};other={a:alternate[a][i] for a in AXES};reasons=review_reasons(x,axes,other)
                    found[k]={'axes':axes,'alternate_axes':other,'decision':'abstain','decision_authority':False,'care_gap_established':False,
                        'model_identity':self.identity,'scope':context_view(x)['trace'],'consistency_issues':consistency_issues(axes),
                        'review_routing':{'requires_review':True,'reasons':reasons,'priority':'context_conflict' if any(r['code'] in ('metadata_inconsistency','copied_history_current','polarity_cue_conflict') for r in reasons) else 'model_disagreement' if any(r['code']=='cross_view_disagreement' for r in reasons) else 'routine_annotation_review',
                            'automatic_api_escalation':False,'scope':'Engineering triage, not clinical severity or calibrated error probability.'},
                        'limitations':interpretation(axes)}
                    self.db.execute('INSERT INTO predictions VALUES (?,?)',(k,json.dumps(found[k])))
            self.stats['new_unique_predictions']+=len(pending)
        self.stats['bindings']+=len(inputs)
        return [found[k] for k in keys]
