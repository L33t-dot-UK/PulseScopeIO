"""Blockwise import of single-channel UINT16 BIN with scope BXM XML metadata."""

from pathlib import Path
import xml.etree.ElementTree as ET
import numpy as np


def metadata(path):
    path = Path(path).resolve()
    if path.suffix.lower() != ".bin" or not path.is_file():
        raise ValueError("Choose an existing .bin recording")
    meta = next(
        (
            p
            for p in path.parent.iterdir()
            if p.name.lower() == path.with_suffix(".bxm").name.lower()
        ),
        None,
    )
    if meta is None:
        raise ValueError(f"Missing matching BXM metadata for {path.name}")
    if meta.stat().st_size > 1024 * 1024:
        raise ValueError("BXM metadata exceeds 1 MiB")
    raw = meta.read_bytes()
    if b"<!DOCTYPE" in raw.upper() or b"<!ENTITY" in raw.upper():
        raise ValueError("BXM document entities are not supported")
    props = [
        e.attrib for e in ET.fromstring(raw).iter() if e.tag.split("}")[-1] == "Prop"
    ]
    values = {}
    for prop in props:
        key = prop.get("Name")
        if key in values:
            raise ValueError("Ambiguous BXM metadata properties")
        values[key] = prop.get("Value")
    try:
        if (
            values["DataFormat"] != "UINT16"
            or values.get("InterleavedTraceCount", "1") != "1"
            or values.get("MultiChannelExport", "1") != "1"
        ):
            raise ValueError("Use single-channel UINT16 BIN/BXM recordings")
        if values["MSBFirst"] not in ("0", "1"):
            raise ValueError("Invalid BXM byte order")
        count = int(values["RecordLength"])
        start, stop, low, high = [
            float(values[k]) for k in ("XStart", "XStop", "BaseYStart", "BaseYStop")
        ]
        units = {p.get("Name"): p.get("Unit") for p in props}
        if (
            units.get("XStart") != "s"
            or units.get("XStop") != "s"
            or units.get("BaseYStart") != "V"
            or units.get("BaseYStop") != "V"
        ):
            raise ValueError("BXM must specify seconds and volts")
        if (
            count < 2
            or not np.all(np.isfinite([start, stop, low, high]))
            or stop <= start
            or high <= low
        ):
            raise ValueError("Invalid BXM sample count, timing or voltage range")
        if path.stat().st_size != count * 2:
            raise ValueError("BIN length does not match BXM RecordLength")
        return dict(
            path=path,
            meta=meta,
            count=count,
            start=start,
            step=(stop - start) / (count - 1),
            low=low,
            scale=(high - low) / 65535,
            dtype=">u2" if values["MSBFirst"] == "1" else "<u2",
        )
    except KeyError as exc:
        raise ValueError(f"BXM metadata is missing {exc.args[0]}") from exc


def ingest(recording):
    from engine import BLOCK, MAX_BYTES, NAMES

    paths = recording.options["binary_paths"]
    if (
        not isinstance(paths, dict)
        or not paths
        or any(k not in NAMES for k in paths)
        or len(paths) > 6
    ):
        raise ValueError("Select one to six named BIN channels")
    channels = [(NAMES.index(name), metadata(path)) for name, path in paths.items()]
    if len({m["path"] for _, m in channels}) != len(channels):
        raise ValueError("Each channel must use a different BIN file")
    if sum(m["path"].stat().st_size for _, m in channels) > MAX_BYTES:
        raise ValueError("Maximum combined BIN source size is 10 GiB")
    align = bool(recording.options.get("bin_align_90"))
    by_channel = dict(channels)
    if align and (0 not in by_channel or 2 not in by_channel):
        raise ValueError("90-degree alignment requires A and B BIN channels")
    ref = by_channel.get(0, channels[0][1])
    # Preserve real time from BXM, but put independently triggered captures on
    # a shared elapsed-time origin. No absolute synchronisation is implied.
    duration = min((m["count"] - 1) * m["step"] for _, m in channels)
    count = min(ref["count"], int(np.floor(duration / ref["step"] + 0.1)) + 1)
    if count < 2:
        raise ValueError("BIN time bases have no usable common duration")
    scaled = []
    origins = []
    for c, m in channels:
        if abs(m["step"] - ref["step"]) * max(count - 1, 1) > ref["step"] * 0.1:
            scaled.append(
                dict(
                    channel=NAMES[c],
                    sample_scale=m["step"] / ref["step"],
                    source_rate=1 / m["step"],
                    target_rate=1 / ref["step"],
                )
            )
        if abs(m["start"] - ref["start"]) > ref["step"] * 0.1:
            origins.append(NAMES[c])
    trimmed = any(
        abs((m["count"] - 1) * m["step"] - (count - 1) * ref["step"])
        > ref["step"] * 0.1
        for _, m in channels
    )
    recording.bin_timebase = dict(
        applied=bool(scaled or origins or trimmed),
        scaled_channels=scaled,
        rebased_channels=origins,
        trimmed=trimmed,
        reference_channel=NAMES[next(c for c, m in channels if m is ref)],
        note="Channels mapped to the reference sample rate using nearest-sample resampling and their BXM timing. Capture starts aligned by elapsed time; absolute synchronisation is not verified. Common duration retained; edge timing and pulse widths have resampling resolution.",
    )
    maps = []
    try:
        maps = [
            (c, m, np.memmap(m["path"], dtype=m["dtype"], mode="r"))
            for c, m in channels
        ]
        with open(recording.dir / "samples.bin", "wb") as out:
            for k in range(0, count, BLOCK):
                recording.check()
                n = min(BLOCK, count - k)
                data = np.zeros((n, 7), dtype="<f8")
                data[:, 0] = ref["start"] + np.arange(k, k + n) * ref["step"]
                for c, m, raw in maps:
                    if any(item["channel"] == NAMES[c] for item in scaled):
                        indices = np.minimum(
                            m["count"] - 1,
                            np.floor(
                                np.arange(k, k + n) * ref["step"] / m["step"] + .5 + 1e-7
                            ).astype("i8"),
                        )
                        values = raw[indices]
                    else:
                        values = raw[np.minimum(np.arange(k, k + n), m["count"] - 1)]
                    data[:, c + 1] = m["low"] + values.astype("f8") * m["scale"]
                data.tofile(out)
                recording.progress = 0.4 * (k + n) / count
    finally:
        for _, _, raw in maps:
            raw._mmap.close()
    recording.count = count
    recording.present = sorted(c for c, _ in channels)
    recording.seconds = recording.has_time = True
    recording.source_time_unit = "s"
    recording.time_heading = "Time_s"
    recording.headers = ["Time_s"] + [NAMES[c] for c in recording.present]
    recording.rate = 1 / ref["step"]
    recording.display_name = "BIN channels: " + ", ".join(
        NAMES[c] for c in recording.present
    )
    recording.data = np.memmap(
        recording.dir / "samples.bin", dtype="<f8", mode="r", shape=(recording.count, 7)
    )
    recording.start = float(recording.data[0, 0])
    recording.end = float(recording.data[-1, 0])

    if align:
        align_quadrature(recording)


def align_quadrature(recording):
    """Translate B/-B by whole samples; never interpolate, stretch or wrap."""
    from engine import BLOCK
    from phase import phase_stats

    edges = []
    for c in (0, 2):
        path = recording.dir / f"bin-alignment-{c}.bin"
        previous = False
        with path.open("wb") as out:
            for k in range(0, recording.count, BLOCK):
                recording.check()
                v = recording.data[k : k + BLOCK, c + 1]
                if c + 1 in recording.present:
                    state = v - recording.data[k : k + BLOCK, c + 2] > 0
                else:
                    threshold = float(recording.options.get("high", 2.5))
                    state = v > threshold
                before = np.r_[previous, state[:-1]]
                (np.flatnonzero(state & ~before) + k).astype("<i8").tofile(out)
                previous = bool(state[-1])
        edges.append(
            np.memmap(path, dtype="<i8", mode="r")
            if path.stat().st_size
            else np.array([], dtype="i8")
        )
    try:
        stats = phase_stats(recording, recording, edges)
        if (
            not stats
            or stats["pairs"] < 5
            or stats["coherence"] < 0.99
            or stats["coverage"] < 0.8
            or stats["period"] / stats["sample_step"] < 20
        ):
            raise ValueError(
                "Cannot establish stable 90-degree alignment without changing pulse widths. Use captures with matching rotation speed and sufficient clean A/B cycles."
            )
        target = 90.0 if stats["phase_degrees"] >= 0 else -90.0
        delta = (target - stats["phase_degrees"] + 180) % 360 - 180
        shift = int(round(delta / 360 * stats["period"] / stats["sample_step"]))
        start = max(0, shift)
        stop = min(recording.count, recording.count + shift)
        if stop - start < 2:
            raise ValueError("No overlapping samples after 90-degree alignment")
        target_path = recording.dir / "aligned-samples.bin"
        with target_path.open("wb") as out:
            for k in range(start, stop, BLOCK):
                recording.check()
                n = min(BLOCK, stop - k)
                block = np.array(recording.data[k : k + n], copy=True)
                for c in (2, 3):
                    if c in recording.present:
                        block[:, c + 1] = recording.data[
                            k - shift : k - shift + n, c + 1
                        ]
                block.tofile(out)
        # Verify the translated edge train before replacing the imported data.
        probe = type("AlignmentView", (), {})()
        probe.data = np.memmap(
            target_path, dtype="<f8", mode="r", shape=(stop - start, 7)
        )
        probe.count = stop - start
        shifted = [
            (
                e[(e >= start) & (e < stop)] - start
                if i == 0
                else e[(e + shift >= start) & (e + shift < stop)] + shift - start
            )
            for i, e in enumerate(edges)
        ]
        try:
            fitted = phase_stats(recording, probe, shifted)
            tolerance = max(1.0, 360 * stats["sample_step"] / stats["period"])
            if (
                not fitted
                or fitted["coherence"] < 0.99
                or abs((fitted["phase_degrees"] - target + 180) % 360 - 180) > tolerance
            ):
                raise ValueError(
                    "A constant shift cannot produce 90-degree alignment without changing pulse widths; captures have incompatible pulse timing"
                )
            a, b = shifted
            window = min(256, max(5, (len(a) - 1) // 8))
            for k in range(0, len(a) - 1, window):
                recording.check()
                aa = a[k : k + window + 1]
                ix = np.searchsorted(b, aa)
                valid = np.diff(ix) == 1
                if np.any(valid):
                    left, right = aa[:-1][valid], aa[1:][valid]
                    phase = (b[ix[:-1][valid]] - left) / (right - left) * 360
                    # Average short cycle groups so ordinary edge jitter is not
                    # mistaken for sustained rotation-speed drift.
                    error = abs(float(np.mean((phase - target + 180) % 360 - 180)))
                    if error > max(2.0, float(np.mean(360 / (right - left)))) * 1.5:
                        raise ValueError(
                            "Rotation speeds differ: cannot hold A/B at 90 degrees with a constant shift while preserving pulse widths"
                        )
        finally:
            probe.data._mmap.close()
        recording.data._mmap.close()
        recording.data = np.memmap(
            target_path, dtype="<f8", mode="r", shape=(stop - start, 7)
        )
        recording.count = stop - start
        recording.start = float(recording.data[0, 0])
        recording.end = float(recording.data[-1, 0])
        recording.bin_alignment = dict(
            assumed=True,
            target_degrees=target,
            shift_samples=shift,
            phase_degrees=fitted["phase_degrees"],
            note="Assumed quadrature: B/-B shifted without resampling. Pulse widths and duty cycles preserved; partial endpoints trimmed. This is not an independent alignment measurement.",
        )
        recording.display_name += " (assumed 90-degree alignment)"
    finally:
        for e in edges:
            if isinstance(e, np.memmap):
                e._mmap.close()
