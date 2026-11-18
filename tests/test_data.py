import json
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
from data import rates, srm, summarize_costs


HEADER = "cohort,segment,eligible,retained,window_days,observed_days\n"


class DataTests(unittest.TestCase):
    def calculate(self, rows, base="before"):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "data.csv"
            path.write_text(HEADER + rows, encoding="utf-8")
            return rates(path, base)

    def test_demo_mix_does_not_prove_declining_segment_rates(self):
        root = Path(__file__).resolve().parents[1]
        result = rates(root / "demo/data.csv", "before")
        self.assertEqual(result["cohorts"]["before"]["rate"], 0.4)
        self.assertEqual(result["cohorts"]["after"]["rate"], 0.35)
        self.assertAlmostEqual(
            result["cohorts"]["after"]["standardized_rate"], 0.45,
        )
        self.assertEqual(result["weights"], {"organic": 0.5, "paid": 0.5})

    def test_immature_is_excluded_not_zero(self):
        result = self.calculate(
            "before,x,10,5,7,7\nafter,x,10,2,7,3\n",
        )
        self.assertNotIn("after", result["cohorts"])
        self.assertEqual(len(result["excluded"]), 1)
        self.assertEqual(result["excluded"][0]["line"], 3)

    def test_missing_segment_prevents_standardization(self):
        result = self.calculate(
            "before,x,10,5,7,7\nbefore,y,10,2,7,7\nafter,x,10,6,7,7\n",
        )
        self.assertIsNone(result["cohorts"]["after"]["standardized_rate"])
        self.assertEqual(
            result["cohorts"]["after"]["missing_base_segments"], ["y"],
        )

    def test_invalid_input_never_silently_changes_denominator(self):
        cases = [
            "before,x,10,11,7,7\n",
            "before,x,10,5,7,7\nbefore,x,10,5,7,7\n",
            "before,x,10,5,7,7\nbefore,y,10,5,14,14\n",
            "before,x,10,,7,7\n",
            "before,x,NaN,5,7,7\n",
            "before,x,0,0,7,7\n",
            "before,x,10,5,7,7,extra\n",
        ]
        for rows in cases:
            with self.subTest(rows=rows), self.assertRaises(ValueError):
                self.calculate(rows)

    def test_zero_nonbase_denominator_is_null(self):
        result = self.calculate("before,x,10,5,7,7\nafter,x,0,0,7,7\n")
        self.assertIsNone(result["cohorts"]["after"]["rate"])
        self.assertIsNone(result["cohorts"]["after"]["standardized_rate"])

    def test_srm_closed_form_and_domain(self):
        self.assertEqual(srm(100, 100, 0.5, 0.0005)["p"], 1)
        result = srm(900, 100, 0.5, 0.0005)
        self.assertEqual(result["chi2"], 640)
        self.assertTrue(result["srm_detected"])
        self.assertGreater(result["p"], 0)
        for values in [
            (1, 1, 0.5, 0.05), (0, 0, 0.5, 0.05),
            (100, 100, "nan", 0.05), (100, 100, 0.5, 0),
        ]:
            with self.subTest(values=values), self.assertRaises(ValueError):
                srm(*values)

    def test_unknown_cost_is_not_free_and_decimal_sum_is_exact(self):
        result = summarize_costs([
            {"usd": "0.1", "minutes": "2"},
            {"usd": "0.2"}, {"usd": None},
        ])
        self.assertEqual(result["known_usd"], "0.3")
        self.assertEqual(result["unknown_entries"], 1)
        self.assertIsNone(result["total_usd"])
        self.assertEqual(result["recorded_minutes"], "2")
        self.assertEqual(summarize_costs([])["total_usd"], "0")
        for price in ["NaN", "Infinity", "-1"]:
            with self.subTest(price=price), self.assertRaises(ValueError):
                summarize_costs([{"usd": price}])
        json.dumps(result, allow_nan=False)


if __name__ == "__main__":
    unittest.main()
