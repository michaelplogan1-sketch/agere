"""Preserve the original question catalog and add proposed, explicitly scoped tasks."""
import json
from pathlib import Path

ADDITIONS = {
"CKD": ["Which apparent kidney declines disappear after reconciling units, corrected results and duplicate labs?", "Where are recurrent potassium problems interrupting otherwise appropriate treatment?", "Which patients have an uncompleted kidney evaluation despite repeated abnormal observations?", "Which groups have missing outside renal care documentation that could distort referral demand?"],
"HF": ["Which missing medication fills are explained by samples or an outside pharmacy?", "Which patients have a documented treatment barrier that remains unresolved at subsequent visits?", "Where do copied medication lists conflict with actual dispensing or documented discontinuation?", "Which patients have changes in ejection fraction that require reassessing their treatment-gap classification?"],
"SEP": ["Which sepsis survivors encounter a gap between discharge antibiotic plans and actual access?", "Where do pending culture results fail to receive documented follow-up?", "Which recurrent infections are associated with fragmented care across facilities?", "Which apparent improvements in sepsis performance disappear after accounting for changes in coding and severity?"],
"BEN": ["Which assistance interventions address a documented financial barrier rather than a neighborhood risk estimate?", "Where do repeated authorizations consume effort without restoring access to treatment?", "Which medication substitutions resolve affordability without creating a new documented clinical problem?", "Which patients lose assistance eligibility mid-treatment and need an alternative plan?"],
"PAL": ["Where are patient-reported symptoms missing despite repeated serious-illness encounters?", "Which patients repeatedly receive care inconsistent with a documented preference, requiring review?", "Which caregivers report burdens that are absent from the patient's structured record?", "Which changes in function or symptoms should prompt a goals-of-care reassessment?"],
"ADM": ["Which admissions follow a documented medication access barrier that persisted across visits?", "Which patients appear stable only because outside admissions have not reached our feeds?", "Which recurring admission sequences suggest a testable service redesign rather than another patient alert?", "Where does an intervention shift utilization to emergency or observation care without improving outcomes?"],
"SNF": ["Which facility comparisons change materially when patient severity and functional status are included?", "Where are SNF rankings too uncertain or based on too few comparable episodes to guide placement?", "Which facilities meet a patient's clinical needs but lack timely capacity or acceptable travel access?", "Which post-acute complications are already developing before transfer, making attribution uncertain?"],
"PCP": ["Which apparent provider gaps are driven by a missing source feed rather than missing care?", "Which practices reliably resolve affordability and authorization barriers after they are documented?", "Where does measured performance change when patients transfer between attributed clinicians?", "Which practice differences persist within comparable disease severity and access groups?"],
"NET": ["Which outside encounters can close an apparent care gap once reconciled with local records?", "Where do network directories claim access that appointment availability does not support?", "Which recurring outside services fill a clinical capability gap in the contracted network?", "Which out-of-network expenditures are likely attribution or duplicate-claim artifacts?"],
"DOC": ["Which copied diagnoses lack current corroboration and should be reviewed before reuse?", "Which documentation requests would add information needed for care rather than merely increase coding volume?", "Where does missing clinical documentation prevent distinguishing appropriate holds from untreated disease?", "Which records contain contradictory condition status across notes, claims and problem lists?"],
"FIN": ["Which apparent savings disappear after claims maturity and runout are accounted for?", "Which contract opportunities remain favorable under conservative uptake and delivery-cost assumptions?", "Which patients would receive conflicting interventions from overlapping contract initiatives?", "Where does a financially attractive proposal lack evidence of patient benefit?"],
"POL": ["Which rule updates change only documentation requirements versus actual patient eligibility?", "Which measure versions are simultaneously active across our contracts?", "Which policy clauses cannot be translated into executable logic without clarification?", "Which published corrections would change prior cohort assignments and require reprocessing?"],
"DISC": ["Which apparent population patterns vanish when copied sentences count as one observation?", "Which newly emerging clusters coincide with changes in data completeness rather than patient health?", "Which candidate interventions would be most informative to test prospectively?", "Which insights persist across practices and time periods after correcting for repeated testing?"],
"LEARN": ["Where do specialist and frontier models disagree, and which disagreement types predict errors?", "Which evidence questions can a smaller model answer at acceptable accuracy and coverage?", "Which cached findings need revision after new clinical evidence or a rule update?", "Which human corrections generalize to new source-patient families rather than memorized templates?"]}

HANDLERS = {
    "HF-01": ("hf_gaps", "partial", "Existing benchmark HFrEF medication-class heuristic; not complete GDMT, target dosing or guideline validation."),
    "HF-04": ("barriers", "partial", "Observed medication gap reasons; does not infer causes of treatment failure."),
    "HF-07": ("outside", "partial", "Existing note-reader classification of outside supply; not verified ingestion."),
    "BEN-04": ("barriers", "partial", "Documented/reconciled reasons under synthetic benchmark rules; no longitudinal causal attribution."),
    "DOC-03": ("documentation", "partial", "Note repetition and benchmark documentation counters; no verified workflow root cause."),
    "DISC-05": ("insights", "partial", "Existing exploratory discovery only; no new confounder adjustment or multiple-testing correction."),
    "DATA-01": ("feed_counts", "implemented", "Counts supplied records, not real-world completeness."),
    "DATA-02": ("documentation", "implemented", "Sentence-normalized copy-forward counts from the existing chart reader."),
    "DATA-03": ("cutoff", "implemented", "Event-date exclusion only; arrival-time completeness is not known."),
    "DATA-04": ("lab_coverage", "implemented", "Observed test coverage only; no clinical adequacy inference."),
    "DATA-05": ("source_references", "implemented", "Exact quote location checks, not semantic entailment."),
    "DATA-06": ("reader_review", "implemented", "Explicit reader review/validation states; does not detect all errors."),
}


def build(source: Path):
    catalog = json.loads(source.read_text())
    catalog["version"] = "executable-registry-1"
    catalog["status"] = "Synthetic development registry. Capability is explicit per question; question text is not proof of implementation."
    for group in catalog["groups"]:
        for q in group["questions"]:
            q["origin"] = "existing user-derived catalog"
        start = len(group["questions"]) + 1
        for number, question in enumerate(ADDITIONS[group["id"]], start):
            group["questions"].append({"id": f"{group['id']}-{number:02d}", "question": question,
                "origin": "additional question", "analysis_type": "proposed_analysis", "build_priority": "P1",
                "expected_output": "Evidence-linked findings, alternative explanations and missing inputs.",
                "candidate_response": "Review findings and define a testable response appropriate to the question.",
                "acceptance_case": "Unsupported inputs or causal claims return an explicit limitation, not an invented conclusion."})
    texts = ["How many records and patients are supplied by each feed?", "How much chart text repeats after sentence normalization?",
             "How many supplied records occur after the analysis cutoff?", "Which lab tests are observed, and in how many patients?",
             "Which returned note quotes cannot be located exactly in the supplied source?", "Which judgments were sent to review by the reader?",
             "Which source feeds arrived too late to have been available at the historical decision time?", "Which patient identities cannot be reconciled across source systems?"]
    catalog["groups"].append({"id": "DATA", "title": "Data and evidence integrity", "required_data": ["Source feeds and provenance", "Arrival timestamps and identity crosswalks for historical/identity questions"],
        "review_owner": "Data quality / clinical informatics", "guardrail": "Missing observations never establish missing care.",
        "questions": [{"id": f"DATA-{i:02d}", "question": text, "origin": "additional question", "analysis_type": "data_quality",
                       "expected_output": "Reproducible counts and explicit limitations", "build_priority": "P0"} for i, text in enumerate(texts, 1)]})
    for group in catalog["groups"]:
        for q in group["questions"]:
            handler, status, scope = HANDLERS.get(q["id"], (None, "not_implemented", "Needs the listed data, executable logic and independent validation."))
            q["execution"] = {"handler": handler, "status": status, "scope": scope,
                              "required_data": group["required_data"], "review_owner": group["review_owner"]}
    catalog["question_count"] = sum(len(g["questions"]) for g in catalog["groups"])
    return catalog


if __name__ == "__main__":
    root = Path(__file__).resolve().parent
    source = root.parent / "caresignal-mvp/deliverables/vbc-intelligence/question_catalog.json"
    target = root / "config/questions.json"
    target.parent.mkdir(exist_ok=True)
    target.write_text(json.dumps(build(source), indent=2))
    print(target)
