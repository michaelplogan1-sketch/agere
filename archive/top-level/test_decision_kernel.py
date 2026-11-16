"""Logical counterfactuals, time boundaries and proof scope—not clinical accuracy."""
import copy
import json
import tempfile
import unittest
from pathlib import Path

from clinical_core.decision_kernel import DecisionProgram
from clinical_core.dependency_cache import DependencyCache, Node
from clinical_core.reasoning import analyze_case, workflow_program, observation_proposals
from clinical_core.service import CorpusService


def fact(value, **overrides):
    return {"value": value, "status": "source_record", "available_date": "2026-09-01",
            "valid_from": "2026-09-01", "valid_to": "2026-09-30", "support_ids": ["synthetic-fixture"], **overrides}


class KernelTests(unittest.TestCase):
    def setUp(self):
        self.spec = workflow_program()
        self.program = DecisionProgram(self.spec)

    def test_absence_is_an_open_set_not_a_false_fact(self):
        result = self.program.analyze({}, "2026-09-30")
        self.assertEqual(result["status"], "unresolved")
        self.assertEqual(len(result["possible_outcomes"]), 5)
        self.assertEqual(result["admissible_completions"], 16)
        self.assertIsNone(result["probabilities"])
        self.assertFalse(result["automatic_action"])

    def test_eligibility_false_produces_minimal_certificate_despite_other_unknowns(self):
        observations = {"eligible": fact(False), "access_barrier": fact(True)}
        result = self.program.analyze(observations, "2026-09-30")
        self.assertEqual(result["certificate"]["facts"], {"eligible": False})
        self.assertEqual(result["outcome"], "not_applicable_under_this_workflow")
        self.assertFalse(result["questions"])

    def test_known_exception_cannot_be_overruled_by_irrelevant_missing_barrier(self):
        result = self.program.analyze({"eligible": fact(True), "care_current": fact(False), "appropriate_exception": fact(True)}, "2026-09-30")
        self.assertEqual(result["outcome"], "exception_review_path")
        self.assertNotIn("access_barrier", result["certificate"]["facts"])

    def test_unknown_exception_can_change_the_workflow(self):
        result = self.program.analyze({"eligible": fact(True), "care_current": fact(False), "access_barrier": fact(False)}, "2026-09-30")
        self.assertEqual(result["possible_outcomes"], ["clinical_opportunity_review_path", "exception_review_path"])
        self.assertEqual(result["minimal_resolution_sets"][0]["facts"], ["appropriate_exception"])
        self.assertEqual(result["questions"][0]["guaranteed_outcome_reduction"], 1)

    def test_proposals_conflicts_future_arrivals_and_missing_validity_remain_unknown(self):
        for changes in ({"status": "proposal"}, {"status": "conflicting"}, {"available_date": "2026-10-01"},
                        {"available_date": None}, {"valid_to": "2026-09-29"}, {"valid_from": None}, {"support_ids": []}):
            with self.subTest(changes=changes):
                result = self.program.analyze({"eligible": fact(False, **changes)}, "2026-09-30")
                self.assertEqual(result["status"], "unresolved")
                self.assertFalse(result["accepted_facts"])

    def test_constrained_correlated_inputs_allow_alternative_resolution_sets(self):
        spec = self.spec
        spec["facts"] = {k: spec["facts"][k] for k in ("eligible", "care_current")}
        spec["constraints"] = [{"any": [{"all": [{"fact": "eligible"}, {"fact": "care_current"}]},
                                       {"all": [{"not": {"fact": "eligible"}}, {"not": {"fact": "care_current"}}]}]}]
        spec["branches"] = [{"when": {"fact": "eligible"}, "outcome": "yes"}]
        spec["default"] = "no"
        result = DecisionProgram(spec).analyze({}, "2026-09-30", {
            "eligible": {"available": True, "cost_units": 5, "basis": "test effort assumption"},
            "care_current": {"available": True, "cost_units": 1, "basis": "test effort assumption"}})
        self.assertEqual(len(result["minimal_resolution_sets"]), 2)
        self.assertTrue(all(q["can_change_outcome"] for q in result["questions"]))
        self.assertEqual(result["least_cost_declared_resolution_set"]["facts"], ["care_current"])
        impossible = DecisionProgram(spec).analyze({"eligible": fact(True), "care_current": fact(False)}, "2026-09-30")
        self.assertEqual(impossible["status"], "inconsistent_inputs")

    def test_xor_requires_joint_information_even_when_individual_outcome_counts_do_not_shrink(self):
        spec = self.spec
        spec["facts"] = {k: spec["facts"][k] for k in ("eligible", "care_current")}
        spec["branches"] = [{"when": {"any": [{"all": [{"fact": "eligible"}, {"not": {"fact": "care_current"}}]},
                                               {"all": [{"not": {"fact": "eligible"}}, {"fact": "care_current"}]}]}, "outcome": "different"}]
        result = DecisionProgram(spec).analyze({}, "2026-09-30")
        self.assertEqual(result["minimal_resolution_sets"][0]["facts"], ["care_current", "eligible"])
        self.assertTrue(all(q["can_change_outcome"] and q["guaranteed_outcome_reduction"] == 0 for q in result["questions"]))

    def test_invalid_programs_dates_costs_and_types_fail_closed(self):
        for value in ("20260930", "2026-W40-3", 20260930):
            with self.assertRaises(ValueError):
                self.program.analyze({}, value)
            with self.assertRaises(ValueError):
                self.program.analyze({"eligible": fact(False, available_date=value)}, "2026-09-30")
            with self.assertRaises(ValueError):
                DecisionProgram({**self.spec, "effective_from": value})
        for change in ({"synthetic": False}, {"effective_to": "2025-12-31"},
                       {"branches": [{"when": {"exec": "bad"}, "outcome": "x"}]}):
            with self.assertRaises(ValueError):
                DecisionProgram({**self.spec, **change})
        with self.assertRaises(ValueError):
            self.program.analyze({}, "2027-01-01")
        with self.assertRaises(ValueError):
            self.program.analyze({"eligible": fact(1)}, "2026-09-30")
        with self.assertRaises(ValueError):
            self.program.analyze({}, "2026-09-30", {"eligible": {"available": True, "cost_units": float("nan"), "basis": "test"}})

    def test_dedupe_cannot_promote_lexical_evidence_to_confirmed_fact(self):
        proposal = observation_proposals({"evidence": [{"kind": "dispense_record", "assertion_id": "a"}]})
        result = self.program.analyze(proposal, "2026-09-30")
        self.assertFalse(result["accepted_facts"])
        self.assertEqual(result["unaccepted_facts"]["care_current"], "unaccepted_or_conflicting_assertion")

    def test_more_consistent_information_cannot_expand_possible_outcomes(self):
        initial = self.program.analyze({}, "2026-09-30")
        for value in (True, False):
            observations = {"eligible": fact(value)}
            partial = self.program.analyze(observations, "2026-09-30")
            self.assertLessEqual(set(partial["possible_outcomes"]), set(initial["possible_outcomes"]))
            for current in (True, False):
                refined = self.program.analyze({**observations, "care_current": fact(current)}, "2026-09-30")
                self.assertLessEqual(set(refined["possible_outcomes"]), set(partial["possible_outcomes"]))

    def test_bounded_compilation_and_mutation_guard(self):
        spec = copy.deepcopy(self.spec)
        spec["facts"].update({f"extra{i}": {"question": "Test?", "source": "fixture"} for i in range(8)})
        with self.assertRaisesRegex(ValueError, "bounded"):
            DecisionProgram(spec)
        self.program.spec["version"] = "changed"
        with self.assertRaisesRegex(ValueError, "mutated"):
            self.program.analyze({}, "2026-09-30")


class DependencyTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.cache = DependencyCache(Path(self.tmp.name) / "cache.sqlite3")

    def tearDown(self):
        self.cache.close()
        self.tmp.cleanup()

    def case(self, scope="p1", observations=None, program=None):
        return analyze_case(self.cache, scope=scope, program=program or workflow_program(),
                            observations=observations or {}, as_of="2026-09-30")

    def test_changed_patient_invalidates_its_analysis_and_keeps_other_patient_cached(self):
        first = self.case()
        self.case("p2")
        self.assertEqual(self.cache.misses, 2)
        self.assertEqual(len(self.cache.affected_heads(first["fact_node_id"])), 1)
        changed = self.case(observations={"eligible": fact(False)})
        self.assertNotEqual(changed["node_id"], first["node_id"])
        self.case("p2")
        self.assertEqual(self.cache.misses, 3)
        self.assertEqual(self.cache.hits, 1)
        self.assertTrue(self.cache.lineage(first["node_id"]))

    def test_policy_change_invalidates_dependents_and_retains_history(self):
        first = self.case()
        self.case("p2")
        self.assertEqual(len(self.cache.affected_heads(first["policy_node_id"])), 2)
        policy = workflow_program()
        policy["version"] = "2"
        self.case(program=policy)
        self.assertEqual(self.cache.misses, 3)

    def test_scope_and_mutated_dependency_are_checked(self):
        source = self.cache.source("p1", "x", {"value": 1})
        with self.assertRaisesRegex(ValueError, "Cross-patient"):
            self.cache.resolve("p2", "read", "v1", {"x": source}, {}, lambda d, p: d)
        with self.assertRaisesRegex(ValueError, "changed"):
            self.cache.resolve("p1", "read", "v1", {"x": Node(source.node_id, source.scope, '{}')}, {}, lambda d, p: d)

    def test_failed_computation_never_populates_cache(self):
        source = self.cache.source("p1", "x", 1)
        with self.assertRaises(ZeroDivisionError):
            self.cache.resolve("p1", "fail", "v1", {"x": source}, {}, lambda d, p: 1/0)
        self.assertEqual(self.cache.misses, 0)

    def test_service_serves_reasoning_artifact_without_inference(self):
        root = Path(self.tmp.name)
        (root / "summary.json").write_text(json.dumps({"worlds": []}))
        path = root / "reasoning.json"
        value = {"schema_version": "agere-conditional-decisions/1", "synthetic": True, "items": []}
        path.write_text(json.dumps(value))
        self.assertEqual(CorpusService(root, reasoning_run=path).get("/reasoning", {}), value)
        with self.assertRaisesRegex(ValueError, "No conditional"):
            CorpusService(root).get("/reasoning", {})


if __name__ == "__main__":
    unittest.main()
