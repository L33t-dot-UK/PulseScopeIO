import unittest
from types import SimpleNamespace
from unittest.mock import patch
import numpy as np
import health_analysis as h

class HealthTests(unittest.TestCase):
    def fixture(self,n=20000):
        sample=np.arange(n);a=(sample%1000)<500;b=((sample-250)%1000)<500;z=(sample%10000)<50
        raw=np.column_stack([a,~a,b,~b,z,~z]).astype(float)*5
        logic=np.column_stack([a,~a,b,~b,z,~z,a,b,z]).astype('u1')
        edges={c:np.flatnonzero(np.diff(logic[:,c].astype(int))==1)+1 for c in range(9)}
        falls={c:np.flatnonzero(np.diff(logic[:,c].astype(int))==-1)+1 for c in range(9)}
        metrics=[dict(c=c,period=.01 if c not in (4,5,8) else .1,high=.005 if c not in (4,5,8) else .0005,low=.005 if c not in (4,5,8) else .0995) for c in range(9)]
        metrics[5]['high'],metrics[5]['low']=.0995,.0005
        events=[]
        r=SimpleNamespace(data=np.column_stack([sample*.00001,raw]),logic=logic,edges=edges,falls=falls,metrics=metrics,present=list(range(6)),count=n,start=0.,end=(n-1)*.00001,seconds=True,validated_z=edges[8],low=2.5,high=2.5,errors=[],check=lambda:None,add_faults=lambda e:events.extend(e),phase_analysis={'typical_degrees':90})
        return r,events
    def test_all_seven_checks_and_clean_truth(self):
        r,e=self.fixture();d=h.analyse_health(r)
        self.assertEqual(set(d)-{'scope'},{'phase','z','speed','widths','quadrature','differential','noise'})
        self.assertAlmostEqual(d['phase']['mean'],90)
        self.assertEqual(d['quadrature']['simultaneous'],0)
        self.assertEqual(d['quadrature']['direction_changes'],0)
        self.assertTrue(all(v['short_pulses']==0 for v in d['noise'].values()))
        self.assertEqual(len(d['differential']),3)
    def test_differential_volts_and_skew(self):
        r,e=self.fixture();q=h.differential_quality(r)['A/-A']
        self.assertAlmostEqual(q['common_mode_mean'],2.5);self.assertAlmostEqual(q['differential_high'],5);self.assertAlmostEqual(q['differential_low'],-5)
        self.assertEqual(q['noncomplementary_percent'],0);self.assertEqual(q['edge_skew']['maximum'],0)
        r.data[:,2]=np.roll(r.data[:,2],2);r.logic[:,1]=np.roll(r.logic[:,1],2)
        r.edges[1]=np.flatnonzero(np.diff(r.logic[:,1].astype(int))==1)+1
        r.falls[1]=np.flatnonzero(np.diff(r.logic[:,1].astype(int))==-1)+1
        q=h.differential_quality(r)['A/-A'];self.assertAlmostEqual(q['edge_skew']['mean'],.00002,places=9);self.assertGreater(q['noncomplementary_percent'],0)
    def test_short_pulse_is_unsmoothed_and_chunk_safe(self):
        r,e=self.fixture();r.present=[0];r.data[698:708,1]=5
        with patch.object(h,'BLOCK',700):d=h.noise_glitches(r)['A']
        self.assertEqual(d['short_pulses'],1);self.assertAlmostEqual(e[0]['width'],.0001)
        self.assertEqual(e[0]['sample'],698)
    def test_ambiguous_transition_on_chunk_boundary(self):
        r,e=self.fixture();r.logic[:,6:8]=0;r.logic[700:,6:8]=1
        with patch.object(h,'BLOCK',700):q=h.quadrature(r)
        self.assertEqual(q['simultaneous'],1);self.assertEqual(e[0]['sample'],700);self.assertEqual(e[0]['channels'],['A','B'])
    def test_measured_speed_and_acceleration(self):
        r,e=self.fixture();r.validated_z=np.array([0,2000,3000,4000]);r.rpm=10
        s=h.speed_trend(r)
        self.assertEqual(s['count'],3);self.assertAlmostEqual(s['minimum'],3000);self.assertAlmostEqual(s['maximum'],6000)
        self.assertEqual(s['acceleration']['count'],2);self.assertGreater(s['acceleration']['maximum'],0)
        r.seconds=False;self.assertFalse(h.speed_trend(r)['available'])
    def test_z_phase_wrap_and_unavailable_channels(self):
        r,e=self.fixture();r.validated_z=np.array([1990,2010,3990,4010])
        q=h.z_stability(r)['A'];self.assertAlmostEqual(q['maximum']-q['minimum'],7.2)
        r.edges.pop(6);self.assertFalse(h.z_stability(r)['A']['available'])
        r.present=[0];self.assertEqual(h.differential_quality(r),{})
    def test_width_and_phase_trends_preserve_spikes(self):
        r,e=self.fixture();r.edges[7]=r.edges[7].copy();r.edges[7][5]+=100
        p=h.phase_trend(r);self.assertAlmostEqual(p['maximum'],126);self.assertAlmostEqual(p['minimum'],90)
        r.falls[0]=r.falls[0].copy();r.falls[0][5]+=100
        w=h.width_trends(r)['A'];self.assertAlmostEqual(w['high']['maximum'],.006)
    def test_trend_all_observations_and_extrema(self):
        r,e=self.fixture();t=h.Trend(r,bins=1);t.add([.1,.11,.12],[1,2,100])
        d=t.result();self.assertEqual(d['count'],3);self.assertEqual(d['maximum'],100);self.assertAlmostEqual(d['mean'],103/3)
    def test_cancellation(self):
        r,e=self.fixture()
        def stop():raise ValueError('cancelled')
        r.check=stop
        with self.assertRaisesRegex(ValueError,'cancelled'):h.analyse_health(r)

if __name__=='__main__':unittest.main()
