"""Snapshot public RxNorm exact-name identities; sends no patient records."""
import argparse,json,hashlib,time,urllib.parse,urllib.request
from pathlib import Path
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime,timezone
from clinical_core.contracts import digest

TERMS=['metoprolol','metoprolol succinate','metoprolol tartrate','bisoprolol','carvedilol',
 'losartan','valsartan','lisinopril','sacubitril / valsartan','spironolactone','eplerenone',
 'dapagliflozin','empagliflozin','metformin','insulin glargine']

def main():
 ap=argparse.ArgumentParser();ap.add_argument('--output',type=Path,default=Path('artifacts/agere-medication-terminology-v1'));a=ap.parse_args()
 if (a.output/'catalog.json').exists():raise ValueError('Preserve the pinned catalog; choose a new version')
 a.output.mkdir(parents=True,exist_ok=True)
 def fetch(url):
  with urllib.request.urlopen(url,timeout=30) as r:raw=r.read(1000000)
  data=json.loads(raw);receipt={'url':url,'body_sha256':hashlib.sha256(raw).hexdigest(),'response':data}
  (a.output/(digest(url)+'.json')).write_text(json.dumps(receipt,indent=2))
  return data,receipt
 version,_=fetch('https://rxnav.nlm.nih.gov/REST/version.json')
 def term(name):
  query='https://rxnav.nlm.nih.gov/REST/rxcui.json?'+urllib.parse.urlencode({'name':name,'search':0,'allsrc':0})
  data,ref=fetch(query);ids=data.get('idGroup',{}).get('rxnormId',[]);concepts=[]
  for ident in ids:
   if not ident.isdigit():raise ValueError('Invalid public RxCUI')
   props,source=fetch('https://rxnav.nlm.nih.gov/REST/rxcui/'+ident+'/properties.json')
   value=props.get('properties')
   if not value or value.get('rxcui')!=ident:raise ValueError('RxNorm property identity mismatch')
   concepts.append({'properties':value,'source':{'url':source['url'],'body_sha256':source['body_sha256']}})
  return {'query':name,'mode':'exact_only','status':'resolved' if len(concepts)==1 else 'ambiguous' if concepts else 'unmatched',
          'concepts':concepts,'lookup':{'url':ref['url'],'body_sha256':ref['body_sha256']}}
 with ThreadPoolExecutor(max_workers=3) as pool:entries=list(pool.map(term,TERMS))
 after,_=fetch('https://rxnav.nlm.nih.gov/REST/version.json')
 if after!=version:raise ValueError('RxNorm version changed during snapshot')
 catalog={'schema':'rxnorm-exact-subset/1','version':version,'retrieved_at':datetime.now(timezone.utc).isoformat(),
          'entries':entries,'scope':'Pinned subset of exact public medication-name concepts, not the full RxNorm release. Lookup transfers standard drug names only.',
          'clinical_decision_authority':False}
 (a.output/'catalog.json').write_text(json.dumps(catalog,indent=2))
 root=Path('artifacts/agere-population-stack-v3');link={'path':str(a.output.resolve()),'catalog_hash':digest(catalog)}
 (root/'medication-terminology-link.json').write_text(json.dumps(link,indent=2))
 print(json.dumps({'version':version,'entries':len(entries),'statuses':{s:sum(e['status']==s for e in entries) for s in ('resolved','ambiguous','unmatched')}},indent=2))

if __name__=='__main__':main()
