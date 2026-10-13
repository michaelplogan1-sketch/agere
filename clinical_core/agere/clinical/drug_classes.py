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
    'nsaid': r'\b(?:ibuprofen|naproxen|diclofenac|meloxicam|celecoxib|indomethacin|ketorolac|nabumetone|etodolac|piroxicam|sulindac)\b',
    'bisphosphonate': r'\b(?:alendron\w*|risedron\w*|ibandron\w*|zoledron\w*)\b',
    'proton_pump_inhibitor': r'\b(?:omeprazole|esomeprazole|pantoprazole|lansoprazole|rabeprazole|dexlansoprazole)\b',
    'skeletal_muscle_relaxant': r'\b(?:cyclobenzaprine|methocarbamol|carisoprodol|metaxalone|tizanidine|baclofen|orphenadrine|chlorzoxazone)\b',
    'first_generation_antihistamine': r'\b(?:diphenhydramine|chlorpheniramine|hydroxyzine|promethazine|doxylamine|cyproheptadine|meclizine|brompheniramine|clemastine|dimenhydrinate)\b',
    'second_generation_antihistamine': r'\b(?:loratadine|desloratadine|cetirizine|levocetirizine|fexofenadine)\b',
    'antipsychotic': r'\b(?:haloperidol|quetiapine|olanzapine|risperidone|aripiprazole|ziprasidone|lurasidone|clozapine|paliperidone)\b',
    'nitrate': r'\b(?:nitroglycerin|isosorbide)\b',
    'iron_supplement': r'\b(?:ferrous|ferric|iron sucrose|polysaccharide iron)\b',
    'sedative_hypnotic': r'\b(?:zolpidem|eszopiclone|zaleplon)\b',
}
GROUPS = {'antihypertensive': ['ace_inhibitor', 'arb', 'arni', 'beta_blocker', 'calcium_channel_blocker', 'thiazide_diuretic', 'mra'],
          'raas_inhibitor': ['ace_inhibitor', 'arb', 'arni'],
          'diabetes_medication': ['metformin', 'insulin', 'sulfonylurea', 'dpp4_inhibitor', 'sglt2_inhibitor', 'glp1_agonist'],
          'beers_high_risk': ['first_generation_antihistamine', 'benzodiazepine', 'skeletal_muscle_relaxant', 'sedative_hypnotic', 'antipsychotic']}
_COMPILED = {k: re.compile(v, re.I) for k, v in CLASSES.items()}


def classify(name):
    """Drug classes for a received medication name (possibly several for combination products)."""
    if not name: return []
    found = [k for k, rx in _COMPILED.items() if rx.search(str(name))]
    if 'arni' in found and 'arb' in found and not _OTHER_ARB.search(_VALSARTAN.sub('', str(name))):
        found.remove('arb')  # sacubitril/valsartan (Entresto) is an ARNI; its valsartan component does not make it an ARB
    return found


_VALSARTAN = re.compile(r'\bvalsartan\b', re.I)
_OTHER_ARB = _COMPILED['arb']
