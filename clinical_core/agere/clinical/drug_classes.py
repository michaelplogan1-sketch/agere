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
GROUPS = {# Guideline-directed medical therapy for heart failure: the four pillars (evidence-based beta-blocker, RAAS inhibitor incl. ARNI, MRA, SGLT2 inhibitor)
          'gdmt': ['hf_evidence_based_beta_blocker', 'ace_inhibitor', 'arb', 'arni', 'mra', 'sglt2_inhibitor'],
          'antihypertensive': ['ace_inhibitor', 'arb', 'arni', 'beta_blocker', 'calcium_channel_blocker', 'thiazide_diuretic', 'mra'],
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


SUBCLASS_PARENTS = {'hf_evidence_based_beta_blocker': 'beta_blocker'}

def group_query_issues(question, calls, final_view=None):
    """Audit named group coverage against literal class filters and native result lineage.

    Read-only broader analyses remain available. A requested registered group
    cannot be satisfied by a strict superset without an exact-membership repair
    contributing to the final result. This is a contract check, not medical judgment.
    """
    import sqlglot
    from sqlglot import exp
    named={k:set(v) for k,v in GROUPS.items() if re.search(r'(?<!\w)'+re.escape(k).replace('_',r'[ _]+')+r's?(?!\w)',str(question),re.I)}
    if not named:return []
    filters={};parents={};positions={};queries={};results={}
    def projection(node):
        while node.parent is not None and not isinstance(node.parent,exp.Select):node=node.parent
        return (node.alias_or_name or '*') if isinstance(node.parent,exp.Select) and node in node.parent.expressions else None
    for index,call in enumerate(calls):
        result=call.get('result') or {}
        if not isinstance(result,dict) or result.get('error'):continue
        view=result.get('result_view')
        if not view:continue
        results[view]=result;positions[view]=index
        assembly=result.get('assembly') or {}
        if assembly:
            parents[view]={assembly['base_result_view']}|{j['result_view']for j in assembly.get('joins',[])}
            filters[view]=[]
        sql=(call.get('input') or {}).get('sql')
        if not isinstance(sql,str):continue
        parents[view]=set(re.findall(r'\bagere_result_\d+\b',sql));positions[view]=index
        try:tree=sqlglot.parse_one(sql,read='sqlite')
        except sqlglot.errors.ParseError:continue
        queries[view]=tree
        values=[]
        for node in tree.find_all(exp.In):
            if isinstance(node.this,exp.Column) and node.this.name.lower()=='drug_class' and node.expressions and all(isinstance(v,exp.Literal) and v.is_string for v in node.expressions):
                values.append(({v.this for v in node.expressions},projection(node)))
        for node in tree.find_all(exp.Or):
            terms=[]
            def alternatives(n):
                if isinstance(n,exp.Paren):return alternatives(n.this)
                if isinstance(n,exp.Or):return alternatives(n.this) and alternatives(n.expression)
                if isinstance(n,exp.EQ):
                    col,lit=n.this,n.expression
                    if isinstance(lit,exp.Column):col,lit=lit,col
                    if isinstance(col,exp.Column) and col.name.lower()=='drug_class' and isinstance(lit,exp.Literal) and lit.is_string:
                        terms.append(lit.this);return True
                return False
            if alternatives(node):values.append((set(terms),projection(node)))
        filters[view]=values
    reached=set()
    def visit(view):
        if view in reached:return
        reached.add(view)
        for parent in parents.get(view,()):visit(parent)
    if final_view:visit(final_view)
    else:reached.update(parents)
    schema={v:dict.fromkeys(r.get('columns') or [],'UNKNOWN') for v,r in results.items()}
    def consumed_columns(members):
        # Trace native output columns, rather than letting an exact filter on
        # an unrelated joined result certify every contributing value.
        from sqlglot.lineage import lineage
        used={};visited=set()
        def trace(view,column,measured=False):
            key=(view,column,measured)
            if key in visited:return
            visited.add(key);used.setdefault(view,set()).add((column,measured))
            if column=='*':
                for name in results.get(view,{}).get('columns') or []:trace(view,name,measured)
                return
            assembly=results.get(view,{}).get('assembly') or {}
            if assembly:
                original=next((k for k,v in assembly.get('column_renames',{}).items()if v==column),column)
                join=next((j for j in assembly.get('joins',[])if j['as']==original),None)
                if join:
                    for name in ([join['items_from']]if join.get('items_from')else join['selected_columns']):trace(join['result_view'],name,measured)
                else:trace(assembly['base_result_view'],original,measured)
                return
            if not parents.get(view):return
            tree=queries.get(view)
            try:
                if tree is None:raise ValueError('Native SQL lineage is unavailable')
                root=lineage(column,tree,schema=schema,dialect='sqlite',trim_selects=False)
                aggregate=measured or any(isinstance(n,exp.AggFunc)for n in root.expression.walk())
                barriers=set()
                where=tree.args.get('where') if isinstance(tree,exp.Select) else None
                tables=[t for t in tree.find_all(exp.Table) if t.find_ancestor(exp.Select) is tree]
                if where is not None:
                    for predicate in where.find_all(exp.In):
                        col=predicate.this
                        if not isinstance(col,exp.Column) or col.name.lower()!='drug_class':continue
                        if not predicate.expressions or not all(isinstance(v,exp.Literal) and v.is_string for v in predicate.expressions):continue
                        if {v.this for v in predicate.expressions}!=members:continue
                        parent=predicate.parent;conjunctive=True
                        while parent is not None and parent is not where:
                            if isinstance(parent,(exp.Or,exp.Not)):conjunctive=False;break
                            parent=parent.parent
                        if not conjunctive:continue
                        for table in tables:
                            if table.name in results and (col.table==table.alias_or_name or not col.table and len(tables)==1):barriers.add(table.name)
                sources=[]
                for node in root.walk():
                    if isinstance(node.expression,exp.Table) and node.expression.name in results:
                        name=sqlglot.parse_one(node.name,into=exp.Column,read='sqlite').name
                        sources.append((node.expression.name,name))
                for parent,name in sources:
                    if parent not in barriers:trace(parent,name,aggregate)
                if not sources:
                    # COUNT(*) and unresolved projections depend on row scope.
                    for parent in parents[view]:trace(parent,'*',True)
            except (sqlglot.errors.SqlglotError,ValueError,RecursionError):
                for parent in parents[view]:trace(parent,'*',True)
        for view in ([final_view]if final_view else reached):
            trace(view,'*')
        return used
    used_cache={}
    issues=[]
    for group,members in named.items():
        bad=[]
        for view,sets in filters.items():
            if view not in reached:continue
            for selected,affected in sets:
                # A CASE expression can widen one pillar without containing
                # the entire registered group in any single IN filter.
                widened={parent for child,parent in SUBCLASS_PARENTS.items()
                         if child in members and child in selected and parent in selected and parent not in members}
                extra=(selected-members) if selected>members else widened
                if extra:bad.append((positions[view],view,extra,affected))
        exact=[v for v,sets in filters.items() if v in reached and any(selected==members for selected,_ in sets)]
        for position,view,extra,affected in bad:
            group_key=tuple(sorted(members))
            if group_key not in used_cache:used_cache[group_key]=consumed_columns(members)
            used=used_cache[group_key]
            columns=used.get(view,set())
            if affected is not None:
                if not any(name in (affected,'*')for name,_ in columns):continue
            elif any(positions[v]>position and any(name!='patient_id' or counted for name,counted in used.get(v,set()))for v in exact):
                if not any(name!='patient_id' or counted for name,counted in columns):continue
            issues.append('Registered medication group '+group+' has exact members '+', '.join(sorted(members))+
                '; contributing query '+view+' widened that set with '+', '.join(sorted(extra))+
                '. Recompute the requested group using its exact members and bind the final table to that repaired result. '
                'Broader therapies may be shown separately; do not count them as satisfying the registered group.')
    return list(dict.fromkeys(issues))
