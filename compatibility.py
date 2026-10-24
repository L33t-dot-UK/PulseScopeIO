"""Bounded-memory equivalents of online timing and receiver helpers."""

import re, math
import numpy as np


def time_unit(header):
    s = header.strip().lower().replace("µ", "u").replace("μ", "u")
    s = s.rstrip(")] ").strip()
    for pattern, scale, name in [
        (r"millisecond|(?:^|[_\W])ms$", 1e-3, "ms"),
        (r"microsecond|(?:^|[_\W])us$", 1e-6, "us"),
        (r"nanosecond|(?:^|[_\W])ns$", 1e-9, "ns"),
        (r"second|time.?s\b|_s$|^sec$|^in\s+s$", 1.0, "s"),
    ]:
        if re.search(pattern, s):
            return scale, name
    return None, "time units"


def threshold_candidates(data, present):
    if len(data) <= 10000:
        selected = data[:, 1:]
    else:
        # A fixed stride can sample the same position in every encoder cycle,
        # making clean changing signals appear constant. Take one reproducible
        # sample per stratum instead; the read remains bounded for large files.
        bounds = np.linspace(0, len(data), 10001, dtype="i8")
        positions = np.random.default_rng(0).integers(bounds[:-1], bounds[1:])
        selected = data[positions, 1:]
    values = np.asarray(selected[:, present])
    v = np.sort(values.ravel())
    if len(v) < 10:
        raise ValueError("Need at least ten numeric values to find logic levels")
    q = lambda a, f: float(a[math.floor((len(a) - 1) * f)])
    v = v[(v >= q(v, 0.001)) & (v <= q(v, 0.999))]
    if v[-1] <= v[0]:
        raise ValueError("No two distinguishable signal levels")
    hist, edges = np.histogram(v, bins=256)
    weight = np.cumsum(hist)
    sums = np.cumsum(hist * np.arange(256))
    high = len(v) - weight
    valid = (weight > 0) & (high > 0)
    score = np.full(256, -np.inf)
    score[valid] = (
        weight[valid]
        * high[valid]
        * (sums[valid] / weight[valid] - (sums[-1] - sums[valid]) / high[valid]) ** 2
    )
    split = edges[int(np.argmax(score)) + 1]
    low = v[v < split]
    high = v[v >= split]
    if not len(low) or not len(high):
        raise ValueError("Could not separate low and high signal levels")
    separation = q(high, 0.5) - q(low, 0.5)
    midpoint = q(low, 0.5) + separation / 2
    pairs = [(midpoint, midpoint)]
    pairs.extend(
        (q(low, l), q(high, h))
        for l in (0.65, 0.8, 0.9, 0.97)
        for h in (0.03, 0.1, 0.2, 0.35)
    )
    pairs.extend(
        (split - separation * f, split + separation * f)
        for f in (0.01, 0.025, 0.05, 0.1, 0.2)
    )
    pairs = list(dict.fromkeys((l, h) for l, h in pairs if l <= h))
    active = [
        c
        for i, c in enumerate(present)
        if np.ptp(values[:, i]) > max((v[-1] - v[0]) * 1e-4, np.finfo(float).eps)
    ]
    if not active:
        raise ValueError("No changing channels were found")
    return pairs, active


def auto_levels(data, present):
    return threshold_candidates(data, present)[0][0]


def receiver_states(positive, negative, previous):
    decisive = positive != negative
    idx = np.maximum.accumulate(np.where(decisive, np.arange(len(positive)), -1))
    return (
        np.where(idx >= 0, positive[np.maximum(idx, 0)], previous).astype("u1"),
        decisive,
    )


def smooth_chunk(values, first, last, window, centered=True):
    # values includes sufficient halo; output corresponds to [first:last].
    n = len(values)
    ix = np.arange(first, last)
    left = (window - 1) // 2 if centered else window - 1
    right = window - 1 - left
    a = np.maximum(0, ix - left)
    b = np.minimum(n, ix + right + 1)
    cs = np.r_[0.0, np.cumsum(values)]
    return (cs[b] - cs[a]) / (b - a)


def adaptive_radius(states, block=131072):
    hist = np.zeros((2, 4097), dtype="i8")
    previous = int(states[0])
    run = 0
    first_run = True
    for k in range(0, len(states), block):
        x = np.asarray(states[k : k + block])
        positions = np.flatnonzero(np.diff(np.r_[previous, x].astype("i2")))
        if not len(positions):
            run += len(x)
            continue
        lengths = np.diff(np.r_[0, positions, len(x)])
        lengths[0] += run
        levels = np.r_[previous, x[positions]]
        for level in (0, 1):
            selected = lengths[:-1][levels[:-1] == level]
            if first_run and previous == level and len(selected):
                selected = selected[1:]
            hist[level] += np.bincount(np.minimum(selected, 4096), minlength=4097)
        first_run = False
        run = int(lengths[-1])
        previous = int(x[-1])
    qs = []
    for h in hist:
        if not h.sum():
            return 0
        qs.append(
            int(np.searchsorted(np.cumsum(h), math.ceil((h.sum() - 1) * 0.9) + 1))
        )
    nominal = min(qs)
    return min(6, max(1, nominal - 1)) if nominal >= 2 else 0


def smoothing_limit(data, present, low, high, hysteresis, check, block=131072):
    limit = 10
    for c in present:
        hist = np.zeros((2, 12), dtype="i8")
        previous = 0
        run = 0
        transitions = 0
        for k in range(0, len(data), block):
            check()
            state = hysteresis(data[k : k + block, c + 1], low, high, previous)
            changes = np.flatnonzero(np.diff(np.r_[previous, state].astype("i2")))
            if not len(changes):
                run += len(state)
                continue
            lengths = np.diff(np.r_[0, changes, len(state)])
            lengths[0] += run
            levels = np.r_[previous, state[changes]]
            for level in (0, 1):
                hist[level] += np.bincount(
                    np.minimum(lengths[:-1][levels[:-1] == level], 11), minlength=12
                )
            transitions += len(changes)
            run = int(lengths[-1])
            previous = int(state[-1])
        hist[previous, min(run, 11)] += 1
        if transitions and all(h.sum() for h in hist):
            med = [
                int(np.searchsorted(np.cumsum(h), (h.sum() - 1) // 2 + 1)) for h in hist
            ]
            limit = min(limit, max(1, min(med)))
    return limit


def online_slew(recording, c, lo=0, hi=None):
    """Online global 10/90 crossing pairing, with crossings spooled to disk."""
    import tempfile

    hi = recording.count if hi is None else hi
    p = (c - 6) * 2 if c >= 6 else c - c % 2
    inverted = c < 6 and c % 2 == 1
    paired = p in recording.present and p + 1 in recording.present
    channel = p if p in recording.present else p + 1
    sign = (
        -1
        if (inverted and paired)
        or (
            not paired
            and ((c >= 6 and channel % 2) or (c < 6 and c % 2 != channel % 2))
        )
        else 1
    )

    def values(first, last):
        left = (
            (recording.window - 1) // 2
            if recording.options.get("smoothing_mode", "centered") == "centered"
            else recording.window - 1
        )
        right = recording.window - 1 - left
        a = max(0, first - left)
        b = min(recording.count, last + right)
        v = recording.data[a:b, channel + 1].copy()
        if paired:
            v -= recording.data[a:b, p + 2]
        if recording.window > 1:
            v = smooth_chunk(
                v,
                first - a,
                last - a,
                recording.window,
                recording.options.get("smoothing_mode", "centered") == "centered",
            )
        else:
            v = v[first - a : last - a]
        return v * sign

    mn = math.inf
    mx = -math.inf
    for k in range(lo, hi, 65536):
        recording.check()
        v = values(k, min(hi, k + 65536))
        mn = min(mn, float(v.min()))
        mx = max(mx, float(v.max()))
    if mx <= mn:
        return None, None
    levels = [mn + 0.1 * (mx - mn), mn + 0.9 * (mx - mn)]
    with tempfile.TemporaryDirectory(dir=recording.dir, prefix="slew-") as folder:
        from pathlib import Path

        paths = [Path(folder) / str(i) for i in range(4)]
        files = [p.open("wb") for p in paths]
        try:
            for k in range(max(1, lo), hi, 65536):
                recording.check()
                stop = min(hi, k + 65536)
                v = values(k - 1, stop)
                a = v[:-1]
                b = v[1:]
                t = recording.data[k - 1 : stop - 1, 0]
                dt = recording.data[k:stop, 0] - t
                for j, (level, up) in enumerate(
                    [
                        (levels[0], True),
                        (levels[1], True),
                        (levels[1], False),
                        (levels[0], False),
                    ]
                ):
                    mask = (
                        (a < level) & (b >= level) if up else (a > level) & (b <= level)
                    )
                    crossings = (
                        t[mask] + (level - a[mask]) / (b[mask] - a[mask]) * dt[mask]
                    )
                    crossings.astype("<f8").tofile(files[j])
        finally:
            for f in files:
                f.close()
        arrays = [
            np.memmap(p, dtype="<f8", mode="r") if p.stat().st_size else np.array([])
            for p in paths
        ]
        result = []
        for low, high in (arrays[:2], arrays[2:]):
            total = 0.0
            n = 0
            next_high = 0
            for k in range(0, len(low), 65536):
                a = low[k : k + 65536]
                idx = np.searchsorted(high, a)
                steps = np.arange(len(a))
                idx = np.maximum.accumulate(np.maximum(idx - steps, next_high)) + steps
                valid = idx < len(high)
                if np.any(valid):
                    total += float((high[idx[valid]] - a[valid]).sum())
                    n += int(valid.sum())
                    next_high = int(idx[valid][-1]) + 1
            result.append(total / n if n else None)
        for a in arrays:
            if isinstance(a, np.memmap):
                a._mmap.close()
        return tuple(result)
