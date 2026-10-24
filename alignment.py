"""Independent A/B phase validation, with bounded-memory edge extraction.

An index-free reference establishes relative phase, not absolute revolution ID.
Time scaling is inferred from pulse periods, never from recording duration.
"""

import math
import shutil
from pathlib import Path
import numpy as np
from engine import Recording, BLOCK, MAX_BYTES, CACHE, hysteresis
from compatibility import auto_levels, receiver_states


def rising_edges(owner, recording, prefix):
    try:
        low, high = auto_levels(recording.data, [0, 1, 2, 3])
    except ValueError:
        return [np.array([], dtype="i8"), np.array([], dtype="i8")]
    result = []
    for channel in (0, 2):
        path = owner.dir / f"{prefix}-{channel}-alignment.bin"
        previous = [0, 0, 0]
        with path.open("wb") as dest:
            for first in range(0, recording.count, BLOCK):
                owner.check()
                data = recording.data[first : first + BLOCK]
                p = hysteresis(data[:, channel + 1], low, high, previous[0])
                n = hysteresis(data[:, channel + 2], low, high, previous[1])
                state, decisive = receiver_states(p, n, previous[2])
                changes = np.diff(np.r_[previous[2], state].astype("i2"))
                if first == 0 and np.any(decisive):
                    changes[int(np.flatnonzero(decisive)[0])] = 0
                (np.flatnonzero(changes == 1) + first).astype("<i8").tofile(dest)
                previous = [int(p[-1]), int(n[-1]), int(state[-1])]
        result.append(
            np.memmap(path, dtype="<i8", mode="r")
            if path.stat().st_size
            else np.array([], dtype="i8")
        )
    return result


def phase_stats(owner, recording, edges):
    from phase import phase_stats as local_phase_stats

    return local_phase_stats(owner, recording, edges, min_edges=5)


def compare_phase(owner, result, main, ref):
    result = dict(result)
    # Reanalysis may remove usable edges or change the measured phase. Do not
    # retain an earlier match or its numbers when the new comparison is invalid.
    for key in (
        "reason",
        "time_scale",
        "sample_scale",
        "time_scale_unit",
        "merged_phase_degrees",
        "reference_phase_degrees",
        "phase_difference_degrees",
        "tolerance_degrees",
        "scaled_reference_period",
        "scaled_reference_phase_offset",
        "compared_pairs",
        "merged_coherence",
        "reference_coherence",
        "merged_coverage",
        "reference_coverage",
    ):
        result.pop(key, None)
    result["status"] = "inconclusive"
    if main is None or ref is None:
        result["reason"] = (
            "Need at least five rising edges on both A and B, with comparable A/B periods."
        )
        return result
    scale = main["period"] / ref["period"]
    delta = (main["phase_degrees"] - ref["phase_degrees"] + 180) % 360 - 180
    uncertainty = 360 * (
        main["sample_step"] / main["period"] + ref["sample_step"] / ref["period"]
    )
    tolerance = max(5.0, uncertainty)
    result.update(
        time_scale=scale,
        sample_scale=(main["period"] / main["sample_step"])
        / (ref["period"] / ref["sample_step"]),
        time_scale_unit=f"{owner.time_heading} units per {result['reference_time_heading']} unit",
        merged_phase_degrees=main["phase_degrees"],
        reference_phase_degrees=ref["phase_degrees"],
        phase_difference_degrees=delta,
        tolerance_degrees=tolerance,
        scaled_reference_period=ref["period"] * scale,
        scaled_reference_phase_offset=ref["phase_degrees"]
        / 360
        * ref["period"]
        * scale,
        compared_pairs=min(main["pairs"], ref["pairs"]),
        merged_coherence=main["coherence"],
        reference_coherence=ref["coherence"],
        merged_coverage=main["coverage"],
        reference_coverage=ref["coverage"],
    )
    if uncertainty > 20:
        result["reason"] = (
            "Sampling resolution is too low for a reliable phase comparison."
        )
    elif ref["coherence"] < 0.9:
        result["reason"] = (
            "A/B phase varies in the reference capture; a single reference phase is unreliable."
        )
    elif main["coherence"] < 0.9:
        result["reason"] = (
            "Merged A/B phase varies within the Z intervals. Separate captures can have "
            "different motion between index pulses; Z synchronization cannot verify their phase there."
        )
    elif min(main["coverage"], ref["coverage"]) < 0.8:
        result["reason"] = (
            "Too few A cycles contain exactly one B rising edge for a reliable comparison."
        )
    else:
        result["status"] = "match" if abs(delta) <= tolerance else "mismatch"
    return result


def refresh_alignment(owner, result):
    """Compare the stored reference with the Rx edges used by measurements.

    The reference can be read once during merging; threshold/filter reanalysis
    must still update its comparison against the newly analysed main signals.
    """
    if "reference_stats" not in result:
        return dict(result)
    empty = np.array([], dtype="i8")
    main = phase_stats(
        owner, owner, [owner.edges.get(6, empty), owner.edges.get(7, empty)]
    )
    result = compare_phase(owner, result, main, result["reference_stats"])
    result["comparison_basis"] = "Analysed RS422 Rx A/B rising edges"
    refined = (
        getattr(owner, "merge_info", {}).get("refinement", {}).get("applied", False)
    )
    result["comparison_independent"] = not refined
    if refined:
        result["note"] = (
            "B timing was refined using this reference. The residual comparison "
            "describes the constructed fit, not an independent alignment check. "
            "The reference has no Z, so absolute revolution and whole-cycle offsets "
            "cannot be verified."
        )
    return result


def check_alignment(owner, path):
    source = Path(path).resolve()
    if not source.is_file() or source.stat().st_size > MAX_BYTES:
        raise ValueError("Alignment capture must exist and be no larger than 10 GiB")
    # Capture C has independent scope export settings, units, time base and levels.
    reference = Recording(
        source, {"capture_role": "C", "delimiter": "auto", "timecol": "auto"}
    )
    reference.check = owner.check
    edges = []
    try:
        owner.stage = "Importing optional A/B alignment capture"
        reference.ingest()
        owner.stage = "Scaling and comparing A/B alignment across both recordings"
        main_edges = rising_edges(owner, owner, "merged")
        edges.extend(main_edges)
        ref_edges = rising_edges(owner, reference, "reference")
        edges.extend(ref_edges)
        main = phase_stats(owner, owner, main_edges)
        ref = phase_stats(owner, reference, ref_edges)
        result = dict(
            source=source.name,
            status="inconclusive",
            reference_samples=reference.count,
            reference_time_heading=reference.time_heading,
            reference_stats=ref,
            note="Relative A/B phase only; no Z in the reference, so absolute revolution and whole-cycle offsets cannot be verified.",
        )
        return compare_phase(owner, result, main, ref)
    finally:
        for e in edges:
            if isinstance(e, np.memmap):
                e._mmap.close()
        if hasattr(reference, "data"):
            reference.data._mmap.close()
        if reference.dir.resolve().parent != CACHE.resolve():
            raise ValueError("Invalid alignment cache location")
        shutil.rmtree(reference.dir)
