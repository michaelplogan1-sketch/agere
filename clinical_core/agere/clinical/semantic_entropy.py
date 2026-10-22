"""Semantic-entropy abstention feature from sampled-output agreement (no hidden states, no local-model loading).

Farquhar et al. (Nature 2024) measure uncertainty over MEANINGS, not strings: sample several answers, cluster the ones that mean
the same thing, take the entropy of the cluster distribution. Hidden-state probes approximate that entropy; Agere already holds
the samples, so it computes the discrete semantic entropy directly:
  * cluster equivalence is verification_v2.same_answer (equal scalars, equal patient sets, equal row signatures)
  * samples come from what the cascade already ran for the answer: the hosted verification clusters (independent candidate
    answers grouped by answer), the hybrid analysts' runs / passing / agree record, and an explicit list of sampled answers
No extra sampling and no model call is made here: a question whose samples agree has entropy 0; one with a single sample has
no estimate (reported as such, never as certainty).
entropy_bits is the Shannon entropy of the cluster mass; normalized divides by log2(samples) (0 = all samples agree, 1 = all differ).
check_answer() is the answer_checks hook: high normalized entropy -> annotation + low confidence, never a withheld answer.
"""
import math

VERSION = 'semantic-entropy/1'
HIGH = 0.5      # normalized entropy at or above this marks the answer's meaning as unstable across samples


def clusters_of(results):
    """Group sampled results (verification_v2 result dicts or answer dicts) by meaning -> list of cluster sizes, largest first."""
    from . import verification_v2 as v2
    from .answer_guard import as_result
    groups = []
    for r in results:
        if r is None: continue
        res = as_result(r) if isinstance(r, dict) and 'answer_type' in r else r
        for g in groups:
            try:
                if v2.same_answer(g[0], res): g.append(res); break
            except Exception:  # noqa: BLE001 - incomparable shapes never merge
                continue
        else:
            groups.append([res])
    return sorted((len(g) for g in groups), reverse=True)


def entropy(sizes):
    """-> {'samples', 'clusters', 'entropy_bits', 'normalized', 'top_share', 'estimable'} from cluster sizes."""
    n = sum(sizes)
    if n < 2:
        return {'samples': n, 'clusters': len(sizes), 'entropy_bits': None, 'normalized': None, 'top_share': 1.0 if n else None,
                'estimable': False}
    h = -sum((s / n) * math.log2(s / n) for s in sizes if s)
    return {'samples': n, 'clusters': len(sizes), 'entropy_bits': round(h, 4), 'normalized': round(h / math.log2(n), 4),
            'top_share': round(max(sizes) / n, 4), 'estimable': True}


def samples_from_receipt(receipt):
    """Cluster sizes the cascade already produced for this answer, or None when it ran only one path."""
    receipt = receipt if isinstance(receipt, dict) else {}
    ver = ((receipt.get('hosted_sql') or {}).get('verification') or {})
    sizes = [len(c.get('labels') or []) for c in ver.get('clusters') or [] if c.get('labels')]
    if sum(sizes) >= 2: return sorted(sizes, reverse=True), 'hosted_verification_clusters'
    h = receipt.get('hybrid') or {}
    runs, passing = h.get('runs'), h.get('passing')
    if isinstance(runs, list): runs = len(runs)
    if isinstance(passing, list): passing = len(passing)
    if isinstance(runs, int) and isinstance(passing, int) and runs >= 2 and 0 <= passing <= runs:
        # passing runs that agree form the largest cluster; the others are singletons or failed
        agree = h.get('agree')
        top = passing if agree in (True, None, 'all') else max(passing // 2, 1)
        return sorted([top] + [1] * (runs - top), reverse=True), 'hybrid_analyst_runs'
    return None, None


def measure(results=None, receipt=None):
    """-> entropy report with its source, or None when there is nothing to compare."""
    if results and len([r for r in results if r is not None]) >= 2:
        out = entropy(clusters_of(results)); out['source'] = 'explicit_samples'; return {**out, 'version': VERSION}
    sizes, source = samples_from_receipt(receipt)
    if not sizes: return None
    out = entropy(sizes); out['source'] = source
    return {**out, 'version': VERSION}


def check_answer(receipt, answer, verdict):
    """answer_checks hook: attach the measurement; an unstable meaning is disclosed and the answer marked low-confidence."""
    m = measure(receipt=receipt)
    if m is None: return None
    verdict['semantic_entropy'] = m
    if m['estimable'] and m['normalized'] >= HIGH:
        verdict['annotations'].append(f"Semantic entropy {m['entropy_bits']:.2f} bits over {m['samples']} sampled answers in {m['clusters']} "
                                      f"meanings (largest cluster {m['top_share']:.0%}): the answer's meaning is unstable across samples.")
        if (verdict.get('certificate') or {}).get('status') != 'certified': verdict.setdefault('confidence', 'low')
    return m
