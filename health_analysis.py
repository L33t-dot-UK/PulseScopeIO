"""Bounded-memory signal-health checks over the complete imported capture."""
import math
import numpy as np
from engine import BLOCK, NAMES, hysteresis


class Trend:
    """Mean/min/max in fixed time bins: every observation contributes."""
    def __init__(self, recording, bins=192):
        self.r = recording
        self.n = np.zeros(bins, dtype='i8')
        self.total = np.zeros(bins)
        self.low = np.full(bins, np.inf)
        self.high = np.full(bins, -np.inf)
        self.sum = self.sum2 = 0.
        self.count = 0

    def add(self, times, values):
        times, values = np.asarray(times), np.asarray(values)
        valid = np.isfinite(times) & np.isfinite(values)
        times, values = times[valid], values[valid]
        if not len(values): return
        ids = np.clip(((times-self.r.start)/max(self.r.end-self.r.start,1e-30)*len(self.n)).astype('i8'),0,len(self.n)-1)
        self.n += np.bincount(ids,minlength=len(self.n))
        self.total += np.bincount(ids,weights=values,minlength=len(self.n))
        np.minimum.at(self.low,ids,values)
        np.maximum.at(self.high,ids,values)
        self.sum += float(values.sum());self.sum2 += float(np.dot(values,values));self.count += len(values)

    def result(self):
        good = self.n>0
        if not self.count:return dict(available=False,count=0,points=[])
        mean = self.sum/self.count
        x = self.r.start+(np.flatnonzero(good)+.5)/len(self.n)*(self.r.end-self.r.start)
        return dict(available=True,count=self.count,mean=mean,
                    std=math.sqrt(max(0.,self.sum2/self.count-mean*mean)),
                    minimum=float(self.low[good].min()),maximum=float(self.high[good].max()),
                    points=np.column_stack([x,self.total[good]/self.n[good],self.low[good],self.high[good]]).tolist())


def add_flags(r, events):
    r.add_faults(events)
    if len(r.errors)<10000:r.errors.extend(events[:10000-len(r.errors)])


def phase_trend(r):
    a,b=r.edges.get(6,[]),r.edges.get(7,[])
    trend=Trend(r)
    base=getattr(r,'phase_analysis',{}).get('typical_degrees')
    if base is None:return dict(available=False,note='Needs A and B with enough paired cycles.',points=[])
    for first in range(0,len(a)-1,BLOCK):
        r.check()
        aa=a[first:min(len(a)-1,first+BLOCK)+1]
        positions=np.searchsorted(b,aa,side='left')
        ok=np.diff(positions)==1
        left,right=aa[:-1][ok],aa[1:][ok]
        middle=b[positions[:-1][ok]]
        t=r.data[left,0];period=r.data[right,0]-t
        valid=period>0
        angle=360*(r.data[middle[valid],0]-t[valid])/period[valid]
        # Display phase continuously about the typical phase, including +/-180 wrap.
        trend.add(t[valid],base+(angle-base+180)%360-180)
    result=trend.result()
    points=np.asarray(result['points'])
    if len(points)>1:
        x=points[:,0]-points[:,0].mean();y=points[:,1]
        result['slope']=float(np.dot(x,y-y.mean())/np.dot(x,x)) if np.dot(x,x)>0 else 0.
        result['net_change']=float(y[-1]-y[0])
    result['note']='Mean and extrema per time bin; all unambiguous A cycles contribute. Phase is wrapped around the typical value. Drift slope is fitted to the binned means, not a mechanical fault limit.'
    return result


def z_stability(r):
    z=r.validated_z
    result={}
    for c,name in ((6,'A'),(7,'B')):
        edges=r.edges.get(c,[])
        trend=Trend(r);hist=np.zeros(3600,dtype='i8')
        def values():
            for first in range(0,len(z),BLOCK):
                r.check();zz=z[first:first+BLOCK]
                positions=np.searchsorted(edges,zz,side='right')-1
                valid=(positions>=0)&(positions<len(edges)-1)
                zz,positions=zz[valid],positions[valid]
                if not len(zz):continue
                t=r.data[zz,0];left=r.data[edges[positions],0]
                period=r.data[edges[positions+1],0]-left
                good=period>0
                yield t[good],360*(t[good]-left[good])/period[good]
        if len(edges)>1:
            for _,angle in values():hist+=np.bincount(np.rint(angle*10).astype('i8') % 3600,minlength=3600)
        if not hist.sum():
            result[name]=dict(available=False,points=[],note='Needs Z edges inside complete '+name+' cycles.');continue
        centers=np.arange(3600)/10
        anchor=centers[int(hist.argmax())]
        delta=(centers-anchor+180)%360-180;order=np.argsort(delta)
        base=(anchor+float(delta[order[np.searchsorted(np.cumsum(hist[order]),(int(hist.sum())+1)//2)]]))%360
        for t,angle in values():trend.add(t,(angle-base+180)%360-180)
        result[name]=trend.result()
        result[name].update(reference_degrees=base,note='Z rising-edge position within the preceding local '+name+' cycle; trend shows deviation from its typical position. This is electrical pulse phase, not absolute shaft angle. No automatic Z alignment limit is assumed.')
    return result


def speed_trend(r):
    trend,acceleration=Trend(r),Trend(r)
    z=r.validated_z
    if not r.seconds or len(z)<2:
        return dict(available=False,points=[],note='Measured speed needs physical timestamps and at least two validated Z pulses.')
    previous=None
    for first in range(0,len(z)-1,BLOCK):
        r.check();zz=z[first:min(len(z)-1,first+BLOCK)+1]
        times=r.data[zz,0];period=np.diff(times);mid=(times[:-1]+times[1:])/2
        good=period>0;rpm=60/period[good];mid=mid[good]
        trend.add(mid,rpm)
        if previous is not None:mid=np.r_[previous[0],mid];rpm=np.r_[previous[1],rpm]
        if len(mid)>1:
            change=np.diff(rpm)
            change[np.abs(change)<=np.maximum(np.abs(rpm[:-1]),np.abs(rpm[1:]))*1e-12]=0.
            acceleration.add(mid[1:],change/np.diff(mid))
        if len(mid):previous=(mid[-1],rpm[-1])
    result=trend.result();result['acceleration']=acceleration.result()
    if result['available']:
        result['variation_percent']=100*(result['maximum']-result['minimum'])/result['mean'] if result['mean'] else None
    result['note']='Measured RPM for each Z-to-Z interval, assuming one validated index pulse per revolution. Acceleration is the change between consecutive revolution-average speeds in RPM/s; entered replay RPM does not alter this measurement.'
    return result


def width_trends(r):
    result={}
    for c in r.present:
        rise,fall=r.edges.get(c,[]),r.falls.get(c,[])
        high,low=Trend(r),Trend(r)
        for first in range(0,len(rise)-1,BLOCK):
            r.check();rr=rise[first:min(len(rise)-1,first+BLOCK)+1]
            positions=np.searchsorted(fall,rr,side='right')
            good=(np.diff(positions)==1)&(positions[:-1]<len(fall))
            left,right=rr[:-1][good],rr[1:][good]
            middle=fall[positions[:-1][good]]
            t=r.data[left,0];end=r.data[right,0];ft=r.data[middle,0]
            ok=(ft>t)&(end>ft)
            high.add(t[ok],ft[ok]-t[ok]);low.add(t[ok],end[ok]-ft[ok])
        result[NAMES[c]]=dict(high=high.result(),low=low.result())
    return result


def quadrature(r):
    result=dict(available=False,simultaneous=0,valid_transitions=0,direction_changes=0,
                note='Needs recovered A and B. Both bits changing between adjacent samples is ambiguous: a glitch, missed transition or insufficient sample resolution can cause it.')
    if 6 not in r.edges or 7 not in r.edges:return result
    previous=int(r.logic[0,6])*2+int(r.logic[0,7]);last_direction=0
    order=np.array([0,3,1,2])
    for first in range(0,r.count,BLOCK):
        r.check();stop=min(r.count,first+BLOCK)
        state=r.logic[first:stop,6]*2+r.logic[first:stop,7]
        before=np.r_[previous,state[:-1]];previous=int(state[-1])
        invalid=(before^state)==3;ids=np.flatnonzero(invalid)+first
        events=[dict(kind='ambiguous quadrature transition',signal_domain='receiver',channel='A',channels=['A','B'],sample=int(i),time=float(r.data[i,0]),end_time=float(r.data[i,0]),pulse=int(np.searchsorted(r.edges[6],i))) for i in ids]
        add_flags(r,events);result['simultaneous']+=len(ids)
        changed=(before!=state)&~invalid
        step=(order[state[changed]]-order[before[changed]])%4
        directions=np.where(step==1,1,-1)
        if len(directions):
            result['direction_changes']+=int(np.count_nonzero(np.diff(np.r_[last_direction,directions])!=0))-(1 if last_direction==0 else 0)
            last_direction=int(directions[-1]);result['valid_transitions']+=len(directions)
    result['available']=True
    return result


def differential_quality(r):
    result={}
    for c,label in ((0,'A/-A'),(2,'B/-B'),(4,'Z/-Z')):
        if c not in r.present or c+1 not in r.present:continue
        cm,levels=[0.,0.,np.inf,-np.inf,0],[[0.,0] for _ in range(2)]
        mismatch=0
        for first in range(0,r.count,BLOCK):
            r.check();raw=r.data[first:first+BLOCK,c+1:c+3]
            common=raw.mean(axis=1);difference=raw[:,0]-raw[:,1]
            cm[0]+=float(common.sum());cm[1]+=float(np.dot(common,common));cm[2]=min(cm[2],float(common.min()));cm[3]=max(cm[3],float(common.max()));cm[4]+=len(raw)
            state=r.logic[first:first+BLOCK,6+c//2]
            for bit in (0,1):
                v=difference[state==bit];levels[bit][0]+=float(v.sum());levels[bit][1]+=len(v)
            mismatch+=int(np.count_nonzero(r.logic[first:first+BLOCK,c]==r.logic[first:first+BLOCK,c+1]))
        level=[s/n if n else None for s,n in levels]
        mean=cm[0]/cm[4]
        skew=Trend(r);unmatched=0
        periods=[m['period'] for m in r.metrics if m['c']==6+c//2 and m['period']]
        limit=periods[0]*.25 if periods else None
        for source,target in ((r.edges[c],r.falls[c+1]),(r.falls[c],r.edges[c+1])):
            for first in range(0,len(source),BLOCK):
                r.check();ss=source[first:first+BLOCK]
                if not len(target):unmatched+=len(ss);continue
                positions=np.searchsorted(target,ss)
                lo=np.maximum(positions-1,0);hi=np.minimum(positions,len(target)-1)
                t=r.data[ss,0];tl=r.data[target[lo],0];th=r.data[target[hi],0]
                offset=np.where(np.abs(th-t)<np.abs(tl-t),th-t,tl-t)
                good=np.abs(offset)<=limit if limit else np.zeros(len(ss),dtype=bool)
                skew.add(t[good],offset[good]);unmatched+=int(np.count_nonzero(~good))
        result[label]=dict(common_mode_mean=mean,common_mode_minimum=cm[2],common_mode_maximum=cm[3],common_mode_std=math.sqrt(max(0.,cm[1]/cm[4]-mean*mean)),
                           differential_low=level[0],differential_high=level[1],
                           level_imbalance_percent=100*(abs(level[1])-abs(level[0]))/(abs(level[1])+abs(level[0])) if all(v is not None for v in level) and abs(level[1])+abs(level[0]) else None,
                           noncomplementary_percent=100*mismatch/r.count,edge_skew=skew.result(),unmatched_edges=unmatched,
                           note='Common-mode is (positive + negative)/2. Differential levels are mean positive-minus-negative voltages in recovered low/high states. Complement mismatch uses the selected raw-channel thresholds; skew matches the nearest opposite-polarity edge within a quarter mean cycle and can include sampling uncertainty. No electrical compliance limits are assumed.')
    return result


def noise_glitches(r):
    result={}
    for c in r.present:
        metrics=next((m for m in r.metrics if m['c']==c),{})
        widths=[metrics.get('low'),metrics.get('high')]
        threshold=[v*.1 if v and v>0 else None for v in widths]
        sums=np.zeros(2);squares=np.zeros(2);counts=np.zeros(2,dtype='i8')
        previous=None;run_start=0;transition_count=0;glitches=0
        for first in range(0,r.count,BLOCK):
            r.check();raw=r.data[first:first+BLOCK,c+1]
            state=hysteresis(raw,r.low,r.high,previous if previous is not None else 0)
            for bit in (0,1):
                v=raw[state==bit];sums[bit]+=float(v.sum());squares[bit]+=float(np.dot(v,v));counts[bit]+=len(v)
            before=np.r_[state[0] if previous is None else previous,state[:-1]]
            changes=np.flatnonzero(state!=before)+first
            events=[]
            starts=np.r_[run_start,changes[:-1]]
            bits=before[changes-first]
            duration=r.data[changes,0]-r.data[starts,0]
            limits=np.array([threshold[int(bit)] or 0 for bit in bits])
            bad=(starts>0)&(duration>0)&(duration<limits)
            for j in np.flatnonzero(bad):
                i=int(starts[j]);events.append(dict(kind='short raw pulse candidate',signal_domain='raw',channel=NAMES[c],sample=i,time=float(r.data[i,0]),end_time=float(r.data[changes[j],0]),pulse=int(transition_count+j+1),width=float(duration[j]),width_limit=float(limits[j])))
            add_flags(r,events);glitches+=len(events);transition_count+=len(changes)
            if len(changes):run_start=int(changes[-1])
            previous=int(state[-1])
        rms=[math.sqrt(max(0.,squares[i]/counts[i]-(sums[i]/counts[i])**2)) if counts[i] else None for i in (0,1)]
        result[NAMES[c]]=dict(analysed_transitions=len(r.edges.get(c,[]))+len(r.falls.get(c,[])),extra_raw_crossings=max(0,transition_count-len(r.edges.get(c,[]))-len(r.falls.get(c,[]))),low_rms=rms[0],high_rms=rms[1],raw_transitions=transition_count,
                             short_pulses=glitches,low_width_limit=threshold[0],high_width_limit=threshold[1],
                             note='Raw unsmoothed hysteresis crossings at the selected thresholds. Candidates are complete interior high/low runs shorter than 10% of that channel’s mean analysed high/low width. Boundary runs are excluded. Plateau RMS variation includes noise, ramps and level drift; it is not a bandwidth-limited noise measurement.')
    return result


def analyse_health(r):
    tasks=[('Phase drift',phase_trend),('Z alignment',z_stability),('Speed stability',speed_trend),
           ('Pulse widths',width_trends),('Quadrature transitions',quadrature),
           ('Differential signal quality',differential_quality),('Noise and glitches',noise_glitches)]
    result={}
    for index,(name,fn) in enumerate(tasks):
        r.check();r.stage='Checking '+name.lower();r.progress=.94+.009*index/len(tasks)
        result[['phase','z','speed','widths','quadrature','differential','noise'][index]]=fn(r)
    result['scope']='Whole recording; trends show time-bin means and min/max envelopes, with every usable observation included. No source samples are modified.'
    return result
