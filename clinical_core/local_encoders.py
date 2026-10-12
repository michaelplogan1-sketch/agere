"""Pinned offline encoders, complete token windows and content-addressed vectors."""
from __future__ import annotations

import hashlib
import json
import sqlite3
import time
from pathlib import Path

import numpy as np

from .contracts import digest


def verified_manifest(path):
    path = Path(path)
    manifest = json.loads((path / "agere-model.json").read_text())
    for name, spec in manifest["files"].items():
        source = path / name
        h = hashlib.sha256()
        with source.open("rb") as stream:
            for chunk in iter(lambda: stream.read(1024 * 1024), b""):
                h.update(chunk)
        if source.stat().st_size != spec["bytes"] or h.hexdigest() != spec["sha256"]:
            raise ValueError("Installed model content changed: " + name)
    return manifest


class LocalEncoder:
    def __init__(self, path, *, pooling, cache, threads=4):
        import torch
        from transformers import AutoModel, AutoTokenizer
        self.path = Path(path)
        self.manifest = verified_manifest(self.path)
        if pooling not in {"cls", "mean"}:
            raise ValueError("Unknown pooling")
        self.identity = digest({"manifest": self.manifest, "pooling": pooling, "version": 1})
        self.pooling = pooling
        self.torch = torch
        torch.set_num_threads(threads)
        self.tokenizer = AutoTokenizer.from_pretrained(self.path, local_files_only=True, trust_remote_code=False)
        self.model = AutoModel.from_pretrained(self.path, local_files_only=True,
            trust_remote_code=False, attn_implementation="eager", dtype=torch.float32).eval()
        self.device = "cpu"
        self.model.to(self.device)
        self.limit = int(self.model.config.max_position_embeddings)
        Path(cache).parent.mkdir(parents=True, exist_ok=True)
        self.db = sqlite3.connect(cache)
        self.db.execute("CREATE TABLE IF NOT EXISTS encoder_vectors (key TEXT PRIMARY KEY, dimensions INTEGER NOT NULL, vector BLOB NOT NULL)")
        self.db.commit()
        self.stats = {"cache_hits": 0, "texts_encoded": 0, "input_tokens": 0, "inference_seconds": 0.0}

    def close(self):
        self.db.close()

    def windows(self, text, *, size=256, overlap=32):
        if not isinstance(text, str):
            raise ValueError("Expected source text")
        if not 0 <= overlap < size or size + self.tokenizer.num_special_tokens_to_add() > self.limit:
            raise ValueError("Invalid token window")
        offsets = self.tokenizer(text, add_special_tokens=False, return_offsets_mapping=True,
                                 truncation=False)["offset_mapping"]
        if not offsets:
            return []
        result = []
        start = 0
        while start < len(offsets):
            end = min(start + size, len(offsets))
            a = offsets[start][0] if start else 0
            b = offsets[end - 1][1] if end < len(offsets) else len(text)
            result.append({"start": a, "end": b, "text": text[a:b], "token_start": start, "token_end": end})
            if end == len(offsets):
                break
            start = end - overlap
        return result

    def encode(self, texts, *, batch_size=4):
        if not texts:
            return np.empty((0, self.model.config.hidden_size), dtype=np.float32)
        keys = [digest([self.identity, text]) for text in texts]
        found, pending = {}, {}
        for key, text in zip(keys, texts):
            if key in found or key in pending:
                self.stats["cache_hits"] += 1
                continue
            row = self.db.execute("SELECT dimensions,vector FROM encoder_vectors WHERE key=?", (key,)).fetchone()
            if row:
                vector = np.frombuffer(row[1], dtype=np.float32).copy()
                if vector.shape != (row[0],) or row[0] != self.model.config.hidden_size or not np.isfinite(vector).all():
                    raise ValueError("Invalid cached vector")
                found[key] = vector
                self.stats["cache_hits"] += 1
            else:
                pending[key] = text
        items = list(pending.items())
        for i in range(0, len(items), batch_size):
            batch = items[i:i + batch_size]
            tokenized = self.tokenizer([t for _, t in batch], padding=True, truncation=False, return_tensors="pt")
            if tokenized["input_ids"].shape[1] > self.limit:
                raise ValueError("Overlength input; use explicit windows rather than truncation")
            started = time.perf_counter()
            with self.torch.inference_mode():
                hidden = self.model(**tokenized).last_hidden_state
                if self.pooling == "cls":
                    vectors = hidden[:, 0]
                else:
                    mask = tokenized["attention_mask"].unsqueeze(-1)
                    vectors = (hidden * mask).sum(1) / mask.sum(1).clamp(min=1)
                vectors = self.torch.nn.functional.normalize(vectors, dim=1).cpu().numpy().astype(np.float32)
            self.stats["inference_seconds"] += time.perf_counter() - started
            self.stats["texts_encoded"] += len(batch)
            self.stats["input_tokens"] += int(tokenized["attention_mask"].sum())
            with self.db:
                for (key, _), vector in zip(batch, vectors):
                    if not np.isfinite(vector).all():
                        raise ValueError("Nonfinite encoder output")
                    found[key] = vector
                    self.db.execute("INSERT OR IGNORE INTO encoder_vectors VALUES (?,?,?)",
                                    (key, len(vector), vector.tobytes()))
        return np.stack([found[key] for key in keys])


class LocalReranker:
    def __init__(self, path, *, cache):
        import torch
        from transformers import AutoModelForSequenceClassification, AutoTokenizer
        self.manifest = verified_manifest(path)
        self.identity = digest({"manifest": self.manifest, "runtime": "raw-logit-no-truncation/1"})
        self.torch = torch
        self.tokenizer = AutoTokenizer.from_pretrained(path, local_files_only=True, trust_remote_code=False)
        self.model = AutoModelForSequenceClassification.from_pretrained(path, local_files_only=True,
                         trust_remote_code=False, dtype=torch.float32).eval()
        self.limit = min(2048, self.model.config.max_position_embeddings - 2)
        self.db = sqlite3.connect(cache)
        self.db.execute("CREATE TABLE IF NOT EXISTS reranker_scores (key TEXT PRIMARY KEY, score REAL NOT NULL)")
        self.db.commit()
        self.stats = {"cache_hits": 0, "pairs_scored": 0, "input_tokens": 0, "inference_seconds": 0.0}

    def close(self):
        self.db.close()

    def rank(self, query, evidence, *, top_k=8):
        ranked = []
        for item in evidence:
            key = digest([self.identity, query, item["text"]])
            cached = self.db.execute("SELECT score FROM reranker_scores WHERE key=?", (key,)).fetchone()
            if cached:
                score = cached[0]
                self.stats["cache_hits"] += 1
            else:
                tokens = self.tokenizer(query, item["text"], return_tensors="pt", truncation=False)
                if tokens["input_ids"].shape[1] > self.limit:
                    raise ValueError("Reranker input exceeds explicit compute/context bound; no truncation performed")
                started = time.perf_counter()
                with self.torch.inference_mode():
                    score = float(self.model(**tokens).logits.reshape(-1)[0])
                if not np.isfinite(score):
                    raise ValueError("Nonfinite reranking score")
                self.stats["pairs_scored"] += 1
                self.stats["input_tokens"] += tokens["input_ids"].numel()
                self.stats["inference_seconds"] += time.perf_counter() - started
                with self.db:
                    self.db.execute("INSERT OR IGNORE INTO reranker_scores VALUES (?,?)", (key, score))
            ranked.append({**item, "reranker_logit": score})
        ranked.sort(key=lambda r: (not r["protected"], -r["reranker_logit"], r["note_id"], r["start"]))
        return ranked[:top_k]
