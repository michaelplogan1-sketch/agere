"""Pinned offline encoders, complete token windows and content-addressed vectors."""
from __future__ import annotations
from contextlib import contextmanager

import hashlib
import json
import sqlite3
import time
from pathlib import Path

import numpy as np

from .contracts import digest


@contextmanager
def _model_use(instance, role):
    from .agere.request_lineage import component
    before = dict(instance.stats)
    with component('local_model', role=role, model_identity=instance.identity,
                   model=instance.manifest.get('repo') or instance.manifest.get('model_id') or instance.manifest.get('model')) as metrics:
        try:
            yield
        finally:
            metrics.update({k: instance.stats[k]-v for k,v in before.items()})
            metrics['stat_scope'] = 'method counter delta; may overlap concurrent calls on a shared model'


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
        with _model_use(self, 'retrieval_embedding'):
            return self._encode(texts, batch_size=batch_size)

    def _encode(self, texts, *, batch_size=4):
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
        with _model_use(self, 'passage_reranking'):
            return self._rank(query, evidence, top_k=top_k)

    def _rank(self, query, evidence, *, top_k=8):
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


# ---------------------------------------------------------------------------------------------------- second pinned encoder
# UltraBERT-Embed-150M, used ONLY for raw-catalog retrieval (answer_guard) and lab/condition terminology fallback (concepts).
# The stack encoder (LocalEncoder above), the learned router, the semantic cache and the dense index are untouched, so no
# certificate that names their identities changes. The manifest lives in the repository (models/ is not versioned) and pins
# every file the load reads, including the two remote-code modules.
import os
import threading

ROOT = Path(__file__).resolve().parents[1]
SECOND_ENCODER_MANIFEST = ROOT / "config" / "agere" / "encoders" / "ultrabert-embed-150m.json"
SECOND_ENCODER_RELPATH = Path("models") / "ultrabert" / "UltraBERT-Embed-150M"


def _sha256(path):
    h = hashlib.sha256()
    with Path(path).open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def verify_pinned(path, manifest):
    """Every pinned file must exist with its exact byte count and sha256; raises ValueError naming the first that does not."""
    path = Path(path)
    if not manifest.get("files"):
        raise ValueError("Pinned manifest lists no files")
    for name, spec in manifest["files"].items():
        source = path / name
        if not source.is_file():
            raise ValueError("Pinned model file missing: " + name)
        if source.stat().st_size != spec["bytes"] or _sha256(source) != spec["sha256"]:
            raise ValueError("Pinned model file changed: " + name)
    return manifest


class PinnedSentenceEncoder:
    """A sentence encoder loaded offline from a hash-pinned directory.

    Load order: read the repository manifest -> hash every pinned file (refuse on any mismatch or missing file) -> check the
    sentence-transformers pooling config and prompts equal the manifest's -> load with local_files_only (trust_remote_code
    for the pinned modeling/configuration modules) -> re-hash the module files transformers actually imported. Pooling
    follows the model's 1_Pooling config (CLS for UltraBERT-Embed), vectors are L2-normalised, and the query prompt is
    applied to the question side only (encode_queries); documents get the document prompt ('' here).
    identity = digest(pinned file hashes, pooling, normalisation, exact prompt strings, attention implementation)."""

    VERSION = 1

    def __init__(self, path, manifest_path=SECOND_ENCODER_MANIFEST, *, cache, threads=4, max_tokens=4096, batch_size=4):
        self.path = Path(path)
        self.manifest_path = Path(manifest_path)
        manifest = json.loads(self.manifest_path.read_text())
        verify_pinned(self.path, manifest)
        pooling = json.loads((self.path / "1_Pooling" / "config.json").read_text())
        st = json.loads((self.path / "config_sentence_transformers.json").read_text())
        prompts = st.get("prompts") or {}
        if manifest.get("pooling") not in {"cls", "mean"} or pooling.get("pooling_mode") != manifest["pooling"]:
            raise ValueError("Pooling config disagrees with the pinned manifest")
        if prompts.get("query") != manifest["query_prompt"] or prompts.get("document", "") != manifest.get("document_prompt", ""):
            raise ValueError("Prompt config disagrees with the pinned manifest")
        self.manifest = manifest
        self.pooling = manifest["pooling"]
        self.normalize = bool(manifest.get("normalize", True))
        self.query_prompt = manifest["query_prompt"]
        self.document_prompt = manifest.get("document_prompt", "")
        self.attn_implementation = "sdpa"
        self.identity = digest({"files": {k: v["sha256"] for k, v in manifest["files"].items()}, "repo": manifest.get("repo"),
                                "revision": manifest.get("revision"), "pooling": self.pooling, "normalize": self.normalize,
                                "query_prompt": self.query_prompt, "document_prompt": self.document_prompt,
                                "attn_implementation": self.attn_implementation, "version": self.VERSION})
        os.environ["HF_HUB_OFFLINE"] = "1"
        os.environ["TRANSFORMERS_OFFLINE"] = "1"
        import torch
        from transformers import AutoModel, AutoTokenizer
        self.torch = torch
        torch.set_num_threads(threads)
        self.tokenizer = AutoTokenizer.from_pretrained(self.path, local_files_only=True, trust_remote_code=False)
        self.model = AutoModel.from_pretrained(self.path, local_files_only=True, trust_remote_code=True,
                                               attn_implementation=self.attn_implementation, dtype=torch.float32).eval()
        self._check_imported_code()
        self.dimensions = int(self.model.config.hidden_size)
        self.max_tokens = int(max_tokens)
        self.batch_size = int(batch_size)
        Path(cache).parent.mkdir(parents=True, exist_ok=True)
        self.db = sqlite3.connect(cache, check_same_thread=False)
        self.db.execute("CREATE TABLE IF NOT EXISTS encoder_vectors (key TEXT PRIMARY KEY, dimensions INTEGER NOT NULL, vector BLOB NOT NULL)")
        self.db.commit()
        self.lock = threading.Lock()
        self.stats = {"cache_hits": 0, "texts_encoded": 0, "input_tokens": 0, "inference_seconds": 0.0}

    def _check_imported_code(self):
        """The remote-code modules transformers imported (copied into its modules cache) must be the pinned files."""
        import inspect
        pinned = set((self.manifest.get("remote_code") or {}).get("files") or [])
        for cls in (type(self.model), type(self.model.config)):
            source = Path(inspect.getsourcefile(cls))
            if source.name not in pinned or _sha256(source) != self.manifest["files"][source.name]["sha256"]:
                raise ValueError("Imported model code differs from the pinned file: " + source.name)

    def close(self):
        with self.lock:
            self.db.close()

    def encode_queries(self, texts):
        return self.encode(texts, prompt=self.query_prompt)

    def encode_documents(self, texts):
        return self.encode(texts, prompt=self.document_prompt)

    def encode(self, texts, *, prompt=""):
        with _model_use(self, 'secondary_embedding'):
            return self._encode(texts, prompt=prompt)

    def _encode(self, texts, *, prompt=""):
        texts = list(texts)
        if not texts:
            return np.empty((0, self.dimensions), dtype=np.float32)
        if not all(isinstance(t, str) for t in texts):
            raise ValueError("Expected text")
        keys = [digest([self.identity, prompt, t]) for t in texts]
        with self.lock:
            found, pending = {}, {}
            for key, text in zip(keys, texts):
                if key in found or key in pending:
                    self.stats["cache_hits"] += 1
                    continue
                row = self.db.execute("SELECT dimensions,vector FROM encoder_vectors WHERE key=?", (key,)).fetchone()
                if row:
                    vector = np.frombuffer(row[1], dtype=np.float32).copy()
                    if vector.shape != (row[0],) or row[0] != self.dimensions or not np.isfinite(vector).all():
                        raise ValueError("Invalid cached vector")
                    found[key] = vector
                    self.stats["cache_hits"] += 1
                else:
                    pending[key] = prompt + text
            items = sorted(pending.items(), key=lambda kv: len(kv[1]))  # similar lengths share a batch: less padding
            for i in range(0, len(items), self.batch_size):
                batch = items[i:i + self.batch_size]
                tokenized = self.tokenizer([t for _, t in batch], padding=True, truncation=False, return_tensors="pt")
                if tokenized["input_ids"].shape[1] > self.max_tokens:
                    raise ValueError("Overlength input; no truncation performed")
                started = time.perf_counter()
                with self.torch.inference_mode():
                    hidden = self.model(input_ids=tokenized["input_ids"], attention_mask=tokenized["attention_mask"]).last_hidden_state
                    if self.pooling == "cls":
                        vectors = hidden[:, 0]
                    else:
                        mask = tokenized["attention_mask"].unsqueeze(-1).to(hidden.dtype)
                        vectors = (hidden * mask).sum(1) / mask.sum(1).clamp(min=1)
                    if self.normalize:
                        vectors = self.torch.nn.functional.normalize(vectors, dim=1)
                    vectors = vectors.cpu().numpy().astype(np.float32)
                self.stats["inference_seconds"] += time.perf_counter() - started
                self.stats["texts_encoded"] += len(batch)
                self.stats["input_tokens"] += int(tokenized["attention_mask"].sum())
                with self.db:
                    for (key, _), vector in zip(batch, vectors):
                        if not np.isfinite(vector).all():
                            raise ValueError("Nonfinite encoder output")
                        found[key] = vector
                        self.db.execute("INSERT OR IGNORE INTO encoder_vectors VALUES (?,?,?)", (key, len(vector), vector.tobytes()))
        return np.stack([found[key] for key in keys])


def second_encoder_path():
    """AGERE_SECOND_ENCODER_PATH, else <repo>/models/ultrabert/UltraBERT-Embed-150M, else (from a .claude/worktrees checkout)
    the main checkout's models directory; None when no candidate holds the weights."""
    env = os.environ.get("AGERE_SECOND_ENCODER_PATH")
    candidates = [Path(env)] if env else []
    candidates.append(ROOT / SECOND_ENCODER_RELPATH)
    parts = ROOT.parts
    if ".claude" in parts and "worktrees" in parts:
        candidates.append(Path(*parts[:parts.index(".claude")]) / SECOND_ENCODER_RELPATH)
    return next((c for c in candidates if (c / "model.safetensors").is_file()), None)


_SECOND = {"encoder": None, "status": "not loaded", "loaded": False}
_SECOND_LOCK = threading.Lock()


def second_encoder():
    """The process-wide pinned second encoder, loaded on first use; None (reason in second_encoder_status()) when
    AGERE_SECOND_ENCODER=off, the weights are absent, or verification/loading refuses. Callers then keep their lexical path."""
    if os.environ.get("AGERE_SECOND_ENCODER", "on") == "off":
        return None
    if _SECOND["loaded"]:
        return _SECOND["encoder"]
    with _SECOND_LOCK:
        if not _SECOND["loaded"]:
            path = second_encoder_path()
            if path is None:
                _SECOND.update(encoder=None, status="model files not installed")
            else:
                cache = os.environ.get("AGERE_SECOND_ENCODER_CACHE") or str(ROOT / "artifacts" / "second-encoder" / "vectors.sqlite3")
                try:
                    _SECOND.update(encoder=PinnedSentenceEncoder(path, cache=cache), status="loaded")
                except Exception as exc:  # noqa: BLE001 - refusal or missing runtime: fall back to lexical, say why
                    _SECOND.update(encoder=None, status=f"refused: {type(exc).__name__}: {exc}"[:300])
            _SECOND["loaded"] = True
    return _SECOND["encoder"]


def second_encoder_status():
    enc = _SECOND["encoder"]
    return {"status": _SECOND["status"], "identity": getattr(enc, "identity", None),
            "manifest": str(SECOND_ENCODER_MANIFEST.relative_to(ROOT)), "enabled": os.environ.get("AGERE_SECOND_ENCODER", "on") != "off"}


def reset_second_encoder():
    """Forget the loaded encoder (tests, or after installing the weights)."""
    with _SECOND_LOCK:
        enc = _SECOND["encoder"]
        if enc is not None:
            enc.close()
        _SECOND.update(encoder=None, status="not loaded", loaded=False)
