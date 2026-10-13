"""Normalization dictionary: map every distinct raw string once to standard terminology (bottled normalization).

Labs -> LOINC-style component/code, property and canonical unit; conditions -> clinical category + ICD-10 chapter + Agere
concept; medications -> RxNorm-style ingredient(s) + drug class; encounter types -> care setting. A frontier model maps
strings in batches with confidence; results are versioned JSON under config/agere/normalization/ and applied at build
time (runtime cost $0). Only strings not already in the dictionary are sent (incremental per release).
Usage: .venv/bin/python tools/normalization/build_dictionary.py [domain ...]
"""
import json, os, re, sqlite3, sys
from pathlib import Path
from concurrent.futures import ThreadPoolExecutor

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
for line in (Path(os.environ.get('AGERE_ENV_FILE', '/Users/mikel/insight-engine/.env')).read_text().splitlines()
             if Path(os.environ.get('AGERE_ENV_FILE', '/Users/mikel/insight-engine/.env')).exists() else []):
    if '=' in line and not line.startswith('#'):
        k, v = line.split('=', 1); os.environ.setdefault(k.strip(), v.strip().strip('"'))
from clinical_core.agere.gateway import BudgetLedger
from clinical_core.agere.clinical import nl2sql, concepts
from clinical_core.agere.clinical.drug_classes import CLASSES

STACK = ROOT / 'artifacts/agere-healthcare-v123/stack'
OUT = ROOT / 'config/agere/normalization'
DOMAINS = {
    'labs': ("SELECT name, unit, count(*), min(value), max(value) FROM measurement GROUP BY name, unit ORDER BY 3 DESC",
             lambda r: {'raw': r[0], 'unit': r[1], 'n': r[2], 'range': [r[3], r[4]]},
             'For each lab/vital (name + unit as received) give: loinc (best-matching LOINC code, or null), component (standard '
             'analyte name), specimen (blood/urine/...), property (mass conc, ratio, ...), canonical_unit, factor_to_canonical '
             '(multiply received value), agere_concept (one of the AGERE IDS or null), category (cbc, bmp/cmp, lipid, renal, '
             'diabetes, thyroid, iron, allergy, vital, ...).'),
    'conditions': ("SELECT label, max(icd10), max(snomed), count(*) FROM condition WHERE label IS NOT NULL GROUP BY label ORDER BY 4 DESC",
                   lambda r: {'raw': r[0], 'icd10': r[1], 'snomed': r[2], 'n': r[3]},
                   'For each condition label (with any ICD-10/SNOMED received) give: is_diagnosis (false for assessment topics, '
                   'history-of, tolerability, social findings), icd10_category (3-character code best matching), chapter (e.g. '
                   'circulatory, endocrine), chronic (true/false), agere_concept (one of the AGERE IDS or null), normalized_label.'),
    'medications': ("SELECT drug_name, count(*) FROM medication GROUP BY drug_name ORDER BY 2 DESC",
                    lambda r: {'raw': r[0], 'n': r[1]},
                    'For each medication string give: ingredients (lowercase generic names), rxnorm_ingredient_cui (or null), '
                    'dose_form, route, drug_classes (from the DRUG CLASSES list; may be empty), therapeutic_category (e.g. '
                    'antihistamine, nsaid, iron supplement, nitrate, bisphosphonate, epinephrine, analgesic, antibiotic, vaccine).'),
    'encounters': ("SELECT type, count(*) FROM encounter WHERE source_table = 'encounters' GROUP BY type ORDER BY 2 DESC",
                   lambda r: {'raw': r[0], 'n': r[1]},
                   'For each encounter type give: setting (emergency, inpatient, observation, urgent_care, primary_care, '
                   'specialty, behavioral_health, telehealth, home_health, procedure, diagnostic, pharmacy_or_med_management, '
                   'preventive, other), in_person (true/false), agere_concept (one of the AGERE IDS or null).'),
}
SYS = """You are a clinical terminology specialist normalising source strings from a healthcare data warehouse to standard
terminologies. Be precise; never guess a code you are not confident in (use null). Do not merge look-alikes (fecal hemoglobin
is not hemoglobin; urine albumin is not UACR; nystatin is not a statin). Reply with a JSON object mapping each input "raw"
string exactly as given to its normalisation object, each including "confidence" (0-1)."""


def main(domains):
    db = sqlite3.connect(f'file:{STACK / "semantic.sqlite3"}?mode=ro', uri=True)
    ids = {'labs': sorted(concepts.MEASUREMENTS), 'conditions': sorted(concepts.CONDITIONS), 'encounters': sorted(concepts.ENCOUNTERS),
           'medications': []}
    total = 0.0
    for domain in domains:
        sql, shape, ask = DOMAINS[domain]
        target = OUT / f'{domain}.json'
        done = json.loads(target.read_text())['entries'] if target.exists() else {}
        items = [shape(r) for r in db.execute(sql) if r[0] is not None]
        todo = [i for i in items if i['raw'] not in done]
        batches = [todo[i:i + 40] for i in range(0, len(todo), 40)]
        def run(batch):
            ledger = BudgetLedger(ROOT / 'artifacts/agere-full/provider-budget.sqlite3')
            m = nl2sql.HostedModel(ledger, STACK / 'provider-receipts', model='claude-opus-5-5')
            extra = f"AGERE IDS: {', '.join(ids[domain])}\n" + (f"DRUG CLASSES: {', '.join(sorted(CLASSES))}\n" if domain == 'medications' else '')
            text, _ = m(SYS, f'{extra}\nEXAMPLES\n{ask}\n\nINPUT\n{json.dumps(batch, default=str)}', max_tokens=12000)
            mm = re.search(r'\{.*\}', text, re.S)
            try: return json.loads(mm.group(0)), m.cost
            except Exception: return {}, m.cost
        with ThreadPoolExecutor(4) as ex:
            for mapping, usd in ex.map(run, batches):
                total += usd or 0
                for raw, v in mapping.items():
                    src = next((i for i in todo if i['raw'] == raw), None)
                    if src is not None: done[raw] = {**v, 'source_count': src['n']}
        missing = [i['raw'] for i in items if i['raw'] not in done]
        target.write_text(json.dumps({'schema': 'agere-normalization/1', 'domain': domain, 'mapped_by': 'claude-opus-5-5',
                                      'review_status': 'unreviewed', 'entries': done}, indent=1, sort_keys=True))
        print(f'{domain}: {len(done)} mapped, {len(missing)} unmapped {missing[:5]}')
    print(f'cost ${total:.2f}')


if __name__ == '__main__':
    main(sys.argv[1:] or list(DOMAINS))
