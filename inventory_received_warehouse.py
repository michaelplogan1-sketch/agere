"""Inventory public received records only; no generator/evaluation inputs."""
import argparse,hashlib,json,sqlite3
from collections import Counter,defaultdict
from pathlib import Path
from clinical_core.contracts import digest


def main():
 p=argparse.ArgumentParser();p.add_argument('--release',type=Path,required=True);p.add_argument('--previous',type=Path,required=True);p.add_argument('--output',type=Path,required=True);p.add_argument('--restatement',action='store_true');p.add_argument('--expansion',action='store_true',help='release adds patients; every prior patient must remain');a=p.parse_args();a.output.mkdir(exist_ok=False)
 m=json.loads((a.release/'manifest.json').read_text());src=a.release/'warehouse.sqlite';previous=a.previous/'warehouse.sqlite'
 def sha(p):
  with p.open('rb') as f:return hashlib.file_digest(f,'sha256').hexdigest()
 assert sha(src)==m['files']['warehouse.sqlite']
 db=sqlite3.connect(src.resolve().as_uri()+'?mode=ro',uri=True);db.row_factory=sqlite3.Row
 old=sqlite3.connect(previous.resolve().as_uri()+'?mode=ro',uri=True)
 try:
  roster=[r[0] for r in db.execute('SELECT patient_id FROM patients ORDER BY patient_id')];prior_roster=[r[0] for r in old.execute('SELECT patient_id FROM patients ORDER BY patient_id')]
  assert roster==prior_roster or (a.expansion and set(prior_roster)<=set(roster)),'roster changed (use --expansion for a release that only adds patients)'
  oldcounts=dict(old.execute('SELECT source_table,count(*) FROM source_records GROUP BY source_table'));counts=dict(db.execute('SELECT source_table,count(*) FROM source_records GROUP BY source_table'));new=set(counts)-set(oldcounts);fields=defaultdict(Counter);types=defaultdict(lambda:defaultdict(Counter));examples={};strings=defaultdict(dict)
  for table in sorted(new):
   for row in db.execute('SELECT * FROM source_records WHERE source_table=? ORDER BY record_id',(table,)):
    facts=json.loads(row['facts']);fields[table].update(facts.keys());examples.setdefault(table,dict(row,facts=facts))
    for key,value in facts.items():
     types[table][key][type(value).__name__]+=1
     if isinstance(value,str) and len(value)>80:strings[table].setdefault(key,value)
  cols=[r[1] for r in db.execute('PRAGMA table_info(source_records)')]
  db.execute('ATTACH DATABASE ? AS previous',(previous.resolve().as_uri()+'?mode=ro',))
  changed=db.execute('SELECT count(*) FROM previous.source_records p LEFT JOIN main.source_records n ON p.record_id=n.record_id WHERE n.record_id IS NULL OR NOT ('+' AND '.join('p."'+c+'" IS n."'+c+'"' for c in cols)+')').fetchone()[0]
  missing=db.execute('SELECT count(*) FROM previous.source_records p LEFT JOIN main.source_records n ON p.record_id=n.record_id WHERE n.record_id IS NULL').fetchone()[0]
  assert changed==0 or (a.restatement and missing==0),'prior rows changed or missing (use --restatement for a restated release; rows may change, none may disappear)'
  result={'synthetic':True,'release':str(a.release.resolve()),'previous':str(a.previous.resolve()),'as_of':m['as_of'],'source_sha256':m['files']['warehouse.sqlite'],'manifest_sha256':sha(a.release/'manifest.json'),'patients':len(roster),'roster_digest':digest(roster),'records':sum(counts.values()),'previous_records':sum(oldcounts.values()),'prior_rows_changed_or_missing':changed,'prior_rows_missing':missing,'restatement':bool(a.restatement),'expansion':bool(a.expansion),'prior_roster_digest':digest(prior_roster),'new_patients':len(set(roster)-set(prior_roster)),'source_categories':len(counts),'new_source_categories':sorted(new),'source_counts':counts,'new_source_fields':fields,'new_source_types':types,'public_tables_read':['patients','source_records'],'private_files_read':False}
  for name,obj in [('inventory.json',result),('new-source-examples.json',examples),('new-long-text-fields.json',strings)]:
   (a.output/name).write_text(json.dumps(obj,indent=2,allow_nan=False)+'\n')
  print(json.dumps({k:result[k] for k in ('as_of','patients','records','previous_records','prior_rows_changed_or_missing','source_categories','new_source_categories')},indent=2))
 finally:db.close();old.close()

if __name__=='__main__':main()
