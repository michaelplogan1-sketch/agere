"""Integrity checks for research artifacts, not clinical/model validation."""
import hashlib
import json
from pathlib import Path
from urllib.parse import urlparse

ROOT=Path(__file__).resolve().parent
PROJECT=ROOT.parent.parent

def read(name):return json.loads((ROOT/name).read_text())
def unique(rows,key='id'):
    values=[r[key] for r in rows]
    assert len(values)==len(set(values)),f'Duplicate {key}'

def validate():
    catalog=read('evidence_catalog.json');unique(catalog)
    sources={x['id'] for x in catalog}
    for s in catalog:
        assert urlparse(s['url']).scheme=='https'
        assert s['reviewed_scope'] and s['limitations'] and s['original_synthesis']
        if s['study_design_or_authority']=='Not substantively reviewed':
            assert s['review_status']=='discovered_not_substantively_reviewed'
    coverage=read('coverage_summary.json')
    assert coverage['source_targets']==len(catalog)==89
    assert coverage['scoped_reviews']==sum(s['review_status']=='scoped_review' for s in catalog)==84
    raw=read('cms_model_directory_raw.json');inv=read('cms_inventory.json');unique(inv)
    assert len(inv)==len(raw)==coverage['cms_directory_entries']==105
    assert sum(r['review_scope']=='Overview reviewed' for r in inv)==40
    assert all(not r['rules_compiled'] for r in inv)
    assert read('directory_provenance.json')['matches_default_endpoint']
    findings=read('mining_findings.json')
    for field,count in findings['blank_fields'].items():
        assert count==sum(not str(r.get(field,'')).strip() for r in raw)
    baseline=json.loads((PROJECT/'config/questions.json').read_text())
    supplement=read('question_supplement.json')
    combined=read('combined_question_snapshot.json')
    old=[q for g in baseline['groups'] for q in g['questions']]
    new=[q for g in supplement['groups'] for q in g['questions']]
    allq=[q for g in combined['groups'] for q in g['questions']];unique(allq)
    assert len(old)==140 and len(new)==96 and len(allq)==236
    assert combined['groups'][:len(baseline['groups'])]==baseline['groups'],'Existing execution states changed'
    assert combined['provenance']['baseline_sha256']==hashlib.sha256((PROJECT/'config/questions.json').read_bytes()).hexdigest()
    existing_ids={q['id'] for q in old};new_ids={q['id'] for q in new}
    cases=read('synthetic_case_specs.json');unique(cases);case_ids={c['id'] for c in cases}
    assert len(cases)==32
    for q in new:
        assert q['execution']=={'status':'proposed_not_implemented','handler':None}
        assert set(q['source_refs'])<=sources
        assert set(q['related_existing_question_ids'])<=existing_ids
        assert set(q['synthetic_case_ids'])<=case_ids
        assert q['required_data'] and q['failure_to_catch'] and q['review_owner']
        assert q['outcome_measure'] and q['process_measure'] and q['balancing_measure']
    for c in cases:
        assert c['synthetic'] is True and c['expert_review']=='Pending'
        assert set(c['source_refs'])<=sources
        assert set(c['proposed_question_ids'])<=new_ids
        assert c['scenario'] and c['expected_behavior']
    result=dict(status='passed',scope='Artifact integrity only; no clinical capability or model accuracy tested',
        source_targets=len(catalog),scoped_reviews=coverage['scoped_reviews'],cms_entries=len(inv),
        original_questions=len(old),research_formulations=len(new),combined_rows=len(allq),
        synthetic_scenario_specs=len(cases),live_registry_matches_snapshot=True,
        checks=['Unique identifiers','Valid source, question and case references','Explicit review scopes',
            'Coverage counts reproduced','CMS blank-field counts reproduced','Research rows unimplemented',
            'Synthetic case labels explicit','Original registry statuses and hash preserved'])
    (ROOT/'validation_result.json').write_text(json.dumps(result,indent=2)+'\n')
    print(json.dumps(result,indent=2))

if __name__=='__main__':validate()
