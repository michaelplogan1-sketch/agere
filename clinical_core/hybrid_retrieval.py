"""Patient-scoped lexical/dense retrieval; relevance is not clinical entailment."""
from __future__ import annotations

import math
import re
from collections import Counter
from datetime import date

import numpy as np

from .contracts import digest


def lexical_scores(query, passages):
    tokens = [re.findall(r"[a-z0-9]+", p.lower()) for p in passages]
    query_terms = set(re.findall(r"[a-z0-9]+", query.lower()))
    frequencies = [Counter(t) for t in tokens]
    average = sum(map(len, tokens)) / max(len(tokens), 1) or 1
    result = [0.0] * len(tokens)
    for term in sorted(query_terms):
        df = sum(term in f for f in frequencies)
        idf = math.log(1 + (len(tokens) - df + 0.5) / (df + 0.5))
        for i, frequency in enumerate(frequencies):
            tf = frequency[term]
            result[i] += idf * tf * 2.2 / (tf + 1.2 * (0.25 + 0.75 * len(tokens[i]) / average))
    return result


def retrieve(encoder, patient_id, notes, query, as_of, *, top_k=8, protected=(), provenance=None):
    if type(top_k) is not int or not 1 <= top_k <= 64:
        raise ValueError("Bounded positive top_k required")
    date.fromisoformat(as_of)
    chunks, excluded = [], 0
    for note in notes:
        if note["patient_id"] != patient_id:
            raise ValueError("Cross-patient retrieval input")
        date.fromisoformat(note["date"])
        if note["date"] > as_of or (note.get("available_date") or as_of) > as_of:
            excluded += 1
            continue
        for window in encoder.windows(note["text"]):
            if provenance is not None and digest(note) not in provenance:
                raise ValueError("Retrieved source lost ledger provenance")
            chunks.append({**window, "patient_id": patient_id, "note_id": note["note_id"],
                "source_hash": digest(note), "record_id": note.get("record_id", note["note_id"]),
                "source_table": note.get("source_table"), "source_field": note.get("source_field", "text"),
                "event_date": note["date"], "available_date": note.get("available_date"),
                "source_origin": (provenance or {}).get(digest(note)),
                "protected": any(p["note_id"] == note["note_id"] and p["start"] < window["end"]
                                 and p["end"] > window["start"] for p in protected)})
    if not chunks:
        return {"status": "no_visible_text", "evidence": [], "indexed_chunks": 0,
                "excluded_notes": excluded, "complete_evidence_guarantee": False}
    text = [c["text"] for c in chunks]
    vectors = encoder.encode(text)
    query_vector = encoder.encode([query])[0]
    dense = vectors @ query_vector
    lexical = lexical_scores(query, text)
    combined = np.zeros(len(chunks))
    for scores in (dense, lexical):
        # Zero lexical scores are ties with no positive evidence, not an arbitrary boost.
        order = sorted(range(len(chunks)), key=lambda i: (-float(scores[i]), i))
        for rank, index in enumerate(order, 1):
            if scores is lexical and scores[index] <= 0:
                continue
            combined[index] += 1 / (60 + rank)
    ordered = sorted(range(len(chunks)), key=lambda i: (-combined[i], i))
    required = [i for i in ordered if chunks[i]["protected"]]
    chosen = required[:top_k]
    chosen += [i for i in ordered if i not in chosen][:top_k - len(chosen)]
    return {"status": "protected_evidence_overflow" if len(required) > top_k else "retrieval_proposal",
            "method": "BM25 + Granite cosine, reciprocal-rank fusion; protected observed exception spans",
            "encoder_identity": encoder.identity, "indexed_chunks": len(chunks), "excluded_notes": excluded,
            "protected_chunks": len(required), "unselected_chunks": len(chunks) - len(chosen),
            "unknown_arrival_notes": sum(not n.get("available_date") for n in notes if n["date"] <= as_of),
            "complete_evidence_guarantee": False,
            "evidence": [{**chunks[i], "rank_score": float(combined[i]), "dense_similarity": float(dense[i]),
                          "lexical_score": float(lexical[i])} for i in chosen]}
