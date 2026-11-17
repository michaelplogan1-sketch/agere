"""Triage trial (2026-11-17): PDC >= 80% by class for 2029, built course code vs a plain SQL PDC. Code only.
    PYTHONPATH=. python eval/tiered/trial_pdc.py"""
import time, collections, sqlite3
from datetime import date
from clinical_core.agere.tiered import store
from clinical_core.agere.clinical import therapy_courses as tc
db=sqlite3.connect(store.path().resolve().as_uri()+'?mode=ro',uri=True)
t=time.time()
fs=db.execute("select min(fill_date) from fill").fetchone()[0]
rows=tc.courses(db,[2029],'2030-05-27',fs)
print('courses rows',len(rows),round(time.time()-t,1),'s')
classes=['statin','ace_inhibitor','arb','metformin','sulfonylurea','dpp4_inhibitor','sglt2_inhibitor']
A=collections.defaultdict(list)
for r in rows:
    if r['drug_class'] in classes and r['pdc'] is not None and r['fills']>=2: A[r['drug_class']].append(r['pdc'])
q="""SELECT drug_class, patient_id, COUNT(*) n, MIN(fill_date) f, SUM(days_supply) s FROM fill WHERE substr(fill_date,1,4)='2029' AND drug_class IN (%s) GROUP BY 1,2 HAVING n>=2"""%(','.join('?'*len(classes)))
B=collections.defaultdict(list)
for cls,pid,n,f,s in db.execute(q,classes):
    span=(date(2029,12,31)-date.fromisoformat(f[:10])).days+1
    B[cls].append(min(1.0,(s or 0)/span))
for c in classes:
    a,b=A.get(c,[]),B.get(c,[])
    pa=sum(x>=0.8 for x in a)/max(1,len(a)); pb=sum(x>=0.8 for x in b)/max(1,len(b))
    print(f'{c:16} courses: n={len(a)} adherent {pa:.3f} | naive SQL: n={len(b)} adherent {pb:.3f} | diff {100*(pb-pa):+.1f} pts')
