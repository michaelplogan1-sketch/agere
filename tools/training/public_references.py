"""Explicit public RxTerms snapshots for terminology tasks; never patient queries.

NLM API: https://clinicaltables.nlm.nih.gov/apidoc/rxterms/v3/doc.html
Usage: https://clinicaltables.nlm.nih.gov/faq.html
Strength/form terminology is not a treatment recommendation or target dose.
Nothing here scrapes a clinician AI service or treats its output as training truth.
"""
import argparse
import datetime
import hashlib
import json
from pathlib import Path
import re
import urllib.parse
import urllib.request
from clinical_core.agere.clinical.component_learning import checked_path

ENDPOINT='https://clinicaltables.nlm.nih.gov/api/rxterms/v3/search'


def fetch_json(url):
    request=urllib.request.Request(url,headers={'User-Agent':'Agere-public-terminology-snapshot/1','Accept':'application/json'})
    with urllib.request.urlopen(request,timeout=60) as response:return response.read()


def snapshot(terms,output,*,fetch=fetch_json):
    if not isinstance(terms,list) or not terms or any(not isinstance(t,str) or not re.fullmatch(r'[A-Za-z][A-Za-z /+()\-]*',t) for t in terms):raise ValueError('Supply explicit public drug-name search terms, never patient information')
    output=checked_path(output);output.mkdir(parents=True,exist_ok=True);records={};receipts=[]
    if (output/'manifest.json').exists():raise ValueError('Choose a new public snapshot directory; preserve prior versions')
    for term in terms:
        offset=0
        while True:
            url=ENDPOINT+'?'+urllib.parse.urlencode({'terms':term,'ef':'STRENGTHS_AND_FORMS,RXCUIS','count':500,'offset':offset})
            raw=fetch(url);payload=json.loads(raw);total,names,extra,_=payload[:4]
            sha=hashlib.sha256(raw).hexdigest();path=output/('response-'+sha+'.json')
            if path.exists() and path.read_bytes()!=raw:raise ValueError('Immutable public response differs')
            path.write_bytes(raw);receipts.append({'url':url,'sha256':sha,'file':path.name,'reported_total':total,'offset':offset,'returned':len(names)})
            for i,name in enumerate(names):
                forms=(extra.get('STRENGTHS_AND_FORMS') or [None]*len(names))[i] or [None]
                ids=(extra.get('RXCUIS') or [None]*len(names))[i] or []
                if ids and len(ids)!=len(forms):raise ValueError('Public terminology code/form alignment changed')
                for j,form in enumerate(forms):
                    code=ids[j] if ids else None;key=(name,form,code)
                    records[key]={'display_name':name,'strength_form':form,'rxcui':code,'source_sha256':sha}
            offset+=len(names)
            if offset>=total:break
            if not names or offset+500>7500:raise ValueError('Public search cannot be marked complete under the API pagination contract')
    rows=sorted(records.values(),key=lambda r:(r['display_name'],str(r['strength_form']),str(r['rxcui'])))
    data=output/'rxterms.json';data.write_text(json.dumps(rows,indent=2)+'\n')
    manifest={'schema':'agere-public-reference/1','source':'NLM RxTerms','retrieved_at':datetime.datetime.now(datetime.timezone.utc).isoformat(),
        'api_documentation':'https://clinicaltables.nlm.nih.gov/apidoc/rxterms/v3/doc.html','usage_terms':'https://clinicaltables.nlm.nih.gov/faq.html',
        'scope':{'public_search_terms':terms,'complete_for_reported_search_results':True,'entire_vocabulary':False},
        'records':len(rows),'data':data.name,'sha256':hashlib.sha256(data.read_bytes()).hexdigest(),'requests':receipts,
        'meaning':'Names, routes and available strength/forms only; not max dose, clinical eligibility, current patient use or guideline truth',
        'training_use':'Authored terminology/analytics task contracts only; no patient data or third-party clinician AI output'}
    target=output/'manifest.json';target.write_text(json.dumps(manifest,indent=2)+'\n');return manifest


def main():
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('--terms',nargs='+',required=True);p.add_argument('--output',required=True,type=Path);a=p.parse_args()
    print(json.dumps(snapshot(a.terms,a.output),indent=2))


if __name__=='__main__':main()
