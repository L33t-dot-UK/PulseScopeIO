"""Deterministic synthetic captures and audit fixtures (no source recordings)."""

from pathlib import Path
import json
import numpy as np


def missing_pulse_cycles(total, rng):
    """Sparse seeded losses: mostly singles/pairs, at most three larger runs."""
    minimum = max(1, int(np.ceil(total * 0.001)))
    maximum = int(np.ceil(total * 0.01)) - 1
    if maximum < minimum:
        raise ValueError(
            "At least 101 pulses are needed to represent missing pulses below 1%"
        )
    remaining = int(rng.integers(minimum, maximum + 1))
    groups = []
    # Larger runs are optional and consume at most a quarter of all losses.
    bunches = int(rng.integers(0, min(3, remaining // 20) + 1))
    budget = min(remaining // 4, 10 * bunches)
    for i in range(bunches):
        size = int(rng.integers(3, min(10, budget - 3 * (bunches - i - 1)) + 1))
        groups.append(size)
        remaining -= size
        budget -= size
    while remaining:
        size = 2 if remaining >= 2 and rng.random() < 0.3 else 1
        groups.append(size)
        remaining -= size
    rng.shuffle(groups)
    # Each group has its own time stratum. Randomise its position within that
    # stratum, retaining a pulse at either boundary so groups cannot join.
    missing = []
    for i, size in enumerate(groups):
        first = i * total // len(groups) + 1
        stop = (i + 1) * total // len(groups) - size - 1
        start = int(rng.integers(first, max(first, stop) + 1))
        missing.extend(range(start, start + size))
    return np.asarray(missing, dtype=int)


def generate_capture(
    path,
    kind="clean",
    ppr=8192,
    revolutions=3,
    differential=True,
    seed=20261020,
    spp=16,
):
    if kind not in ("clean", "missing", "jitter", "duty", "reversed", "z-glitch", "bearing"):
        raise ValueError("Unknown synthetic data type")
    if not 1 <= ppr <= 8192 or not 2 <= revolutions <= 5 or not 16 <= spp <= 160:
        raise ValueError("Synthetic capture size outside limits")
    rng = np.random.default_rng(seed)
    rate = ppr * spp
    period = 1 / ppr
    total = ppr * revolutions
    cycles = np.arange(-1, total + 1)
    times = np.arange(-1, total * spp + 1, dtype=float) / rate
    offsets = (0.25, 0.5)
    signals = []
    missing = np.array([], dtype=int)
    bearing = None
    if kind == "missing":
        missing = missing_pulse_cycles(total, rng)
    elif kind == "bearing":
        low,high=int(np.ceil(ppr/360)),int(np.floor(5*ppr/360))
        if high<low:
            raise ValueError('Bearing-error capture needs at least 72 pulses/revolution')
        lost=int(rng.integers(low,high+1))
        first=ppr+int(rng.integers(max(1,ppr//5),max(2,3*ppr//5)))
        missing=np.arange(first,first+lost,dtype=int)
        bearing=dict(error_degrees=360*lost/ppr,missing_cycles=lost,ppr=ppr,
                     first_missing_cycle=first,start_time=first/ppr,end_time=(first+lost)/ppr,
                     reset_time=2.,seed=seed,
                     note='Both A and B omit the same complete electrical cycles. Uncorrected count-based bearing lags by this amount after the gap until the next Z reset; x1/x2/x4 decoding gives the same angular deficit when calibrated consistently. This is a specified decoder model, not a guarantee for every radar.')
    for phase, offset in enumerate(offsets):
        rises = (cycles + offset) * period
        falls = (
            cycles + offset + (0.375 if kind == "duty" and phase == 0 else 0.5)
        ) * period
        if kind == "jitter":
            rises += np.clip(rng.normal(0, 0.01, len(cycles)), -0.03, 0.03) * period
            falls += np.clip(rng.normal(0, 0.01, len(cycles)), -0.03, 0.03) * period
        keep = (
            ~np.isin(cycles, missing)
            if phase == 0 or kind == "bearing"
            else np.ones(len(cycles), dtype=bool)
        )
        signals.append((rises[keep], falls[keep]))
    if kind == "jitter":
        events = np.concatenate([e for pair in signals for e in pair])
        events = events[(events >= times[0]) & (events <= times[-1])]
        times = np.unique(np.r_[times, events, events - period * 1e-6])
    logic = []
    for rises, falls in signals:
        logic.append(
            (
                (
                    np.searchsorted(rises, times, side="right")
                    - np.searchsorted(falls, times, side="right")
                )
                % 2
            ).astype(float)
        )
    a, b = logic
    if kind == "reversed":
        a, b = b, a
    z = ((times % 1) < 4 * spp / rate).astype(float)
    if kind == "z-glitch":
        z[(times > 0.35) & (times < 0.35 + 1.5 / rate)] = 1
    states = np.c_[a, 1 - a, b, 1 - b, z, 1 - z]
    values = 0.9 + states * 3.15 + rng.normal(0, 0.015, states.shape)
    chosen = list(range(6)) if differential else [0, 2, 4]
    names = ["A", "-A", "B", "-B", "Z", "-Z"]
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    np.savetxt(
        path,
        np.c_[times, values[:, chosen]],
        delimiter=",",
        header="Time_s," + ",".join(names[c] for c in chosen),
        comments="",
        fmt="%.12g",
    )
    return {
        "path": str(path.resolve()),
        "kind": kind,
        "bearing_error": bearing,
        "ppr": ppr,
        "rpm": 60,
        "samples": len(times),
        "seed": seed,
        "missing_cycles": missing.tolist(),
        "missing_percent": 100 * len(missing) / total,
        "max_missing_bunch": 10 if kind == "missing" else 0,
        "differential": differential,
        "sigma_period_percent": 1 if kind == "jitter" else 0,
    }


if __name__ == "__main__":
    folder = Path("audit/fixtures")
    items = []
    for kind in ("clean", "missing", "jitter", "duty", "reversed", "z-glitch"):
        items.append(generate_capture(folder / (kind + ".csv"), kind, ppr=32, spp=160))
    Path("audit/fixture-manifest.json").write_text(json.dumps(items, indent=2))
