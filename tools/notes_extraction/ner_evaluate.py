"""Check the local clinical NER against the existing Sonnet labels and write clinical_ner_admission.json.

For every labelled unit (dev + test splits, artifacts/notes-extraction): rule facts = note_facts.extract (NER not applied);
NER-added facts = clinical_ner.candidates not already among the rule facts. Against the labels (same key as evaluate.py:
fact_type, normalised value, negation):
  symptom                an NER-added fact is a true positive when a label states it; precision, Wilson lower bound, and the
                         recall gain it buys (labels the rules missed that NER found) are reported;
  medication_qualifier   rule facts with no qualifier that NER fills: correct when the label's qualifier for that fact agrees.
A type is ADMITTED (used by note_facts) when its added-fact precision >= BAR (0.90), n >= MIN_N and it gains recall.
Usage: python tools/notes_extraction/ner_evaluate.py [--splits dev test] [--limit N] [--no-write]
"""
import argparse, json, sys
from collections import Counter
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from common import OUT, read_jsonl, norm_value, note_facts  # noqa: E402
from evaluate import key, valid_label, wilson, BAR, MIN_N, LABEL_MODEL  # noqa: E402
from clinical_core.agere.clinical import clinical_ner  # noqa: E402


def run(splits=('dev', 'test'), limit=None, model=LABEL_MODEL):
    units = [u for u in read_jsonl(OUT / 'sample.jsonl') if u['split'] in splits]
    labels = {r['unit_id']: r for r in read_jsonl(OUT / f'labels-{model}.jsonl') if 'error' not in r}
    units = [u for u in units if u['unit_id'] in labels][:limit]
    sym = Counter(); q = Counter(); errors = []; qerrors = []
    base_tp = Counter(); base_ext = Counter(); gold_n = Counter()
    for u in units:
        gold = [f for f in labels[u['unit_id']]['facts'] if valid_label(f)]
        gk = Counter(map(key, gold))
        rules = note_facts.extract(u['text'], u['stream'], u['field'])   # NER not admitted yet unless the file says so
        rules = [f for f in rules if f.get('source') != 'ner']
        rk = Counter(map(key, rules))
        for k, n in gk.items(): gold_n[k[0]] += n
        for k, n in (rk & gk).items(): base_tp[k[0]] += n
        for k, n in rk.items(): base_ext[k[0]] += n
        ents = clinical_ner.entities(u['text'])
        added = [f for f in clinical_ner.candidates(u['text'], u['stream'], u['field'], ents) if key(f) not in rk]
        seen = set()
        for f in added:
            k = key(f)
            if k in seen: continue
            seen.add(k); sym['added'] += 1
            if gk.get(k): sym['tp'] += 1
            else: errors.append({'unit': u['unit_id'], 'fact': list(k), 'quote': f['evidence_text'], 'text': u['text'][:160]})
        for k in gk:
            if k[0] == 'symptom' and k not in rk and k in seen: sym['recall_gain'] += 1
        gold_by = {key(f): f for f in gold}
        for f in rules:
            if f['fact_type'].startswith('medication') and not f.get('qualifier'):
                probe = [dict(f)]; clinical_ner.qualifiers(u['text'], probe, ents)
                if probe[0].get('qualifier') and key(f) in gold_by and gold_by[key(f)].get('qualifier'):
                    ok = str(gold_by[key(f)]['qualifier']).lower().startswith(probe[0]['qualifier'])
                    q['filled'] += 1; q['correct'] += int(ok)
                    if not ok: qerrors.append({'ner': probe[0]['qualifier'], 'label': gold_by[key(f)]['qualifier'], 'text': u['text'][:160]})
    rep = {'units': len(units), 'splits': list(splits), 'bar': BAR, 'min_n': MIN_N, 'rules_symptom': {
               'extracted': base_ext['symptom'], 'true_positive': base_tp['symptom'], 'reference': gold_n['symptom']},
           'symptom': {'added': sym['added'], 'true_positive': sym['tp'], 'recall_gain': sym['recall_gain'],
                       'precision': round(sym['tp'] / sym['added'], 3) if sym['added'] else None,
                       'wilson_low': round(wilson(sym['tp'], sym['added']), 3) if sym['added'] else None},
           'medication_qualifier': {'filled_and_labelled': q['filled'], 'correct': q['correct'],
                                    'precision': round(q['correct'] / q['filled'], 3) if q['filled'] else None},
           'errors_sample': errors[:25], 'qualifier_errors_sample': qerrors[:15]}
    admit = []
    s = rep['symptom']
    if s['added'] >= MIN_N and s['precision'] is not None and s['precision'] >= BAR and s['recall_gain'] > 0: admit.append('symptom')
    m = rep['medication_qualifier']
    if m['filled_and_labelled'] >= MIN_N and m['precision'] is not None and m['precision'] >= BAR: admit.append('medication_qualifier')
    rep['admitted'] = admit
    return rep


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--splits', nargs='+', default=['dev', 'test'])
    ap.add_argument('--limit', type=int)
    ap.add_argument('--no-write', action='store_true')
    a = ap.parse_args()
    rep = run(tuple(a.splits), a.limit)
    print(json.dumps({k: v for k, v in rep.items() if k not in ('errors_sample', 'qualifier_errors_sample')}, indent=1))
    print(json.dumps({'errors': rep['errors_sample'][:8], 'qerrors': rep['qualifier_errors_sample'][:8]}, indent=1))
    if not a.no_write:
        clinical_ner.ADMISSION.write_text(json.dumps({'schema': 'agere-clinical-ner-admission/1', 'version': clinical_ner.VERSION,
                                                     'models': {k: v.name for k, v in clinical_ner.MODELS.items()}, 'bar': BAR,
                                                     'admitted': rep['admitted'], 'evaluation': {k: v for k, v in rep.items() if k != 'errors_sample'},
                                                     'errors_sample': rep['errors_sample']}, indent=1))
        (OUT / 'ner-evaluation.json').write_text(json.dumps(rep, indent=1))


if __name__ == '__main__':
    main()
