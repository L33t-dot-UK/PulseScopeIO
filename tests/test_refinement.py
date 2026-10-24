"""Truth cases for explicitly selected reference-derived B timing."""

from pathlib import Path
import hashlib
import shutil
import unittest
import uuid
import numpy as np
from cache_control import CacheManager
from engine import CACHE, Recording
from merge import MergedRecording
from server import Handler


class RefinementTests(unittest.TestCase):
    def setUp(self):
        self.folder = CACHE / ("refinement-tests-" + uuid.uuid4().hex)
        self.folder.mkdir()
        self.recordings = []
        self.x = np.arange(12000)
        a = (self.x % 100 < 50).astype(float) * 5
        phase = 0.12 + 0.1 * np.sin(self.x / 1000)
        b = ((self.x / 100 - phase) % 1 < 0.5).astype(float) * 5
        z = ((self.x - 150) % 1000 < 20).astype(float) * 5
        self.full = np.column_stack((self.x / 10000, a, 5 - a, b, 5 - b, z, 5 - z))
        self.paths = [self.folder / name for name in ("A.csv", "B.csv", "C.csv")]
        self.write()

    def tearDown(self):
        CacheManager.close_recordings(self.recordings)
        for recording in self.recordings:
            if recording.dir.parent.resolve() == CACHE.resolve():
                shutil.rmtree(recording.dir)
        if self.folder.parent.resolve() == CACHE.resolve():
            shutil.rmtree(self.folder)

    def write(self, missing_b=False, missing_a=False, extra_b=False, poor_reference=False):
        full = self.full.copy()
        if missing_b:
            full[3500:3700, 3], full[3500:3700, 4] = 0, 5
        if missing_a:
            full[3000:3100, 1], full[3000:3100, 2] = 0, 5
        if extra_b:
            full[4080:4085, 3], full[4080:4085, 4] = 5, 0
        for path, columns, header in (
            (self.paths[0], [0, 1, 2, 5, 6], "Time_s,A,-A,Z,-Z"),
            (self.paths[1], [0, 3, 4, 5, 6], "Time_s,B,-B,Z,-Z"),
        ):
            np.savetxt(path, full[:, columns], delimiter=",", header=header, comments="", fmt="%.17g")
        t = np.arange(4000) / 8000
        a = (np.arange(4000) % 80 < 40).astype(float)
        b = ((np.arange(4000) - 60) % 80 < 40).astype(float)
        if poor_reference:
            b[2000:] = 1 - b[2000:]
        np.savetxt(
            self.paths[2], np.column_stack(((t + 9) * 1000, a, 1 - a, b, 1 - b)),
            delimiter=",", header="Time_ms,A,-A,B,-B", comments="", fmt="%.17g",
        )

    def merge(self, mode="refine"):
        r = MergedRecording(*self.paths[:2], {"alignment_mode": mode, "slew_mode": "none"}, path_c=self.paths[2])
        self.recordings.append(r)
        r.run()
        return r

    def test_explicit_check_mode_keeps_the_existing_composite(self):
        r = self.merge("check")
        plain = MergedRecording(*self.paths[:2], {"slew_mode": "none"})
        self.recordings.append(plain)
        plain.run()
        self.assertIsNone(r.error)
        self.assertIsNone(plain.error)
        np.testing.assert_array_equal(r.data, plain.data)
        self.assertNotIn("refinement", r.merge_info)

    def test_reference_refines_variable_phase_without_changing_a_or_z(self):
        before = self.merge("check")
        hashes = [hashlib.sha256(p.read_bytes()).hexdigest() for p in self.paths]
        r = self.merge()
        self.assertIsNone(r.error)
        self.assertTrue(r.ready)
        meta = r.merge_info["refinement"]
        self.assertTrue(meta["applied"])
        self.assertFalse(meta["independent_validation"])
        self.assertAlmostEqual(meta["target_phase_degrees"], -90, delta=1)
        self.assertAlmostEqual(r.phase_degrees, -90, delta=360 / 100)
        self.assertGreater(r.phase_coherence, 0.999)
        self.assertGreater(meta["max_shift_samples"], 10)
        lo = np.searchsorted(before.data[:, 0], r.start)
        hi = lo + r.count
        np.testing.assert_array_equal(r.data[:, [0, 1, 2, 5, 6]], before.data[lo:hi, [0, 1, 2, 5, 6]])
        np.testing.assert_allclose(r.data[:, 3] + r.data[:, 4], 5, atol=1e-12)
        self.assertEqual(r.missing_pulse_totals, {"A": 0, "B": 0})
        self.assertEqual(hashes, [hashlib.sha256(p.read_bytes()).hexdigest() for p in self.paths])

    def test_first_target_uses_the_a_cycle_straddling_z(self):
        r = self.merge()
        self.assertIsNone(r.error)
        first = int(np.searchsorted(r.edges[7], r.validated_z[0], side="left"))
        # Z=150; A cycles100..200; phase.75 gives B=175, not B=275.
        t = float(r.data[r.edges[7][first], 0])
        self.assertAlmostEqual(t, (175 - 150) / 10000, delta=1 / 10000)

    def test_internal_missing_b_pulses_are_not_filled_in(self):
        self.write(missing_b=True)
        r = self.merge()
        self.assertIsNone(r.error)
        self.assertEqual(r.missing_pulse_totals, {"A": 0, "B": 2})
        rows = r.pulse_counts()["rows"]
        self.assertEqual(sum(row["missing"]["B"] for row in rows), 2)
        self.assertTrue(any(row["missing"]["B"] == 2 for row in rows))
        self.assertAlmostEqual(r.phase_degrees, -90, delta=360 / 100)

    def test_missing_a_and_extra_b_correspondence_are_rejected(self):
        for kwargs, message in (({"missing_a": True}, "A has missing"), ({"extra_b": True}, "B pulse counts")):
            with self.subTest(kwargs=kwargs):
                self.write(**kwargs)
                r = self.merge()
                self.assertIsNotNone(r.error)
                self.assertIn(message, r.error)
                self.assertNotIn("refinement", r.merge_info)

    def test_unstable_reference_is_rejected_without_silent_fallback(self):
        self.write(poor_reference=True)
        r = self.merge()
        self.assertIn("stable, sufficiently sampled", r.error)
        self.assertFalse(r.ready)

    def test_refinement_requires_a_reference_and_known_mode(self):
        with self.assertRaisesRegex(ValueError, "requires"):
            MergedRecording(*self.paths[:2], {"alignment_mode": "refine"})
        with self.assertRaisesRegex(ValueError, "mode"):
            MergedRecording(*self.paths[:2], {"alignment_mode": "other"}, path_c=self.paths[2])

    def test_export_round_trip_contains_refined_six_channels(self):
        r = self.merge()
        self.assertIsNone(r.error)
        path, _ = Handler.prepare_export(r, {"kind": "csv"})
        imported = Recording(path, {"slew_mode": "none"})
        self.recordings.append(imported)
        imported.run()
        self.assertIsNone(imported.error)
        np.testing.assert_array_equal(imported.data, r.data)
        self.assertAlmostEqual(imported.phase_degrees, -90, delta=360 / 100)

    def test_cancellation_does_not_commit_a_partial_refinement(self):
        r = MergedRecording(*self.paths[:2], {"alignment_mode": "refine"}, path_c=self.paths[2])
        self.recordings.append(r)
        def check():
            if r.stage == "Resampling B timing from the selected A/B reference":
                raise ValueError("Refinement cancelled for test")
        r.check = check
        r.run()
        self.assertIn("Refinement cancelled", r.error)
        self.assertFalse(r.ready)
        self.assertFalse((r.dir / "refined-samples.bin").exists())
        self.assertTrue(np.isfinite(r.data[0]).all())


if __name__ == "__main__":
    unittest.main()
