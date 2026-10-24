"""Disk-backed full recording analysis. No web or scientific framework required."""

from contextlib import closing
import re, csv, json, math, os, threading, uuid, wave, struct, sqlite3, sys, io
from pathlib import Path
import numpy as np
from compatibility import (
    online_slew,
    threshold_candidates,
    time_unit,
    auto_levels,
    receiver_states,
    smooth_chunk,
    adaptive_radius,
    smoothing_limit,
)

ROOT = Path(getattr(sys, "_MEIPASS", Path(__file__).resolve().parent))
WORK = Path(sys.executable).resolve().parent if getattr(sys, "frozen", False) else ROOT
CACHE = WORK / ".cache"
CACHE.mkdir(exist_ok=True)
NAMES = ["A", "-A", "B", "-B", "Z", "-Z"]
BLOCK = 131072
MAX_BYTES = 10 * 1024**3


class PCMReader:
    """Streaming RIFF/RF64 PCM reader, including WAVE_FORMAT_EXTENSIBLE."""

    def __init__(self, path, channels=6):
        self.f = open(path, "rb")
        header = self.f.read(12)
        if (
            len(header) != 12
            or header[:4] not in (b"RIFF", b"RF64")
            or header[8:] != b"WAVE"
        ):
            raise ValueError("Invalid WAV header")
        rfsize = None
        fmt = None
        while True:
            chunk = self.f.read(8)
            if len(chunk) != 8:
                raise ValueError("WAV has no data chunk")
            kind, size = struct.unpack("<4sI", chunk)
            if kind == b"ds64":
                if size < 28:
                    raise ValueError("Invalid RF64 size table")
                _, rfsize, _, _ = struct.unpack("<QQQI", self.f.read(28))
                self.f.seek(size - 28, 1)
            elif kind == b"fmt ":
                if size < 16 or size > 4096:
                    raise ValueError("Invalid WAV format chunk")
                fmt = self.f.read(size)
                code, self.channels, self.rate, _, align, bits = struct.unpack(
                    "<HHIIHH", fmt[:16]
                )
                self.width = bits // 8
                if code == 65534 and len(fmt) >= 40:
                    code = struct.unpack("<H", fmt[24:26])[0]
                self.float = code == 3
                if (
                    code not in (1, 3)
                    or self.channels != channels
                    or self.width not in (1, 2, 3, 4, 8)
                    or align != channels * self.width
                    or (self.float and self.width not in (4, 8))
                    or (not self.float and self.width == 8)
                    or not self.rate
                ):
                    raise ValueError(
                        f"Use {channels}-channel PCM 8/16/24/32-bit or IEEE float 32/64-bit WAV"
                    )
            elif kind == b"data":
                if fmt is None:
                    raise ValueError("WAV format must precede audio data")
                self.size = (
                    rfsize if size == 0xFFFFFFFF and rfsize is not None else size
                )
                self.offset = self.f.tell()
                if (
                    self.size % (self.channels * self.width)
                    or self.offset + self.size > os.fstat(self.f.fileno()).st_size
                ):
                    raise ValueError("Truncated or misaligned WAV data")
                self.frames = self.size // (self.channels * self.width)
                self.remaining = self.size
                break
            else:
                self.f.seek(size, 1)
            if size % 2:
                self.f.seek(1, 1)

    def __enter__(self):
        return self

    def __exit__(self, *args):
        self.f.close()

    def read(self, n):
        b = self.f.read(min(self.remaining, n * self.channels * self.width))
        self.remaining -= len(b)
        return b


def norm(s):
    s = s.strip().lower().replace("−", "-").replace("–", "-").replace("_", "")
    if len(s) == 2 and s[0] in "abz" and s[1] in "+-":
        s = ("" if s[1] == "+" else "-") + s[0]
    return s


def hysteresis(x, low, high, previous=0):
    # Vectorized last decisive crossing, carrying state across chunk boundaries.
    decisive = (x <= low) | (x >= high)
    indices = np.maximum.accumulate(np.where(decisive, np.arange(len(x)), -1))
    result = np.where(indices >= 0, x[np.maximum(indices, 0)] > low, previous).astype(
        "u1"
    )
    return result


class Recording:
    def __init__(self, path, options=None):
        self.source = Path(path).resolve()
        self.options = options or {}
        self.id = uuid.uuid4().hex
        self.dir = CACHE / self.id
        self.dir.mkdir()
        self.progress = 0.0
        self.stage = "Queued"
        self.error = None
        self.cancelled = False
        self.ready = False

    def check(self):
        if self.cancelled:
            raise ValueError("Analysis cancelled")

    def run(self):
        try:
            if not self.source.is_file():
                raise ValueError("File does not exist")
            if self.source.stat().st_size > MAX_BYTES:
                raise ValueError("Maximum source size is 10 GiB")
            if not hasattr(self, "data"):
                self.stage = "Importing full recording"
                self.ingest()
            self.stage = "Analysing every sample"
            self.analyse()
            if getattr(self, "merge_info", {}).get("alignment_check"):
                from alignment import refresh_alignment

                self.stage = "Checking analysed A/B phase against alignment capture"
                self.merge_info = dict(
                    self.merge_info,
                    alignment_check=refresh_alignment(
                        self, self.merge_info["alignment_check"]
                    ),
                )
            self.build_replay()
            self.stage = "Building waveform overview"
            self.build_overview()
            self.ready = True
            self.progress = 1.0
            self.stage = "Complete"
        except Exception as e:
            self.error = str(e)
            self.stage = "Cancelled" if self.cancelled else "Failed"
            if getattr(self, "faultdb", None) is not None:
                self.faultdb.close()
                self.faultdb = None

    def ingest(self):
        if self.options.get("csv_channel_paths"):
            from csv_channels import ingest

            return ingest(self)
        if self.options.get("binary_paths"):
            from binary_import import ingest

            return ingest(self)
        role = self.options.get("capture_role")
        channels = (
            [0, 1, 4, 5]
            if role == "A"
            else (
                [2, 3, 4, 5]
                if role == "B"
                else [0, 1, 2, 3] if role == "C" else list(range(6))
            )
        )
        self.count = 0
        self.present = []
        self.seconds = False
        self.headers = []
        self.has_time = False
        self.source_time_unit = "samples"
        self.time_heading = "Sample"
        previous = -np.inf
        with open(self.dir / "samples.bin", "wb") as dest:
            if self.source.suffix.lower() == ".wav":
                with PCMReader(self.source, len(channels)) as w:
                    self.rate = w.rate
                    self.seconds = True
                    self.has_time = True
                    self.source_time_unit = "s"
                    self.time_heading = "Time_s"
                    self.present = channels
                    width = w.width
                    total = w.frames
                    while True:
                        self.check()
                        raw = w.read(BLOCK)
                        if not raw:
                            break
                        if w.float:
                            v = np.frombuffer(
                                raw, dtype="<f4" if width == 4 else "<f8"
                            ).astype("f8")
                        elif width == 3:
                            b = np.frombuffer(raw, dtype="u1").reshape(-1, 3)
                            v = (
                                b[:, 0].astype("i4")
                                | (b[:, 1].astype("i4") << 8)
                                | (b[:, 2].astype("i4") << 16)
                            )
                            v = (v ^ 0x800000) - 0x800000
                        else:
                            v = np.frombuffer(
                                raw, dtype={1: "u1", 2: "<i2", 4: "<i4"}[width]
                            ).astype("f8")
                            v = v - 128 if width == 1 else v
                        v = v.reshape(-1, len(channels)) / (
                            1 if w.float else 2 ** (8 * width - 1)
                        )
                        if not np.all(np.isfinite(v)):
                            raise ValueError("Non-finite WAV sample")
                        a = np.zeros((len(v), 7))
                        a[:, 0] = np.arange(self.count, self.count + len(v)) / self.rate
                        a[:, np.array(channels) + 1] = v
                        a.astype("<f8").tofile(dest)
                        self.count += len(v)
                        self.progress = 0.4 * self.count / max(total, 1)
                    if self.count != total:
                        raise ValueError("Truncated WAV recording")
            else:
                with open(self.source, encoding="utf-8-sig", newline="") as f:
                    first = f.readline(1024 * 1024 + 1)
                    if len(first) > 1024 * 1024:
                        raise ValueError(
                            "CSV header exceeds 1 MiB; check the delimiter and file format"
                        )
                    delim = self.options.get("delimiter", "auto")
                    delim = (
                        max([",", ";", "\t"], key=first.count)
                        if delim == "auto"
                        else delim
                    )
                    if delim == "\\t":
                        delim = "\t"
                    if delim not in [",", ";", "\t"]:
                        raise ValueError("Choose comma, semicolon or tab delimiter")
                    self.csv_delimiter = delim
                    self.headers = next(csv.reader([first], delimiter=delim))
                    headerless = False
                    try:
                        [float(v) for v in self.headers]
                        headerless = bool(self.headers)
                    except ValueError:
                        pass
                    self.csv_headerless = headerless
                    if headerless:
                        f.seek(0)
                        self.headers = (["Voltage"] if len(self.headers)==1 and "single_csv_channel" in self.options
                                        else ["Time_s"] + [f"Column {i+1}" for i in range(1,len(self.headers))])
                    h = [norm(x) for x in self.headers]
                    mapping = [
                        h.index(norm(n)) if norm(n) in h else None for n in NAMES
                    ]
                    if role:
                        if not all(mapping[c] is not None for c in channels):
                            generic = [
                                next(
                                    (
                                        i
                                        for i, name in enumerate(h)
                                        if name in (f"ch{n}", f"channel{n}")
                                    ),
                                    None,
                                )
                                for n in range(1, 5)
                            ]
                            mapping = [None] * 6
                            if not any(i is None for i in generic):
                                for c, i in zip(channels, generic):
                                    mapping[c] = i
                        else:
                            mapping = [
                                v if c in channels else None
                                for c, v in enumerate(mapping)
                            ]
                    self.present = [i for i, c in enumerate(mapping) if c is not None]
                    tc = self.options.get("timecol", "auto")
                    ti = (
                        next(
                            (
                                i
                                for i, v in enumerate(h)
                                if re.sub(r"[^a-z]", "", v)
                                in (
                                    "times",
                                    "time",
                                    "timestamp",
                                    "t",
                                    "seconds",
                                    "sec",
                                    "timems",
                                    "timeus",
                                    "timens",
                                    "milliseconds",
                                    "microseconds",
                                    "nanoseconds",
                                    "ins",
                                    "inms",
                                    "inus",
                                    "inns",
                                )
                            ),
                            None,
                        )
                        if tc == "auto"
                        else (None if tc == "index" else int(tc))
                    )
                    if ti is not None and not 0 <= ti < len(h):
                        raise ValueError("Invalid time column")
                    assumed_columns = False
                    single = "single_csv_channel" in self.options
                    # Preserve valid labels (including sparse A/B-only files); otherwise
                    # use the requested positional layout instead of silently dropping columns.
                    signal_indices = [i for i in range(len(h)) if i != ti and not (tc=="index" and (time_unit(self.headers[i])[0] is not None or h[i] in ("time","timestamp","t","sample","samples")))]
                    mapped_indices = [v for v in mapping if v is not None]
                    complete_labels = bool(mapped_indices) and len(set(mapped_indices))==len(signal_indices) and set(mapped_indices)==set(signal_indices)
                    if single and ti is None and tc=="auto" and len(h)==2:
                        ti=0
                        assumed_columns=True
                    elif not single and (headerless or not complete_labels):
                        if tc=="auto":ti=0
                        layout_time=0 if ti is None and tc=="index" else ti
                        signal_indices=[i for i in range(len(h)) if i!=layout_time]
                        targets=channels if role else list(range(6))
                        if (ti is None and tc!="index") or not 1<=len(signal_indices)<=len(targets) or (role and len(signal_indices)!=len(targets)):
                            raise ValueError("Unlabelled CSV needs a time column followed by one to six signal columns (four signals for a merge capture)")
                        mapping=[None]*6
                        for target,column in zip(targets,signal_indices):mapping[target]=column
                        self.present=[i for i,column in enumerate(mapping) if column is not None]
                        assumed_columns=True
                    if single:
                        target = int(self.options["single_csv_channel"])
                        signal_columns = ([1] if tc=="index" and len(h)==2 else [i for i in range(len(h)) if i != ti])
                        if target not in range(6) or len(signal_columns) != 1:
                            raise ValueError("Single-channel CSV needs exactly one signal column and an optional recognised time column")
                        mapping = [None] * 6
                        mapping[target] = signal_columns[0]
                        self.present = [target]
                    self.has_time = ti is not None
                    unit_scale, self.source_time_unit = (
                        time_unit(self.headers[ti])
                        if ti is not None
                        else (None, "samples")
                    )
                    if (assumed_columns or headerless) and ti is not None and unit_scale is None:
                        unit_scale,self.source_time_unit=1.,"s"
                    if assumed_columns or headerless:
                        self.csv_columns=dict(assumed=True,headerless=headerless,time_column=ti,
                            assignments={NAMES[c]:column+1 for c,column in enumerate(mapping) if column is not None},
                            note=("Headerless single-channel signal; samples are indexed by row." if ti is None else "CSV column order assumed: first column is time; following columns assigned by position. Time is assumed to be seconds unless its heading specifies units. Check the assignments against the capture."))
                    self.seconds = unit_scale is not None
                    self.time_heading = (
                        "Time_s"
                        if self.seconds
                        else (self.headers[ti] if ti is not None else "Sample")
                    )
                    batch = []
                    batch_bytes = 0
                    line = 0 if headerless else 1

                    def save(batch):
                        nonlocal previous
                        try:
                            a = np.loadtxt(
                                io.StringIO("".join(batch)),
                                dtype="f8",
                                delimiter=delim,
                                comments=None,
                                quotechar='"',
                                ndmin=2,
                            )
                        except ValueError as exc:
                            raise ValueError(
                                f"Invalid numeric CSV near source row {line}: {exc}"
                            )
                        if a.shape[1] != len(h):
                            raise ValueError(
                                f"Wrong column count near row {line}: expected {len(h)}, got {a.shape[1]}"
                            )
                        t = (
                            a[:, ti]
                            if ti is not None
                            else np.arange(self.count, self.count + len(a), dtype="f8")
                        )
                        if unit_scale is not None:
                            t = t * unit_scale
                        if not np.all(np.isfinite(a)):
                            raise ValueError(
                                "Non-finite numeric value near row " + str(line)
                            )
                        if t[0] <= previous or np.any(np.diff(t) <= 0):
                            raise ValueError(
                                "Timestamps must be strictly increasing near row "
                                + str(line)
                            )
                        previous = t[-1]
                        out = np.zeros((len(a), 7))
                        out[:, 0] = t
                        for j, c in enumerate(mapping):
                            if c is not None:
                                out[:, j + 1] = a[:, c]
                        out.tofile(dest)
                        self.count += len(a)

                    while True:
                        self.check()
                        row = f.readline(1024 * 1024 + 1)
                        if not row:
                            break
                        line += 1
                        if len(row) > 1024 * 1024:
                            raise ValueError(
                                f"CSV row {line} exceeds 1 MiB; expected a short numeric sample row"
                            )
                        if not row.strip():
                            continue
                        batch.append(row)
                        batch_bytes += len(row)
                        if len(batch) >= BLOCK or batch_bytes >= 16 * 1024 * 1024:
                            save(batch)
                            batch = []
                            batch_bytes = 0
                            self.progress = min(
                                0.4,
                                0.4
                                * f.buffer.tell()
                                / max(self.source.stat().st_size, 1),
                            )
                    if batch:
                        save(batch)
        if self.count < 2:
            raise ValueError("At least two samples required")
        self.data = np.memmap(
            self.dir / "samples.bin", dtype="<f8", mode="r", shape=(self.count, 7)
        )
        self.start = float(self.data[0, 0])
        self.end = float(self.data[-1, 0])

    def search_thresholds(self):
        """Evaluate online threshold candidates against every sample, serially."""
        import shutil

        pairs, active = threshold_candidates(self.data, self.present)
        best = None
        winner = None
        for index, (low, high) in enumerate(pairs):
            self.check()
            self.stage = f"Threshold search {index+1}/{len(pairs)} (whole recording)"
            probe = Recording(
                self.source,
                dict(self.options, auto=False, low=low, high=high, slew_mode="none"),
            )
            for key in (
                "data",
                "count",
                "present",
                "seconds",
                "headers",
                "start",
                "end",
                "has_time",
                "source_time_unit",
                "time_heading",
            ):
                setattr(probe, key, getattr(self, key))
            probe.check = self.check
            probe._threshold_probe = True
            try:
                probe.analyse()
                rises = {c: len(probe.edges[c]) for c in active}
                dead = sum(
                    not len(probe.edges[c]) and not len(probe.falls[c]) for c in active
                )
                mismatch = sum(
                    abs(rises[a] - rises[b])
                    for a, b in ((0, 1), (2, 3), (0, 2))
                    if a in rises and b in rises
                )
                rank = (dead, probe.flag_count, mismatch, -sum(rises.values()))
                if sum(rises.values()) and (best is None or rank < best):
                    best = rank
                    winner = (low, high)
            finally:
                if probe.faultdb is not None:
                    probe.faultdb.close()
                for attr in ("logic", "validated_z"):
                    a = getattr(probe, attr, None)
                    if isinstance(a, np.memmap) and not a._mmap.closed:
                        a._mmap.close()
                for attr in ("edges", "falls"):
                    for a in getattr(probe, attr, {}).values():
                        if isinstance(a, np.memmap) and not a._mmap.closed:
                            a._mmap.close()
                # Only the temporary probe's UUID directory is removed; raw input is shared read-only.
                if probe.dir.resolve().parent != CACHE.resolve():
                    raise ValueError("Invalid probe cache location")
                shutil.rmtree(probe.dir)
            self.progress = 0.4 + 0.45 * (index + 1) / len(pairs)
        if winner is None:
            raise ValueError("Could not find thresholds that preserve pulse trains")
        self.stage = "Analysing every sample with selected thresholds"
        return winner

    def analyse(self):
        self.faultdb = sqlite3.connect(self.dir / "faults.sqlite")
        self.faultdb.execute(
            "CREATE TABLE faults (id INTEGER PRIMARY KEY, time REAL, payload TEXT)"
        )
        self.flag_count = 0
        low = float(self.options.get("low", 2.5))
        high = float(self.options.get("high", 2.5))
        if self.options.get("auto"):
            low, high = (
                self.search_thresholds()
                if self.options.get("auto_mode") == "search"
                else auto_levels(self.data, self.present)
            )
        elif low == high == 2.5:
            values = np.asarray(
                self.data[:: max(1, math.ceil(self.count / 200000)), 1:][
                    :, self.present
                ]
            )
            lo = float(values.min())
            hi = float(values.max())
            if (lo >= -0.3 and lo <= 0.2 and 0.8 <= hi <= 1.3) or (
                -1.3 <= lo <= -0.5 and 0.5 <= hi <= 1.3
            ):
                low = lo + 0.25 * (hi - lo)
                high = lo + 0.75 * (hi - lo)
        if not math.isfinite(low + high) or low > high:
            raise ValueError("Thresholds must be finite and low <= high")
        self.low = low
        self.high = high
        self.window = int(self.options.get("window", 1))
        self.window = max(1, min(self.window, 10001))
        self.requested_window = self.window
        if (
            self.window > 1
            and self.options.get("smoothing_mode", "centered") == "centered"
        ):
            self.window = min(
                self.window,
                smoothing_limit(
                    self.data, self.present, low, high, hysteresis, self.check, BLOCK
                ),
            )
        self.metrics = []
        self.edges = {}
        self.falls = {}
        self.logic = np.memmap(
            self.dir / "logic.bin", dtype="u1", mode="w+", shape=(self.count, 9)
        )
        self.logic[:] = 0
        self.available = self.present + [
            6 + j
            for j, p in enumerate([0, 2, 4])
            if p in self.present or p + 1 in self.present
        ]
        for c in self.available:
            self.check()
            previous = 0
            known = False
            with open(self.dir / f"rise{c}.bin", "wb") as rises, open(
                self.dir / f"fall{c}.bin", "wb"
            ) as falls:
                for k in range(0, self.count, BLOCK):
                    self.check()
                    stop = min(self.count, k + BLOCK)
                    centered = (
                        self.options.get("smoothing_mode", "centered") == "centered"
                    )
                    halo_left = (self.window - 1) // 2 if centered else self.window - 1
                    halo_right = self.window - 1 - halo_left
                    left = max(0, k - halo_left)
                    a = self.data[left : min(self.count, stop + halo_right), 1:]
                    online_pair = (
                        c >= 6
                        and self.options.get("receiver_mode", "online") == "online"
                        and (c - 6) * 2 in self.present
                        and (c - 6) * 2 + 1 in self.present
                    )
                    if online_pair:
                        p = (c - 6) * 2
                        state, decisive = receiver_states(
                            self.logic[k:stop, p], self.logic[k:stop, p + 1], previous
                        )
                        x = None
                    if c < 6:
                        x = a[:, c]
                    elif not online_pair:
                        p = (c - 6) * 2
                        x = (
                            a[:, p] - a[:, p + 1]
                            if p in self.present and p + 1 in self.present
                            else (a[:, p] if p in self.present else a[:, p + 1])
                        )
                    # Causal moving average, overlap makes chunk boundaries identical.
                    if x is not None:
                        x = (
                            smooth_chunk(
                                x, k - left, stop - left, self.window, centered
                            )
                            if self.window > 1
                            else x[k - left : stop - left]
                        )
                    invert = c >= 6 and (c - 6) * 2 not in self.present
                    if not online_pair:
                        state = (
                            1 - hysteresis(x, low, high, 1 - previous)
                            if invert
                            else hysteresis(x, low, high, previous)
                        )
                    changes = np.diff(np.r_[previous, state].astype("i2"))
                    if online_pair and not known and np.any(decisive):
                        changes[int(np.flatnonzero(decisive)[0])] = 0
                        known = True
                    up = np.flatnonzero(changes == 1) + k
                    down = np.flatnonzero(changes == -1) + k
                    # Initial state establishes a baseline, not an edge.
                    up = up[up > 0]
                    down = down[down > 0]
                    up.astype("<i8").tofile(rises)
                    down.astype("<i8").tofile(falls)
                    self.logic[k:stop, c] = state
                    previous = int(state[-1])
                    self.progress = 0.4 + 0.5 * (
                        self.available.index(c) + (stop / self.count)
                    ) / len(self.available)
            if self.options.get("smooth_edges") and not (
                c >= 6
                and self.options.get("receiver_mode", "online") == "online"
                and (c - 6) * 2 in self.present
                and (c - 6) * 2 + 1 in self.present
            ):
                # Three-sample majority removes isolated one-sample spikes. Halo is
                # read before mutation so results do not depend on block size.
                radius = (
                    adaptive_radius(self.logic[:, c], BLOCK)
                    if self.options.get("edge_mode", "adaptive") == "adaptive"
                    else 1
                )
                previous_raw = self.logic[:radius, c].copy()
                for k in range(0, self.count, BLOCK):
                    self.check()
                    stop = min(self.count, k + BLOCK)
                    if radius == 0:
                        break
                    raw = self.logic[k : min(self.count, stop + radius), c].copy()
                    left_pad = previous_raw if k else np.repeat(raw[0], radius)
                    joined = np.r_[
                        left_pad,
                        raw,
                        np.repeat(raw[-1], max(0, radius - (len(raw) - (stop - k)))),
                    ]
                    previous_raw = joined[stop - k : stop - k + radius].copy()
                    cs = np.r_[0, np.cumsum(joined, dtype="i8")]
                    ones = cs[2 * radius + 1 :] - cs[: -2 * radius - 1]
                    result = (ones[: stop - k] >= radius + 1).astype("u1")
                    valid = np.arange(k, stop)
                    original = raw[: stop - k]
                    result[(valid < radius) | (valid >= self.count - radius)] = (
                        original[(valid < radius) | (valid >= self.count - radius)]
                    )
                    self.logic[k:stop, c] = result
                with open(self.dir / f"rise{c}.bin", "wb") as rises, open(
                    self.dir / f"fall{c}.bin", "wb"
                ) as falls:
                    previous = int(self.logic[0, c])
                    for k in range(0, self.count, BLOCK):
                        state = self.logic[k : k + BLOCK, c]
                        changes = np.diff(np.r_[previous, state].astype("i2"))
                        (np.flatnonzero(changes == 1) + k).astype("<i8").tofile(rises)
                        (np.flatnonzero(changes == -1) + k).astype("<i8").tofile(falls)
                        previous = int(state[-1])
            self.edges[c] = self.read_edges(c, "rise")
            self.falls[c] = self.read_edges(c, "fall")
        self.validate_z()
        z = self.validated_z
        a = self.edges.get(6, self.edges.get(7, np.array([], dtype="i8")))
        self.revs = []
        self.errors = []
        self.error_count = 0
        self.inferred = 0.0
        sum_ppr = 0
        nrev = max(0, len(z) - 1)
        expected = self.options.get("ppr")
        expected = int(expected) if self.options.get("reference") and expected else None
        if expected is not None and expected <= 0:
            raise ValueError("Expected pulses/rev must be positive")
        inferred_reference = None
        if expected is None and nrev:
            count_hist = {}
            for offset in range(0, nrev, BLOCK):
                zz = z[offset : min(nrev, offset + BLOCK) + 1]
                values = np.diff(np.searchsorted(a, zz, side="left"))
                unique, n = np.unique(values, return_counts=True)
                for value, frequency in zip(unique, n):
                    count_hist[int(value)] = count_hist.get(int(value), 0) + int(
                        frequency
                    )
            inferred_reference = max(
                count_hist, key=lambda value: (count_hist[value], value)
            )
        reference = expected or inferred_reference
        self.missing_pulse_totals = {"A" if 6 in self.edges else "B": 0}
        self.b_reference_ppr = None
        for k in range(0, nrev, BLOCK):
            self.check()
            zz = z[k : min(nrev, k + BLOCK) + 1]
            counts = np.diff(np.searchsorted(a, zz, side="left"))
            sum_ppr += int(counts.sum())
            if reference:
                self.missing_pulse_totals["A" if 6 in self.edges else "B"] += int(
                    np.maximum(reference - counts, 0).sum()
                )
            for j in np.flatnonzero(counts != reference) if reference else []:
                if expected is None and counts[j] >= reference:
                    continue
                event = {
                    "sample": int(zz[j]),
                    "time": float(self.data[zz[j], 0]),
                    "end_time": float(self.data[zz[j + 1], 0]),
                    "channel": "A" if 6 in self.edges else "B",
                    "pulse": int(np.searchsorted(a, zz[j])),
                    "observed": int(counts[j]),
                    "expected": reference,
                    "missing": max(0, reference - int(counts[j])),
                    "reference_source": (
                        "entered" if expected else "most common observed count"
                    ),
                }
                self.error_count += 1
                event["kind"] = "missing pulses" if event["missing"] else "extra pulses"
                event["signal_domain"] = "receiver"
                self.add_faults([event])
                if len(self.errors) < 10000:
                    self.errors.append(event)
            if len(self.revs) < 10000:
                self.revs.extend(
                    {"start": float(self.data[zz[j], 0]), "count": int(v)}
                    for j, v in enumerate(counts[: 10000 - len(self.revs)])
                )
        self.inferred = sum_ppr / nrev if nrev else 0.0
        self.ppr = self.inferred
        self.reference_ppr = reference
        self.z_period = (
            float(self.data[z[-1], 0] - self.data[z[0], 0]) / nrev if nrev else None
        )
        self.measured_rpm = (
            60 * nrev / (float(self.data[z[-1], 0] - self.data[z[0], 0]))
            if self.seconds and nrev
            else None
        )
        self.set_rpm(self.options.get("rpm", ""))
        # The online viewer checks both independently available quadrature phases.
        if 6 in self.edges and 7 in self.edges and nrev:
            b = self.edges[7]
            histogram = {}
            for offset in range(0, nrev, BLOCK):
                zz = z[offset : min(nrev, offset + BLOCK) + 1]
                counts = np.diff(np.searchsorted(b, zz, side="left"))
                unique, n = np.unique(counts, return_counts=True)
                for value, frequency in zip(unique, n):
                    histogram[int(value)] = histogram.get(int(value), 0) + int(
                        frequency
                    )
            b_reference = expected or max(
                histogram, key=lambda value: (histogram[value], value)
            )
            self.b_reference_ppr = b_reference
            self.missing_pulse_totals["B"] = 0
            for offset in range(0, nrev, BLOCK):
                zz = z[offset : min(nrev, offset + BLOCK) + 1]
                counts = np.diff(np.searchsorted(b, zz, side="left"))
                self.missing_pulse_totals["B"] += int(
                    np.maximum(b_reference - counts, 0).sum()
                )
                for j in np.flatnonzero(counts != b_reference):
                    if not expected and counts[j] >= b_reference:
                        continue
                    event = {
                        "sample": int(zz[j]),
                        "time": float(self.data[zz[j], 0]),
                        "end_time": float(self.data[zz[j + 1], 0]),
                        "channel": "B",
                        "pulse": int(np.searchsorted(b, zz[j])),
                        "observed": int(counts[j]),
                        "expected": int(b_reference),
                        "missing": max(0, int(b_reference - counts[j])),
                        "reference_source": (
                            "entered" if expected else "most common observed count"
                        ),
                    }
                    event["kind"] = (
                        "missing pulses" if event["missing"] else "extra pulses"
                    )
                    self.error_count += 1
                    event["signal_domain"] = "receiver"
                    self.add_faults([event])
                    if len(self.errors) < 10000:
                        self.errors.append(event)
        slew_cache = {}
        for c in self.available:
            measurement_c = 6 + c // 2 if c < 6 and 6 + c // 2 in self.available else c
            inverted = c < 6 and c % 2 == 1 and measurement_c != c
            r = self.falls[measurement_c] if inverted else self.edges[measurement_c]
            f = self.edges[measurement_c] if inverted else self.falls[measurement_c]
            periods = total_high = total_low = duty_sum = 0.0
            cycles = oos = valid = 0
            hist = np.zeros(40, dtype="i8")
            outside = 0
            mean = (
                (float(self.data[r[-1], 0] - self.data[r[0], 0]) / (len(r) - 1))
                if len(r) > 1
                else None
            )
            for k in range(0, max(0, len(r) - 1), BLOCK):
                self.check()
                rr = r[k : min(len(r) - 1, k + BLOCK) + 1]
                t = self.data[rr, 0]
                p = np.diff(t)
                fi = np.searchsorted(f, rr[:-1], side="right")
                ok = fi < len(f)
                ff = (
                    f[np.minimum(fi, max(0, len(f) - 1))]
                    if len(f)
                    else np.zeros(len(fi), dtype="i8")
                )
                ok &= ff < rr[1:]
                highwidth = self.data[ff, 0] - t[:-1]
                d = 100 * highwidth[ok] / p[ok] if c not in (4, 5, 8) else np.array([])
                total_high += float(highwidth[ok].sum())
                total_low += float((p[ok] - highwidth[ok]).sum())
                duty_sum += float(d.sum())
                valid += len(d)
                outside_duty = (d < 45 - 1e-9) | (d > 55 + 1e-9)
                if c not in (4, 5, 8):
                    oos += int(outside_duty.sum())
                cycles += len(p)
                if c in (6, 7):
                    bad = np.flatnonzero(ok)[outside_duty]
                    self.add_faults(
                        [
                            {
                                "sample": int(rr[j]),
                                "time": float(t[j]),
                                "end_time": float(t[j + 1]),
                                "channel": (NAMES + ["Rx A", "Rx B", "Rx Z"])[c],
                                "pulse": int(k + j + 1),
                                "kind": "duty outside 45–55%",
                                "signal_domain": "receiver",
                                "duty": float(100 * highwidth[j] / p[j]),
                            }
                            for j in bad
                        ]
                    )
                jitter = (p - mean) / mean * 100 if mean else np.array([])
                hist += np.histogram(jitter, bins=40, range=(-5, 5))[0]
                outside += int(((jitter < -5) | (jitter > 5)).sum())
            channel_ppr = (
                (int(np.searchsorted(r, z[-1])) - int(np.searchsorted(r, z[0]))) / nrev
                if nrev
                else None
            )
            rise = (
                None
                if self.options.get("slew_mode") == "none"
                else self.transition_average(c, r, True)
            )
            fall = (
                None
                if self.options.get("slew_mode") == "none"
                else self.transition_average(c, f, False)
            )
            if self.options.get("slew_mode") == "online":
                base = c - c % 2 if c < 6 else (c - 6) * 2
                if base not in slew_cache:
                    slew_cache[base] = online_slew(self, base)
                rise, fall = slew_cache[base][::-1] if inverted else slew_cache[base]
            ones = 0
            for k in (range(0, self.count, BLOCK) if c not in (4, 5, 8) else ()):
                ones += int(np.count_nonzero(self.logic[k : k + BLOCK, measurement_c]))
            width_high, width_low = self.width_means(r, f)
            sample_duty = 100 * ones / self.count if c not in (4, 5, 8) else None
            if inverted and sample_duty is not None:
                sample_duty = 100 - sample_duty
            self.metrics.append(
                {
                    "channel": (NAMES + ["Rx A", "Rx B", "Rx Z"])[c],
                    "c": c,
                    "rise": rise,
                    "fall": fall,
                    "high": width_high,
                    "low": width_low,
                    "period": mean,
                    "ppr": channel_ppr,
                    "frequency": 1 / mean if mean else None,
                    "duty": sample_duty,
                    "cycle_duty": duty_sum / valid if valid else None,
                    "oos": oos,
                    "edges": len(r),
                    "hist": hist.tolist(),
                    "outside": outside,
                }
            )
        self.differential_vpp = self.mean_differential_vpp()
        self.update_z_metrics()
        self.logic.flush()
        from phase import phase_cycle_outliers

        self.phase_analysis = phase_cycle_outliers(self)
        from health_analysis import analyse_health

        self.advanced_analysis = None if getattr(self, "_threshold_probe", False) else analyse_health(self)
        self.faultdb.execute("CREATE INDEX fault_time ON faults(time)")
        self.faultdb.execute("CREATE INDEX fault_domain_time ON faults(json_extract(payload, '$.signal_domain'), time)")
        self.faultdb.commit()
        self.faultdb.close()
        self.faultdb = None
        self.phase_degrees, self.phase_dt, self.phase_pairs = self.phase_statistics()

    def mean_differential_vpp(self, edges=None):
        """Mean raw (positive minus negative) peak-to-peak per complete cycle."""
        edges = self.edges if edges is None else edges
        result = {}
        for pair, label in ((0, "A/-A"), (2, "B/-B"), (4, "Z/-Z")):
            if pair not in self.present or pair + 1 not in self.present:
                continue
            rises = edges.get(6 + pair // 2, [])
            total = 0.0
            cycles = 0
            carry_min, carry_max = np.inf, -np.inf
            if len(rises) >= 2:
                for start in range(int(rises[0]), int(rises[-1]), BLOCK):
                    self.check()
                    stop = min(start + BLOCK, int(rises[-1]))
                    inner = rises[
                        np.searchsorted(rises, start, side="right") : np.searchsorted(
                            rises, stop
                        )
                    ]
                    cuts = np.r_[start, inner, stop].astype("i8") - start
                    values = (
                        self.data[start:stop, pair + 1]
                        - self.data[start:stop, pair + 2]
                    )
                    lows = np.minimum.reduceat(values, cuts[:-1])
                    highs = np.maximum.reduceat(values, cuts[:-1])
                    lows[0] = min(lows[0], carry_min)
                    highs[0] = max(highs[0], carry_max)
                    index = np.searchsorted(rises, stop)
                    complete = (
                        len(lows)
                        if index < len(rises) and rises[index] == stop
                        else len(lows) - 1
                    )
                    total += float((highs[:complete] - lows[:complete]).sum())
                    cycles += complete
                    if complete < len(lows):
                        carry_min, carry_max = float(lows[-1]), float(highs[-1])
                    else:
                        carry_min, carry_max = np.inf, -np.inf
            result[label] = dict(
                volts=total / cycles if cycles else None, cycles=cycles
            )
        return result

    def update_z_metrics(self):
        """Index timing uses validated revolutions; index duty is not applicable."""
        z = self.validated_z
        falls = self.falls.get(8, [])
        n = max(0, len(z) - 1)
        high = period = None
        hist = np.zeros(40, dtype="i8")
        outside = 0
        if n and len(falls):
            period = float(self.data[z[-1], 0] - self.data[z[0], 0]) / n
            total = 0.0
            count = 0
            for k in range(0, n, BLOCK):
                self.check()
                zz = z[k : min(n, k + BLOCK) + 1]
                fi = np.searchsorted(falls, zz[:-1], side="right")
                ok = fi < len(falls)
                ff = falls[np.minimum(fi, len(falls) - 1)]
                ok &= ff < zz[1:]
                total += float((self.data[ff[ok], 0] - self.data[zz[:-1][ok], 0]).sum())
                count += int(ok.sum())
                jitter = (np.diff(self.data[zz, 0]) - period) / period * 100
                hist += np.histogram(jitter, bins=40, range=(-5, 5))[0]
                outside += int(((jitter < -5) | (jitter > 5)).sum())
            high = total / count if count else None
        low = period - high if period is not None and high is not None else None
        for m in self.metrics:
            if m["c"] not in (4, 5, 8):
                continue
            negative = m["c"] == 5
            m.update(
                high=low if negative else high,
                low=high if negative else low,
                period=period,
                ppr=1.0 if n else None,
                frequency=1 / period if period else None,
                duty=None,
                cycle_duty=None,
                oos=0,
                hist=hist.tolist(),
                outside=outside,
                duty_basis="not applicable to index channels",
                validated_cycles=n,
            )

    def z_eyes(self, lo, hi):
        """Index detail preserves edges and shares one trigger/scale for both sides."""
        z = self.validated_z
        falls = self.falls.get(8, [])
        result = {}
        scopes = {}
        labels = {}
        first = int(np.searchsorted(z, lo))
        last = int(np.searchsorted(z, hi))
        nearby = first == last
        chosen = (
            np.linspace(first, last - 1, min(50, last - first), dtype=int)
            if last > first
            else []
        )

        def pulse(j):
            rise = int(z[j])
            fi = int(np.searchsorted(falls, rise, side="right"))
            if fi >= len(falls):
                return None
            fall = int(falls[fi])
            width = float(self.data[fall, 0] - self.data[rise, 0])
            t = float(self.data[rise, 0])
            if width <= 0 or t - width < self.start or t + 2 * width > self.end:
                return None
            return rise, fall, width

        pulses = [v for j in chosen if (v := pulse(j)) is not None]
        if not pulses and len(z):
            candidates = sorted(
                range(max(0, first - 2), min(len(z), first + 3)),
                key=lambda j: abs(j - first),
            )
            pulses = [v for j in candidates if (v := pulse(j)) is not None][:1]
            nearby = True
        reference = float(np.median([p[2] for p in pulses])) if pulses else 1.0
        for c in (4, 5):
            if c not in self.present:
                continue
            traces = []
            for rise, fall, width in pulses:
                t0 = float(self.data[rise, 0]) - reference
                t1 = float(self.data[rise, 0]) + 2 * reference
                a = max(0, int(np.searchsorted(self.data[:, 0], t0)))
                b = min(self.count - 1, int(np.searchsorted(self.data[:, 0], t1)))
                anchors = np.clip(
                    np.array(
                        [rise - 1, rise, rise + 1, fall - 1, fall, fall + 1, a, b]
                    ),
                    a,
                    b,
                )
                ids = np.unique(
                    np.r_[np.linspace(a, b, min(160, b - a + 1), dtype=int), anchors]
                )
                traces.append(
                    [
                        [
                            float((self.data[i, 0] - t0) / (t1 - t0) * 2),
                            float(self.data[i, c + 1]),
                        ]
                        for i in ids
                    ]
                )
            result[str(c)] = traces
            scopes[str(c)] = (
                "Nearby index pulses with complete trigger context"
                if nearby
                else "Visible index pulses"
            )
            if not traces:
                scopes[str(c)] = "Need a complete Z pulse with surrounding samples"
            labels[str(c)] = "Z-triggered index detail (3 reference pulse widths)"
        return result, scopes, labels

    def validate_z(self):
        rises = self.edges.get(8, np.array([], dtype="i8"))
        falls = self.falls.get(8, np.array([], dtype="i8"))
        path = self.dir / "validated-z.bin"
        self.z_rejected = 0
        self.z_detected = 0
        with open(path, "wb") as dest:
            for k in range(0, len(rises), BLOCK):
                self.check()
                r = rises[k : k + BLOCK]
                idx = np.searchsorted(falls, r, side="right")
                valid = idx < len(falls)
                if len(falls):
                    valid &= (
                        falls[np.minimum(idx, len(falls) - 1)]
                        < np.r_[
                            rises[k + 1 : min(len(rises), k + len(r) + 1)], self.count
                        ][: len(r)]
                    )
                r[valid].astype("<i8").tofile(dest)
        valid = (
            np.memmap(path, dtype="<i8", mode="r")
            if path.stat().st_size
            else np.array([], dtype="i8")
        )
        self.z_detected = len(valid)
        if 3 <= len(valid) <= 1000000:
            idx = np.searchsorted(falls, valid, side="right")
            width = self.data[falls[idx], 0] - self.data[valid, 0]
            sorted_width = np.sort(width)
            ratios = sorted_width[1:] / sorted_width[:-1]
            where = int(np.argmax(ratios)) + 1
            if ratios[where - 1] >= 4 and len(valid) - where >= 2:
                cutoff = float(np.sqrt(sorted_width[where - 1] * sorted_width[where]))
                keep = width >= cutoff
                if keep.sum() >= 2:
                    filtered = self.dir / "validated-z-filtered.bin"
                    valid[keep].astype("<i8").tofile(filtered)
                    self.z_rejected = int((~keep).sum())
                    valid = np.memmap(filtered, dtype="<i8", mode="r")
        self.validated_z = (
            self.filter_many_z(valid, falls) if len(valid) > 1000000 else valid
        )

    def filter_many_z(self, valid, falls):
        """Width-cluster validation for arbitrarily many Z events, using disk sorting."""
        path = self.dir / "z-width-sort.bin"
        widths = np.memmap(path, dtype="<f8", mode="w+", shape=(len(valid),))
        try:
            for k in range(0, len(valid), BLOCK):
                self.check()
                z = valid[k : k + BLOCK]
                fi = np.searchsorted(falls, z, side="right")
                widths[k : k + len(z)] = self.data[falls[fi], 0] - self.data[z, 0]
            widths.sort(kind="heapsort")
            best = 0.0
            where = 0
            for k in range(0, len(widths) - 1, BLOCK):
                self.check()
                w = widths[k : min(len(widths), k + BLOCK + 1)]
                ratios = w[1:] / w[:-1]
                i = int(np.argmax(ratios))
                if ratios[i] > best:
                    best = float(ratios[i])
                    where = k + i + 1
            if best < 4 or len(valid) - where < 2:
                return valid
            cutoff = float(np.sqrt(widths[where - 1] * widths[where]))
            target = self.dir / ("validated-z-filtered-" + uuid.uuid4().hex + ".bin")
            retained = 0
            with target.open("wb") as f:
                for k in range(0, len(valid), BLOCK):
                    self.check()
                    z = valid[k : k + BLOCK]
                    fi = np.searchsorted(falls, z, side="right")
                    w = self.data[falls[fi], 0] - self.data[z, 0]
                    keep = z[w >= cutoff]
                    keep.astype("<i8").tofile(f)
                    retained += len(keep)
            self.z_rejected = len(valid) - retained
            return np.memmap(target, dtype="<i8", mode="r")
        finally:
            widths._mmap.close()
            path.unlink(missing_ok=True)

    def add_faults(self, events):
        for event in events:
            event.setdefault("signal_domain", "raw")
        self.faultdb.executemany(
            "INSERT INTO faults(time,payload) VALUES (?,?)",
            ((e["time"], json.dumps(e)) for e in events),
        )
        self.flag_count += len(events)

    def faults(self, start, end, limit=500, signal_domain=None):
        where = "time>=? AND time<=?"
        params = (start,end)
        if signal_domain:
            where += " AND json_extract(payload, '$.signal_domain')=?"
            params += (signal_domain,)
        with closing(sqlite3.connect(self.dir / "faults.sqlite")) as db:
            total = db.execute(
                "SELECT COUNT(*) FROM faults WHERE "+where, params
            ).fetchone()[0]
            events = []
            for i, payload in db.execute(
                "SELECT id,payload FROM faults WHERE "+where+" ORDER BY time,id LIMIT ?",
                params+(limit,),
            ):
                e = json.loads(payload)
                e["id"] = i
                self.fault_window(e)
                events.append(e)
        return {"count": total, "events": events}

    def set_rpm(self, value):
        entered = float(value) if str(value).strip() else None
        if entered is not None and (not math.isfinite(entered) or entered <= 0):
            raise ValueError(
                "RPM must be greater than zero, or leave it blank for automatic Z timing"
            )
        self.options["rpm"] = str(value).strip()
        self.rpm = entered if entered is not None else self.measured_rpm
        self.rpm_source = (
            "entered"
            if entered is not None
            else (
                "Z timing"
                if self.measured_rpm is not None
                else "needs time and two Z edges"
            )
        )
        self.effective_time_scale = (
            (60 / entered / self.z_period)
            if entered and self.z_period
            else (1.0 if self.seconds else None)
        )

    def phase_statistics(self, edges=None):
        from phase import phase_stats

        edges = self.edges if edges is None else edges
        stats = phase_stats(self, self, [edges.get(6, []), edges.get(7, [])])
        self.phase_coherence = stats["coherence"] if stats else None
        self.phase_coverage = stats["coverage"] if stats else 0.0
        if stats is None:
            return None, None, 0
        angle = stats["phase_degrees"]
        return angle, angle / 360 * stats["period"], stats["pairs"]

    def display_metrics(self):
        output = []
        scale = self.effective_time_scale
        for original in self.metrics:
            m = dict(original)
            m["base_period"] = m["period"]
            for key in ("rise", "fall", "high", "low", "period"):
                if m[key] is not None and scale is not None:
                    m[key] *= scale
            if m["ppr"] is not None and self.rpm is not None:
                m["frequency"] = m["ppr"] * self.rpm / 60
            elif m["frequency"] is not None and scale is not None:
                m["frequency"] /= scale
            m["time_unit"] = (
                "s"
                if scale is not None
                else ("time units" if self.has_time else "samples")
            )
            m["frequency_unit"] = (
                "Hz"
                if scale is not None
                else "per sample" if not self.has_time else "per time unit"
            )
            output.append(m)
        return output

    def diagnostics(self):
        scale = self.effective_time_scale
        sample_rate = (
            (self.count - 1) / (self.end - self.start) if self.seconds else None
        )
        if sample_rate is None and self.rpm and len(self.validated_z) > 1:
            sample_rate = (
                float(self.validated_z[-1] - self.validated_z[0])
                / (len(self.validated_z) - 1)
                * self.rpm
                / 60
            )
        phases = [m for m in self.metrics if m["c"] in (6, 7)]
        duties = [m["duty"] for m in phases if m["duty"] is not None]
        periods = [m["period"] for m in phases if m["period"] is not None]
        return {
            "sample_rate": sample_rate,
            "differential_vpp": getattr(self, "differential_vpp", {}),
            "oos_cycles": sum(m["oos"] for m in phases),
            "mean_duty": float(np.mean(duties)) if duties else None,
            "mean_period": (
                60 / self.rpm / self.ppr
                if self.options.get("rpm") and self.rpm and self.ppr
                else float(np.mean(periods)) * (scale or 1) if periods else None
            ),
            "angular_period": 360 / self.ppr if self.ppr else None,
            "pulses_per_degree": self.ppr / 360 if self.ppr else None,
            "phase_degrees": self.phase_degrees,
            "phase_coherence": self.phase_coherence,
            "phase_coverage": self.phase_coverage,
            "phase_dt": (
                self.phase_dt * (scale or 1) if self.phase_dt is not None else None
            ),
            "phase_pairs": self.phase_pairs,
            "validated_revolutions": max(0, len(self.validated_z) - 1),
            "z_rejected": self.z_rejected,
            "reference_ppr": self.reference_ppr,
            "time_scale": scale,
            "display_unit": (
                "s"
                if scale is not None
                else "time units" if self.has_time else "samples"
            ),
        }

    def wav_sample_rate(self):
        z = self.validated_z
        if len(z) > 1 and self.rpm:
            path = self.dir / ("wav-intervals-" + uuid.uuid4().hex + ".bin")
            widths = np.memmap(path, dtype="<i8", mode="w+", shape=(len(z) - 1,))
            try:
                for k in range(0, len(z) - 1, BLOCK):
                    widths[k : min(len(z) - 1, k + BLOCK)] = np.diff(
                        z[k : min(len(z) - 1, k + BLOCK) + 1]
                    )
                widths.sort(kind="heapsort")
                n = len(widths)
                median = (
                    float(widths[n // 2])
                    if n % 2
                    else (int(widths[n // 2 - 1]) + int(widths[n // 2])) / 2
                )
                return max(1, math.floor(median * self.rpm / 60 + 0.5))
            finally:
                widths._mmap.close()
                path.unlink(missing_ok=True)
        return round(self.diagnostics()["sample_rate"] or getattr(self, "rate", 48000))

    def width_means(self, r, f):
        output = []
        for first, second in ((r, f), (f, r)):
            total = 0.0
            n = 0
            for k in range(0, len(first), BLOCK):
                e = first[k : k + BLOCK]
                j = np.searchsorted(second, e, side="right")
                valid = j < len(second)
                if np.any(valid):
                    total += float(
                        (self.data[second[j[valid]], 0] - self.data[e[valid], 0]).sum()
                    )
                    n += int(valid.sum())
            output.append(total / n if n else None)
        return output

    def span_measurements(self, start, end):
        """Measure complete cycles in a selected span without copying recording samples."""
        import copy

        start = max(self.start, float(start))
        end = min(self.end, float(end))
        if end <= start:
            raise ValueError("Select start < end")
        lo = int(np.searchsorted(self.data[:, 0], start))
        hi = int(np.searchsorted(self.data[:, 0], end, side="right"))
        edges = {
            c: e[np.searchsorted(e, lo) : np.searchsorted(e, hi)]
            for c, e in self.edges.items()
        }
        falls = {
            c: e[np.searchsorted(e, lo) : np.searchsorted(e, hi)]
            for c, e in self.falls.items()
        }
        z = self.validated_z
        z = z[np.searchsorted(z, lo) : np.searchsorted(z, hi)]
        if len(z):
            fz = self.falls[8]
            fi = np.searchsorted(fz, z, side="right")
            z = z[(fi < len(fz)) & (fz[np.minimum(fi, len(fz) - 1)] < hi)]
        nrev = max(0, len(z) - 1)
        scope = copy.copy(self)
        scope.metrics = []
        scope.edges = edges
        scope.falls = falls
        scope.validated_z = z
        scope.count = hi - lo
        scope.start = start
        scope.end = end
        scope.options = self.options.copy()
        scope.z_period = (
            float(self.data[z[-1], 0] - self.data[z[0], 0]) / nrev if nrev else None
        )
        scope.measured_rpm = (
            60 / scope.z_period if scope.seconds and scope.z_period else None
        )
        scope.set_rpm(scope.options.get("rpm", ""))
        scope.ppr = (
            (
                int(np.searchsorted(edges.get(6, edges.get(7, [])), z[-1]))
                - int(np.searchsorted(edges.get(6, edges.get(7, [])), z[0]))
            )
            / nrev
            if nrev
            else None
        )
        for original in self.metrics:
            c = original["c"]
            mc = 6 + c // 2 if c < 6 and 6 + c // 2 in edges else c
            inverted = c < 6 and c % 2 and mc != c
            r = falls[mc] if inverted else edges[mc]
            f = edges[mc] if inverted else falls[mc]
            mean = (
                float(self.data[r[-1], 0] - self.data[r[0], 0]) / (len(r) - 1)
                if len(r) > 1
                else None
            )
            valid = 0
            hs = ls = ds = 0.0
            oos = outside = 0
            hist = np.zeros(40, dtype="i8")
            for k in range(0, max(0, len(r) - 1), BLOCK):
                rr = r[k : min(len(r) - 1, k + BLOCK) + 1]
                t = self.data[rr, 0]
                period = np.diff(t)
                fi = np.searchsorted(f, rr[:-1], side="right")
                ok = fi < len(f)
                ff = (
                    f[np.minimum(fi, max(0, len(f) - 1))]
                    if len(f)
                    else np.zeros(len(fi), dtype="i8")
                )
                ok &= ff < rr[1:]
                high = self.data[ff[ok], 0] - t[:-1][ok]
                d = high / period[ok] * 100 if c not in (4, 5, 8) else np.array([])
                valid += len(d)
                hs += float(high.sum())
                ls += float((period[ok] - high).sum())
                ds += float(d.sum())
                if c not in (4, 5, 8):
                    oos += int(((d < 45 - 1e-9) | (d > 55 + 1e-9)).sum())
                jitter = (period - mean) / mean * 100 if mean else np.array([])
                hist += np.histogram(jitter, bins=40, range=(-5, 5))[0]
                outside += int(((jitter < -5) | (jitter > 5)).sum())
            ppr = (
                (int(np.searchsorted(r, z[-1])) - int(np.searchsorted(r, z[0]))) / nrev
                if nrev
                else None
            )
            ones = sum(
                int(np.count_nonzero(self.logic[k : min(hi, k + BLOCK), mc]))
                for k in (range(lo, hi, BLOCK) if c not in (4, 5, 8) else ())
            )
            duty = 100 * ones / (hi - lo) if hi > lo and c not in (4, 5, 8) else None
            if inverted and duty is not None:
                duty = 100 - duty
            sr, sf = (
                online_slew(self, c, lo, hi)
                if self.options.get("slew_mode") == "online"
                else (
                    self.transition_average(c, r, True),
                    self.transition_average(c, f, False),
                )
            )
            wh, wl = self.width_means(r, f)
            scope.metrics.append(
                dict(
                    original,
                    edges=len(r),
                    rise=sr,
                    fall=sf,
                    period=mean,
                    high=wh,
                    low=wl,
                    duty=duty,
                    oos=oos,
                    hist=hist.tolist(),
                    outside=outside,
                    ppr=ppr,
                    frequency=1 / mean if mean else None,
                )
            )
        scope.differential_vpp = self.mean_differential_vpp(edges)
        scope.update_z_metrics()
        scope.phase_degrees, scope.phase_dt, scope.phase_pairs = scope.phase_statistics(
            edges
        )
        diagnostic = scope.diagnostics()
        diagnostic["sample_rate"] = self.diagnostics()["sample_rate"]
        return dict(
            metrics=scope.display_metrics(),
            diagnostics=diagnostic,
            count=scope.count,
            ppr=scope.ppr,
            start=start,
            end=end,
            rpm=scope.rpm,
            rpm_source=scope.rpm_source,
        )

    def next_fault(self, after_time=None, after_id=0, kind=None, exclude_kind=None):
        condition = "json_extract(payload, '$.kind')=?" if kind else "1=1"
        params = (kind,) if kind else ()
        if exclude_kind:
            condition += " AND COALESCE(json_extract(payload, '$.kind'),'')<>?"
            params += (exclude_kind,)
        with closing(sqlite3.connect(self.dir / "faults.sqlite")) as db:
            row = None
            if after_time is not None:
                row = db.execute(
                    "SELECT id,payload FROM faults WHERE " + condition + " AND time>=? AND (time>? OR id>?) ORDER BY time,id LIMIT 1",
                    params + (after_time, after_time, after_id),
                ).fetchone()
            if row is None:
                row = db.execute(
                    "SELECT id,payload FROM faults WHERE " + condition + " ORDER BY time,id LIMIT 1", params
                ).fetchone()
        if row is None:
            return None
        event = json.loads(row[1])
        event["id"] = row[0]
        self.fault_window(event)
        return event

    def fault_window(self, event):
        channel = event["channel"].replace("Rx ", "").replace("−", "-")
        c = {"A": 6, "-A": 6, "B": 7, "-B": 7}.get(channel, self.replay_channel)
        edges = self.edges.get(c, [])
        if len(edges) < 2:
            edges = self.replay_edges
        if len(edges) < 2:
            event.update(zoom_start=self.start, zoom_end=self.end, zoom_cycles=0)
            return
        center = (event["time"] + event.get("end_time", event["time"])) / 2
        sample = int(np.searchsorted(self.data[:, 0], center))
        middle = int(np.searchsorted(edges, sample))
        left = max(0, min(middle - 8, len(edges) - 17))
        right = min(len(edges) - 1, left + 16)
        event.update(
            zoom_start=float(self.data[edges[left], 0]),
            zoom_end=float(self.data[edges[right], 0]),
            zoom_cycles=right - left,
        )

    def transition_average(self, c, edges, rising):
        """Interpolate local 10/90 crossings only for resolved analog ramps.
        A local 17-sample window defines the transition plateaus. A single
        sample jump cannot establish analog rise time and is excluded.
        """
        total = 0.0
        count = 0
        for k in range(0, len(edges), 8192):
            self.check()
            e = edges[k : k + 8192]
            ids = np.clip(e[:, None] + np.arange(-8, 9), 0, self.count - 1)
            p = (c - 6) * 2 if c >= 6 else c
            if (
                (c >= 6 or c in (0, 2, 4))
                and p in self.present
                and p + 1 in self.present
            ):
                v = self.data[ids, p + 1] - self.data[ids, p + 2]
            else:
                v = self.data[ids, (p if p in self.present else p + 1) + 1]
                if c >= 6 and p not in self.present:
                    v = -v
            if not rising:
                v = -v
            lo = v.min(1)
            span = v.max(1) - lo
            a = lo + 0.1 * span
            b = lo + 0.9 * span

            def crossing(level):
                ok = (v[:, :-1] <= level[:, None]) & (v[:, 1:] > level[:, None])
                distances = np.abs(np.arange(16) - 7.5)
                index = np.argmin(np.where(ok, distances, 1e9), axis=1)
                rows = np.arange(len(v))
                denom = v[rows, index + 1] - v[rows, index]
                fraction = np.divide(
                    level - v[rows, index],
                    denom,
                    out=np.zeros(len(v)),
                    where=denom != 0,
                )
                t = self.data[ids, 0]
                times = t[rows, index] + fraction * (
                    t[rows, index + 1] - t[rows, index]
                )
                return times, index, np.any(ok, axis=1)

            ta, ia, oka = crossing(a)
            tb, ib, okb = crossing(b)
            valid = oka & okb & (ib > ia) & (tb > ta) & (span > 0)
            total += float((tb - ta)[valid].sum())
            count += int(valid.sum())
        return total / count if count else None

    def build_overview(self):
        self.overview_path = self.dir / "overview.bin"
        stride = 1024
        self.overview_stride = stride
        with open(self.overview_path, "wb") as f:
            for k in range(0, self.count, stride):
                if k % (stride * 128) == 0:
                    self.check()
                    self.progress = 0.95 + 0.04 * k / self.count
                a = self.data[k : k + stride]
                l = self.logic[k : k + stride]
                np.r_[
                    a[0, 0],
                    a[-1, 0],
                    a[:, 1:].min(0),
                    a[:, 1:].max(0),
                    l.min(0),
                    l.max(0),
                    a[0, 1:],
                ].astype("<f8").tofile(f)
        self.overview = np.memmap(
            self.overview_path,
            dtype="<f8",
            mode="r",
            shape=(math.ceil(self.count / stride), 38),
        )

    def read_edges(self, c, kind):
        path = self.dir / f"{kind}{c}.bin"
        return (
            np.memmap(path, dtype="<i8", mode="r")
            if path.stat().st_size
            else np.array([], dtype="i8")
        )

    def pulse_counts(self, offset=0, limit=200):
        """Page complete revolutions without sending unbounded data to the browser."""
        total = max(0, len(self.validated_z) - 1)
        offset = max(0, min(total, int(offset)))
        limit = max(1, min(200, int(limit)))
        stop = min(total, offset + limit)
        z = self.validated_z[offset : stop + 1]
        channels = {}
        primary = "A" if 6 in self.edges else "B"
        if self.reference_ppr is not None:
            channels[primary] = (
                self.edges.get(6, self.edges.get(7, [])),
                self.reference_ppr,
            )
        if self.b_reference_ppr is not None:
            channels["B"] = (self.edges[7], self.b_reference_ppr)
        counts = {
            name: np.diff(np.searchsorted(edges, z, side="left"))
            for name, (edges, ref) in channels.items()
        }
        rows = []
        for i in range(stop - offset):
            missing = {
                name: max(0, int(ref - counts[name][i]))
                for name, (_, ref) in channels.items()
            }
            rows.append(
                dict(
                    revolution=offset + i + 1,
                    missing=missing,
                    total=sum(missing.values()),
                )
            )
        return dict(
            rows=rows,
            offset=offset,
            next_offset=stop if stop < total else None,
            revolutions=total,
            missing_total=sum(self.missing_pulse_totals.values()),
            missing_by_channel=self.missing_pulse_totals,
        )

    def summary(self):
        from csv_export import layout

        return dict(
            id=self.id,
            stage=self.stage,
            progress=self.progress,
            error=self.error,
            ready=self.ready,
            **(
                dict(
                    filename=getattr(self, "display_name", self.source.name),
                    merge_info=getattr(self, "merge_info", None),
                    options=self.options,
                    count=self.count,
                    start=self.start,
                    end=self.end,
                    seconds=self.seconds,
                    headers=self.headers,
                    present=self.present,
                    low=self.low,
                    window=self.window,
                    requested_window=self.requested_window,
                    high=self.high,
                    bin_alignment=getattr(self, "bin_alignment", None),
                    bin_timebase=getattr(self, "bin_timebase", None),
                    metrics=self.display_metrics(),
                    diagnostics=self.diagnostics(),
                    has_time=self.has_time,
                    axis_unit=(
                        "s"
                        if self.seconds
                        else "time units" if self.has_time else "samples"
                    ),
                    flag_count=self.flag_count,
                    errors=self.errors,
                    error_count=self.error_count,
                    revolutions=self.revs,
                    pulse_counts=self.pulse_counts(),
                    phase_analysis=getattr(self, "phase_analysis", None),
                    advanced_analysis=getattr(self, "advanced_analysis", None),
                    csv_columns=getattr(self, "csv_columns", None),
                    csv_export_columns=layout(self),
                    ppr=self.ppr,
                    rpm=self.rpm,
                    rpm_source=self.rpm_source,
                    replay_available=bool(len(self.replay_edges)),
                    replay_ppr=self.replay_ppr,
                    first_z=(
                        float(self.data[self.validated_z[0], 0])
                        if len(self.validated_z)
                        else None
                    ),
                )
                if self.ready
                else {}
            ),
        )

    def plot(self, start, end, pixels=1400):
        start = max(self.start, float(start))
        end = min(self.end, float(end))
        if not math.isfinite(start + end) or end <= start:
            raise ValueError(
                "Select a finite span with start < end inside the recording"
            )
        lo = min(self.count - 1, int(np.searchsorted(self.data[:, 0], start)))
        hi = int(np.searchsorted(self.data[:, 0], end, side="right"))
        hi = max(lo + 1, min(self.count, hi))
        points = []
        # Min/max per bucket preserves narrow pulses; bounded response regardless of file size.
        step = max(1, math.ceil((hi - lo) / min(2000, max(100, pixels))))
        for k in range(lo, hi, step):
            stop = min(k + step, hi)
            if step >= self.overview_stride * 2 and hasattr(self, "overview"):
                first = math.ceil(k / self.overview_stride)
                last = stop // self.overview_stride
                mid = self.overview[first:last]
                left_stop = min(stop, first * self.overview_stride)
                right_start = max(left_stop, last * self.overview_stride)
                boundary = np.concatenate(
                    (self.data[k:left_stop], self.data[right_start:stop])
                )
                bl = np.concatenate(
                    (self.logic[k:left_stop], self.logic[right_start:stop])
                )
                amin = boundary[:, 1:].min(0) if len(boundary) else mid[0, 2:8]
                amax = boundary[:, 1:].max(0) if len(boundary) else mid[0, 8:14]
                lmin = bl.min(0) if len(bl) else mid[0, 14:23]
                lmax = bl.max(0) if len(bl) else mid[0, 23:32]
                if len(mid):
                    amin = np.minimum(amin, mid[:, 2:8].min(0))
                    amax = np.maximum(amax, mid[:, 8:14].max(0))
                    lmin = np.minimum(lmin, mid[:, 14:23].min(0))
                    lmax = np.maximum(lmax, mid[:, 23:32].max(0))
                points.append(
                    [
                        float(self.data[k, 0]),
                        float(self.data[stop - 1, 0]),
                        amin.tolist(),
                        amax.tolist(),
                        lmin.tolist(),
                        lmax.tolist(),
                        self.data[k, 1:].tolist(),
                    ]
                )
            else:
                a = self.data[k:stop]
                l = self.logic[k:stop]
                points.append(
                    [
                        float(a[0, 0]),
                        float(a[-1, 0]),
                        a[:, 1:].min(0).tolist(),
                        a[:, 1:].max(0).tolist(),
                        l.min(0).tolist(),
                        l.max(0).tolist(),
                        a[0, 1:].tolist(),
                    ]
                )
        eyes = {}
        eye_scope = {}
        eye_oos = {}
        for c in self.present:
            if c >= 4:
                continue
            if c not in self.edges:
                continue
            receiver = 6 + c // 2
            r = (
                (self.falls[receiver] if c % 2 else self.edges[receiver])
                if receiver in self.available
                else self.edges[c]
            )
            p = int(np.searchsorted(r, lo, side="left"))
            last = int(np.searchsorted(r, hi - 1, side="right")) - 3
            traces = []
            trace_faults = []
            eye_scope[str(c)] = "Visible span"
            if len(r) < 3:
                eyes[str(c)] = []
                eye_oos[str(c)] = []
                eye_scope[str(c)] = (
                    "Need at least three rising edges for a two-period eye"
                )
                continue
            if last < p:
                p = max(0, min(len(r) - 3, p - 1))
                last = p
                eye_scope[str(c)] = "Nearby complete cycles (visible span is too short)"
            for j in np.linspace(p, last, min(50, last - p + 1), dtype=int):
                falls = (
                    (self.edges[receiver] if c % 2 else self.falls[receiver])
                    if receiver in self.available
                    else self.falls[c]
                )
                fault = False
                for cycle in (j, j + 1):
                    fi = int(np.searchsorted(falls, r[cycle], side="right"))
                    if fi < len(falls) and falls[fi] < r[cycle + 1]:
                        duty = (
                            100
                            * float(self.data[falls[fi], 0] - self.data[r[cycle], 0])
                            / float(self.data[r[cycle + 1], 0] - self.data[r[cycle], 0])
                        )
                        fault |= c < 4 and (duty < 45 - 1e-9 or duty > 55 + 1e-9)
                trace_faults.append(bool(fault))
                x = int(r[j])
                stop = int(r[j + 2])
                ids = np.linspace(x, stop, min(200, stop - x + 1), dtype=int)
                traces.append(
                    [
                        [
                            float(
                                (self.data[i, 0] - self.data[x, 0])
                                / (self.data[stop, 0] - self.data[x, 0])
                                * 2
                            ),
                            float(self.data[i, c + 1]),
                        ]
                        for i in ids
                    ]
                )
            eyes[str(c)] = traces
            eye_oos[str(c)] = trace_faults
        zi, zscope, zlabels = self.z_eyes(lo, hi)
        eyes.update(zi)
        eye_scope.update(zscope)
        eye_oos.update({c: [False] * len(traces) for c, traces in zi.items()})
        return {
            "eye_labels": zlabels,
            "points": points,
            "start": start,
            "end": end,
            "eyes": eyes,
            "eye_scope": eye_scope,
            "eye_oos": eye_oos,
            "faults": self.faults(start, end),
            "receiver_faults": self.faults(start,end,signal_domain="receiver"),
        }

    def position(self, t):
        a = self.replay_edges
        z = self.edges.get(8, [])
        if not len(a):
            return {"available": False}
        t = max(self.start, min(self.end, float(t)))
        i = max(
            0,
            min(
                self.count - 1,
                int(np.searchsorted(self.data[:, 0], t, side="right")) - 1,
            ),
        )
        zi = int(np.searchsorted(z, i, side="right")) - 1
        origin = int(z[zi]) if zi >= 0 else 0
        l = int(np.searchsorted(a, origin, side="right"))
        h = int(np.searchsorted(a, i, side="right"))
        count = int(self.replay_prefix[h] - self.replay_prefix[l])
        rpm = float(self.options.get("rpm") or self.rpm or 0)
        elapsed = t - float(self.data[origin, 0])

        # Integrate the same quadrature direction used by pulse-counted position.
        # Time, rather than pulse count, keeps genuine missing pulses visible.
        def directed_clock(at):
            j = max(
                0,
                int(
                    np.searchsorted(
                        a,
                        int(np.searchsorted(self.data[:, 0], at, side="right")) - 1,
                        side="right",
                    )
                )
                - 1,
            )
            sign = int(self.replay_prefix[j + 1] - self.replay_prefix[j])
            return float(self.replay_clock[j]) + (at - float(self.data[a[j], 0])) * sign

        directed_elapsed = directed_clock(t) - directed_clock(
            float(self.data[origin, 0])
        )
        expected = (directed_elapsed * rpm * 6) % 360 if self.seconds and rpm else None
        if self.options.get("rpm") and zi >= 0 and zi + 1 < len(z):
            expected = (
                directed_elapsed
                / float(self.data[z[zi + 1], 0] - self.data[origin, 0])
                * 360
            ) % 360
        if len(z) < 2 and len(a) > 1 and hasattr(self, "replay_expected"):
            # Without a complete Z revolution RPM controls playback speed, not
            # the recording's time-base calibration. Infer normal pulse timing;
            # retain extra expected counts across unusually long pulse gaps.
            def pulse_clock(at):
                sample = int(np.searchsorted(self.data[:, 0], at, side="right")) - 1
                j = max(
                    0,
                    min(len(a) - 2, int(np.searchsorted(a, sample, side="right")) - 1),
                )
                left, right = float(self.data[a[j], 0]), float(self.data[a[j + 1], 0])
                return float(self.replay_expected[j]) + (at - left) / (
                    right - left
                ) * float(self.replay_expected[j + 1] - self.replay_expected[j])

            expected = (
                (pulse_clock(t) - pulse_clock(float(self.data[origin, 0])))
                * 360
                / self.replay_ppr
            ) % 360
        z_angle = 0.0
        if self.replay_channel == 8 and len(z) > 1:
            left = max(0, min(len(z) - 2, zi))
            period = float(self.data[z[left + 1], 0] - self.data[z[left], 0])
            z_angle = (elapsed / period * 360) % 360
        return dict(
            available=True,
            angle=(
                (count * 360 / self.replay_ppr) % 360
                if self.replay_channel != 8
                else z_angle
            ),
            count=count,
            expected=expected,
            expected_source="Z timing" if len(z) > 1 else "pulse timing estimate",
            time=t,
            channel={6: "A", 7: "B", 8: "Z"}[self.replay_channel],
            direction_known=self.direction_known,
            ppr=self.replay_ppr,
            ppr_estimated=not bool(self.ppr),
            at_end=t >= self.end,
        )

    def build_replay(self):
        self.replay_channel = next(
            (c for c in (6, 7, 8) if len(self.edges.get(c, []))), None
        )
        self.replay_edges = self.edges.get(
            self.replay_channel, np.array([], dtype="i8")
        )
        self.replay_ppr = self.reference_ppr or self.ppr or 8192.0
        other = 7 if self.replay_channel == 6 else 6
        self.direction_known = self.replay_channel in (6, 7) and other in self.available
        self.replay_prefix = np.memmap(
            self.dir / "replay-prefix.bin",
            dtype="<i8",
            mode="w+",
            shape=(len(self.replay_edges) + 1,),
        )
        self.replay_prefix[0] = 0
        self.replay_clock = np.memmap(
            self.dir / "replay-clock.bin",
            dtype="<f8",
            mode="w+",
            shape=(max(1, len(self.replay_edges)),),
        )
        clock_total = 0.0
        previous_time = None
        previous_direction = 1
        total = 0
        for k in range(0, len(self.replay_edges), BLOCK):
            self.check()
            e = self.replay_edges[k : k + BLOCK]
            if self.direction_known:
                clockwise = self.logic[e, other] == (
                    0 if self.replay_channel == 6 else 1
                )
                others = self.edges[other]
                if len(others) > 1:
                    ix = np.searchsorted(others, e)
                    valid = (ix > 0) & (ix < len(others))
                    before = (
                        self.data[e, 0] - self.data[others[np.maximum(0, ix - 1)], 0]
                    )
                    after = (
                        self.data[others[np.minimum(len(others) - 1, ix)], 0]
                        - self.data[e, 0]
                    )
                    unequal = valid & (before != after) & (before > 0) & (after > 0)
                    edge_clockwise = (
                        after < before if self.replay_channel == 6 else before < after
                    )
                    clockwise[unequal] = edge_clockwise[unequal]
                direction = np.where(clockwise, 1, -1)
            else:
                direction = np.ones(len(e), dtype="i8")
            times = np.asarray(self.data[e, 0])
            durations = np.empty(len(e), dtype="f8")
            durations[0] = (
                0
                if previous_time is None
                else (times[0] - previous_time) * previous_direction
            )
            durations[1:] = np.diff(times) * direction[:-1]
            clocks = np.cumsum(durations) + clock_total
            self.replay_clock[k : k + len(e)] = clocks
            clock_total = float(clocks[-1])
            previous_time = float(times[-1])
            previous_direction = int(direction[-1])
            sums = np.cumsum(direction) + total
            self.replay_prefix[k + 1 : k + 1 + len(e)] = sums
            total = int(sums[-1])
        self.replay_prefix.flush()
        self.replay_clock.flush()
        self.replay_expected = np.memmap(
            self.dir / "replay-expected.bin",
            dtype="<f8",
            mode="w+",
            shape=(max(1, len(self.replay_edges)),),
        )
        self.replay_expected[0] = 0
        expected_total = 0.0
        # Small groups track speed changes without treating an isolated gap as
        # the normal period. Extra missing counts remain independent of edges.
        for k in range(0, len(self.replay_edges) - 1, 64):
            self.check()
            stop = min(len(self.replay_edges) - 1, k + 64)
            e = self.replay_edges[k : stop + 1]
            periods = np.diff(self.data[e, 0])
            normal = float(np.median(periods))
            multiples = np.maximum(1, np.rint(periods / normal))
            signs = np.diff(self.replay_prefix[k : stop + 1])
            values = np.cumsum(multiples * signs) + expected_total
            self.replay_expected[k + 1 : stop + 1] = values
            expected_total = float(values[-1])
        self.replay_expected.flush()

    def advance(self, t, seconds):
        rpm = float(self.options.get("rpm") or 0)
        z = self.edges.get(8, [])
        if rpm > 0 and len(z) > 1:
            sample = int(np.searchsorted(self.data[:, 0], t, side="right")) - 1
            j = max(
                0, min(len(z) - 2, int(np.searchsorted(z, sample, side="right")) - 1)
            )
            a = float(self.data[z[j], 0])
            b = float(self.data[z[j + 1], 0])
            phase = j + (t - a) / (b - a) + seconds * rpm / 60
            j2 = math.floor(phase)
            if j2 >= len(z) - 1:
                return min(
                    self.end,
                    float(self.data[z[-1], 0]) + (phase - (len(z) - 1)) * (b - a),
                )
            j2 = max(0, j2)
            a2 = float(self.data[z[j2], 0])
            b2 = float(self.data[z[j2 + 1], 0])
            return max(self.start, min(self.end, a2 + (phase - j2) * (b2 - a2)))
        if rpm > 0 and len(self.replay_edges) > 1:
            edges = self.replay_edges
            sample = int(np.searchsorted(self.data[:, 0], t, side="right")) - 1
            index = max(
                0,
                min(
                    len(edges) - 2,
                    int(np.searchsorted(edges, sample, side="right")) - 1,
                ),
            )
            left = float(self.data[edges[index], 0])
            right = float(self.data[edges[index + 1], 0])
            phase = (
                index
                + (t - left) / (right - left)
                + seconds * rpm / 60 * self.replay_ppr
            )
            target = math.floor(phase)
            if target >= len(edges) - 1:
                return self.end
            target = max(0, target)
            a = float(self.data[edges[target], 0])
            b = float(self.data[edges[target + 1], 0])
            return max(self.start, min(self.end, a + (phase - target) * (b - a)))
        return min(self.end, t + seconds * (1 if self.seconds else 100))
