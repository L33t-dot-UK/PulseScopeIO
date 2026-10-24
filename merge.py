"""Bounded-memory, Z-triggered combination of two four-channel captures.

Aligned captures retain their original samples. Independent capture speed drift
is corrected against corresponding Z revolutions before combining A and B.
"""

import math
import shutil
from pathlib import Path
import numpy as np
from engine import Recording, BLOCK, MAX_BYTES, CACHE, NAMES, hysteresis
from compatibility import auto_levels, receiver_states


class MergedRecording(Recording):
    def __init__(self, path_a, path_b, options=None, path_c=None):
        mode = (options or {}).get("alignment_mode", "check")
        if mode not in ("check", "refine"):
            raise ValueError("Choose alignment mode check or refine")
        if mode == "refine" and not path_c:
            raise ValueError("B timing refinement requires an optional A/B alignment capture")
        super().__init__(path_a, dict(options or {}))
        self.merge_paths = [str(self.source), str(Path(path_b).resolve())]
        if path_c:
            self.merge_paths.append(str(Path(path_c).resolve()))
        self.display_name = f"Merged: {self.source.name} + {Path(path_b).name}"

    def sample_interval(self, r):
        dt = float(r.data[1, 0] - r.data[0, 0])
        for k in range(0, r.count, BLOCK):
            self.check()
            t = r.data[max(0, k - 1) : k + BLOCK, 0]
            # Allow floating point roundoff in long timestamps, not irregular sampling.
            tol = max(dt * 1e-5, float(np.max(np.abs(t))) * np.finfo(float).eps * 8)
            if np.any(np.abs(np.diff(t) - dt) > tol):
                raise ValueError(
                    "Merging requires uniformly sampled captures with the same sample rate"
                )
        return dt

    def z_edges(self, r):
        low = float(self.options.get("low", 2.5))
        high = float(self.options.get("high", 2.5))
        if self.options.get("auto") or (
            r.source.suffix.lower() == ".wav" and low == high == 2.5
        ):
            low, high = auto_levels(r.data, r.present)
        elif low == high == 2.5:
            lo, hi = math.inf, -math.inf
            for k in range(0, r.count, BLOCK):
                self.check()
                v = r.data[k : k + BLOCK, 5:7]
                lo, hi = min(lo, float(v.min())), max(hi, float(v.max()))
            if (-0.3 <= lo <= 0.2 and 0.8 <= hi <= 1.3) or (
                -1.3 <= lo <= -0.5 and 0.5 <= hi <= 1.3
            ):
                low, high = lo + 0.25 * (hi - lo), lo + 0.75 * (hi - lo)
        if not math.isfinite(low + high) or low > high:
            raise ValueError("Thresholds must be finite and low <= high")
        previous = [0, 0, 0]
        rp, fp = r.dir / "sync-rise.bin", r.dir / "sync-fall.bin"
        with rp.open("wb") as rises, fp.open("wb") as falls:
            for k in range(0, r.count, BLOCK):
                self.check()
                pos = hysteresis(r.data[k : k + BLOCK, 5], low, high, previous[0])
                neg = hysteresis(r.data[k : k + BLOCK, 6], low, high, previous[1])
                state, _ = receiver_states(pos, neg, previous[2])
                delta = np.diff(np.r_[previous[2], state].astype("i2"))
                # A recording starting high is a partial pulse, not a trigger.
                if k == 0:
                    delta[0] = 0
                (np.flatnonzero(delta > 0) + k).astype("<i8").tofile(rises)
                (np.flatnonzero(delta < 0) + k).astype("<i8").tofile(falls)
                previous = [int(pos[-1]), int(neg[-1]), int(state[-1])]
        r.edges = {
            8: (
                np.memmap(rp, dtype="<i8", mode="r")
                if rp.stat().st_size
                else np.array([], dtype="i8")
            )
        }
        r.falls = {
            8: (
                np.memmap(fp, dtype="<i8", mode="r")
                if fp.stat().st_size
                else np.array([], dtype="i8")
            )
        }
        r.validate_z()
        if not len(r.validated_z):
            raise ValueError(
                f"{r.source.name}: no complete Z pulse at the selected thresholds; check Z/-Z polarity and levels"
            )
        return r.validated_z

    def indexed_z(self, z):
        """Assign revolution numbers, allowing unambiguous missing index events."""
        if len(z) < 2:
            yield 0, int(z[0])
            return
        ix = np.linspace(0, len(z) - 2, min(len(z) - 1, 200000), dtype=int)
        nominal = float(np.median(z[ix + 1] - z[ix]))
        ordinal = 0
        yield ordinal, int(z[0])
        for k in range(0, len(z) - 1, BLOCK):
            self.check()
            delta = np.diff(z[k : min(len(z), k + BLOCK + 1)]) / nominal
            steps = np.rint(delta).astype("i8")
            if np.any((steps < 1) | (np.abs(delta - steps) > 0.2)):
                raise ValueError(
                    "Z pulse spacing is ambiguous: cannot distinguish speed changes "
                    "from missing/spurious index pulses. Use captures with stable "
                    "rotation and clean Z signals."
                )
            numbers = ordinal + np.cumsum(steps)
            for number, sample in zip(numbers, z[k + 1 : k + 1 + len(numbers)]):
                yield int(number), int(sample)
            if len(numbers):
                ordinal = int(numbers[-1])

    def matching_z(self, za, zb):
        """Disk-backed correspondence prevents a missing Z shifting later pairs."""
        left, right = iter(self.indexed_z(za)), iter(self.indexed_z(zb))
        a, b = next(left, None), next(right, None)
        count = 0
        path = self.dir / "merge-z-pairs.bin"
        with path.open("wb") as dest:
            pending = []
            while a is not None and b is not None:
                if a[0] == b[0]:
                    pending.append((a[1], b[1]))
                    count += 1
                    a, b = next(left, None), next(right, None)
                    if len(pending) == 4096:
                        self.check()
                        np.asarray(pending, dtype="<i8").tofile(dest)
                        pending = []
                elif a[0] < b[0]:
                    a = next(left, None)
                else:
                    b = next(right, None)
            if pending:
                np.asarray(pending, dtype="<i8").tofile(dest)
        return np.memmap(path, dtype="<i8", mode="r", shape=(count, 2))

    def check_mixed_levels(self, a, b):
        if (a.source.suffix.lower() == ".wav") == (b.source.suffix.lower() == ".wav"):
            return
        low = float(self.options.get("low", 2.5))
        high = float(self.options.get("high", 2.5))
        ranges = []
        for r, channels in ((a, [1, 2]), (b, [3, 4])):
            values = r.data[:: max(1, math.ceil(r.count / 100000)), channels]
            ranges.append(np.quantile(values, [0.1, 0.9]))
        if not all(lo < low <= high < hi for lo, hi in ranges):
            raise ValueError(
                "The primary CSV and WAV captures use incompatible signal levels. "
                "WAV samples are normalized; use the same export format/voltage "
                "scale for both captures, or set thresholds valid for both."
            )

    def ingest(self):
        captures = []
        pairs = mapping = refinement_z = None
        try:
            for i, (path, role) in enumerate(zip(self.merge_paths, ("A", "B"))):
                self.check()
                source = Path(path)
                if not source.is_file() or source.stat().st_size > MAX_BYTES:
                    raise ValueError(
                        f"Capture {role} must exist and be no larger than 10 GiB"
                    )
                r = Recording(path, dict(self.options, capture_role=role))
                captures.append(r)

                def import_check(r=r, i=i):
                    self.check()
                    self.progress = 0.12 * (i + min(1, r.progress / 0.4))

                r.check = import_check
                self.stage = f"Importing capture {role} (whole file)"
                r.ingest()
                r.check = self.check
                self.progress = 0.12 * (i + 1)
            a, b = captures
            self.stage = "Checking sample rates and aligning complete Z pulses"
            da, db = self.sample_interval(a), self.sample_interval(b)
            if (
                a.seconds != b.seconds
                or a.has_time != b.has_time
                or not math.isclose(da, db, rel_tol=1e-6, abs_tol=0)
            ):
                raise ValueError(
                    "Both captures must use the same sample rate and compatible time units"
                )
            za, zb = self.z_edges(a), self.z_edges(b)
            self.check_mixed_levels(a, b)
            anchor_a, anchor_b = int(za[0]), int(zb[0])
            offset = anchor_b - anchor_a
            first = max(0, -offset)
            last = min(a.count, b.count - offset)
            pairs = self.matching_z(za, zb)
            matched, max_delta = len(pairs), 0
            for k in range(0, matched, BLOCK):
                self.check()
                p = pairs[k : k + BLOCK]
                delta = np.abs(p[:, 1] - offset - p[:, 0])
                if len(delta):
                    max_delta = max(max_delta, int(delta.max()))
            resample = matched >= 2 and max_delta > 0
            if resample:
                # Restrict phase correction to known Z fiducials. One low sample
                # before the first rising edge establishes the initial logic state;
                # the last falling edge keeps the final index pulse complete.
                last_a, last_b = (int(v) for v in pairs[-1])
                fall_a = int(
                    a.falls[8][np.searchsorted(a.falls[8], last_a, side="right")]
                )
                fall_b = int(
                    b.falls[8][np.searchsorted(b.falls[8], last_b, side="right")]
                )
                first, last = anchor_a - 1, fall_a + 1
                map_path = self.dir / "merge-z-map.bin"
                with map_path.open("wb") as dest:
                    np.asarray([[anchor_a - 1, anchor_b - 1]], dtype="<i8").tofile(dest)
                    for k in range(0, matched, BLOCK):
                        self.check()
                        pairs[k : k + BLOCK].tofile(dest)
                    np.asarray([[fall_a, fall_b]], dtype="<i8").tofile(dest)
                mapping = np.memmap(
                    map_path, dtype="<i8", mode="r", shape=(matched + 2, 2)
                )
            self.count = last - first
            if self.count < 2:
                raise ValueError(
                    "Captures have no shared recording span after Z alignment"
                )
            self.stage = "Writing six-channel merged recording"
            origin = float(a.data[anchor_a, 0])
            with (self.dir / "samples.bin").open("wb") as dest:
                for k in range(first, last, BLOCK):
                    self.check()
                    stop = min(last, k + BLOCK)
                    block = np.array(a.data[k:stop], copy=True)
                    block[:, 0] -= origin
                    if resample:
                        target = np.arange(k, stop)
                        interval = np.clip(
                            np.searchsorted(mapping[:, 0], target, side="right") - 1,
                            0,
                            len(mapping) - 2,
                        )
                        begin, finish = mapping[interval], mapping[interval + 1]
                        position = begin[:, 1] + (
                            (target - begin[:, 0]) / (finish[:, 0] - begin[:, 0])
                        ) * (finish[:, 1] - begin[:, 1])
                        left = np.floor(position).astype("i8")
                        fraction = (position - left)[:, None]
                        right = np.minimum(left + 1, b.count - 1)
                        block[:, 3:5] = (
                            b.data[left, 3:5] * (1 - fraction)
                            + b.data[right, 3:5] * fraction
                        )
                    else:
                        block[:, 3:5] = b.data[k + offset : stop + offset, 3:5]
                    block.astype("<f8").tofile(dest)
                    self.progress = 0.28 + 0.12 * (stop - first) / self.count
            self.data = np.memmap(
                self.dir / "samples.bin", dtype="<f8", mode="r", shape=(self.count, 7)
            )
            self.present = list(range(6))
            if (
                all(Path(p).suffix.lower() == ".wav" for p in self.merge_paths[:2])
                and float(self.options.get("low", 2.5))
                == float(self.options.get("high", 2.5))
                == 2.5
            ):
                self.options["low"], self.options["high"] = auto_levels(
                    self.data, self.present
                )
            self.seconds, self.has_time = a.seconds, a.has_time
            self.source_time_unit, self.time_heading = (
                a.source_time_unit,
                a.time_heading,
            )
            self.headers = [self.time_heading] + NAMES
            self.start, self.end = float(self.data[0, 0]), float(self.data[-1, 0])
            self.merge_info = dict(
                sources=[Path(p).name for p in self.merge_paths[:2]],
                method=(
                    "Corresponding complete Z pulses; B time-scaled per revolution to capture A"
                    if resample
                    else "Corresponding complete Z pulses; exact whole-sample offset, no resampling"
                ),
                offset_samples=offset,
                sample_interval=da,
                matched_z=matched,
                max_z_mismatch_samples=max_delta,
                resampled_b=resample,
                alignment_span_limited=resample,
                unmatched_z=len(za) + len(zb) - 2 * matched,
                source_samples=[a.count, b.count],
                retained_samples=self.count,
                z_source="A capture",
                warning=(
                    "Only one Z pulse matched; later alignment could not be verified."
                    if matched < 2
                    else (
                        "Unmatched Z events were skipped when pairing revolutions; check for missing index pulses or different capture spans."
                        if len(za) + len(zb) > 2 * matched
                        else ""
                    )
                ),
            )
            if len(self.merge_paths) > 2:
                from alignment import check_alignment

                self.merge_info["alignment_check"] = check_alignment(
                    self, self.merge_paths[2]
                )
                if self.options.get("alignment_mode", "check") == "refine":
                    from refinement import refine_b

                    # Only complete validated A-source Z pulses with one sample
                    # of initial context can delimit a refinement span.
                    loz = int(np.searchsorted(za, first + 1, side="left"))
                    hiz = int(np.searchsorted(za, last, side="left"))
                    while hiz > loz:
                        last_z = int(za[hiz - 1])
                        last_fall = int(a.falls[8][np.searchsorted(a.falls[8], last_z, side="right")])
                        if last_fall < last:
                            break
                        hiz -= 1
                    if hiz - loz < 2:
                        raise ValueError("B timing refinement needs at least two complete Z pulses")
                    zpath = self.dir / "refinement-z.bin"
                    with zpath.open("wb") as dest:
                        for k in range(loz, hiz, BLOCK):
                            self.check()
                            (za[k : min(hiz, k + BLOCK)] - first).astype("<i8").tofile(dest)
                    refinement_z = np.memmap(zpath, dtype="<i8", mode="r")
                    refine_b(self, refinement_z, last_fall - first + 1)
        finally:
            for obj in (pairs, mapping, refinement_z):
                if isinstance(obj, np.memmap) and not obj._mmap.closed:
                    obj._mmap.close()
            for r in captures:
                for obj in [
                    getattr(r, "data", None),
                    getattr(r, "validated_z", None),
                    *getattr(r, "edges", {}).values(),
                    *getattr(r, "falls", {}).values(),
                ]:
                    if isinstance(obj, np.memmap) and not obj._mmap.closed:
                        obj._mmap.close()
                if r.dir.resolve().parent != CACHE.resolve():
                    raise ValueError("Invalid temporary capture directory")
                shutil.rmtree(r.dir)
