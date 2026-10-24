"""Independent truth cases for the local-cycle A/B phase estimator."""

from types import SimpleNamespace
from unittest.mock import patch
import unittest
import numpy as np
from phase import phase_stats


class LocalPhaseTests(unittest.TestCase):
    def recording(self, periods, fractions=0.25):
        periods = np.asarray(periods, dtype="i8")
        a = np.r_[0, np.cumsum(periods)]
        fractions = np.broadcast_to(fractions, len(periods))
        b = a[:-1] + np.rint(periods * fractions).astype("i8")
        data = np.zeros((int(a[-1]) + 1, 1))
        data[:, 0] = np.arange(len(data)) * 0.001
        r = SimpleNamespace(data=data, count=len(data), check=lambda: None)
        return r, a, b

    def test_forward_reverse_and_exact_half_open_boundaries(self):
        for fraction, degrees in ((0.25, 90), (0.75, -90), (0, 0), (0.5, -180)):
            with self.subTest(fraction=fraction):
                r, a, b = self.recording(np.full(100, 100), fraction)
                result = phase_stats(r, r, [a, b])
                self.assertAlmostEqual(result["phase_degrees"], degrees)
                self.assertAlmostEqual(result["coherence"], 1)
                self.assertEqual(result["pairs"], 100)
                self.assertEqual(result["coverage"], 1)

    def test_speed_change_preserves_constant_local_quadrature(self):
        r, a, b = self.recording(np.r_[np.full(800, 100), np.full(200, 400)])
        result = phase_stats(r, r, [a, b])
        self.assertAlmostEqual(result["phase_degrees"], 90)
        self.assertAlmostEqual(result["coherence"], 1)
        self.assertEqual(result["pairs"], 1000)
        self.assertEqual(result["coverage"], 1)
        self.assertAlmostEqual(result["period"], 0.1)

    def test_slow_reversed_section_cannot_be_silently_discarded(self):
        r, a, b = self.recording(
            np.r_[np.full(800, 100), np.full(200, 400)],
            np.r_[np.full(800, 0.25), np.full(200, 0.75)],
        )
        result = phase_stats(r, r, [a, b])
        self.assertEqual(result["pairs"], 1000)
        self.assertEqual(result["coverage"], 1)
        self.assertAlmostEqual(result["coherence"], 0.6)

    def test_missing_a_or_b_excludes_ambiguous_intervals(self):
        r, a, b = self.recording(np.full(100, 100))
        for missing in ("A", "B"):
            with self.subTest(missing=missing):
                aa = np.delete(a, np.arange(20, 30)) if missing == "A" else a
                bb = np.delete(b, np.arange(20, 30)) if missing == "B" else b
                result = phase_stats(r, r, [aa, bb])
                self.assertAlmostEqual(result["phase_degrees"], 90)
                self.assertAlmostEqual(result["coherence"], 1)
                self.assertLess(result["coverage"], 1)
                self.assertEqual(result["pairs"], 89 if missing == "A" else 90)

    def test_extra_b_edges_reject_only_the_ambiguous_cycles(self):
        r, a, b = self.recording(np.full(100, 100))
        b = np.sort(np.r_[b, a[10:20] + 60])
        result = phase_stats(r, r, [a, b])
        self.assertEqual(result["pairs"], 90)
        self.assertAlmostEqual(result["phase_degrees"], 90)

    def test_phase_across_zero_is_averaged_circularly(self):
        r, a, b = self.recording(np.full(100, 100), np.tile([0.99, 0.01], 50))
        result = phase_stats(r, r, [a, b])
        self.assertAlmostEqual(result["phase_degrees"], 0, places=8)
        self.assertGreater(result["coherence"], 0.99)

    def test_no_edges_no_overlap_and_alignment_minimum(self):
        r, a, b = self.recording(np.full(10, 100))
        self.assertIsNone(phase_stats(r, r, [a, []]))
        self.assertIsNone(phase_stats(r, r, [a[:3], b[:2]], min_edges=5))
        self.assertIsNotNone(phase_stats(r, r, [a[:3], b[:2]]))
        self.assertIsNone(phase_stats(r, r, [a[:3], b[7:]]))

    def test_opposing_local_phases_report_low_coherence(self):
        r, a, b = self.recording(np.full(100, 100), np.tile([0.25, 0.75], 50))
        result = phase_stats(r, r, [a, b])
        self.assertLess(result["coherence"], 1e-12)
        self.assertEqual(result["coverage"], 1)

    def test_chunk_invariance_and_no_source_reimport(self):
        r, a, b = self.recording(np.tile([100, 200, 400], 500), 0.25)
        b = np.delete(b, np.arange(20, 30))
        with patch("builtins.open", side_effect=AssertionError("Source file reopened")):
            first = phase_stats(r, r, [a, b], block=7)
            second = phase_stats(r, r, [a, b], block=1000)
        for key in first:
            self.assertAlmostEqual(first[key], second[key], places=10)


if __name__ == "__main__":
    unittest.main()
