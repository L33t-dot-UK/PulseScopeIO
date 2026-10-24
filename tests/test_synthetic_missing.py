import unittest
import numpy as np
from synthetic import missing_pulse_cycles, generate_capture
from engine import CACHE, Recording


class MissingGeneratorTests(unittest.TestCase):
    def test_random_rate_spread_and_maximum_run(self):
        total = 8192 * 3
        counts = set()
        for seed in range(100):
            missing = missing_pulse_cycles(total, np.random.default_rng(seed))
            counts.add(len(missing))
            self.assertGreaterEqual(len(missing) / total, 0.001)
            self.assertLess(len(missing) / total, 0.01)
            self.assertTrue(np.all(np.diff(missing) > 0))
            self.assertGreater(missing[0], 0)
            self.assertLess(missing[-1], total - 1)
            runs = np.split(missing, np.flatnonzero(np.diff(missing) != 1) + 1)
            self.assertLessEqual(max(map(len, runs)), 10)
            self.assertLessEqual(sum(len(run) > 2 for run in runs), 3)
            self.assertGreaterEqual(
                sum(len(run) for run in runs if len(run) <= 2), 0.75 * len(missing)
            )
            # Losses reach both the early and late portions, not one prefix.
            self.assertLess(missing[0], total / 3)
            self.assertGreater(missing[-1], total * 2 / 3)
        self.assertGreater(len(counts), 50)

    def test_reproducible_seed(self):
        np.testing.assert_array_equal(
            missing_pulse_cycles(24576, np.random.default_rng(123)),
            missing_pulse_cycles(24576, np.random.default_rng(123)),
        )

    def test_csv_matches_selected_losses_and_keeps_b_and_z(self):
        path = CACHE / "missing-generator-verification.csv"
        meta = generate_capture(path, "missing", ppr=512, seed=321)
        data = np.loadtxt(path, delimiter=",", skiprows=1)
        missing = set(meta["missing_cycles"])
        # Read the middle of each A high half-cycle directly from the CSV.
        cycle = np.arange(512 * 3)
        indices = cycle * 16 + 8  # source has one leading sample
        high = data[indices, 1] > 2.5
        self.assertEqual(set(cycle[~high]), missing)
        np.testing.assert_array_equal(data[:, 1] > 2.5, ~(data[:, 2] > 2.5))
        self.assertAlmostEqual(meta["missing_percent"], 100 * len(missing) / len(cycle))
        # B rising edges and index pulses still occur throughout the recording.
        self.assertEqual(
            np.count_nonzero(np.diff((data[:, 3] > 2.5).astype(int)) == 1), len(cycle)
        )
        r = Recording(path, {"reference": True, "ppr": 512})
        r.run()
        self.assertIsNone(r.error)
        counts = r.pulse_counts()
        expected = [
            sum(i * 512 <= c < (i + 1) * 512 for c in missing)
            for i in range(counts["revolutions"])
        ]
        self.assertEqual([row["missing"]["A"] for row in counts["rows"]], expected)
        self.assertEqual(counts["missing_total"], sum(expected))
        self.assertTrue(all(row["missing"]["B"] == 0 for row in counts["rows"]))

    def test_revolution_count_pagination(self):
        r = Recording.__new__(Recording)
        r.validated_z = np.arange(502) * 100
        r.edges = {6: np.setdiff1d(np.arange(50100), [5, 404, 50001])}
        r.reference_ppr = 100
        r.b_reference_ppr = None
        r.missing_pulse_totals = {"A": 3}
        first = r.pulse_counts()
        self.assertEqual(len(first["rows"]), 200)
        self.assertEqual(first["next_offset"], 200)
        self.assertEqual(first["missing_total"], 3)
        last = r.pulse_counts(400)
        self.assertEqual(len(last["rows"]), 101)
        self.assertEqual(last["rows"][-1]["revolution"], 501)
        self.assertEqual(last["rows"][-1]["total"], 1)
        self.assertIsNone(last["next_offset"])


if __name__ == "__main__":
    unittest.main()
