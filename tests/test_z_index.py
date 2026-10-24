import unittest
from pathlib import Path
import numpy as np
from engine import Recording, CACHE
from synthetic import generate_capture


class IndexPulseTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.folder = CACHE / "z-index-tests"
        cls.folder.mkdir(exist_ok=True)
        cls.recordings = {}
        for kind in ("clean", "jitter"):
            path = cls.folder / (kind + ".csv")
            generate_capture(path, kind, ppr=8192)
            r = Recording(path, {"low": 2.5, "high": 2.5, "rpm": 60})
            r.run()
            if r.error:
                raise RuntimeError(r.error)
            cls.recordings[kind] = r

    def test_narrow_index_timing_and_precision(self):
        r = self.recordings["clean"]
        z = next(m for m in r.metrics if m["c"] == 4)
        negative = next(m for m in r.metrics if m["c"] == 5)
        self.assertAlmostEqual(z["high"], 4 / 8192)
        self.assertAlmostEqual(z["period"], 1)
        self.assertEqual(z["ppr"], 1)
        self.assertIsNone(z["duty"])
        self.assertIsNone(negative["duty"])
        self.assertIsNone(z["cycle_duty"])
        self.assertAlmostEqual(negative["high"], z["low"])
        self.assertEqual(z["oos"], 0)

    def test_irregular_sampling_does_not_bias_index_duty(self):
        z = next(m for m in self.recordings["jitter"].metrics if m["c"] == 4)
        self.assertIsNone(z["duty"])
        self.assertAlmostEqual(z["high"], 4 / 8192)

    def test_narrow_eyes_retain_both_edges_with_common_trigger(self):
        r = self.recordings["clean"]
        plot = r.plot(0, 0.002)
        self.assertIn("pulse widths", plot["eye_labels"]["4"])
        self.assertTrue(plot["eyes"]["4"])
        for z, n in zip(plot["eyes"]["4"], plot["eyes"]["5"]):
            np.testing.assert_allclose(np.array(z)[:, 0], np.array(n)[:, 0])
            self.assertLess(len(z), 180)
            self.assertTrue(any(0.6666 <= x <= 0.6668 and y > 3 for x, y in z))
            self.assertTrue(any(1.3332 <= x <= 1.3334 and y < 2 for x, y in z))
            self.assertTrue(any(0.6666 <= x <= 0.6668 and y < 2 for x, y in n))
            self.assertTrue(any(1.3332 <= x <= 1.3334 and y > 3 for x, y in n))

    def test_span_without_revolution_does_not_report_occupancy_as_duty(self):
        r = self.recordings["clean"]
        scope = r.span_measurements(0, 0.002)
        z = next(m for m in scope["metrics"] if m["c"] == 4)
        self.assertIsNone(z["duty"])
        self.assertIsNone(z["period"])

    def test_glitch_is_not_a_revolution_for_index_measurements(self):
        path = self.folder / "glitch.csv"
        generate_capture(path, "z-glitch", ppr=32, spp=160)
        r = Recording(path, {"low": 2.5, "high": 2.5})
        r.run()
        self.assertIsNone(r.error)
        z = next(m for m in r.metrics if m["c"] == 4)
        self.assertEqual(z["ppr"], 1)
        self.assertEqual(z["period"], 1)
        self.assertIsNone(z["duty"])


if __name__ == "__main__":
    unittest.main()
