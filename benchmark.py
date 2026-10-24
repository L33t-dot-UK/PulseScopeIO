"""Reproducible disk-backed load test; defaults to 64 MiB of PCM."""

import argparse, json, time, wave
from pathlib import Path
import numpy as np
from engine import Recording, CACHE

p = argparse.ArgumentParser()
p.add_argument("--mib", type=int, default=64)
args = p.parse_args()
path = CACHE / "benchmark.wav"
frames = args.mib * 1024**2 // 12
k = np.arange(65536)
a = (k % 64) < 32
b = ((k - 16) % 64) < 32
z = (k % 8192) < 4
block = (np.column_stack([a, ~a, b, ~b, z, ~z]) * 2 - 1).astype("<i2") * 20000
with wave.open(str(path), "wb") as w:
    w.setnchannels(6)
    w.setsampwidth(2)
    w.setframerate(64000)
    for start in range(0, frames, len(block)):
        w.writeframesraw(block[: min(len(block), frames - start)].tobytes())
t = time.perf_counter()
r = Recording(path, {"low": 0, "high": 0})
r.run()
elapsed = time.perf_counter() - t
if r.error:
    raise RuntimeError(r.error)
t = time.perf_counter()
plot = r.plot(r.start, r.end)
latency = time.perf_counter() - t
result = {
    "input_bytes": path.stat().st_size,
    "samples": r.count,
    "analysis_seconds": elapsed,
    "full_span_plot_seconds": latency,
    "plot_buckets": len(plot["points"]),
    "cache_bytes": sum(p.stat().st_size for p in r.dir.iterdir()),
    "ppr": r.ppr,
}
try:
    import psutil

    result["process_peak_working_set_bytes"] = getattr(
        psutil.Process().memory_info(), "peak_wset", None
    )
except ImportError:
    pass
Path("benchmark-results.json").write_text(json.dumps(result, indent=2))
print(json.dumps(result, indent=2))
