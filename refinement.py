"""Explicit, reference-derived B timing refinement on a disk-backed composite.

The reference supplies the requested relative phase. It does not validate the
result independently. Existing B waveforms are moved through a monotone sample
mapping; no ideal pulse train or replacement amplitudes are generated.
"""

import math
import numpy as np
from engine import BLOCK
from alignment import rising_edges


def median_period(edges):
    positions = np.linspace(0, len(edges) - 2, min(len(edges) - 1, 200000), dtype="i8")
    return float(np.median(edges[positions + 1] - edges[positions]))


def refine_b(owner, z, span_stop):
    """Refine B against local A cycles, retaining verified complete Z context.

    ``z`` contains validated index positions in the current composite. Ambiguous
    missing/extra A cycles and B losses at revolution boundaries are rejected;
    unambiguous internal missing B cycles remain missing in their ordinal slots.
    All arrays and output writes are bounded, including the timing map itself.
    """
    reference = owner.merge_info["alignment_check"]
    stats = reference.get("reference_stats")
    if (
        not stats
        or stats["coherence"] < 0.9
        or stats["coverage"] < 0.8
        or stats["sample_step"] / stats["period"] * 360 > 20
    ):
        raise ValueError(
            "B timing refinement needs a stable, sufficiently sampled A/B reference. "
            "Use Check alignment only or choose a clearer third capture."
        )
    if len(z) < 2:
        raise ValueError("B timing refinement needs at least two complete Z pulses")
    phase = float(stats["phase_degrees"])
    if not math.isfinite(phase):
        raise ValueError("The reference has no finite A/B phase")
    fraction = (phase / 360) % 1
    original = owner.data
    span_start = max(0, int(z[0]) - 1)
    span_stop = int(span_stop)
    edges = []
    timing = output = checked = None
    target_path = owner.dir / "refined-samples.bin"
    applied = False
    try:
        owner.stage = "Building reference-derived B pulse correspondence"
        edges = rising_edges(owner, owner, "refinement-source")
        a, b = edges
        if min(len(a), len(b)) < 5:
            raise ValueError("B timing refinement needs at least five A and B rising edges")
        pa, pb = median_period(a), median_period(b)
        if min(pa, pb) < 20:
            raise ValueError("B timing refinement needs at least 20 samples per pulse cycle")
        # A is the unchanged target clock. Inventing an A event to place B would
        # make an original fault disappear from the constructed correspondence.
        for first in range(0, len(a) - 1, BLOCK):
            owner.check()
            periods = np.diff(a[first : first + BLOCK + 1]) / pa
            if np.any(np.abs(periods - 1) > 0.2):
                raise ValueError(
                    "A has missing, extra or ambiguous pulse cycles. B timing cannot "
                    "be refined safely; use Check alignment only."
                )

        def grid(indices):
            indices = np.asarray(indices, dtype="i8")
            left = np.clip(indices, 0, len(a) - 2)
            width = a[left + 1] - a[left]
            return a[left] + (indices - left + fraction) * width

        def first_grid_at(sample):
            index = int(np.searchsorted(a, sample, side="left")) - 1
            if float(grid(index)) < sample - 1e-9:
                index += 1
            return index

        map_path = owner.dir / "refinement-map.bin"
        count = 0
        previous = None
        maximum_shift = 0.0

        with map_path.open("wb") as dest:

            def append(targets, sources):
                nonlocal count, previous, maximum_shift
                rows = np.column_stack((targets, sources)).astype("<f8")
                if not len(rows):
                    return
                if previous is not None:
                    if np.array_equal(rows[0], previous):
                        rows = rows[1:]
                        if not len(rows):
                            return
                    if np.any(rows[0] <= previous):
                        raise ValueError("B timing correspondence would reorder pulses")
                if np.any(np.diff(rows, axis=0) <= 0):
                    raise ValueError("B timing correspondence is not strictly increasing")
                maximum_shift = max(maximum_shift, float(np.abs(rows[:, 0] - rows[:, 1]).max()))
                rows.tofile(dest)
                count += len(rows)
                previous = rows[-1].copy()

            append([span_start], [span_start])
            for zi in range(len(z) - 1):
                owner.check()
                lo, hi = int(z[zi]), int(z[zi + 1])
                ia, stop_a = first_grid_at(lo), first_grid_at(hi)
                expected = stop_a - ia
                ib, stop_b = (int(v) for v in np.searchsorted(b, [lo, hi], side="left"))
                observed = stop_b - ib
                if expected < 1 or observed < 1 or observed > expected:
                    raise ValueError(
                        "B pulse counts cannot be matched unambiguously to the A/Z cycles. "
                        "Use Check alignment only."
                    )
                ordinal = 0
                for first in range(ib, stop_b, BLOCK):
                    owner.check()
                    stop = min(stop_b, first + BLOCK)
                    source = b[first:stop]
                    if first == ib:
                        steps = np.r_[0, np.diff(source) / pb]
                    else:
                        steps = np.diff(b[first - 1 : stop]) / pb
                    rounded = np.rint(steps).astype("i8")
                    start = 1 if first == ib else 0
                    if np.any(rounded[start:] < 1) or np.any(np.abs(steps - rounded) > 0.3):
                        raise ValueError(
                            "B has extra or ambiguous pulse spacing. Refinement cannot "
                            "locate its missing pulses safely; use Check alignment only."
                        )
                    numbers = ordinal + np.cumsum(rounded)
                    target = grid(ia + numbers)
                    if np.any((target < lo - 1e-9) | (target >= hi - 1e-9)):
                        raise ValueError("B pulse correspondence crosses a Z boundary")
                    append(target, source)
                    ordinal = int(numbers[-1])
                if ordinal != expected - 1:
                    raise ValueError(
                        "Missing B pulses at a Z boundary cannot be located unambiguously. "
                        "Use Check alignment only."
                    )
            # Partial cycles beyond the last full revolution are retained with
            # their original edge positions, never extended with invented data.
            tail = int(np.searchsorted(b, z[-1], side="left"))
            for first in range(tail, len(b), BLOCK):
                source = b[first : first + BLOCK]
                source = source[(source > span_start) & (source < span_stop - 1)]
                append(source, source)
            append([span_stop - 1], [span_stop - 1])
        timing = np.memmap(map_path, dtype="<f8", mode="r", shape=(count, 2))
        owner.stage = "Resampling B timing from the selected A/B reference"
        with target_path.open("wb") as dest:
            for first in range(span_start, span_stop, BLOCK):
                owner.check()
                stop = min(span_stop, first + BLOCK)
                target = np.arange(first, stop)
                ix = np.clip(np.searchsorted(timing[:, 0], target, side="right") - 1, 0, len(timing) - 2)
                left, right = timing[ix], timing[ix + 1]
                position = left[:, 1] + (target - left[:, 0]) / (right[:, 0] - left[:, 0]) * (right[:, 1] - left[:, 1])
                low = np.floor(position).astype("i8")
                high = np.minimum(low + 1, owner.count - 1)
                weight = (position - low)[:, None]
                block = np.array(original[first:stop], copy=True)
                block[:, 3:5] = original[low, 3:5] * (1 - weight) + original[high, 3:5] * weight
                block.astype("<f8").tofile(dest)
        output = np.memmap(target_path, dtype="<f8", mode="r", shape=(span_stop - span_start, 7))
        # Check the resulting captured transitions before committing the new map.
        class View:
            pass
        view = View()
        view.dir, view.data, view.count = owner.dir, output, len(output)
        checked = rising_edges(owner, view, "refinement-output")
        retained_source_rises = int(np.searchsorted(b, span_stop, side="left")) - int(
            np.searchsorted(b, span_start, side="right")
        )
        if len(checked[1]) != retained_source_rises:
            raise ValueError(
                "B timing refinement changed sampled edge counts in the retained span. "
                "Use Check alignment only or capture at a higher sample rate."
            )
        for first in range(0, len(z) - 1, BLOCK):
            owner.check()
            zz = z[first : first + BLOCK + 1]
            source_counts = np.diff(np.searchsorted(b, zz, side="left"))
            output_counts = np.diff(np.searchsorted(checked[1], zz - span_start, side="left"))
            if not np.array_equal(source_counts, output_counts):
                raise ValueError(
                    "B timing refinement changed sampled pulse counts. Use Check alignment "
                    "only or capture at a higher sample rate."
                )
        old_name = original.filename
        owner.data = output
        owner.count = len(output)
        owner.start, owner.end = float(output[0, 0]), float(output[-1, 0])
        owner.merge_info["retained_samples"] = owner.count
        owner.merge_info["resampled_b"] = True
        owner.merge_info["alignment_span_limited"] = True
        owner.merge_info["refinement"] = dict(
            applied=True,
            source=reference["source"],
            target_phase_degrees=phase,
            max_shift_samples=maximum_shift,
            method="Captured B waveform resampled onto local A cycles at the reference phase",
            independent_validation=False,
            note="B timing was changed using this reference; phase agreement is constructed, not an independent validation.",
        )
        original._mmap.close()
        # This is the intermediate composite, never either user input file.
        from pathlib import Path
        old_path = Path(old_name)
        if old_path.parent == owner.dir and old_path != target_path:
            try:
                old_path.unlink(missing_ok=True)
            except OSError:
                pass  # A retained intermediate cache is harmless.
        applied = True
    finally:
        for values in (edges, checked or []):
            for value in values:
                if isinstance(value, np.memmap) and not value._mmap.closed:
                    value._mmap.close()
        if isinstance(timing, np.memmap) and not timing._mmap.closed:
            timing._mmap.close()
        if not applied:
            if isinstance(output, np.memmap) and not output._mmap.closed:
                output._mmap.close()
            target_path.unlink(missing_ok=True)
