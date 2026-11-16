"""Inventory public received records only; no generator/evaluation inputs."""
import argparse,hashlib,json,sqlite3
from collections import Counter,defaultdict
from pathlib import Path
from clinical_core.contracts import digest
from clinical_core.agere import warehouse_retraction


def main():
 p=argparse.ArgumentParser();p.add_argument('--release',type=Path,required=True);p.add_argument('--previous',type=Path,required=True);p.add_argument('--output',type=Path,required=True);p.add_argument('--restatement',action='store_true');p.add_argument('--expansion',action='store_true',help='release adds patients; every prior patient must remain');p.add_argument('--retraction',action='store_true',help='prior rows may be missing when each is explained by the declared post-death rule (warehouse_retraction)');p.add_argument('--rebaseline',action='store_true',help='fresh baseline load: the prior-release comparison is recorded (informational) and does not gate; exclusive with the other modes');a=p.parse_args()
 if a.rebaseline and (a.restatement or a.expansion or a.retraction):p.error('--rebaseline cannot be combined with --restatement/--expansion/--retraction')
 a.output.mkdir(exist_ok=False)
 m=json.loads((a.release/'manifest.json').read_text());src=a.release/'warehouse.sqlite';previous=a.previous/'warehouse.sqlite'
 def sha(p):
  with p.open('rb') as f:return hashlib.file_digest(f,'sha256').hexdigest()
 assert sha(src)==m['files']['warehouse.sqlite']
 db=sqlite3.connect(src.resolve().as_uri()+'?mode=ro',uri=True);db.row_factory=sqlite3.Row
 old=sqlite3.connect(previous.resolve().as_uri()+'?mode=ro',uri=True)
 try:
  roster=[r[0] for r in db.execute('SELECT patient_id FROM patients ORDER BY patient_id')];prior_roster=[r[0] for r in old.execute('SELECT patient_id FROM patients ORDER BY patient_id')]
  assert a.rebaseline or roster==prior_roster or (a.expansion and set(prior_roster)<=set(roster)),'roster changed (use --expansion for a release that only adds patients)'
  oldcounts=dict(old.execute('SELECT source_table,count(*) FROM source_records GROUP BY source_table'));counts=dict(db.execute('SELECT source_table,count(*) FROM source_records GROUP BY source_table'));new=set(counts)-set(oldcounts);fields=defaultdict(Counter);types=defaultdict(lambda:defaultdict(Counter));examples={};strings=defaultdict(dict)
  for table in sorted(new):
   for row in db.execute('SELECT * FROM source_records WHERE source_table=? ORDER BY record_id',(table,)):
    facts=json.loads(row['facts']);fields[table].update(facts.keys());examples.setdefault(table,dict(row,facts=facts))
    for key,value in facts.items():
     types[table][key][type(value).__name__]+=1
     if isinstance(value,str) and len(value)>80:strings[table].setdefault(key,value)
  prior_cols={r[1] for r in old.execute('PRAGMA table_info(source_records)')};cols=[r[1] for r in db.execute('PRAGMA table_info(source_records)') if r[1] in prior_cols];added_cols=sorted({r[1] for r in db.execute('PRAGMA table_info(source_records)')}-prior_cols)
  db.execute('ATTACH DATABASE ? AS previous',(previous.resolve().as_uri()+'?mode=ro',))
  changed=db.execute('SELECT count(*) FROM previous.source_records p LEFT JOIN main.source_records n ON p.record_id=n.record_id WHERE n.record_id IS NULL OR NOT ('+' AND '.join('p."'+c+'" IS n."'+c+'"' for c in cols)+')').fetchone()[0]
  missing=db.execute('SELECT count(*) FROM previous.source_records p LEFT JOIN main.source_records n ON p.record_id=n.record_id WHERE n.record_id IS NULL').fetchone()[0]
  retraction=warehouse_retraction.classify(warehouse_retraction.missing_rows(db),warehouse_retraction.death_dates(db),set(roster)) if a.retraction or a.rebaseline else {}
  assert a.rebaseline or changed==missing or a.restatement,'prior rows changed (use --restatement for a restated release)'
  assert a.rebaseline or missing==0 or (a.retraction and retraction['prior_rows_unexplained']==0),('prior rows missing (use --retraction for a release that retracts rows by a declared rule)' if not a.retraction else 'missing prior rows not explained by '+warehouse_retraction.RULE+': '+json.dumps({k:retraction[k] for k in ('prior_rows_unexplained','unexplained_by_reason','unexplained_by_table')}))
  result={'synthetic':True,'release':str(a.release.resolve()),'previous':str(a.previous.resolve()),'as_of':m['as_of'],'source_sha256':m['files']['warehouse.sqlite'],'manifest_sha256':sha(a.release/'manifest.json'),'patients':len(roster),'roster_digest':digest(roster),'records':sum(counts.values()),'previous_records':sum(oldcounts.values()),'prior_rows_changed_or_missing':changed,'prior_rows_missing':missing,'restatement':bool(a.restatement),'expansion':bool(a.expansion),'retraction':bool(a.retraction),'rebaseline':bool(a.rebaseline),'migration_gated':not a.rebaseline,'prior_patients_missing':len(set(prior_roster)-set(roster)),**retraction,'new_source_record_columns':added_cols,'prior_roster_digest':digest(prior_roster),'new_patients':len(set(roster)-set(prior_roster)),'source_categories':len(counts),'new_source_categories':sorted(new),'source_counts':counts,'new_source_fields':fields,'new_source_types':types,'public_tables_read':['patients','source_records'],'private_files_read':False}
  for name,obj in [('inventory.json',result),('new-source-examples.json',examples),('new-long-text-fields.json',strings)]:
   (a.output/name).write_text(json.dumps(obj,indent=2,allow_nan=False)+'\n')
  print(json.dumps({k:result[k] for k in ('as_of','patients','records','previous_records','prior_rows_changed_or_missing','prior_rows_missing','source_categories','new_source_categories')+(('prior_rows_retracted','retracted_patients') if a.retraction else ())},indent=2))
 finally:db.close();old.close()

if __name__=='__main__':main()
