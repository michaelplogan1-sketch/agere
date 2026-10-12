"""Typed source graph with time, identity and semantic links. No hidden ontology access."""
from .operators import ALIASES

def patient_graph(patient,records):
    pid=patient['patient_id'];nodes=[{'id':pid,'type':'patient','synthetic':True}];edges=[];concepts=set()
    for r in records:
        rid=r['record_id'];nodes.append({'id':rid,'type':r['source_table'],'event_date':r['event_date'],
                                       'available_date':r['available_date'],'source':r['source_system']})
        edges.append({'from':rid,'to':pid,'type':'observed_for'})
        concept=None
        if r.get('drug_class'):concept='medication_class:'+r['drug_class']
        if r['source_table']=='labs':
            concept=next(('lab:'+k for k,v in ALIASES.items() if str(r.get('test','')).lower() in v),None)
        if concept:concepts.add(concept);edges.append({'from':rid,'to':concept,'type':'records_concept'})
        if r.get('supersedes_record_id'):edges.append({'from':rid,'to':r['supersedes_record_id'],'type':'supersedes'})
    nodes.extend({'id':c,'type':'concept'} for c in sorted(concepts))
    return {'nodes':nodes,'edges':edges,'ontology_scope':'Local observed-source ontology; employer ontology not available',
            'missing_edge_meaning':'Unknown, not evidence that no care occurred'}
