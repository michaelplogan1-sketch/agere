"""Build an interactive lab from exact kernel outputs, not browser-side inference."""
import itertools
import json
from pathlib import Path

from clinical_core.decision_kernel import DecisionProgram
from clinical_core.reasoning import workflow_program, analyze_case
from clinical_core.dependency_cache import DependencyCache
from clinical_core.information_value import expected_information_value

ROOT = Path(__file__).resolve().parent


def main():
    out = ROOT / "artifacts/agere-reasoning"
    out.mkdir(parents=True, exist_ok=True)
    spec = workflow_program()
    program = DecisionProgram(spec)
    names = list(spec["facts"])
    lookup = {}
    for states in itertools.product(("unknown", "false", "true"), repeat=len(names)):
        observations = {name: {"value": value == "true", "status": "source_record", "available_date": "2026-09-01",
                             "valid_from": "2026-09-01", "valid_to": "2026-09-30", "support_ids": ["fictional-assumption:" + name]}
                        for name, value in zip(names, states) if value != "unknown"}
        lookup["|".join(states)] = program.analyze(observations, "2026-09-30")
    payload = {"program": spec, "names": names, "lookup": lookup}
    (out / "lab-states.json").write_text(json.dumps(payload, indent=2))
    data = json.dumps(payload).replace("<", "\\u003c").replace("&", "\\u0026")
    (out / "lab.html").write_text(PAGE.replace("__DATA__", data))
    # Illustrate conditional acquisition value using fictional, fully declared
    # probabilities and utilities, not healthcare estimates or provider prices.
    scenario = {"prior": {"state_a": .5, "state_b": .5},
                "utilities": {"path_a": {"state_a": 1, "state_b": 0}, "path_b": {"state_a": 0, "state_b": 1}},
                "utility_scale": "fictional correct-path units", "assumption_source": "Engineering illustration, not patient data",
                "channels": {
                    "new_reliable_observation": {"available": True, "cost": .1, "basis": "Assumed reliability and effort", "likelihoods": {"state_a": {"a": .9, "b": .1}, "state_b": {"a": .1, "b": .9}}},
                    "no_new_information": {"available": True, "cost": .05, "basis": "Assumed uninformative repeated work", "likelihoods": {"state_a": {"a": .5, "b": .5}, "state_b": {"a": .5, "b": .5}}}}}
    (out / "information-assumptions.json").write_text(json.dumps(scenario, indent=2))
    (out / "information-value.json").write_text(json.dumps(expected_information_value(**scenario), indent=2))
    # A deterministic audit of incremental recomputation with two independent
    # fictional cases. This is an engineering demonstration, not a new world.
    cache = DependencyCache(":memory:")
    try:
        first = analyze_case(cache, scope="fixture:one", program=spec, observations={}, as_of="2026-09-30")
        analyze_case(cache, scope="fixture:two", program=spec, observations={}, as_of="2026-09-30")
        affected = cache.affected_heads(first["fact_node_id"])
        known = {"eligible": {"value": False, "status": "source_record", "available_date": "2026-09-01", "valid_from": "2026-09-01", "valid_to": "2026-09-30", "support_ids": ["fictional-assumption:eligible"]}}
        changed = analyze_case(cache, scope="fixture:one", program=spec, observations=known, as_of="2026-09-30")
        analyze_case(cache, scope="fixture:two", program=spec, observations={}, as_of="2026-09-30")
        audit = {"synthetic": True, "kind": "engineering_fixture", "initial_operators": 2,
                 "total_operators_after_one_fact_change": cache.misses, "reused_operators": cache.hits,
                 "affected_heads_before_change": affected, "before": first["analysis"], "after": changed["analysis"]}
        (out / "dependency-audit.json").write_text(json.dumps(audit, indent=2))
    finally:
        cache.close()
    print(json.dumps({"lab": str(out / "lab.html"), "precomputed_states": len(lookup), "paid_calls": 0}))


PAGE = r'''<!doctype html><html lang="en"><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>Agere · What would change the answer?</title>
<style>*{box-sizing:border-box}body{margin:0;background:#f3f5ed;color:#193631;font:15px/1.55 system-ui,sans-serif}header{padding:23px 5vw;border-bottom:1px solid #cedbcd;display:flex;justify-content:space-between;gap:15px}.brand{font-size:27px;font-weight:650;letter-spacing:-1px}main{max-width:1220px;margin:auto;padding:42px 5vw}h1{font:clamp(34px,4.5vw,58px)/1.1 Georgia,serif;font-weight:400;letter-spacing:-1.5px;margin:12px 0 20px}p{max-width:850px;color:#4b655b}.eyebrow{text-transform:uppercase;letter-spacing:2px;font-size:11px;color:#267058}.grid{display:grid;grid-template-columns:330px minmax(0,1fr);gap:28px;margin-top:32px}.panel{background:#fffef9;border:1px solid #cddbcc;border-radius:10px;padding:25px}label{display:block;margin-bottom:20px;font-size:14px}select{display:block;width:100%;margin-top:9px;border:1px solid #91a897;border-radius:6px;padding:10px;background:white;color:#193631;font:inherit}select:focus-visible{outline:3px solid #b57a10}h2{font-size:23px;font-weight:550}h3{font-size:13px;letter-spacing:1px;text-transform:uppercase;margin-top:27px}.outcome{padding:11px;border-bottom:1px solid #dce5d7;font-size:14px}.badge{padding:7px 11px;border-radius:20px;background:#e4ebdc;font-size:12px;display:inline-block}.status{font:11px ui-monospace,monospace;color:#4b655b;overflow-wrap:anywhere}.proof{background:#e8f0df;padding:16px;border-left:3px solid #267058}.question{padding:13px 0;border-top:1px solid #dce5d7}.question small{color:#52685b}ul{padding-left:20px}.notice{margin-top:25px;padding:16px;border:1px dashed #9bad9a;font-size:13px}footer{margin-top:28px;font-size:12px;color:#52685b}@media(max-width:850px){.grid{grid-template-columns:1fr}main{padding:25px 5vw}.panel{padding:19px}}</style>
<header><span class="brand">agere.</span><span class="eyebrow">Conditional reasoning lab · synthetic</span></header><main><div class="eyebrow">Evidence → possibilities → the next useful question</div><h1>What would change<br>the answer?</h1><p>Missing data should create explicit possibilities. Explore when a conclusion becomes stable, which facts support it, and which unknowns still matter. Each result below was computed by Agere's bounded decision kernel.</p><div class="notice">These controls set fictional assumptions. They do not edit patient records, create reviewed labels or establish clinical eligibility. The workflow is an engineering illustration, not a clinical guideline or payer measure.</div><div class="grid"><section class="panel"><h2>Assumed facts</h2><div id="controls"></div><p class="status">No LLM calls. No probabilities invented. No automatic action.</p></section><section class="panel" aria-live="polite"><span class="badge" id="status"></span><h2 id="heading"></h2><div id="outcomes"></div><div id="proof"></div><h3 id="questionsHeading"></h3><div id="questions"></div><div id="sets"></div></section></div><footer id="footer"></footer></main><script type="application/json" id="data">__DATA__</script><script>
const data=JSON.parse(document.getElementById('data').textContent),$=id=>document.getElementById(id),controls=[];
function el(tag,text,cls){const e=document.createElement(tag);if(text!==undefined)e.textContent=text;if(cls)e.className=cls;return e}
const short={eligible:'Eligibility confirmed',care_current:'Current care confirmed',appropriate_exception:'Appropriate exception confirmed',access_barrier:'Current access barrier confirmed'};
const names={not_applicable_under_this_workflow:'Not applicable under this workflow',care_addressed_review_monitoring:'Care addressed: review monitoring',exception_review_path:'Review the appropriate exception',barrier_support_review_path:'Review support for an access barrier',clinical_opportunity_review_path:'Review a possible clinical opportunity'};
for(const name of data.names){const label=el('label',short[name]),select=document.createElement('select');select.setAttribute('aria-label',short[name]);for(const [value,text]of [['unknown','Not known'],['false','Assume no'],['true','Assume yes']]){const o=el('option',text);o.value=value;select.append(o)}select.onchange=render;label.append(select);$('controls').append(label);controls.push(select)}
function render(){const r=data.lookup[controls.map(s=>s.value).join('|')];$('status').textContent=r.status==='determined'?'Stable under these assumptions':'Unresolved';$('heading').textContent=r.status==='determined'?'The remaining unknowns do not change this result.':`${r.possible_outcomes.length} outcomes remain possible.`;$('outcomes').replaceChildren();for(const o of r.possible_outcomes)$('outcomes').append(el('div',names[o]||o,'outcome'));$('proof').replaceChildren();if(r.certificate){$('proof').append(el('h3','Smallest supporting fact set'));const box=el('div',undefined,'proof');for(const [k,v]of Object.entries(r.certificate.facts))box.append(el('div',`${short[k]}: ${v?'yes':'no'}`));box.append(el('p','Sufficient for this declared workflow. It does not establish that the assumptions are clinically true.'));$('proof').append(box)}$('questions').replaceChildren();$('questionsHeading').textContent=r.questions.length?'What information could matter next?':'No additional fact is needed to settle this workflow result.';for(const q of r.questions){const div=el('div',undefined,'question');div.append(el('div',q.question),el('small',q.can_change_outcome?`Decision-relevant. Worst-case outcomes remaining after resolution: ${q.worst_case_remaining_outcomes}.`:'Not decision-relevant in the current declared state.'));$('questions').append(div)}$('sets').replaceChildren();if(r.minimal_resolution_sets.length){$('sets').append(el('h3','Fact sets that would settle the workflow'));const ul=el('ul');for(const s of r.minimal_resolution_sets)ul.append(el('li',s.facts.map(x=>short[x]).join(' + ')));$('sets').append(ul)}$('footer').textContent=`${r.admissible_completions} logically admissible completions, not equally likely patient states. Policy ${r.program_id} v${r.program_version}. No acquisition prices or probabilities are assumed in this view.`}render();
</script></html>'''


if __name__ == "__main__":
    main()
