"""Drug classes by active ingredient (generic names), for medication questions across order, dispense, list,
claim, administration and patient-report sources. Patterns match ingredient words, so 'nystatin' is not a statin.
Evidence-based HF beta-blockers follow ACC/AHA HFrEF guidance (carvedilol, bisoprolol, metoprolol succinate)."""
import re

CLASSES = {
    'statin': r'\b(?:atorva|rosuva|simva|prava|lova|fluva|pitava)statin\b',
    'beta_blocker': r'\b(?:metoprolol|carvedilol|bisoprolol|atenolol|propranolol|nebivolol|labetalol|nadolol|sotalol)\b',
    'hf_evidence_based_beta_blocker': r'\bcarvedilol\b|\bbisoprolol\b|\bmetoprolol succinate\b|\b(?:24 hr|er|xl|extended[- ]release)\b[^,;]*\bmetoprolol\b|\bmetoprolol\b[^,;]*\b(?:succinate|24 hr|er\b|xl\b|extended[- ]release)',
    'ace_inhibitor': r'\b(?:lisinopril|enalapril|ramipril|benazepril|quinapril|captopril|fosinopril|perindopril|trandolapril|moexipril)\b',
    'arb': r'\b(?:losartan|valsartan|irbesartan|olmesartan|candesartan|telmisartan|azilsartan|eprosartan)\b',
    'arni': r'\bsacubitril\b',
    'mra': r'\b(?:spironolactone|eplerenone|finerenone)\b',
    'sglt2_inhibitor': r'\b(?:empagliflozin|dapagliflozin|canagliflozin|ertugliflozin|sotagliflozin)\b',
    'glp1_agonist': r'\b(?:semaglutide|liraglutide|dulaglutide|exenatide|tirzepatide|lixisenatide)\b',
    'metformin': r'\bmetformin\b',
    'insulin': r'\binsulin\b',
    'sulfonylurea': r'\b(?:glipizide|glyburide|glimepiride)\b',
    'dpp4_inhibitor': r'\b(?:sitagliptin|linagliptin|saxagliptin|alogliptin)\b',
    'anticoagulant': r'\b(?:warfarin|apixaban|rivaroxaban|dabigatran|edoxaban|enoxaparin|heparin)\b',
    'antiplatelet': r'\b(?:clopidogrel|ticagrelor|prasugrel|aspirin)\b',
    'loop_diuretic': r'\b(?:furosemide|bumetanide|torsemide)\b',
    'thiazide_diuretic': r'\b(?:hydrochlorothiazide|chlorthalidone|indapamide|metolazone)\b',
    'calcium_channel_blocker': r'\b(?:amlodipine|nifedipine|diltiazem|verapamil|felodipine)\b',
    'opioid': r'\b(?:hydrocodone|oxycodone|morphine|hydromorphone|fentanyl|tramadol|codeine|methadone|buprenorphine|tapentadol|oxymorphone)\b',
    'benzodiazepine': r'\b(?:alprazolam|lorazepam|clonazepam|diazepam|temazepam|chlordiazepoxide)\b',
    'inhaled_corticosteroid': r'\b(?:fluticasone|budesonide|beclomethasone|mometasone|ciclesonide)\b[^,;]*(?:inhal|actuat|dpi|mdi)',
    'short_acting_beta_agonist': r'\b(?:albuterol|levalbuterol)\b',
    'ssri': r'\b(?:sertraline|fluoxetine|citalopram|escitalopram|paroxetine|fluvoxamine)\b',
    'levothyroxine': r'\blevothyroxine\b',
}
GROUPS = {'antihypertensive': ['ace_inhibitor', 'arb', 'arni', 'beta_blocker', 'calcium_channel_blocker', 'thiazide_diuretic', 'mra'],
          'raas_inhibitor': ['ace_inhibitor', 'arb', 'arni'],
          'diabetes_medication': ['metformin', 'insulin', 'sulfonylurea', 'dpp4_inhibitor', 'sglt2_inhibitor', 'glp1_agonist']}
_COMPILED = {k: re.compile(v, re.I) for k, v in CLASSES.items()}


def classify(name):
    """Drug classes for a received medication name (possibly several for combination products)."""
    if not name: return []
    return [k for k, rx in _COMPILED.items() if rx.search(str(name))]
