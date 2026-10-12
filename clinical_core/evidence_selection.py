"""Experimental learned retrieval, exact provenance, and bounded evidence packets.

This ranks passages. It does not diagnose, establish absence of care, or replace
the full chart. A protected-span overflow returns no executable evidence task.
"""
from __future__ import annotations

import hashlib
import json
import re
from datetime import date
from pathlib import Path

from .contracts import digest

PROTECTED = re.compile(
    r"\b(hold\w*|held|contraindic\w*|intoleran\w*|deni\w*|declin\w*|refus\w*|"
    r"outside|samples?|copay|afford\w*|transport\w*|caregiver|daughter|mother|father|"
    r"no\s+barriers|no\s+intolerance|not\s+taking|discontinu\w*|potassium|eGFR|"
    r"creatinine|HbA1c|A1c|EF)\b", re.I)


def feature_text(sentence):
    # Identifiers, dates, site and source-family labels are deliberately absent.
    return f"previous: {sentence.get('prev','')}\nsentence: {sentence['text']}"


class LocalTokenCounter:
    """Exact counts for the supplied local tokenizer, never a vendor bill estimate."""
    def __init__(self, tokenizer_path):
        from tokenizers import Tokenizer
        self.path=Path(tokenizer_path)
        self.sha256=hashlib.sha256(self.path.read_bytes()).hexdigest()
        self.tokenizer=Tokenizer.from_file(str(self.path))

    def __call__(self,text):
        return len(self.tokenizer.encode(text,add_special_tokens=False).ids)


class EvidenceSelector:
    def __init__(self, model_dir, token_counter):
        import joblib
        self.directory=Path(model_dir)
        self.card=json.loads((self.directory/'model_card.json').read_text())
        model_path=self.directory/'selector.joblib'
        if hashlib.sha256(model_path.read_bytes()).hexdigest()!=self.card['model_sha256']:
            raise ValueError('Local selector artifact checksum mismatch')
        # Only load this locally produced, checksummed artifact. Never load an
        # untrusted pickle/joblib file downloaded with research material.
        self.model=joblib.load(model_path)
        self.counter=token_counter
        self.threshold=self.card['selection_threshold']

    def scores(self,sentences):
        if not sentences:return []
        return self.model.predict_proba([feature_text(s) for s in sentences])[:,1].tolist()

    def packet(self,notes,base_reader,*,as_of,subject,token_budget=1600):
        if date.fromisoformat(as_of).isoformat()!=as_of:raise ValueError('Use YYYY-MM-DD dates')
        if type(token_budget) is not int or token_budget<1:raise ValueError('Invalid token budget')
        if not isinstance(subject,str) or not subject.strip():raise ValueError('Subject is required')
        if len({n['note_id'] for n in notes})!=len(notes):raise ValueError('Duplicate note ID')
        dated=[]
        for n in notes:
            if date.fromisoformat(n['date']).isoformat()!=n['date']:raise ValueError('Use YYYY-MM-DD dates')
            if n.get('available_date') is not None and date.fromisoformat(n['available_date']).isoformat()!=n['available_date']:
                raise ValueError('Use YYYY-MM-DD arrival dates')
            if n['date']<=as_of and (n.get('available_date') is None or n['available_date']<=as_of):dated.append(n)
        eligible=sorted(dated,key=lambda n:(n['date'],n['note_id']))
        chart=base_reader.read_chart(eligible)
        sentences=chart['sentences']
        scores=self.scores(sentences)
        source_by_id={n['note_id']:n for n in eligible}
        atoms=[]
        query_words=set(re.findall(r'[a-zA-Z]{3,}',subject.lower()))
        for index,(s,score) in enumerate(zip(sentences,scores)):
            note=source_by_id[s['note_id']]
            start=note['text'].find(s['text'])
            if start<0:raise ValueError('Reader passage cannot be located in original note')
            end=start+len(s['text'])
            previous=s.get('prev','')
            # Keep local antecedent as an exact contiguous slice when possible.
            prev_start=note['text'].rfind(previous,0,start) if previous else -1
            if prev_start>=0:start=prev_start
            text=note['text'][start:end]
            protected=bool(PROTECTED.search(s['text']))
            overlap=len(query_words & set(re.findall(r'[a-zA-Z]{3,}',text.lower())))
            atoms.append(dict(id=f'e{index}',text=text,date=s['date'],note_id=s['note_id'],
                start=start,end=end,document_hash=digest(note),score=score,
                protected=protected,query_overlap=overlap,
                token_cost=self.counter(text),sentence_text=s['text']))

        def payload(selected):
            return {'subject':subject,'as_of':as_of,'evidence':[
                {k:a[k] for k in ('id','text','date','note_id')} for a in sorted(selected,key=lambda a:(a['date'],a['note_id'],a['start']))]}
        def size(selected):return self.counter(json.dumps(payload(selected),ensure_ascii=False,separators=(',',':')))
        selected=[a for a in atoms if a['protected']]
        if size(selected)>token_budget:
            return dict(status='review_required',reason='Protected evidence exceeds packet budget; no silent truncation',
                synthetic=True,as_of=as_of,subject=subject,evidence=[],required_tokens=size(selected),
                token_budget=token_budget,model_revision=self.card['model_sha256'])
        candidates=[a for a in atoms if not a['protected'] and a['score']>=self.threshold]
        # Greedy utility per token; this is a heuristic, not an optimal solver.
        # Source diversity is rewarded; protected evidence is never traded away.
        while candidates:
            represented={a['note_id'] for a in selected}
            best=max(candidates,key=lambda a:(a['score']+0.05*a['query_overlap']+0.1*(a['note_id'] not in represented))/max(a['token_cost'],1))
            candidates.remove(best)
            if size(selected+[best])<=token_budget:selected.append(best)
        result=payload(selected)
        selected_ids={a['id'] for a in selected}
        refs=[{k:a[k] for k in ('id','note_id','start','end','document_hash','score','protected','sentence_text')} for a in selected]
        return dict(result,status='retrieval_proposal',synthetic=True,provenance=refs,
            input_note_count=len(eligible),excluded_future_notes=len(notes)-len(eligible),
            total_sentences=chart['total_sentences'],copy_forward_sentence_count=chart['echo_sentences'],
            unique_sentences=len(atoms),selected_sentences=len(selected),omitted_sentences=len(atoms)-len(selected),
            omitted_sentence_ids=[a['id'] for a in atoms if a['id'] not in selected_ids],
            raw_note_tokens=self.counter('\n'.join(n['text'] for n in eligible)),
            packet_tokens=size(selected),token_budget=token_budget,
            tokenizer_sha256=getattr(self.counter,'sha256',None),
            token_scope='Local tokenizer; complete packet JSON counted; provider instructions/schema and vendor tokenization excluded',
            model_revision=self.card['model_sha256'],selection_threshold=self.threshold,
            evidence_exhaustive=False,clinical_acceptance='unvalidated_retrieval_only',
            limitations=['Weakly supervised relevance model, not a clinical classifier.',
                'Protected-span rules are incomplete and do not guarantee clinical evidence recall.',
                'Known future arrivals are excluded; absent arrival times remain unknown.',
                'Copied text is deduplicated by the existing reader; the source chart remains authoritative.',
                'No care-gap or absence-of-care conclusion may be based only on omitted evidence.'])


def prepare_hosted_task(packet,options):
    """Connect the packet to existing adapters without issuing a network call."""
    from .providers import EvidenceTask
    if packet.get('status')!='retrieval_proposal':raise ValueError('Packet requires review')
    task=EvidenceTask(packet['subject'],packet['as_of'],options,packet['evidence'])
    task.state()
    return task
