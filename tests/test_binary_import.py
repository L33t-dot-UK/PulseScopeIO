import unittest
import tempfile
from pathlib import Path
from unittest.mock import patch
import numpy as np
import engine
from engine import Recording
from binary_import import metadata
from cache_control import CacheManager
from server import Handler


class BinaryTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(dir=engine.CACHE)
        self.folder = Path(self.tmp.name)
        self.items = []

    def tearDown(self):
        CacheManager.close_recordings(self.items)
        self.tmp.cleanup()

    def channel(self, name="A", count=2000, start=1.0, stop=1.1999, big=False):
        p = self.folder / (name + ".BIN")
        v = np.where(np.arange(count) % 100 < 50, 50000, 10000).astype(
            ">u2" if big else "<u2"
        )
        v.tofile(p)
        props = dict(
            XStart=start,
            XStop=stop,
            BaseYStart=-1,
            BaseYStop=6,
            RecordLength=count,
            DataFormat="UINT16",
            MSBFirst=int(big),
            InterleavedTraceCount=1,
            MultiChannelExport=1,
        )
        xml = (
            "<Database><Group>"
            + "".join(
                f'<Prop Name="{k}" Value="{v}"'
                + (
                    ' Unit="s"'
                    if k in ("XStart", "XStop")
                    else ' Unit="V"' if k in ("BaseYStart", "BaseYStop") else ""
                )
                + "/>"
                for k, v in props.items()
            )
            + "</Group></Database>"
        )
        p.with_suffix(".BXM").write_text(xml)
        return p

    def recording(self, paths):
        r = Recording(
            next(iter(paths.values())),
            dict(binary_paths={k: str(v) for k, v in paths.items()}, slew_mode="none"),
        )
        self.items.append(r)
        return r

    def test_six_channels_endian_scaling_and_csv_export(self):
        paths = {n: self.channel(n, big=i % 2 == 1) for i, n in enumerate(engine.NAMES)}
        r = self.recording(paths)
        r.run()
        self.assertIsNone(r.error)
        self.assertEqual(r.present, list(range(6)))
        np.testing.assert_allclose(r.data[:, 0], 1 + np.arange(2000) * 0.0001)
        for c in range(6):
            np.testing.assert_allclose(
                r.data[:, c + 1],
                -1 + np.where(np.arange(2000) % 100 < 50, 50000, 10000) * 7 / 65535,
            )
        target, kind = Handler.prepare_export(r, {"kind": "csv"})
        self.assertEqual(target.read_text().splitlines()[0], "Time_s,A,-A,B,-B,Z,-Z")
        data = np.loadtxt(target, delimiter=",", skiprows=1)
        np.testing.assert_allclose(data, r.data)
        target.unlink()

    def test_adjusted_time_bases_and_invalid_length(self):
        for kwargs in (dict(start=1.01), dict(stop=1.3), dict(count=1999)):
            a = self.channel("A")
            b = self.channel("B", **kwargs)
            r = self.recording({"A": a, "B": b})
            r.run()
            self.assertIsNone(r.error, r.error)
            self.assertTrue(r.bin_timebase["applied"])
            self.assertLessEqual(r.count, 2000)
        p = self.channel()
        p.write_bytes(b"bad")
        with self.assertRaisesRegex(ValueError, "length"):
            metadata(p)

    def test_metadata_missing_unsupported_and_duplicate_files(self):
        p = self.channel()
        p.with_suffix(".BXM").unlink()
        with self.assertRaisesRegex(ValueError, "Missing matching"):
            metadata(p)
        p = self.channel()
        m = p.with_suffix(".BXM")
        m.write_text(m.read_text().replace("UINT16", "FLOAT32"))
        with self.assertRaisesRegex(ValueError, "UINT16"):
            metadata(p)
        p = self.channel()
        r = self.recording({"A": p, "B": p})
        r.run()
        self.assertIn("different BIN", r.error)

    def test_assumed_alignment_preserves_widths_and_raw_samples(self):
        a = self.channel("A")
        b = self.channel("B", start=3.0, stop=3.1999)
        neg = self.channel("BBAR", start=3.0, stop=3.1999)
        values = np.where((np.arange(2000) - 10) % 100 < 30, 50000, 10000).astype("<u2")
        values.tofile(b)
        (60000 - values).astype("<u2").tofile(neg)
        original = b.read_bytes()
        r = self.recording({"A": a, "B": b, "-B": neg})
        r.options["bin_align_90"] = True
        r.run()
        self.assertIsNone(r.error, r.error)
        self.assertEqual(r.bin_alignment["shift_samples"], 15)
        self.assertAlmostEqual(r.phase_degrees, 90, delta=0.01)
        self.assertEqual(b.read_bytes(), original)
        # Positive and negative B samples are copied exactly at a constant offset.
        low = -1
        scale = 7 / 65535
        np.testing.assert_array_equal(
            r.data[:, 3], low + values[: r.count].astype(float) * scale
        )
        metric = next(m for m in r.metrics if m["c"] == 7)
        self.assertAlmostEqual(metric["cycle_duty"], 30, delta=0.001)
        self.assertAlmostEqual(metric["high"], 0.003, delta=1e-9)
        self.assertTrue(r.bin_alignment["assumed"])

    def test_negative_phase_shift_and_unstable_rotation_rejection(self):
        a = self.channel("A")
        b = self.channel("B")
        values = np.where((np.arange(2000) + 10) % 100 < 40, 50000, 10000).astype("<u2")
        values.tofile(b)
        r = self.recording({"A": a, "B": b})
        r.options["bin_align_90"] = True
        r.run()
        self.assertIsNone(r.error, r.error)
        self.assertEqual(r.bin_alignment["shift_samples"], -15)
        self.assertAlmostEqual(r.phase_degrees, -90, delta=0.01)
        np.testing.assert_array_equal(
            r.data[:, 3], -1 + values[15:].astype(float) * 7 / 65535
        )
        np.where((np.arange(2000) - 10) % 101 < 40, 50000, 10000).astype("<u2").tofile(
            b
        )
        r = self.recording({"A": a, "B": b})
        r.options["bin_align_90"] = True
        r.run()
        self.assertIsNotNone(r.error)
        self.assertFalse(r.ready)

    def test_assumed_alignment_requires_a_b_and_matching_rates(self):
        a = self.channel("A")
        r = self.recording({"A": a})
        r.options["bin_align_90"] = True
        r.run()
        self.assertIn("requires A and B", r.error)
        b = self.channel("B", stop=1.4)
        r = self.recording({"A": a, "B": b})
        r.options["bin_align_90"] = True
        r.run()
        self.assertIsNotNone(r.error)

    def test_no_z_expected_marker_tracks_pulses_but_exposes_gaps(self):
        p = self.channel()
        for missing in (False, True):
            values = np.where(np.arange(2000) % 100 < 50, 50000, 10000).astype("<u2")
            if missing:
                values[1000:1050] = 10000
            values.tofile(p)
            r = self.recording({"A": p})
            r.options["rpm"] = 60
            r.run()
            self.assertIsNone(r.error, r.error)
            r.replay_ppr = 100
            angles = []
            for edge in r.replay_edges[-4:-1]:
                d = r.position(float(r.data[edge, 0]))
                error = (d["expected"] - d["angle"] + 180) % 360 - 180
                angles.append(error)
                self.assertEqual(d["expected_source"], "pulse timing estimate")
            np.testing.assert_allclose(angles, 3.6 if missing else 0, atol=1e-7)

    def test_resampling_matches_bxm_elapsed_time_and_warns(self):
        a = self.channel("A", count=2000, start=1.0, stop=1.1999)
        b = self.channel("B", count=1000, start=4.0, stop=4.1998)
        values = np.arange(1000, dtype="<u2")
        values.tofile(b)
        r = self.recording({"A": a, "B": b})
        r.ingest()
        self.assertEqual(r.count, 1999)
        indices = np.minimum(999, np.floor(np.arange(1999) * 0.5 + .5).astype(int))
        np.testing.assert_allclose(
            r.data[:, 3], -1 + values[indices].astype(float) * 7 / 65535, atol=1e-12
        )
        self.assertTrue(r.bin_timebase["applied"])
        self.assertEqual(r.bin_timebase["scaled_channels"][0]["channel"], "B")
        self.assertEqual(r.bin_timebase["rebased_channels"], ["B"])
        self.assertTrue(r.bin_timebase["trimmed"])
        r = self.recording({"A": a})
        r.ingest()
        self.assertFalse(r.bin_timebase["applied"])

    def test_blocks_and_cancellation(self):
        p = self.channel()
        r = self.recording({"A": p})
        with patch.object(engine, "BLOCK", 127):
            r.ingest()
        self.assertEqual(len(r.data), 2000)
        r2 = self.recording({"A": p})
        r2.cancelled = True
        r2.run()
        self.assertEqual(r2.stage, "Cancelled")


if __name__ == "__main__":
    unittest.main()
