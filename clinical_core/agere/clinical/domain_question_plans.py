"""Exact source-review commands; qualifiers are never stripped to fit a recipe."""
import copy
from .cohorts import empty

VERSION='domain-question-plans/1'
def examples():
    result=[]
    for domain in ('respiratory','behavioral'):
        for work in (False,True):
            title=('Prepare '+domain+' source-review work') if work else ('Review '+domain+' sources across the population')
            nodes=[{'id':'population','operation':'cohort','inputs':[],'parameters':{'specification':empty()},'purpose':'Complete received synthetic population'},
                {'id':'domain','operation':'domain_review','inputs':['population'],'parameters':{'domain':domain,'filter':'source_review_candidates'},'purpose':'Nominate received accounts for review; others retain unknown need'}]
            if work:nodes.append({'id':'work','operation':'worklist','inputs':['domain'],'parameters':{'program':domain+'_source_review'},'purpose':'Prepare internal source-bound review work'})
            result.append({'question':title,'graph':{'interpretation':title+'. Received source selection does not establish current clinical need.',
                'coverage':'partial','unmet_requirements':['Current clinical status, service indication, patient goals, consent and exact benefits remain unverified.'],
                'nodes':nodes}})
    return result

def local_question(question):
    key=' '.join(question.strip().split()).casefold().rstrip('?.')
    for row in examples():
        if key==' '.join(row['question'].split()).casefold():return copy.deepcopy(row['graph'])
    return None
