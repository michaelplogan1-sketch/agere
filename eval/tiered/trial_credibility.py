"""Triage trial (2026-11-17): practice PMPM next year, raw vs Bühlmann credibility vs overall mean. Code only.
    PYTHONPATH=. python eval/tiered/trial_credibility.py"""
import collections, statistics, sqlite3
from clinical_core.agere.tiered import store
db=store.connect()
# member months and paid per practice-year (patient-month level, paid by service month)
mm=collections.defaultdict(int); paid=collections.defaultdict(float); pm={}
for pid,m,prac in db.execute("select patient_id, month, attribution_practice_id from member_month where attribution_practice_id is not null"):
    mm[(prac,m[:4])]+=1; pm[(pid,m)]=prac
for pid,sd,n in db.execute("select patient_id, service_date, net_payer_paid_cents from claim where service_date is not null"):
    prac=pm.get((pid,sd[:7]))
    if prac: paid[(prac,sd[:4])]+=(n or 0)/100
def rates(y): return {p:(paid[(p,yy)]/mm[(p,yy)], mm[(p,yy)]) for (p,yy) in mm if yy==y and mm[(p,yy)]>0}
def buhlmann(prev, prev2):
    # EPV and VHM from two years of practice rates weighted by exposure (non-parametric Bühlmann-Straub, 2 periods)
    ps=[p for p in prev if p in prev2]
    tot=sum(prev[p][1]+prev2[p][1] for p in ps); grand=sum(prev[p][0]*prev[p][1]+prev2[p][0]*prev2[p][1] for p in ps)/tot
    epv=sum(prev[p][1]*(prev[p][0]-(prev[p][0]*prev[p][1]+prev2[p][0]*prev2[p][1])/(prev[p][1]+prev2[p][1]))**2+prev2[p][1]*(prev2[p][0]-(prev[p][0]*prev[p][1]+prev2[p][0]*prev2[p][1])/(prev[p][1]+prev2[p][1]))**2 for p in ps)/len(ps)
    mbar={p:(prev[p][0]*prev[p][1]+prev2[p][0]*prev2[p][1])/(prev[p][1]+prev2[p][1]) for p in ps}
    w={p:prev[p][1]+prev2[p][1] for p in ps}
    vhm=max(1e-9,(sum(w[p]*(mbar[p]-grand)**2 for p in ps)-(len(ps)-1)*epv)/(tot-sum(x*x for x in w.values())/tot))
    return epv/vhm, grand
for fit_years,test in ((('2026','2027'),('2027','2028')),(('2027','2028'),('2028','2029'))):
    k,grand=buhlmann(rates(fit_years[1]),rates(fit_years[0]))
    base,nxt=rates(test[0]),rates(test[1])
    ps=[p for p in base if p in nxt]
    small=[p for p in ps if base[p][1]<600]
    def err(pred,sel): 
        w=sum(nxt[p][1] for p in sel); return (sum(nxt[p][1]*(pred(p)-nxt[p][0])**2 for p in sel)/w)**0.5
    raw=lambda p: base[p][0]
    cred=lambda p: (base[p][1]/(base[p][1]+k))*base[p][0]+(k/(base[p][1]+k))*grand
    mean=lambda p: grand
    print(f'fit {fit_years} test {test}: practices {len(ps)} (small <600 mm: {len(small)}), k={k:.0f} member-months')
    for name,f in (('raw',raw),('credibility',cred),('overall mean',mean)):
        print(f'   {name:12} RMSE PMPM all ${err(f,ps):.2f}  small panels ${err(f,small) if small else 0:.2f}')
