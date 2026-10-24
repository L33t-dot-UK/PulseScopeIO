import unittest
from types import SimpleNamespace
import numpy as np
from phase import phase_cycle_outliers

class PhaseOutlierTests(unittest.TestCase):
    def fixture(self,fractions,periods=None):
        fractions=np.asarray(fractions)
        periods=np.full(len(fractions),1000) if periods is None else np.asarray(periods)
        a=np.r_[0,np.cumsum(periods)].astype('i8')
        b=(a[:-1]+np.rint(periods*fractions)).astype('i8')
        events=[]
        r=SimpleNamespace(data=(np.arange(a[-1]+1)*.00001)[:,None],count=int(a[-1]+1),edges={6:a,7:b},check=lambda:None,errors=[],error_count=0,add_faults=lambda e:events.extend(e))
        return r,events
    def test_constant_nonquadrature_is_not_an_error(self):
        r,events=self.fixture(np.full(100,.32))
        p=phase_cycle_outliers(r,block=7)
        self.assertTrue(p['available']);self.assertEqual(p['outliers'],0);self.assertAlmostEqual(p['typical_degrees'],115.2,delta=.01);self.assertFalse(events)
    def test_single_large_phase_cycle(self):
        v=np.full(100,.25);v[37]=.4
        r,events=self.fixture(v);p=phase_cycle_outliers(r,block=7)
        self.assertEqual(p['outliers'],1);self.assertEqual(events[0]['pulse'],38);self.assertAlmostEqual(events[0]['phase_degrees'],144);self.assertEqual(r.error_count,1)
        self.assertEqual(events[0]['channels'],['A','B'])
    def test_ten_percent_boundary_in_both_directions(self):
        # 90 degrees: 81 and 99 are exactly 10%; 79.2 and 100.8 exceed it.
        for typical in (.25,.75):
            for change,expected in ((.024,0),(.025,0),(.03,1),(-.024,0),(-.025,0),(-.03,1)):
                with self.subTest(typical=typical,change=change):
                    values=np.full(100,typical);values[40]+=change
                    r,events=self.fixture(values,np.full(100,10000))
                    p=phase_cycle_outliers(r)
                    self.assertAlmostEqual(p['tolerance_degrees'],9,delta=.001)
                    self.assertEqual(p['outliers'],expected)
    def test_threshold_tracks_typical_phase(self):
        values=np.full(100,.125);values[20]=.14 # 45 degrees to 50.4: 12%.
        r,events=self.fixture(values,np.full(100,10000))
        p=phase_cycle_outliers(r)
        self.assertAlmostEqual(p['tolerance_degrees'],4.5,delta=.001)
        self.assertEqual(p['outliers'],1)

    def test_adjustable_tolerance_and_invalid_values(self):
        values=np.full(100,.25);values[40]=.27
        for percent,expected in ((5,1),(10,0),(20,0)):
            r,events=self.fixture(values,np.full(100,10000));r.options={'phase_tolerance_percent':percent}
            p=phase_cycle_outliers(r)
            self.assertEqual(p['outliers'],expected)
            self.assertEqual(p['tolerance_percent'],percent)
            self.assertAlmostEqual(p['tolerance_degrees'],90*percent/100)
        for percent in (4,21,float('nan'),float('inf')):
            r,events=self.fixture(values);r.options={'phase_tolerance_percent':percent}
            with self.assertRaisesRegex(ValueError,'between 5% and 20%'):phase_cycle_outliers(r)

    def test_normal_jitter_and_speed_changes(self):
        r,events=self.fixture(np.tile([.248,.25,.252,.25],25),np.tile([1000,1500,2000,3000],25))
        self.assertEqual(phase_cycle_outliers(r)['outliers'],0)
    def test_missing_and_extra_b_edges_are_excluded(self):
        r,events=self.fixture(np.full(100,.25))
        r.edges[7]=np.sort(np.r_[np.delete(r.edges[7],10),20500])
        p=phase_cycle_outliers(r)
        self.assertEqual(p['excluded_cycles'],2);self.assertEqual(p['outliers'],0)
    def test_reverse_phase_and_wrap(self):
        for typical,odd in ((.75,.6),(.999,.1)):
            v=np.full(100,typical);v[80]=odd
            r,events=self.fixture(v);p=phase_cycle_outliers(r)
            self.assertEqual(p['outliers'],1);self.assertEqual(events[0]['pulse'],81)
    def test_insufficient_and_missing_channels(self):
        r,events=self.fixture(np.full(7,.25));self.assertFalse(phase_cycle_outliers(r)['available'])
        r.edges.pop(7);self.assertFalse(phase_cycle_outliers(r)['available'])
    def test_chunk_invariance_and_low_resolution(self):
        v=np.full(100,.25);v[20]=.27
        r,events=self.fixture(v,np.full(100,100))
        self.assertEqual(phase_cycle_outliers(r,block=5)['outliers'],0)
        v[20]=.5
        r,events=self.fixture(v);a=phase_cycle_outliers(r,block=5)
        r,events2=self.fixture(v);b=phase_cycle_outliers(r,block=1000)
        self.assertEqual(a,b);self.assertEqual(events,events2)

class PhaseRecordingTests(unittest.TestCase):
    def test_recording_flags_summary_and_filtered_navigation(self):
        from tests.test_csv_channels import CsvChannelTests
        fixture=CsvChannelTests();fixture.setUp()
        try:
            a,_=fixture.channel('A',count=10000)
            b,_=fixture.channel('B',count=10000)
            samples=np.arange(10000)
            phase=samples%100
            voltage=np.where((phase>=25)&(phase<75),5.,0.)
            voltage[4000:4100]=np.where((phase[4000:4100]>=40)&(phase[4000:4100]<90),5.,0.)
            np.savetxt(b,np.column_stack([samples*.0001,voltage]),delimiter=',',header='Time_s,Voltage',comments='')
            r=fixture.recording({'A':a,'B':b});r.run()
            self.assertIsNone(r.error,r.error)
            self.assertEqual(r.phase_analysis['outliers'],1)
            self.assertEqual(r.summary()['phase_analysis']['outliers'],1)
            event=r.next_fault(kind='A/B phase outlier')
            self.assertEqual(event['kind'],'A/B phase outlier')
            self.assertEqual(event['channels'],['A','B'])
            self.assertEqual(event['zoom_cycles'],16)
            self.assertGreater(event['zoom_end'],event['end_time'])
            again=r.next_fault(event['time'],event['id'],kind='A/B phase outlier')
            self.assertEqual(again['id'],event['id'])
            self.assertIsNone(r.next_fault(kind='A/B phase outlier',exclude_kind='A/B phase outlier'))
            visible=r.next_fault(exclude_kind='A/B phase outlier')
            self.assertTrue(visible is None or visible['kind']!='A/B phase outlier')
            self.assertIn(event['id'],[e['id'] for e in r.faults(r.start,r.end)['events']])
        finally:fixture.tearDown()

if __name__=='__main__':unittest.main()
