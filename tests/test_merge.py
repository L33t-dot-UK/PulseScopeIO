import unittest
import wave
import numpy as np
from engine import Recording, CACHE
from merge import MergedRecording
from server import Handler, reanalyse, JOBS
import time


class MergeTests(unittest.TestCase):
    def setUp(self):
        self.folder = CACHE / "merge-tests"
        self.folder.mkdir(exist_ok=True)
        self.x = np.arange(5000)
        a = ((self.x % 100) < 50).astype(float) * 5
        b = (((self.x - 25) % 100) < 50).astype(float) * 5
        z = (((self.x - 100) % 1000) < 20).astype(float) * 5
        self.full = np.column_stack((self.x / 10000, a, 5 - a, b, 5 - b, z, 5 - z))

    def write(self, role, offset=0, header=None, data=None):
        channels = [0, 1, 2, 5, 6] if role == "A" else [0, 3, 4, 5, 6]
        v = np.array(
            (self.full if data is None else data)[offset:, channels], copy=True
        )
        v[:, 0] += 10 if role == "B" else -2
        path = self.folder / (role + ".csv")
        np.savetxt(
            path,
            v,
            delimiter=",",
            header=header
            or ("Time_s,A,-A,Z,-Z" if role == "A" else "Time_s,B,-B,Z,-Z"),
            comments="",
            fmt="%.17g",
        )
        return path

    def merge(self, **kwargs):
        r = MergedRecording(self.write("A"), self.write("B", 37), kwargs)
        r.run()
        self.assertIsNone(r.error)
        self.assertTrue(r.ready)
        return r

    def test_alignment_and_export_roundtrip(self):
        r = self.merge()
        self.assertEqual(r.merge_info["offset_samples"], -37)
        self.assertEqual(r.count, 4963)
        self.assertEqual(r.present, list(range(6)))
        np.testing.assert_array_equal(r.data[:, 1:], self.full[37:, 1:])
        self.assertAlmostEqual(r.data[63, 0], 0)
        self.assertEqual(r.merge_info["max_z_mismatch_samples"], 0)
        self.assertGreater(r.merge_info["matched_z"], 1)
        path, _ = Handler.prepare_export(r, {"kind": "csv", "csv_mode": "original"})
        self.assertEqual(path.read_text().splitlines()[0], "Time_s,A,-A,B,-B,Z,-Z")
        imported = Recording(path)
        imported.ingest()
        np.testing.assert_array_equal(imported.data, r.data)
        self.assertEqual(imported.present, list(range(6)))
        self.assertEqual(len(r.plot(r.start, r.end)["points"][0]), 7)

    def test_generic_scope_headings_and_sample_index(self):
        a = self.write("A", header="Time_s,CH1,CH2,CH3,CH4")
        b = self.write("B", 37, header="Time_s,Channel1,Channel2,Channel3,Channel4")
        r = MergedRecording(a, b, {"timecol": "index"})
        r.ingest()
        self.assertFalse(r.has_time)
        self.assertEqual(r.merge_info["offset_samples"], -37)
        np.testing.assert_array_equal(r.data[:, 1:], self.full[37:, 1:])

    def test_missing_z_rejected_and_temporary_files_removed(self):
        bad = self.full.copy()
        bad[:, 5], bad[:, 6] = 0, 5
        a, b = self.write("A"), self.write("B", data=bad)
        before = set(CACHE.iterdir())
        r = MergedRecording(a, b)
        with self.assertRaisesRegex(ValueError, "no complete Z"):
            r.ingest()
        self.assertEqual(set(CACHE.iterdir()) - before, {r.dir})

    def test_different_sample_rate_and_irregular_time_rejected(self):
        a = self.write("A")
        for irregular in (False, True):
            bad = self.full.copy()
            if irregular:
                bad[50, 0] += 0.00001
            else:
                bad[:, 0] *= 2
            b = self.write("B", data=bad)
            with self.assertRaisesRegex(ValueError, "sample"):
                MergedRecording(a, b).ingest()

    def test_partial_initial_z_is_not_used(self):
        r = MergedRecording(self.write("A", 105), self.write("B", 110))
        r.ingest()
        self.assertEqual(r.merge_info["offset_samples"], -5)
        self.assertAlmostEqual(r.data[990, 0], 0)

    def test_later_index_drift_is_corrected_per_revolution(self):
        bad = self.full.copy()
        z = (((self.x - 100) % 1002) < 20).astype(float) * 5
        bad[:, 5], bad[:, 6] = z, 5 - z
        b = ((((self.x - 100) / 100.2 - 0.25) % 1) < 0.5).astype(float) * 5
        bad[:, 3], bad[:, 4] = b, 5 - b
        r = MergedRecording(self.write("A"), self.write("B", data=bad))
        r.run()
        self.assertIsNone(r.error)
        self.assertEqual(r.merge_info["max_z_mismatch_samples"], 8)
        self.assertTrue(r.merge_info["resampled_b"])
        # Phase is resolved to one output sample (3.6 degrees at 100 samples/cycle).
        self.assertAlmostEqual(r.phase_degrees, 90, delta=360 / 100)
        self.assertEqual(r.missing_pulse_totals, {"A": 0, "B": 0})
        self.assertEqual(r.flag_count, 0)

    def drift_data(self, losses=(), missing_z=(), count=100000):
        x = np.arange(count)
        a = ((x % 100) < 50).astype(float) * 5
        z = (((x - 100) % 1000) < 20).astype(float) * 5
        phase = (x - 100) / 100.2
        b = (((phase - 0.25) % 1) < 0.5).astype(float) * 5
        b[np.isin(np.floor(phase - 0.25).astype(int), losses)] = 0
        bz = ((phase % 10) < 0.2).astype(float) * 5
        bz[np.isin(np.floor(phase / 10).astype(int), missing_z)] = 0
        first = np.column_stack((x / 10000, a, 5 - a, b, 5 - b, z, 5 - z))
        second = first.copy()
        second[:, 5], second[:, 6] = bz, 5 - bz
        return first, second

    def test_small_speed_difference_does_not_create_phase_or_missing_errors(self):
        a, b = self.drift_data()
        source_b = Recording(self.write("B", data=b), {"capture_role": "B"})
        source_b.run()
        self.assertIsNone(source_b.error)
        self.assertEqual(source_b.missing_pulse_totals, {"B": 0})
        r = MergedRecording(self.write("A", data=a), self.write("B", data=b))
        r.run()
        self.assertIsNone(r.error)
        self.assertTrue(r.merge_info["resampled_b"])
        self.assertEqual(r.merge_info["matched_z"], 100)
        # Phase is resolved to one output sample (3.6 degrees at 100 samples/cycle).
        self.assertAlmostEqual(r.phase_degrees, 90, delta=360 / 100)
        self.assertEqual(r.missing_pulse_totals, {"A": 0, "B": 0})
        self.assertEqual(r.flag_count, 0)
        # A thresholded edge has one output-sample uncertainty (1% of a cycle).
        self.assertAlmostEqual(r.metrics[2]["duty"], 50, delta=0.5)

    def test_real_missing_b_pulses_survive_speed_alignment(self):
        a, b = self.drift_data(losses=(150, 151))
        r = MergedRecording(self.write("A", data=a), self.write("B", data=b))
        r.run()
        self.assertIsNone(r.error)
        self.assertEqual(r.missing_pulse_totals, {"A": 0, "B": 2})
        self.assertTrue(
            any(e["channel"] == "B" and e["missing"] == 2 for e in r.errors)
        )
        # Phase is resolved to one output sample (3.6 degrees at 100 samples/cycle).
        self.assertAlmostEqual(r.phase_degrees, 90, delta=360 / 100)

    def test_changing_speed_is_corrected_across_disk_blocks(self):
        a, b = self.drift_data(count=270000)
        x = np.arange(len(b))
        periods = np.resize(np.array([1002, 998, 1008, 993, 1004]), 280)
        anchors = np.r_[100, 100 + np.cumsum(periods)]
        interval = np.clip(
            np.searchsorted(anchors, x, side="right") - 1, 0, len(periods) - 1
        )
        phase = 10 * (interval + (x - anchors[interval]) / periods[interval])
        pulse = (((phase - 0.25) % 1) < 0.5).astype(float) * 5
        index = ((phase % 10) < 0.2).astype(float) * 5
        b[:, 3], b[:, 4], b[:, 5], b[:, 6] = pulse, 5 - pulse, index, 5 - index
        r = MergedRecording(self.write("A", data=a), self.write("B", data=b))
        r.run()
        self.assertIsNone(r.error)
        self.assertGreater(r.count, 2 * 131072)
        self.assertTrue(r.merge_info["resampled_b"])
        # Phase is resolved to one output sample (3.6 degrees at 100 samples/cycle).
        self.assertAlmostEqual(r.phase_degrees, 90, delta=360 / 100)
        self.assertEqual(r.missing_pulse_totals, {"A": 0, "B": 0})
        self.assertEqual(r.flag_count, 0)

    def test_missing_index_does_not_shift_later_revolution_pairs(self):
        a, b = self.drift_data(missing_z=(4,))
        r = MergedRecording(self.write("A", data=a), self.write("B", data=b))
        r.run()
        self.assertIsNone(r.error)
        self.assertEqual(r.merge_info["matched_z"], 99)
        self.assertEqual(r.merge_info["unmatched_z"], 1)
        self.assertIn("Unmatched Z", r.merge_info["warning"])
        self.assertEqual(r.missing_pulse_totals, {"A": 0, "B": 0})
        # Phase is resolved to one output sample (3.6 degrees at 100 samples/cycle).
        self.assertAlmostEqual(r.phase_degrees, 90, delta=360 / 100)
        self.assertEqual(r.flag_count, 0)

    def test_ambiguous_index_spacing_is_rejected(self):
        a, b = self.drift_data(count=10000)
        # An extra complete pulse half way between revolutions cannot be
        # assigned a revolution number reliably.
        b[600:620, 5], b[600:620, 6] = 5, 0
        r = MergedRecording(self.write("A", data=a), self.write("B", data=b))
        r.run()
        self.assertIn("Z pulse spacing is ambiguous", r.error)

    def test_mixed_csv_wav_incompatible_levels_are_rejected(self):
        path = self.folder / "B.wav"
        with wave.open(str(path), "wb") as w:
            w.setnchannels(4)
            w.setsampwidth(2)
            w.setframerate(10000)
            w.writeframes(
                (self.full[:, [3, 4, 5, 6]] / 5 * 20000).astype("<i2").tobytes()
            )
        r = MergedRecording(self.write("A"), path)
        r.run()
        self.assertIn("incompatible signal levels", r.error)

    def test_four_channel_wav(self):
        paths = []
        for role, columns, offset in (("A", [1, 2, 5, 6], 0), ("B", [3, 4, 5, 6], 37)):
            path = self.folder / (role + ".wav")
            samples = (self.full[offset:, columns] / 5 * 20000).astype("<i2")
            with wave.open(str(path), "wb") as w:
                w.setnchannels(4)
                w.setsampwidth(2)
                w.setframerate(10000)
                w.writeframes(samples.tobytes())
            paths.append(path)
        r = MergedRecording(*paths)
        r.run()
        self.assertIsNone(r.error)
        self.assertEqual(r.merge_info["offset_samples"], -37)
        np.testing.assert_array_equal(
            r.data[:, 1:], self.full[37:, 1:] / 5 * 20000 / 32768
        )

    def test_reanalysis_keeps_merge_and_export(self):
        old = self.merge()
        result = reanalyse(old, {"low": 2, "high": 3})
        r = JOBS[result["id"]]
        for _ in range(500):
            if r.ready or r.error:
                break
            time.sleep(0.01)
        self.assertIsNone(r.error)
        self.assertTrue(r.ready)
        self.assertEqual(r.merge_info, old.merge_info)
        self.assertEqual(r.merge_paths, old.merge_paths)
        path, _ = Handler.prepare_export(r, {"kind": "csv"})
        self.assertEqual(len(path.read_text().splitlines()[0].split(",")), 7)

    def test_multiple_disk_blocks_preserve_every_channel(self):
        x = np.arange(270000)
        a = (x % 100 < 50).astype(float) * 5
        b = ((x - 25) % 100 < 50).astype(float) * 5
        z = ((x - 100) % 1000 < 20).astype(float) * 5
        full = np.column_stack((x / 10000, a, 5 - a, b, 5 - b, z, 5 - z))
        r = MergedRecording(self.write("A", data=full), self.write("B", 37, data=full))
        r.ingest()
        np.testing.assert_array_equal(r.data[:, 1:], full[37:, 1:])
        self.assertEqual(r.count, 269963)

    def test_bad_b_duty_remains_visible_after_merge(self):
        bad = self.full.copy()
        pulse = (((self.x - 25) % 100) < 30).astype(float) * 5
        bad[:, 3], bad[:, 4] = pulse, 5 - pulse
        r = MergedRecording(self.write("A"), self.write("B", 37, data=bad))
        r.run()
        self.assertIsNone(r.error)
        np.testing.assert_array_equal(r.data[:, 3:5], bad[37:, 3:5])
        self.assertGreater(r.flag_count, 0)
        self.assertTrue(
            any(m["oos"] > 0 for m in r.display_metrics() if m["c"] in (2, 3))
        )


if __name__ == "__main__":
    unittest.main()
