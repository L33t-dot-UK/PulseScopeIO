"""10 GB CSV capacity test using a repeated six-channel scientific-format capture."""

import json, threading, time, shutil
from pathlib import Path
from engine import Recording, CACHE

size = 10_000_000_000
folder = CACHE / "capacity-test"
folder.mkdir(exist_ok=True)
path = folder / "ten-gigabyte.csv"
if shutil.disk_usage(folder).free < size * 2:
    raise RuntimeError("Capacity test needs at least 20 GB free")
lines = []
for k in range(8192):
    a = int(k % 64 < 32)
    b = int((k - 16) % 64 < 32)
    z = int(k % 8192 < 4)
    lines.append(",".join(f"{v:.17e}" for v in [a, 1 - a, b, 1 - b, z, 1 - z]) + "\n")
block = "".join(lines).encode("ascii")
header = b"A,-A,B,-B,Z,-Z\n"
line_size = len(lines[0])
rows = (size - len(header)) // line_size
remaining = rows * line_size
print(f"Generating exactly {size:,} source bytes, {rows:,} samples", flush=True)
if not path.exists() or path.stat().st_size != size:
    with open(path, "wb") as f:
        f.write(header)
        while remaining:
            n = min(remaining, len(block))
            f.write(block[:n])
            remaining -= n
        f.write(b" " * (size - f.tell() - 1) + b"\n")
r = Recording(path, {"low": 0, "high": 0,"slew_mode":"online","receiver_mode":"online"})
t = time.perf_counter()
worker = threading.Thread(target=r.run)
worker.start()
while worker.is_alive():
    worker.join(5)
    print(f"{time.perf_counter()-t:.1f}s {r.stage}: {r.progress:.1%}", flush=True)
if r.error:
    raise RuntimeError(r.error)
elapsed = time.perf_counter() - t
t = time.perf_counter()
plot = r.plot(r.start, r.end)
latency = time.perf_counter() - t
result = {
    "analysis_options": r.options,
    "source_bytes": path.stat().st_size,
    "samples": r.count,
    "analysis_seconds": elapsed,
    "overview_seconds": latency,
    "overview_buckets": len(plot["points"]),
    "cache_bytes": sum(p.stat().st_size for p in r.dir.iterdir()),
    "ppr": r.ppr,
    "seconds_timebase": r.seconds,
    "kind": "Synthetic 10 GB CSV; six scientific-format columns; sample-index timebase",
}
Path("capacity-results.json").write_text(json.dumps(result, indent=2))
print(json.dumps(result, indent=2), flush=True)
# Only this explicitly created capacity-test source is removed, after validation.
path.unlink()
