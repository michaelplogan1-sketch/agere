"""Train an actual local relevance model using synthetic development charts.

Teacher labels are inherited-reader weak supervision, not clinician-adjudicated
truth. Training excludes reserved patient families and all evaluation worlds.
"""
import argparse
import hashlib
import json
import random
import time
from collections import Counter
from pathlib import Path

import joblib
import numpy as np
from sklearn.feature_extraction.text import TfidfVectorizer
from sklearn.linear_model import LogisticRegression
from sklearn.metrics import average_precision_score,precision_score,recall_score
from sklearn.pipeline import Pipeline
from threadpoolctl import threadpool_limits

from clinical_core.bridge import InsightBridge
from clinical_core.corpus import load_world,partition_ids
from clinical_core.contracts import digest
from clinical_core.evidence_selection import EvidenceSelector,LocalTokenCounter,feature_text

ROOT=Path(__file__).resolve().parent
SUBJECTS=('mra','sglt2','arni','bb','statin','a1c','access')

def collect(world,split,manifest,bridge):
    entry=next(x for x in manifest['worlds'] if x['name']==world)
    batch=load_world(Path(entry['path']),'2026-09-30')
    if split=='test':
        ids=[p for p in batch.patients if manifest['partition_by_family'][manifest['source_family_by_patient'][p]]=='test']
    else:ids=partition_ids(manifest,world,split)
    rows=[];charts=[]
    for pid in sorted(ids):
        notes=sorted(batch.patients[pid]['notes'],key=lambda n:(n['date'],n['note_id']))
        chart=bridge.reader.read_chart(notes)
        evidence={}
        for subject in SUBJECTS:
            for e in bridge.reader.evidence(chart,subject):
                evidence.setdefault((e['note_id'],e['text']),[]).append(subject+':'+e['type'])
        for s in chart['sentences']:
            labels=list(evidence.get((s['note_id'],s['text']),[]))
            if bridge.reader.EF_RE.search(s['text']):labels.append('ef:measurement')
            rows.append(dict(world=world,patient_id=pid,family=manifest['source_family_by_patient'][pid],
                split=split,synthetic=True,text=feature_text(s),sentence=s['text'],previous=s.get('prev',''),
                note_id=s['note_id'],date=s['date'],label=int(bool(labels)),teacher_types=labels,
                label_source='inherited lexicon/EF reader weak supervision; no oracle or clinician label'))
        charts.append((pid,notes,evidence))
    return rows,charts

def metrics(rows,scores,threshold):
    y=np.array([r['label'] for r in rows]);pred=np.asarray(scores)>=threshold
    return dict(examples=len(rows),positive_examples=int(y.sum()),
        precision=float(precision_score(y,pred,zero_division=0)),recall=float(recall_score(y,pred,zero_division=0)),
        average_precision=float(average_precision_score(y,scores)),selected_fraction=float(pred.mean()),
        label_scope='Agreement with weak teacher relevance; not clinical accuracy')

def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--output',type=Path,default=ROOT/'artifacts/learned-evidence')
    p.add_argument('--engine',type=Path,default=Path('/Users/mikel/insight-engine'))
    args=p.parse_args();args.output.mkdir(parents=True,exist_ok=True)
    start=time.perf_counter();manifest=json.loads((ROOT/'artifacts/corpus/manifest.json').read_text())
    bridge=InsightBridge(args.engine)
    train=[];cal=[]
    for world in ('dev','hard_dev'):
        r,_=collect(world,'train',manifest,bridge);train.extend(r)
        r,_=collect(world,'calibration',manifest,bridge);cal.extend(r)
        print(json.dumps({'collected':world,'train_rows':len(train),'calibration_rows':len(cal)}),flush=True)
    # Avoid giving repeated synthetic phrasing thousands of additional votes.
    unique={}
    for r in train:unique.setdefault((r['text'],r['label']),r)
    positive=[r for r in unique.values() if r['label']]
    negative=[r for r in unique.values() if not r['label']]
    random.Random(173).shuffle(negative)
    selected=positive+negative[:max(len(positive)*5,1000)]
    random.Random(173).shuffle(selected)
    with (args.output/'training_examples.jsonl').open('w') as f:
        for r in selected:f.write(json.dumps(r)+'\n')
    model=Pipeline([('tfidf',TfidfVectorizer(analyzer='char_wb',ngram_range=(3,5),min_df=2,max_features=60000,sublinear_tf=True)),
                    ('classifier',LogisticRegression(C=2.0,class_weight='balanced',max_iter=400,random_state=173))])
    with threadpool_limits(limits=4):model.fit([r['text'] for r in selected],[r['label'] for r in selected])
    cal_scores=model.predict_proba([r['text'] for r in cal])[:,1]
    # Calibration-family recall operating point, not a probability guarantee.
    positives=cal_scores[np.array([r['label'] for r in cal])==1]
    threshold=float(np.quantile(positives,0.005,method='lower'))
    joblib.dump(model,args.output/'selector.joblib',compress=3)
    import importlib.metadata
    card=dict(name='Local synthetic clinical-evidence relevance selector',version=1,
        architecture='Character TF-IDF and logistic regression; trained locally',
        purpose='Rank evidence passages for bounded retrieval; never diagnose or establish absence of care',
        model_sha256=hashlib.sha256((args.output/'selector.joblib').read_bytes()).hexdigest(),
        teacher_revision=bridge.version,training_worlds=['dev','hard_dev'],
        training_partition='train source-patient families only',calibration_partition='calibration families in development worlds only',
        raw_training_rows=len(train),unique_training_rows=len(unique),fitted_examples=len(selected),
        training_families=len({r['family'] for r in train}),selection_threshold=threshold,
        threshold_basis='Lower empirical score quantile of positive weak labels in calibration families; no clinical guarantee',
        feature_count=len(model['tfidf'].vocabulary_),dependencies={m:importlib.metadata.version(m) for m in ['scikit-learn','numpy','scipy','joblib','tokenizers']},
        limitations=['Inherited reader can miss or misinterpret evidence; this student may reproduce those errors.',
            'Synthetic phrase templates repeat across families; family holdout is not an unseen-language guarantee.',
            'Historical benchmark development may have exposed evaluation language.',
            'No clinical validation and no evidence of superiority to Jev, frontier LLMs or vendor products.'])
    (args.output/'model_card.json').write_text(json.dumps(card,indent=2)+'\n')
    counter=LocalTokenCounter(ROOT/'models/qwen3-4b/tokenizer.json');selector=EvidenceSelector(args.output,counter)
    report=dict(synthetic=True,model=card,calibration=metrics(cal,cal_scores,threshold),evaluations={},packet_evaluations={},paid_calls=0)
    train_families={r['family'] for r in train};cal_families={r['family'] for r in cal}
    assert not train_families&cal_families
    train_feature_texts={k[0] for k in unique}
    for world in ('dev','hard_dev','hard16_llm'):
        rows,charts=collect(world,'test',manifest,bridge)
        assert not {r['family'] for r in rows}&(train_families|cal_families)
        scores=model.predict_proba([r['text'] for r in rows])[:,1]
        report['evaluations'][world]=metrics(rows,scores,threshold)
        report['evaluations'][world]['feature_text_overlap_with_train']=sum(r['text'] in train_feature_texts for r in rows)/len(rows)
        # Bounded deterministic sample; keep refusals and zero-token savings in totals.
        sample=charts[::max(1,len(charts)//40)][:40]
        stats=dict(charts=len(sample),packets=0,review_required=0,raw_note_tokens=0,packet_tokens=0,
            raw_note_tokens_for_completed_packets=0,teacher_evidence_spans=0,retained_teacher_spans=0)
        for pid,notes,evidence in sample:
            packet=selector.packet(notes,bridge.reader,as_of='2026-09-30',subject='HF medication holds, access barriers and outside care',token_budget=1600)
            stats['raw_note_tokens']+=counter('\n'.join(n['text'] for n in notes))
            if packet['status']=='review_required':stats['review_required']+=1;continue
            stats['packets']+=1;stats['packet_tokens']+=packet['packet_tokens']
            stats['raw_note_tokens_for_completed_packets']+=packet['raw_note_tokens']
            retained={(a['note_id'],a['sentence_text']) for a in packet['provenance']}
            stats['teacher_evidence_spans']+=len(evidence)
            stats['retained_teacher_spans']+=len(set(evidence)&retained)
            if world=='hard16_llm' and evidence and not (args.output/'example_packet.json').exists():
                (args.output/'example_packet.json').write_text(json.dumps(dict(packet,patient_id=pid),indent=2)+'\n')
        stats['input_token_reduction_completed_packets']=1-stats['packet_tokens']/stats['raw_note_tokens_for_completed_packets'] if stats['raw_note_tokens_for_completed_packets'] else None
        stats['scope']='Local Qwen tokenizer; packet JSON versus raw note text; not a vendor bill or end-to-end answer-quality comparison'
        report['packet_evaluations'][world]=stats
        print(json.dumps({'evaluated':world,'metrics':report['evaluations'][world],'packets':stats}),flush=True)
    report['elapsed_seconds']=time.perf_counter()-start
    (args.output/'training_report.json').write_text(json.dumps(report,indent=2)+'\n')
    (args.output/'training-manifest.json').write_text(json.dumps(dict(training_data_sha256=hashlib.sha256((args.output/'training_examples.jsonl').read_bytes()).hexdigest(),
        model_sha256=card['model_sha256'],corpus_manifest_sha256=digest(manifest),training_family_ids=sorted(train_families),calibration_family_ids=sorted(cal_families)),indent=2)+'\n')
    print(json.dumps({'complete':True,'seconds':report['elapsed_seconds'],'output':str(args.output)}),flush=True)

if __name__=='__main__':main()
