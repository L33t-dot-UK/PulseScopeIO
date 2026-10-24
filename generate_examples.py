import csv
from pathlib import Path
import numpy as np


def generate(folder="examples", ppr=32, revs=4):
    Path(folder).mkdir(exist_ok=True)
    n = ppr * 16 * revs
    k = np.arange(n)
    phase = k % 16
    a = (phase < 8).astype(float)
    b = ((phase - 4) % 16 < 8).astype(float)
    z = (k % (ppr * 16) < 4).astype(float)
    for name in ["clean", "missing-pulse", "jitter", "duty-failure", "reversed"]:
        aa = a.copy()
        bb = b.copy()
        zz = z.copy()
        t = k / 16000
        if name == "missing-pulse":
            aa[(k >= 16 * 40) & (k < 16 * 41)] = 0
        if name == "duty-failure":
            aa = (phase < 6).astype(float)
        if name == "reversed":
            aa, bb = bb, aa
        if name == "jitter":
            t = np.cumsum((1 + 0.03 * np.sin(k / 13)) / 16000)
        np.savetxt(
            Path(folder) / (name + ".csv"),
            np.column_stack([t, aa, 1 - aa, bb, 1 - bb, zz, 1 - zz]),
            delimiter=",",
            header="Time_s,A,-A,B,-B,Z,-Z",
            comments="",
            fmt="%.10g",
        )


if __name__ == "__main__":
    generate()
