import unittest
from clinical_core.information_value import expected_information_value


class InformationValueTests(unittest.TestCase):
    def scenario(self):
        return {"prior": {"a": .5, "b": .5}, "utilities": {"choose_a": {"a": 1, "b": 0}, "choose_b": {"a": 0, "b": 1}},
                "utility_scale": "fictional correctness units", "assumption_source": "mathematical fixture, not patient estimates",
                "channels": {
                    "perfect": {"available": True, "cost": .1, "basis": "fixture", "likelihoods": {"a": {"yes": 1, "no": 0}, "b": {"yes": 0, "no": 1}}},
                    "uninformative": {"available": True, "cost": .1, "basis": "fixture", "likelihoods": {"a": {"yes": .5, "no": .5}, "b": {"yes": .5, "no": .5}}}}}

    def test_perfect_information_and_uninformative_reprocessing(self):
        result = expected_information_value(**self.scenario())
        channels = {c["channel"]: c for c in result["channels"]}
        self.assertAlmostEqual(channels["perfect"]["gross_value"], .5)
        self.assertAlmostEqual(channels["perfect"]["net_value"], .4)
        self.assertEqual(channels["uninformative"]["gross_value"], 0)
        self.assertEqual(result["preferred_channel_under_assumptions"], "perfect")
        self.assertFalse(result["automatic_acquisition"])

    def test_unavailable_or_excessively_expensive_observation_is_not_selected(self):
        scenario = self.scenario()
        scenario["channels"]["perfect"]["available"] = False
        self.assertIsNone(expected_information_value(**scenario)["preferred_channel_under_assumptions"])
        scenario["channels"]["perfect"].update(available=True, cost=1)
        self.assertIsNone(expected_information_value(**scenario)["preferred_channel_under_assumptions"])

    def test_noisy_observation_is_worth_less_than_perfect_information(self):
        scenario = self.scenario()
        scenario["channels"]["noisy"] = {"available": True, "cost": .1, "basis": "fixture",
            "likelihoods": {"a": {"yes": .8, "no": .2}, "b": {"yes": .2, "no": .8}}}
        values = {c["channel"]: c["gross_value"] for c in expected_information_value(**scenario)["channels"]}
        self.assertAlmostEqual(values["noisy"], .3)
        self.assertLess(values["noisy"], values["perfect"])

    def test_bad_probabilities_missing_utility_and_implicit_scale_are_rejected(self):
        scenario = self.scenario()
        scenario["prior"]["a"] = .8
        with self.assertRaises(ValueError):
            expected_information_value(**scenario)
        scenario = self.scenario()
        del scenario["utilities"]["choose_a"]["b"]
        with self.assertRaises(ValueError):
            expected_information_value(**scenario)
        scenario = self.scenario()
        scenario["utility_scale"] = ""
        with self.assertRaises(ValueError):
            expected_information_value(**scenario)


if __name__ == "__main__":
    unittest.main()
