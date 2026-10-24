import unittest
import wave
import time
from pathlib import Path
import numpy as np
from engine import CACHE
from merge import MergedRecording
from server import Handler
from server import JOBS, reanalyse
from compatibility import time_unit
from alignment import refresh_alignment


class AlignmentTests(unittest.TestCase):
    def setUp(self):
        self.folder = CACHE / "alignment-tests"
        self.folder.mkdir(exist_ok=True)
        self.a = Path("examples/merge-A.csv")
        self.b = Path("examples/merge-B.csv")

    def reference(
        self,
        phase=0.25,
        rate=25000,
        frequency=200,
        count=10000,
        generic=False,
        wav=False,
    ):
        t = np.arange(count) / rate
        cycle = t * frequency + 0.137
        a = (cycle % 1 < 0.5).astype(float)
        b = ((cycle - phase) % 1 < 0.5).astype(float)
        data = np.column_stack((a, 1 - a, b, 1 - b))
        path = self.folder / ("reference.wav" if wav else "reference.csv")
        if wav:
            with wave.open(str(path), "wb") as w:
                w.setnchannels(4)
                w.setsampwidth(2)
                w.setframerate(rate)
                w.writeframes((data * 20000).astype("<i2").tobytes())
        else:
            header = "Time_ms,CH1,CH2,CH3,CH4" if generic else "Time_ms,A,-A,B,-B"
            np.savetxt(
                path,
                np.column_stack(((t + 11) * 1000, 0.2 + data * 2)),
                delimiter=",",
                header=header,
                comments="",
                fmt="%.14g",
            )
        return path

    def merged(self, path):
        r = MergedRecording(self.a, self.b, path_c=path)
        r.run()
        self.assertIsNone(r.error)
        self.assertTrue(r.ready)
        return r

    def test_different_sample_rate_duration_speed_and_origin(self):
        r = self.merged(self.reference())
        c = r.merge_info["alignment_check"]
        self.assertEqual(c["status"], "match")
        self.assertAlmostEqual(c["time_scale"], 2, places=6)
        self.assertAlmostEqual(c["sample_scale"], 0.8, places=6)
        self.assertAlmostEqual(c["reference_phase_degrees"], 90, delta=3)
        self.assertLess(abs(c["phase_difference_degrees"]), 3)
        self.assertAlmostEqual(c["scaled_reference_period"], 0.01)
        self.assertGreater(c["compared_pairs"], 40)
        self.assertEqual(len(r.merge_paths), 3)
        # Checking alignment never replaces the original merged samples.
        baseline = MergedRecording(self.a, self.b)
        baseline.ingest()
        np.testing.assert_array_equal(r.data, baseline.data)
        path, _ = Handler.prepare_export(r, {"kind": "csv"})
        self.assertEqual(path.read_text().splitlines()[0], "Time_s,A,-A,B,-B,Z,-Z")

    def test_wrong_quadrature_direction_reports_mismatch(self):
        r = self.merged(self.reference(phase=-0.25))
        c = r.merge_info["alignment_check"]
        self.assertEqual(c["status"], "mismatch")
        self.assertGreater(abs(c["phase_difference_degrees"]), 170)

    def test_generic_headers_and_four_channel_wav(self):
        for wav in (False, True):
            with self.subTest(wav=wav):
                r = self.merged(self.reference(generic=True, wav=wav))
                self.assertEqual(r.merge_info["alignment_check"]["status"], "match")

    def test_insufficient_reference_cycles_are_inconclusive(self):
        r = self.merged(self.reference(count=200))
        self.assertEqual(r.merge_info["alignment_check"]["status"], "inconclusive")

    def test_poor_resolution_does_not_claim_a_match(self):
        r = self.merged(self.reference(rate=1000, frequency=200, count=1000))
        self.assertEqual(r.merge_info["alignment_check"]["status"], "inconclusive")

    def test_long_periodic_reference_does_not_alias_level_detection(self):
        # The old 100-sample stride always hit the same phase in this capture,
        # hiding all 10,000 perfectly clean cycles from automatic thresholds.
        r = self.merged(self.reference(frequency=250, count=1_000_000))
        c = r.merge_info["alignment_check"]
        self.assertEqual(c["status"], "match")
        self.assertAlmostEqual(c["time_scale"], 2.5, places=6)
        self.assertAlmostEqual(c["reference_phase_degrees"], 90, delta=1)
        self.assertLess(abs(c["phase_difference_degrees"]), 3)

    def test_scope_unit_headings(self):
        for header, expected in (
            ("in s", (1.0, "s")),
            ("in ms", (1e-3, "ms")),
            ("in us", (1e-6, "us")),
            ("in ns", (1e-9, "ns")),
        ):
            with self.subTest(header=header):
                self.assertEqual(time_unit(header), expected)

    def test_refresh_compares_actual_processed_rx_edges(self):
        r = self.merged(self.reference())
        original = r.merge_info["alignment_check"]
        # Emulate a different result from main signal processing. The third
        # capture stays fixed; its comparison must use the processed main edges.
        r.edges[7] = r.edges[7] + 5
        updated = refresh_alignment(r, original)
        self.assertEqual(updated["status"], "mismatch")
        self.assertAlmostEqual(updated["phase_difference_degrees"], 18, delta=3)
        self.assertEqual(original["status"], "match")

    def test_reanalysis_does_not_retain_a_stale_alignment_match(self):
        old = self.merged(self.reference())
        original = dict(old.merge_info["alignment_check"])
        result = reanalyse(old, {"low": 6, "high": 6})
        new = JOBS[result["id"]]
        deadline = time.monotonic() + 15
        while not new.ready and not new.error and time.monotonic() < deadline:
            time.sleep(0.01)
        self.assertIsNone(new.error)
        self.assertTrue(new.ready)
        updated = new.merge_info["alignment_check"]
        self.assertEqual(updated["status"], "inconclusive")
        self.assertNotIn("merged_phase_degrees", updated)
        self.assertEqual(updated["reference_stats"], original["reference_stats"])
        self.assertEqual(old.merge_info["alignment_check"], original)


if __name__ == "__main__":
    unittest.main()
