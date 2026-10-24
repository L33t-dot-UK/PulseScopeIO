"""Regression checks backed by deterministic captures and saved online DOM results."""

import json, unittest, shutil
from pathlib import Path
from unittest.mock import patch
import numpy as np
import engine
from engine import Recording
from synthetic import generate_capture
from compatibility import receiver_states, smooth_chunk, time_unit, adaptive_radius


class ParityTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.folder = engine.CACHE / "parity-tests"
        cls.folder.mkdir(exist_ok=True)
        cls.recordings = {}
        for kind in ("clean", "missing", "duty", "jitter", "reversed", "z-glitch"):
            path = cls.folder / (kind + ".csv")
            if kind == "missing":
                # Preserve the exact recording used for the saved online oracle.
                shutil.copyfile(engine.ROOT / "audit/fixtures/missing.csv", path)
            else:
                generate_capture(path, kind, ppr=32, spp=160)
            r = Recording(
                path, {"low": 2.5, "high": 2.5, "rpm": 60, "slew_mode": "online"}
            )
            r.run()
            if r.error:
                raise RuntimeError(r.error)
            cls.recordings[kind] = r

    def test_saved_online_measurements(self):
        def numeric(cell):
            tokens = cell.replace("%", "").split()
            value = float(tokens[0])
            unit = tokens[1] if len(tokens) > 1 else ""
            return value * {"ns": 1e-9, "µs": 1e-6, "ms": 1e-3}.get(unit, 1)

        for kind, r in self.recordings.items():
            oracle = json.loads(
                (engine.ROOT / "audit" / ("online-" + kind + ".json")).read_text(
                    encoding="utf8"
                )
            )
            rows = oracle["measurements"].splitlines()[1:]
            metrics = [m for m in r.display_metrics() if m["c"] < 6]
            self.assertEqual(len(rows), len(metrics))
            for row, m in zip(rows, metrics):
                cells = row.split("\t")
                for column, key in enumerate(
                    (
                        "rise",
                        "fall",
                        "high",
                        "low",
                        "period",
                        "ppr",
                        "frequency",
                        "duty",
                        "oos",
                        "edges",
                    ),
                    1,
                ):
                    if m["c"] >= 4:
                        continue  # Z now uses validated time-based index measurements.
                    if cells[column] == "—":
                        continue
                    expected = numeric(cells[column])
                    actual = m[key]
                    with self.subTest(kind=kind, channel=m["channel"], metric=key):
                        self.assertIsNotNone(actual)
                        self.assertAlmostEqual(
                            actual,
                            expected,
                            delta=(
                                0.050001
                                if key == "duty"
                                else max(abs(expected) * 0.001, 1e-12)
                            ),
                        )

    def test_clean_has_no_false_errors(self):
        r = self.recordings["clean"]
        self.assertEqual(r.flag_count, 0)
        self.assertEqual(r.ppr, 32)
        self.assertAlmostEqual(r.phase_degrees, 90)

    def test_faults_and_six_eyes(self):
        r = self.recordings["duty"]
        self.assertEqual(r.diagnostics()["oos_cycles"], 95)
        e = r.next_fault()
        self.assertEqual(e["zoom_cycles"], 16)
        self.assertIn("duty", e["kind"])
        view = r.plot(e["zoom_start"], e["zoom_end"])
        self.assertEqual(len(view["eyes"]), 6)
        self.assertTrue(view["eye_oos"]["0"])
        self.assertFalse(any(view["eye_oos"]["4"]))

    def test_missing_automatic_and_manual(self):
        r = self.recordings["missing"]
        self.assertEqual(r.reference_ppr, 31)
        self.assertEqual(r.error_count, 1)
        self.assertEqual(r.ppr, 30.5)
        r = Recording(r.source, {"low": 2.5, "high": 2.5, "reference": True, "ppr": 32})
        r.run()
        self.assertIsNone(r.error)
        self.assertEqual(r.error_count, 2)
        self.assertEqual(sum(e["missing"] for e in r.errors), 3)
        self.assertEqual(r.ppr, 30.5)

    def test_missing_b_phase(self):
        a = np.loadtxt(self.recordings["missing"].source, delimiter=",", skiprows=1)
        a[:, [1, 2, 3, 4]] = a[:, [3, 4, 1, 2]]
        path = self.folder / "missing-b.csv"
        np.savetxt(path, a, delimiter=",", header="Time_s,A,-A,B,-B,Z,-Z", comments="")
        r = Recording(path, {"low": 2.5, "high": 2.5})
        r.run()
        self.assertIsNone(r.error)
        self.assertEqual(r.errors[0]["channel"], "B")

    def test_z_noise_and_duty_exclusion(self):
        r = self.recordings["z-glitch"]
        self.assertEqual(r.z_rejected, 1)
        self.assertEqual(r.ppr, 32)
        # Raw glitch candidates are now reported separately from Z duty faults.
        self.assertEqual(r.flag_count, 2)
        self.assertTrue(all(e['kind']=='short raw pulse candidate' for e in r.faults(r.start,r.end)['events']))

    def test_reverse_position(self):
        r = self.recordings["reversed"]
        self.assertEqual(r.phase_degrees, -90)
        self.assertLess(r.position(0.04)["count"], 0)
        self.assertAlmostEqual(r.position(1)["angle"], 0)

    def test_span_and_full_metrics_independent(self):
        r = self.recordings["clean"]
        m = r.span_measurements(0.1, 0.7)
        self.assertLess(m["count"], r.count)
        self.assertEqual(r.count, 15362)
        self.assertEqual(m["diagnostics"]["sample_rate"], 5120)
        self.assertAlmostEqual(m["diagnostics"]["phase_degrees"], 90)
        self.assertEqual(r.metrics[0]["edges"], 96)

    def test_explicit_units_and_sample_scaling(self):
        a = np.loadtxt(self.recordings["clean"].source, delimiter=",", skiprows=1)
        a[:, 0] *= 1000
        path = self.folder / "milliseconds.csv"
        np.savetxt(
            path, a, delimiter=",", header="Time (ms),A,-A,B,-B,Z,-Z", comments=""
        )
        r = Recording(path, {"low": 2.5, "high": 2.5})
        r.run()
        self.assertIsNone(r.error)
        self.assertTrue(r.seconds)
        self.assertAlmostEqual(r.measured_rpm, 60)
        r = Recording(
            self.recordings["clean"].source,
            {"low": 2.5, "high": 2.5, "timecol": "index", "rpm": 60},
        )
        r.run()
        self.assertIsNone(r.error)
        self.assertAlmostEqual(r.diagnostics()["sample_rate"], 5120)
        self.assertAlmostEqual(r.display_metrics()[0]["period"], 1 / 32)

    def test_visible_span_matches_online(self):
        r = self.recordings["z-glitch"]
        span = r.span_measurements(0.1, 0.7)
        self.assertEqual(span["count"], 3073)
        self.assertIsNone(span["ppr"])
        self.assertEqual(span["rpm"], 60)
        # Twenty rises delimit nineteen complete local A cycles.
        self.assertEqual(span["diagnostics"]["phase_pairs"], 19)
        self.assertEqual(span["diagnostics"]["oos_cycles"], 0)
        self.assertAlmostEqual(span["diagnostics"]["mean_duty"], 49.9, delta=0.05)
        self.assertTrue((engine.ROOT / "audit/online-z-glitch-span.json").is_file())

    def test_online_receiver_hold(self):
        state, decisive = receiver_states(
            np.array([0, 1, 1, 0, 0]), np.array([1, 0, 1, 0, 1]), 0
        )
        np.testing.assert_array_equal(state, [0, 1, 1, 1, 0])
        np.testing.assert_array_equal(decisive, [1, 1, 0, 0, 1])

    def test_centered_average_and_adaptive_radius(self):
        np.testing.assert_allclose(
            smooth_chunk(np.arange(7.0), 0, 7, 3), [0.5, 1, 2, 3, 4, 5, 5.5]
        )
        state = np.tile(np.r_[np.zeros(8), np.ones(8)], 20).astype("u1")
        self.assertEqual(adaptive_radius(state, 17), 6)

    def test_chunk_invariance_online_filters(self):
        opts = {
            "low": 2.5,
            "high": 2.5,
            "window": 7,
            "smooth_edges": True,
            "slew_mode": "online",
        }
        baseline = Recording(self.recordings["clean"].source, opts)
        baseline.run()
        self.assertIsNone(baseline.error)
        with patch.object(engine, "BLOCK", 137):
            small = Recording(self.recordings["clean"].source, opts)
            small.run()
        self.assertIsNone(small.error)
        np.testing.assert_array_equal(baseline.logic, small.logic)
        self.assertEqual(baseline.flag_count, small.flag_count)

    def test_generator_deterministic_and_single_ended(self):
        one = self.folder / "single.csv"
        two = self.folder / "same.csv"
        generate_capture(one, "jitter", ppr=32, differential=False)
        generate_capture(two, "jitter", ppr=32, differential=False)
        self.assertEqual(one.read_bytes(), two.read_bytes())
        r = Recording(one, {"low": 2.5, "high": 2.5})
        r.run()
        self.assertIsNone(r.error)
        self.assertEqual(r.present, [0, 2, 4])
        self.assertEqual(r.ppr, 32)

    def test_auto_threshold_candidate_search(self):
        r = Recording(
            self.recordings["clean"].source,
            {"auto": True, "auto_mode": "search", "slew_mode": "online", "rpm": 60},
        )
        r.run()
        self.assertIsNone(r.error)
        self.assertEqual(r.flag_count, 0)
        self.assertEqual(r.ppr, 32)
        self.assertEqual(r.low, r.high)
        self.assertGreater(r.low, 2)
        self.assertLess(r.high, 3)

    def test_disk_z_width_validation(self):
        r = self.recordings["z-glitch"]
        raw = r.edges[8]
        fi = np.searchsorted(r.falls[8], raw, side="right")
        valid = raw[fi < len(r.falls[8])]
        filtered = r.filter_many_z(valid, r.falls[8])
        self.assertEqual(len(filtered), 3)
        self.assertEqual(r.z_rejected, 1)

    def test_units(self):
        for header, scale in [
            ("Time_s", 1),
            ("Time (ms)", 0.001),
            ("Time_us", 1e-6),
            ("Time [ns]", 1e-9),
        ]:
            self.assertEqual(time_unit(header)[0], scale)
        self.assertIsNone(time_unit("Time")[0])


if __name__ == "__main__":
    unittest.main()
