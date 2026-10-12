"""Trained proposal router for the original research curriculum; no execution."""
import hashlib
import json
from pathlib import Path


class QuestionPlanner:
    def __init__(self,directory):
        import joblib
        directory=Path(directory)
        self.card=json.loads((directory/'planner_card.json').read_text())
        artifact=directory/'planner.joblib'
        if hashlib.sha256(artifact.read_bytes()).hexdigest()!=self.card['sha256']:
            raise ValueError('Planner artifact checksum mismatch')
        self.model=joblib.load(artifact)
        self.encoder=None
        if 'encoder' in self.card:
            from sentence_transformers import SentenceTransformer
            location=Path(self.card['encoder']['path'])
            manifest=location/'local_manifest.json'
            if hashlib.sha256(manifest.read_bytes()).hexdigest()!=self.card['encoder']['manifest_sha256']:
                raise ValueError('Encoder manifest checksum mismatch')
            for name,metadata in json.loads(manifest.read_text())['files'].items():
                if hashlib.sha256((location/name).read_bytes()).hexdigest()!=metadata['sha256']:
                    raise ValueError('Encoder file checksum mismatch: '+name)
            self.encoder=SentenceTransformer(str(location),local_files_only=True,trust_remote_code=False,device='cpu')

    def propose(self,question):
        if not isinstance(question,str) or not question.strip() or len(question)>4000:
            raise ValueError('Provide a question of 1-4000 characters')
        features=[question]
        if self.encoder is not None:
            if len(self.encoder.tokenizer.encode(question,truncation=False))>self.encoder.max_seq_length:
                raise ValueError('Question exceeds encoder context; split it into specific questions')
            features=self.encoder.encode([question],normalize_embeddings=True,show_progress_bar=False)
        scores=self.model.predict_proba(features)[0]
        order=scores.argsort()[::-1][:3]
        routes=[]
        for index in order:
            name=str(self.model.classes_[index]);template=self.card['domains'][name]
            routes.append(dict(domain=name,score=float(scores[index]),**template))
        return dict(question=question,status='plan_proposal_requires_review',routes=routes,
            automatic_execution=False,model_sha256=self.card['sha256'],
            limitations=['Small analyst-labeled training set; scores are not calibrated correctness probabilities.',
                'Question family does not establish a valid query, available data or clinical answer.',
                'Novel questions may fit none of these domains; review all plans before use.'])
