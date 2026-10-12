"""Additive, source-bound option bundles without changing prior saved evidence."""
from pathlib import Path
import hashlib,json,math
from ...contracts import digest
from . import option_evidence_registry as O
VERSION='supplemental-option-bundles/1'
CATALOG=Path(__file__).with_name('functional_option_bundle.json')

def identity():return digest([VERSION,hashlib.sha256(Path(__file__).read_bytes()).hexdigest()])

def bundles():
 data=json.loads(CATALOG.read_text())
 if data['schema']!=VERSION:raise ValueError('Versioned supplemental bundle required')
 out=[]
 for raw in data['bundles']:
  ref=raw['reference'];e=raw['evidence']
  if not raw['key'] or not raw['version'] or ref['kind']!='guideline_recommendation':raise ValueError('Typed versioned reference required')
  if any(x['review_status']!='machine_curated_unreviewed' for x in (ref,e)):raise ValueError('Unreviewed source status required')
  if e['schema']!='care-option-evidence/1' or ref['citation']['doi']!=e['reference_doi']:raise ValueError('Exact reference/evidence DOI required')
  q=[x['key'] for x in ref['questions']]
  if len(q)!=len(set(q)) or not {'population','assessment','preference','access','coverage','currency'}<=set(q):raise ValueError('Explicit distinct applicability questions required')
  alts=e['alternatives'];keys={a['key'] for a in alts}
  if len(alts)<2 or len(keys)!=len(alts):raise ValueError('Distinct alternatives required')
  for alt in alts:
   if not alt['compatible_modalities'] or not set(alt['compatible_modalities'])<={'virtual','hybrid','in_person'} or not alt['delivery_questions']:raise ValueError('Explicit delivery scope and questions required')
  for estimate in e['estimates']:
   values=[estimate['point'],*estimate['interval'],estimate['null_value']]
   if len(estimate['interval'])!=2 or any(type(v) not in (int,float) or not math.isfinite(v) for v in values):raise ValueError('Finite source estimates required')
   if not estimate['interval'][0]<=estimate['point']<=estimate['interval'][1] or not 0<estimate['interval_level']<1:raise ValueError('Ordered source interval required')
   if estimate['alternative_key'] not in keys or not all(estimate[k] for k in ('comparator','source_url','locator','evidence_family')):raise ValueError('Exact estimate alternative, comparator and source required')
   if estimate['metric'] not in ('incidence_rate_ratio','risk_ratio') or estimate['null_value']!=1 or min(values)<=0:raise ValueError('Positive typed ratio with null one required')
   if estimate.get('head_to_head_between_listed_alternatives') is not False:raise ValueError('Separate controlled estimates cannot establish a head-to-head comparison')
  if not e['estimates'] and not e.get('quantitative_evidence_status'):raise ValueError('Explain absent estimates')
  marker={'key':raw['key'],'version':raw['version'],'bundle_hash':digest(raw),'implementation':identity()}
  reference={**ref,'registry_entry':marker};reference['id']=digest(reference)
  out.append({'reference':reference,'evidence':{**e,'registry_entry':marker}})
 return out

def install():
 O.install()
 if getattr(O.bundles,'supplemental_bundles',False):return
 original=O.bundles
 def combined():
  rows=original()+bundles();keys=[r['reference']['registry_entry']['key'] for r in rows];dois=[r['reference']['citation']['doi'] for r in rows]
  if len(keys)!=len(set(keys)) or len(dois)!=len(set(dois)):raise ValueError('Distinct supplemental keys and reference DOIs required')
  return rows
 combined.supplemental_bundles=True;O.bundles=combined
