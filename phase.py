"""Local-cycle A/B phase statistics over existing disk-backed edge indexes.

Each usable A-to-A interval must contain exactly one B rising edge. Its own
duration normalises the phase, so speed changes do not become phase changes.
Missing/extra B edges and missing A cycles cannot alias a distant edge through
a global-period modulo. Coverage and coherence let callers decide whether a
single displayed phase is representative.
"""

import math
import numpy as np


def phase_stats(owner, recording, edges, block=131072, min_edges=2):
    """Return bounded-memory local phase statistics, or None without usable data.

    ``edges`` contains sorted A and B rising-edge sample indexes. ``recording``
    supplies the already imported ``data`` time column and sample ``count``;
    this function never opens or reimports a source capture.

    The angle is a circular mean of local cycle fractions in [-180, 180).
    ``period`` is a robust median from at most 200,000 usable cycles, sampled
    evenly across their ranks. Obtaining that sample needs a second bounded
    pass over the existing indexes, not a second file import. ``coverage`` is
    the usable fraction of all consecutive A rising-edge intervals.
    """
    a, b = (np.asarray(e) for e in edges)
    if len(a) < min_edges or len(b) < min_edges or len(a) < 2:
        return None
    block = max(1, int(block))
    total_cycles = len(a) - 1

    def cycles():
        for first in range(0, total_cycles, block):
            owner.check()
            aa = a[first : min(total_cycles, first + block) + 1]
            positions = np.searchsorted(b, aa, side="left")
            usable = np.diff(positions) == 1
            if not np.any(usable):
                continue
            left = aa[:-1][usable]
            right = aa[1:][usable]
            middle = b[positions[:-1][usable]]
            begin = recording.data[left, 0]
            period = recording.data[right, 0] - begin
            offset = recording.data[middle, 0] - begin
            valid = (
                np.isfinite(period)
                & np.isfinite(offset)
                & (period > 0)
                & (offset >= 0)
                & (offset < period)
            )
            if np.any(valid):
                yield period[valid], offset[valid]

    cs = sn = 0.0
    pairs = 0
    for periods, offsets in cycles():
        # Wrap only the measured fractional angle, never a distant time offset.
        angle = ((offsets / periods + 0.5) % 1 - 0.5) * (2 * np.pi)
        cs += float(np.cos(angle).sum())
        sn += float(np.sin(angle).sum())
        pairs += len(periods)
    if not pairs:
        return None

    ranks = np.linspace(0, pairs - 1, min(pairs, 200000), dtype="i8")
    sample = np.empty(len(ranks), dtype="f8")
    seen = 0
    for periods, _ in cycles():
        lo = int(np.searchsorted(ranks, seen, side="left"))
        hi = int(np.searchsorted(ranks, seen + len(periods), side="left"))
        sample[lo:hi] = periods[ranks[lo:hi] - seen]
        seen += len(periods)

    sample_step = (
        float(recording.data[-1, 0] - recording.data[0, 0]) / (len(recording.data) - 1)
        if len(recording.data) > 1
        else 0.0
    )
    return dict(
        period=float(np.median(sample)),
        phase_degrees=math.degrees(math.atan2(sn, cs)),
        coherence=min(1.0, math.hypot(cs, sn) / pairs),
        pairs=pairs,
        coverage=pairs / total_cycles,
        total_cycles=total_cycles,
        sample_step=sample_step,
    )


def phase_cycle_outliers(recording, block=131072):
    """Compare every unambiguous local A/B cycle against a robust circular baseline.

    Fixed-size histograms keep whole-capture checks bounded in memory. Missing or
    extra B edges are excluded and remain the responsibility of pulse-count checks.
    """
    percent=float(getattr(recording,'options',{}).get('phase_tolerance_percent',10))
    if not math.isfinite(percent) or not 5<=percent<=20:
        raise ValueError('A/B phase tolerance must be between 5% and 20%')
    a = recording.edges.get(6, [])
    b = recording.edges.get(7, [])
    result = dict(available=False, cycles=0, excluded_cycles=max(0, len(a)-1),
                  outliers=0, typical_degrees=None, tolerance_degrees=None, tolerance_percent=percent,
                  max_deviation_degrees=None,
                  note="Needs both A and B and at least eight complete, unambiguous cycles.")
    if len(a) < 2 or not len(b):
        return result
    a, b = np.asarray(a), np.asarray(b)
    bins = 36000
    histogram = np.zeros(bins, dtype='i8')

    def cycles():
        for first in range(0, len(a)-1, block):
            recording.check()
            aa = a[first:min(len(a)-1,first+block)+1]
            positions = np.searchsorted(b, aa, side='left')
            good = np.flatnonzero(np.diff(positions)==1)
            left, right = aa[good], aa[good+1]
            middle = b[positions[good]]
            begin = recording.data[left,0]
            periods = recording.data[right,0]-begin
            offsets = recording.data[middle,0]-begin
            valid = np.isfinite(periods) & np.isfinite(offsets) & (periods>0) & (offsets>=0) & (offsets<periods)
            yield first+good[valid], left[valid], right[valid], periods[valid], 360*offsets[valid]/periods[valid]

    for _,_,_,_,angles in cycles():
        histogram += np.bincount(np.rint(angles*100).astype('i8') % bins,minlength=bins)
    count = int(histogram.sum())
    result.update(cycles=count, excluded_cycles=len(a)-1-count)
    if count < 8:
        return result
    centers = np.arange(bins)/100
    anchor = centers[int(np.argmax(histogram))]
    relative = (centers-anchor+180)%360-180
    def median(values):
        order=np.argsort(values)
        index=np.searchsorted(np.cumsum(histogram[order]),(count+1)//2)
        return float(values[order[index]])
    typical=(anchor+median(relative))%360
    typical_signed=(typical+180)%360-180
    tolerance=percent/100*abs(typical_signed)
    result.update(available=True, typical_degrees=typical_signed, tolerance_percent=percent,
                  tolerance_degrees=tolerance, max_deviation_degrees=0.,
                  note=f"Rising-edge A/B phase per A cycle, relative to the typical phase across the whole file. Flags deviations strictly greater than {percent:g}% of the magnitude of the typical signed phase, and above three samples of timing resolution. A 90° typical phase has a {90*percent/100:g}° threshold. Missing/extra B-edge intervals are excluded. Not a test against an assumed 90° target.")
    for ranks,left,right,periods,angles in cycles():
        delta=(angles-typical+180)%360-180
        if len(delta):
            result['max_deviation_degrees']=max(result['max_deviation_degrees'],float(np.max(np.abs(delta))))
        steps=np.maximum(recording.data[np.minimum(left+1,recording.count-1),0]-recording.data[left,0],
                         recording.data[right,0]-recording.data[np.maximum(right-1,0),0])
        limits=np.maximum(tolerance,1080*np.abs(steps)/periods)
        events=[]
        for j in np.flatnonzero(np.abs(delta)>limits+.01):
            events.append(dict(sample=int(left[j]),time=float(recording.data[left[j],0]),
                               end_time=float(recording.data[right[j],0]),channel='B',channels=['A','B'],
                               pulse=int(ranks[j]+1),kind='A/B phase outlier',signal_domain='receiver',
                               phase_degrees=float((angles[j]+180)%360-180),
                               typical_degrees=result['typical_degrees'],
                               deviation_degrees=float(delta[j]),tolerance_degrees=float(limits[j])))
        recording.add_faults(events)
        result['outliers']+=len(events)
        recording.error_count+=len(events)
        if len(recording.errors)<10000:
            recording.errors.extend(events[:10000-len(recording.errors)])
    return result
