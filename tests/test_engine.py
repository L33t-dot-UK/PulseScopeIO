import unittest, tempfile, csv, wave, struct
from pathlib import Path
import numpy as np
import engine
from engine import Recording, hysteresis
from generate_examples import generate


class AnalysisTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.folder = engine.CACHE / "test-fixtures"
        cls.folder.mkdir(exist_ok=True)
        generate(cls.folder)

    @classmethod
    def tearDownClass(cls):
        pass

    def load(self, name="clean", **options):
        r = Recording(self.folder / (name + ".csv"), {"low": 0, "high": 0, **options})
        r.run()
        self.assertIsNone(r.error, r.error)
        return r

    def test_hysteresis(self):
        np.testing.assert_array_equal(
            hysteresis(np.array([-1, 2, 1, 0, -1]), -0.5, 1.5), [0, 1, 1, 1, 0]
        )
        np.testing.assert_array_equal(hysteresis(np.array([0, 1, 0]), 0, 0), [0, 1, 0])

    def test_scope_in_units_time_column_is_detected(self):
        x = np.arange(2000)
        a = (x % 100 < 50).astype(float) * 5
        b = ((x - 25) % 100 < 50).astype(float) * 5
        z = ((x - 20) % 500 < 5).astype(float) * 5
        for unit, multiplier in (("s", 1), ("ms", 1e3), ("us", 1e6), ("ns", 1e9)):
            with self.subTest(unit=unit):
                path = self.folder / f"scope-{unit}.csv"
                np.savetxt(
                    path,
                    np.column_stack(
                        ((x / 10000 - 0.1) * multiplier, a, 5 - a, b, 5 - b, z, 5 - z)
                    ),
                    delimiter=",",
                    header=f"in {unit},A,-A,B,-B,Z,-Z",
                    comments="",
                    fmt="%.17g",
                )
                r = Recording(path, {"slew_mode": "none"})
                r.run()
                self.assertIsNone(r.error)
                self.assertTrue(r.seconds)
                self.assertTrue(r.has_time)
                self.assertAlmostEqual(r.data[1, 0] - r.data[0, 0], 0.0001)
                self.assertAlmostEqual(r.diagnostics()["sample_rate"], 10000)
                self.assertAlmostEqual(r.phase_degrees, 90)
                self.assertAlmostEqual(r.display_metrics()[0]["frequency"], 100)

    def test_clean_ppr(self):
        r = self.load()
        self.assertEqual(r.ppr, 32)
        self.assertEqual(r.error_count, 0)
        self.assertAlmostEqual(next(m["duty"] for m in r.metrics if m["c"] == 6), 50)

    def test_8192_ppr(self):
        k = np.arange(8192 * 4 * 3 + 1)
        a = (k % 4 < 2).astype(int)
        b = ((k - 1) % 4 < 2).astype(int)
        z = (k % (8192 * 4) < 1).astype(int)
        path = self.folder / "8192.csv"
        np.savetxt(
            path,
            np.c_[a, 1 - a, b, 1 - b, z, 1 - z],
            delimiter=",",
            header="A,-A,B,-B,Z,-Z",
            comments="",
            fmt="%d",
        )
        r = Recording(path, {"low": 0, "high": 0})
        r.run()
        self.assertIsNone(r.error)
        self.assertEqual(r.ppr, 8192)

    def test_missing(self):
        r = self.load("missing-pulse", reference=True, ppr=32)
        self.assertEqual(r.error_count, 1)
        self.assertEqual(r.errors[0]["missing"], 1)

    def test_duty(self):
        r = self.load("duty-failure")
        self.assertGreater(next(m["oos"] for m in r.metrics if m["c"] == 6), 0)

    def test_z_index_is_not_duty_fault(self):
        r = self.load()
        self.assertEqual(r.flag_count, 0)
        self.assertTrue(all(m["oos"] == 0 for m in r.metrics if m["c"] in (4, 5, 8)))

    def test_recovered_duty_ignores_common_mode(self):
        k = np.arange(4000)
        a = (k % 16 < 8).astype(float)
        b = ((k - 4) % 16 < 8).astype(float)
        z = (k % 512 < 4).astype(float)
        common = 3 + np.sin(k / 17)
        path = self.folder / "common-mode.csv"
        np.savetxt(
            path,
            np.c_[a + common, 1 - a + common, b, 1 - b, z, 1 - z],
            delimiter=",",
            header="A,-A,B,-B,Z,-Z",
            comments="",
        )
        r = Recording(path, {"low": 0, "high": 0, "receiver_mode": "differential"})
        r.run()
        self.assertIsNone(r.error)
        self.assertEqual(r.flag_count, 0)
        self.assertAlmostEqual(next(m["duty"] for m in r.metrics if m["c"] == 0), 50)

    def test_eye_remains_available_at_tight_zoom(self):
        r = self.load()
        p = r.plot(0.0051, 0.0052)
        for c in ("0", "1", "2", "3"):
            self.assertTrue(p["eyes"][c])
            self.assertIn("Nearby", p["eye_scope"][c])

    def test_real_duty_faults_are_not_duplicated(self):
        r = self.load("duty-failure")
        events = r.faults(r.start, r.end, 1000)["events"]
        self.assertTrue(events)
        self.assertEqual({e["channel"] for e in events}, {"Rx A"})

    def test_chunks(self):
        old = engine.BLOCK
        try:
            baseline = self.load()
            engine.BLOCK = 37
            r = self.load()
            np.testing.assert_array_equal(baseline.logic, r.logic)
            self.assertEqual(baseline.ppr, r.ppr)
            [
                self.assertAlmostEqual(m["duty"], n["duty"])
                for m, n in zip(baseline.metrics, r.metrics)
            ]
        finally:
            engine.BLOCK = old

    def test_smoothing_chunk_consistency(self):
        old = engine.BLOCK
        try:
            a = self.load(window=5, smooth_edges=True)
            engine.BLOCK = 37
            b = self.load(window=5, smooth_edges=True)
            np.testing.assert_array_equal(a.logic, b.logic)
        finally:
            engine.BLOCK = old

    def test_resolved_ramp(self):
        path = self.folder / "ramps.csv"
        phase = np.arange(2000) % 100
        v = np.where(
            phase < 50, np.minimum(phase / 10, 1), np.maximum(1 - (phase - 50) / 10, 0)
        )
        np.savetxt(
            path,
            np.c_[np.arange(2000) * 0.001, v, 1 - v],
            delimiter=",",
            header="Time_s,A,-A",
            comments="",
        )
        r = Recording(path, {"low": 0, "high": 0, "receiver_mode": "differential"})
        r.run()
        self.assertIsNone(r.error)
        self.assertAlmostEqual(
            next(m["rise"] for m in r.metrics if m["c"] == 6), 0.008, places=6
        )

    def test_rpm_replay(self):
        r = self.load(rpm=60)
        t = float(r.data[r.edges[8][0], 0])
        advanced = r.advance(t, 1)
        self.assertAlmostEqual(advanced, float(r.data[r.edges[8][1], 0]))
        self.assertEqual(r.position(advanced)["angle"], 0)

    def test_fault_index(self):
        r = self.load("duty-failure")
        result = r.faults(r.start, r.end, 2)
        self.assertGreater(result["count"], 2)
        self.assertEqual(len(result["events"]), 2)

    def test_fault_navigation_and_extents(self):
        r = self.load("missing-pulse", reference=True, ppr=32)
        first = r.next_fault()
        self.assertIsNotNone(first)
        current = first
        seen = []
        for _ in range(r.flag_count):
            seen.append(current)
            self.assertGreater(current["end_time"], current["time"])
            self.assertEqual(current["zoom_cycles"], 16)
            self.assertGreater(current["zoom_end"], current["zoom_start"])
            current = r.next_fault(current["time"], current["id"])
        self.assertEqual(current["id"], first["id"])
        self.assertEqual(len({e["id"] for e in seen}), r.flag_count)
        self.assertTrue(
            any(e["kind"] == "missing pulses" and e["missing"] == 1 for e in seen)
        )
        self.assertTrue(any("duty" in e["kind"] for e in seen))

    def test_overview_envelope(self):
        r = self.load()
        r.overview_stride = 8
        # Exercise the overview path on the real 1024-bin overview with a larger fixture.
        r.overview_stride = 1024
        p = r.plot(r.start, r.end, 100)
        self.assertEqual(p["points"][0][2][0], 0)
        self.assertEqual(p["points"][0][3][0], 1)

    def test_position_reset_reverse(self):
        r = self.load()
        z = r.edges[8][0]
        self.assertEqual(r.position(float(r.data[z, 0]))["angle"], 0)
        rev = self.load("reversed")
        self.assertGreater(r.position(0.005)["count"], 0)
        self.assertLess(rev.position(0.005)["count"], 0)

    def test_expected_position_follows_quadrature(self):
        for name, sign in (("clean", 1), ("reversed", -1)):
            for rpm in (None, 60):
                r = self.load(name, **({"rpm": rpm} if rpm else {}))
                z = r.edges[8]
                t0 = float(r.data[z[0], 0])
                t1 = float(r.data[z[1], 0])
                p = r.position(t0 + (t1 - t0) * 0.2)
                self.assertEqual(np.sign(p["count"]), sign)
                self.assertAlmostEqual(p["expected"], (sign * 72) % 360, delta=1)
                self.assertEqual(r.position(t0)["expected"], 0)

    def test_expected_clock_integrates_reversal_without_counting_losses(self):
        r = Recording.__new__(Recording)
        r.data = np.column_stack((np.arange(11, dtype=float), np.zeros(11)))
        r.count = 11
        r.start = 0
        r.end = 10
        r.seconds = True
        r.edges = {8: np.array([0, 10])}
        r.replay_edges = np.array([1, 2, 5, 6, 8])
        r.replay_prefix = np.array([0, 1, 2, 3, 2, 1])
        r.replay_clock = np.array([0.0, 1.0, 4.0, 5.0, 3.0])
        r.replay_channel = 6
        r.direction_known = True
        r.replay_ppr = 10
        r.ppr = 10
        r.rpm = 6
        r.options = {"rpm": 6}
        # Gap 2..5 represents missing edges: expected motion still advances.
        self.assertAlmostEqual(r.position(4)["expected"], 144)
        self.assertEqual(r.position(4)["angle"], 72)
        # Reverse after t=6: expected motion follows the signed time integral.
        self.assertAlmostEqual(r.position(7)["expected"], 180)
        self.assertAlmostEqual(r.position(8)["expected"], 144)
        self.assertEqual(r.position(10)["expected"], 0)

    def test_mean_differential_peak_to_peak(self):
        from unittest.mock import patch

        r = Recording.__new__(Recording)
        r.cancelled = False
        r.present = [0, 1, 2, 3, 4, 5]
        r.data = np.zeros((34, 7))
        rises = np.array([2, 12, 22, 32])
        r.edges = {6: rises, 7: rises, 8: rises}
        for pair in (0, 2, 4):
            # Common mode cancels. Differential cycle peak-to-peak: 4,8,12 V.
            for j, amp in enumerate((1, 2, 3)):
                v = np.r_[np.ones(5), -np.ones(5)] * amp
                r.data[2 + j * 10 : 12 + j * 10, pair + 1] = 10 + v
                r.data[2 + j * 10 : 12 + j * 10, pair + 2] = 10 - v
        for block in (3, 10, 11, 100):
            with patch.object(engine, "BLOCK", block):
                stats = r.mean_differential_vpp()
                self.assertEqual(stats["A/-A"], {"volts": 8.0, "cycles": 3})
                self.assertEqual(stats["Z/-Z"], {"volts": 8.0, "cycles": 3})
                self.assertEqual(
                    r.mean_differential_vpp({6: rises[1:3]})["A/-A"],
                    {"volts": 8.0, "cycles": 1},
                )
        r.present = [0, 1, 2]
        stats = r.mean_differential_vpp({6: np.array([2])})
        self.assertEqual(stats, {"A/-A": {"volts": None, "cycles": 0}})

    def test_z_replay_reference_still_resets_both_markers(self):
        r = self.load(rpm=60)
        z = r.edges[8]
        for edge in z[1:-1]:
            d = r.position(float(r.data[edge, 0]))
            self.assertEqual(d["angle"], 0)
            self.assertEqual(d["expected"], 0)
            self.assertEqual(d["expected_source"], "Z timing")
        start = float(r.data[z[1], 0])
        stop = float(r.data[z[2], 0])
        d = r.position(start + (stop - start) * 0.25)
        self.assertAlmostEqual(d["expected"], 90, delta=0.1)

    def test_plot_bound(self):
        r = self.load()
        p = r.plot(r.start, r.end, 100)
        self.assertLessEqual(len(p["points"]), 100)
        self.assertTrue(p["eyes"])

    def test_sample_index(self):
        path = self.folder / "index.csv"
        np.savetxt(
            path,
            np.array([[0, 1], [1, 0], [0, 1], [1, 0]]),
            delimiter=",",
            header="A,-A",
            comments="",
        )
        r = Recording(path, {"low": 0, "high": 0})
        r.run()
        self.assertIsNone(r.error)
        self.assertFalse(r.seconds)
        self.assertIsNone(r.rpm)

    def test_entered_rpm_and_automatic_fallback(self):
        r = self.load(rpm="120")
        self.assertEqual(r.rpm, 120)
        self.assertEqual(r.rpm_source, "entered")
        r.set_rpm("")
        self.assertEqual(r.rpm, r.measured_rpm)
        self.assertEqual(r.rpm_source, "Z timing")
        r.seconds = False
        r.measured_rpm = None
        r.set_rpm("60")
        self.assertEqual(r.rpm, 60)
        r.set_rpm("")
        self.assertIsNone(r.rpm)

    def test_invalid(self):
        path = self.folder / "bad.csv"
        path.write_text("Time_s,A\n0,1\n0,0\n")
        r = Recording(path)
        r.run()
        self.assertIn("strictly increasing", r.error)

    def test_wav(self):
        path = self.folder / "test.wav"
        with wave.open(str(path), "wb") as w:
            w.setnchannels(6)
            w.setsampwidth(2)
            w.setframerate(1000)
            w.writeframes(np.zeros((100, 6), dtype="<i2").tobytes())
        r = Recording(path, {"low": 0, "high": 0})
        r.run()
        self.assertIsNone(r.error)
        self.assertTrue(r.seconds)
        self.assertEqual(r.count, 100)

    def test_rf64_and_float(self):
        path = self.folder / "rf64.wav"
        size = 1200
        with open(path, "wb") as f:
            f.write(
                b"RF64"
                + struct.pack("<I", 0xFFFFFFFF)
                + b"WAVEds64"
                + struct.pack("<IQQQI", 28, size + 72, size, 100, 0)
            )
            f.write(
                b"fmt "
                + struct.pack("<IHHIIHH", 16, 1, 6, 1000, 12000, 12, 16)
                + b"data"
                + struct.pack("<I", 0xFFFFFFFF)
                + b"\0" * size
            )
        r = Recording(path, {"low": 0, "high": 0})
        r.run()
        self.assertIsNone(r.error)
        self.assertEqual(r.count, 100)
        path = self.folder / "float.wav"
        audio = np.zeros((100, 6), dtype="<f4").tobytes()
        with open(path, "wb") as f:
            f.write(
                b"RIFF"
                + struct.pack("<I", 36 + len(audio))
                + b"WAVEfmt "
                + struct.pack("<IHHIIHH", 16, 3, 6, 1000, 24000, 24, 32)
                + b"data"
                + struct.pack("<I", len(audio))
                + audio
            )
        r = Recording(path, {"low": 0, "high": 0})
        r.run()
        self.assertIsNone(r.error)
        self.assertEqual(r.count, 100)

    def test_bad_numeric_headers_and_thresholds(self):
        for text in ["A,B\n1,nan\n0,1\n", "A,B\n1,0,3\n0,1,2\n", "x,y\n1,0\n0,1\n"]:
            path = self.folder / "invalid-numeric.csv"
            path.write_text(text)
            r = Recording(path)
            r.run()
            self.assertIsNotNone(r.error)
        r = Recording(self.folder / "clean.csv", {"low": 1, "high": 0})
        r.run()
        self.assertIn("low <= high", r.error)

    def test_cancel(self):
        r = Recording(self.folder / "clean.csv")
        r.cancelled = True
        r.run()
        self.assertEqual(r.stage, "Cancelled")

    def test_partial_capture_replay(self):
        k = np.arange(400)
        a = (k % 8 < 4).astype(int)
        b = ((k - 2) % 8 < 4).astype(int)
        z = (k % 80 < 2).astype(int)
        for headings, values in [
            ("A", a[:, None]),
            ("B", b[:, None]),
            ("B,Z", np.c_[b, z]),
            ("-A,-B,Z", np.c_[1 - a, 1 - b, z]),
        ]:
            path = self.folder / "partial.csv"
            np.savetxt(
                path, values, delimiter=",", header=headings, comments="", fmt="%d"
            )
            r = Recording(path, {"low": 0, "high": 0, "rpm": 60})
            r.run()
            self.assertIsNone(r.error)
            r.replay_ppr = 10
            self.assertTrue(r.position(40)["available"])
            self.assertNotEqual(r.position(40)["angle"], r.position(48)["angle"])
            if "Z" in headings:
                self.assertEqual(
                    r.position(float(r.data[r.edges[8][0], 0]))["angle"], 0
                )
            self.assertGreater(r.advance(r.start, 0.1), r.start)

    def test_negative_only_analog_fallback(self):
        phase = np.arange(2000) % 100
        v = np.where(
            phase < 50, np.minimum(phase / 10, 1), np.maximum(1 - (phase - 50) / 10, 0)
        )
        path = self.folder / "negative-only.csv"
        np.savetxt(
            path,
            np.c_[np.arange(2000) * 0.001, 1 - 2 * v],
            delimiter=",",
            header="Time_s,-A",
            comments="",
        )
        r = Recording(path, {"low": 0, "high": 0})
        r.run()
        self.assertIsNone(r.error)
        self.assertAlmostEqual(
            next(m["rise"] for m in r.metrics if m["c"] == 6), 0.008, places=6
        )

    def test_oversized_header(self):
        path = self.folder / "long-header.csv"
        path.write_text("A" * (1024 * 1024 + 1))
        r = Recording(path)
        r.run()
        self.assertIn("header exceeds", r.error)


if __name__ == "__main__":
    unittest.main()
