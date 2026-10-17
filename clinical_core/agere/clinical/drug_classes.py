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
    'opioid': r'\b(?:hydrocodone|oxycodone|morphine|hydromorphone|fentanyl|tramadol|codeine|methadone|buprenorphine|tapentadol|oxymorphone|meperidine)\b',
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
    # Build log step 13: classes across all of healthcare (about 14k payer fills were unclassified: ADHD,
    # antidepressants, contraceptives, antibiotics...). Ingredient names only; brands fall back via drug_brands.json.
    'adhd_stimulant': r'\b(?:methylphenidate|dexmethylphenidate|amphetamine|dextroamphetamine|lisdexamfetamine|methamphetamine)\b',
    'adhd_nonstimulant': r'\b(?:atomoxetine|viloxazine)\b',
    'snri': r'\b(?:duloxetine|venlafaxine|desvenlafaxine|levomilnacipran|milnacipran)\b',
    'ndri': r'\bbupropion\b',
    'sari_antidepressant': r'\b(?:trazodone|nefazodone)\b',
    'tetracyclic_antidepressant': r'\bmirtazapine\b',
    'tricyclic_antidepressant': r'\b(?:amitriptyline|nortriptyline|imipramine|desipramine|clomipramine|doxepin|protriptyline)\b',
    'lithium': r'\blithium\b',
    'anticonvulsant': r'\b(?:levetiracetam|lamotrigine|valproic|valproate|divalproex|carbamazepine|oxcarbazepine|topiramate|phenytoin|lacosamide|zonisamide|phenobarbital)\b',
    'gabapentinoid': r'\b(?:gabapentin|pregabalin)\b',
    'alpha1_blocker': r'\b(?:tamsulosin|alfuzosin|silodosin|doxazosin|terazosin|prazosin)\b',
    'five_alpha_reductase_inhibitor': r'\b(?:finasteride|dutasteride)\b',
    'urinary_antimuscarinic': r'\b(?:oxybutynin|tolterodine|solifenacin|darifenacin|trospium|fesoterodine)\b',
    'beta3_agonist': r'\b(?:mirabegron|vibegron)\b',
    'lama': r'\b(?:tiotropium|umeclidinium|aclidinium|revefenacin)\b|\b(?:glycopyrrolate|glycopyrronium)\b[^,;]*(?:inhal|powder|spray|actuat)',
    'laba': r'\b(?:salmeterol|formoterol|vilanterol|olodaterol|arformoterol)\b',
    'leukotriene_modifier': r'\b(?:montelukast|zafirlukast|zileuton)\b',
    'smoking_cessation': r'\b(?:nicotine|varenicline)\b',
    'hormonal_contraceptive': r'\b(?:norethindrone|levonorgestrel|norgestimate|desogestrel|drospirenone|etonogestrel|norelgestromin|dienogest|norgestrel|ethynodiol|segesterone)\b',
    'penicillin': r'\b(?:amoxicillin|ampicillin|penicillin|dicloxacillin|piperacillin)\b',
    'cephalosporin': r'\b(?:cephalexin|cefadroxil|cefuroxime|cefdinir|cefpodoxime|cefprozil|ceftriaxone|cefazolin|cefixime|cefepime)\b',
    'tetracycline_antibiotic': r'\b(?:doxycycline|minocycline|tetracycline)\b',
    'macrolide': r'\b(?:azithromycin|clarithromycin|erythromycin)\b',
    'fluoroquinolone': r'\b(?:ciprofloxacin|levofloxacin|moxifloxacin|ofloxacin)\b',
    'sulfonamide_antibiotic': r'\bsulfamethoxazole\b',
    'nitrofuran': r'\bnitrofurantoin\b',
    'lincosamide': r'\bclindamycin\b',
    'nitroimidazole': r'\b(?:metronidazole|tinidazole)\b',
    'antiviral': r'\b(?:valacyclovir|acyclovir|famciclovir|oseltamivir|nirmatrelvir|baloxavir)\b',
    'systemic_corticosteroid': r'\b(?:prednisone|prednisolone|methylprednisolone|dexamethasone|hydrocortisone)\b[^,;]*(?:oral|tablet|injection|solution)',
    'csdmard': r'\b(?:methotrexate|hydroxychloroquine|sulfasalazine|leflunomide)\b',
    'cholinesterase_inhibitor': r'\b(?:donepezil|galantamine|rivastigmine)\b',
    'nmda_antagonist': r'\bmemantine\b',
    'antiemetic_5ht3': r'\b(?:ondansetron|granisetron)\b',
    'h2_blocker': r'\b(?:famotidine|cimetidine|nizatidine)\b',
    'triptan': r'\b(?:sumatriptan|rizatriptan|zolmitriptan|eletriptan|naratriptan)\b',
    'opioid_antagonist': r'\b(?:naloxone|naltrexone)\b',
    'vitamin_d': r'\b(?:cholecalciferol|ergocalciferol|calcitriol)\b',
    'thiazolidinedione': r'\b(?:pioglitazone|rosiglitazone)\b',
    'antiretroviral': r'\b(?:tenofovir|emtricitabine|dolutegravir|bictegravir|elvitegravir|raltegravir|cabotegravir|rilpivirine|efavirenz|darunavir|atazanavir|abacavir|lamivudine|zidovudine)\b',
    'tnf_inhibitor': r'\b(?:adalimumab|etanercept|infliximab|certolizumab|golimumab)\b',
    'epinephrine_autoinjector': r'\bepinephrine\b[^,;]*auto-?injector',
    'vitamin_b12': r'\b(?:cyanocobalamin|hydroxocobalamin|vitamin b12)\b',
    'acetaminophen': r'\bacetaminophen\b',
}
GROUPS = {'antihypertensive': ['ace_inhibitor', 'arb', 'arni', 'beta_blocker', 'calcium_channel_blocker', 'thiazide_diuretic', 'mra'],
          'raas_inhibitor': ['ace_inhibitor', 'arb', 'arni'],
          'diabetes_medication': ['metformin', 'insulin', 'sulfonylurea', 'dpp4_inhibitor', 'sglt2_inhibitor', 'glp1_agonist', 'thiazolidinedione'],
          'beers_high_risk': ['first_generation_antihistamine', 'benzodiazepine', 'skeletal_muscle_relaxant', 'sedative_hypnotic', 'antipsychotic'],
          'antidepressant': ['ssri', 'snri', 'ndri', 'sari_antidepressant', 'tetracyclic_antidepressant', 'tricyclic_antidepressant'],
          'adhd_medication': ['adhd_stimulant', 'adhd_nonstimulant'],
          'antibiotic': ['penicillin', 'cephalosporin', 'tetracycline_antibiotic', 'macrolide', 'fluoroquinolone',
                         'sulfonamide_antibiotic', 'nitrofuran', 'lincosamide', 'nitroimidazole'],
          'copd_maintenance': ['lama', 'laba', 'inhaled_corticosteroid'],
          'dementia_medication': ['cholinesterase_inhibitor', 'nmda_antagonist'],
          'bph_medication': ['alpha1_blocker', 'five_alpha_reductase_inhibitor'],
          # Maintenance = long-term medication for a chronic condition (the sense of PBM maintenance-drug lists, typically
          # 90-day fills). Acute and as-needed classes (antibiotics, antivirals, opioids, benzodiazepines, NSAIDs, SABA,
          # triptans, antiemetics, systemic steroid courses, antihistamines, muscle relaxants, sedatives, smoking cessation,
          # acetaminophen, epinephrine auto-injectors) are excluded. Build log: RX-09 had no definition to use.
          'maintenance': ['ace_inhibitor', 'arb', 'arni', 'beta_blocker', 'calcium_channel_blocker', 'thiazide_diuretic', 'mra',
                          'loop_diuretic', 'statin', 'metformin', 'insulin', 'sulfonylurea', 'dpp4_inhibitor', 'sglt2_inhibitor',
                          'glp1_agonist', 'thiazolidinedione', 'anticoagulant', 'antiplatelet', 'levothyroxine', 'nitrate',
                          'ssri', 'snri', 'ndri', 'sari_antidepressant', 'tetracyclic_antidepressant', 'tricyclic_antidepressant',
                          'antipsychotic', 'anticonvulsant', 'lithium', 'gabapentinoid', 'inhaled_corticosteroid', 'lama', 'laba',
                          'leukotriene_modifier', 'proton_pump_inhibitor', 'h2_blocker', 'alpha1_blocker',
                          'five_alpha_reductase_inhibitor', 'urinary_antimuscarinic', 'beta3_agonist', 'bisphosphonate',
                          'adhd_stimulant', 'adhd_nonstimulant', 'cholinesterase_inhibitor', 'nmda_antagonist', 'antiretroviral',
                          'csdmard', 'tnf_inhibitor', 'hormonal_contraceptive']}
_COMPILED = {k: re.compile(v, re.I) for k, v in CLASSES.items()}


def classify(name):
    """Drug classes for a received medication name (possibly several for combination products). Ingredient words decide;
    a name with no classifiable ingredient falls back to its brand (config/agere/drug_brands.json: 'Jardiance' ->
    sglt2_inhibitor), and a brand never adds to or replaces an ingredient classification."""
    if not name: return []
    found = [k for k, rx in _COMPILED.items() if rx.search(str(name))]
    if 'arni' in found and 'arb' in found and not _OTHER_ARB.search(_VALSARTAN.sub('', str(name))):
        found.remove('arb')  # sacubitril/valsartan (Entresto) is an ARNI; its valsartan component does not make it an ARB
    if not found:
        found = brand_classes(name)
    return found


# --- Brand -> generic (RxNorm naming: brand = BN, ingredient = IN) ---------------------------------------------------------
import json as _json
from functools import lru_cache as _lru_cache
from pathlib import Path as _Path

BRANDS_PATH = _Path(__file__).resolve().parents[3] / 'config' / 'agere' / 'drug_brands.json'
_BRANDS = []


def brands():
    """{brand: {'ingredients': [...], 'classes': [...]}} from config/agere/drug_brands.json (empty when absent)."""
    if not _BRANDS:
        table = _json.loads(BRANDS_PATH.read_text())['brands'] if BRANDS_PATH.is_file() else {}
        bad = sorted({c for v in table.values() for c in v['classes'] if c not in CLASSES})
        if bad: raise ValueError(f'drug_brands.json names unknown drug classes: {bad}')
        names = sorted(table, key=lambda b: (-len(b), b))
        rx = re.compile(r'(?<![A-Za-z0-9])(?:' + '|'.join(r'[\s\-]?'.join(re.escape(p) for p in re.split(r'[\s\-]+', b)) for b in names)
                        + r')(?![A-Za-z0-9])', re.I) if names else None
        key = {re.sub(r'[\s\-]+', '', b).lower(): b for b in names}
        _BRANDS.append((table, rx, key))
    return _BRANDS[0][0]


def brand_lookup(name):
    """Brands named in a medication string, longest first: [{'brand', 'ingredients', 'classes'}]. 'JARDIANCE 10 MG' and
    '[Jardiance]' both find Jardiance; 'Toprol XL' and 'Toprol-XL' are the same brand."""
    if not name: return []
    table = brands()
    _, rx, key = _BRANDS[0]
    if rx is None: return []
    out, seen = [], set()
    for m in rx.finditer(str(name)):
        b = key.get(re.sub(r'[\s\-]+', '', m.group(0)).lower())
        if b and b not in seen:
            seen.add(b); out.append({'brand': b, **table[b]})
    return out


def brand_classes(name):
    """Classes of the brands a name mentions, in CLASSES order (empty when it names none)."""
    return list(_brand_classes(str(name))) if name else []


@_lru_cache(maxsize=65536)
def _brand_classes(name):  # medication names repeat across every fill/order/list row
    hit = {c for b in brand_lookup(name) for c in b['classes']}
    return tuple(k for k in CLASSES if k in hit)


_INGREDIENT_SKIP = {'hf_evidence_based_beta_blocker'}  # a sub-class of beta_blocker whose pattern spans dose-form words
_STEM_FORM = re.compile(r'^(alendron|risedron|ibandron|zoledron)\w*$')  # 'alendronic acid' / 'alendronate sodium' -> alendronate


def ingredients(name):
    """Registered ingredient words a medication string names, in order of appearance ('losartan potassium 100 MG' ->
    ['losartan']); brand names are not resolved here (generic_name)."""
    if not name: return []
    spans = []
    for k, rx in _COMPILED.items():
        if k in _INGREDIENT_SKIP: continue
        for m in rx.finditer(str(name)):
            word = re.match(r'[A-Za-z]+', m.group(0))
            if word: spans.append((m.start(), _STEM_FORM.sub(r'\1ate', word.group(0).lower())))
    out = []
    for _, w in sorted(spans):
        if w not in out: out.append(w)
    return out


def generic_name(name):
    """Ingredient names for a brand-named medication ('Entresto' -> ['sacubitril', 'valsartan']), or [] when no brand matches."""
    out = []
    for b in brand_lookup(name):
        out += [i for i in b['ingredients'] if i not in out]
    return out


_VALSARTAN = re.compile(r'\bvalsartan\b', re.I)
_OTHER_ARB = _COMPILED['arb']
