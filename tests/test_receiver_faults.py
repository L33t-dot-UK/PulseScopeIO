from contextlib import closing
import unittest
import json,sqlite3
import numpy as np
from tests import test_csv_layout as layouts

class ReceiverFaultTests(unittest.TestCase):
    def setUp(self):self.fixture=layouts.CsvLayoutTests();self.fixture.setUp()
    def tearDown(self):self.fixture.tearDown()
    def recording(self,common_glitch=False,phase_jump=False):
        # A complete differential capture with a disturbance on both A input legs.
        i=np.arange(5000);a=np.where(i%100<50,5.,0.);b=np.where((i-25)%100<50,5.,0.);z=np.where(i%1000<5,5.,0.)
        data=np.column_stack([i*.0001,a,5-a,b,5-b,z,5-z])
        if common_glitch:data[270:273,1:3]+=3
        if phase_jump:
            data[2000:2100,3]=np.where((i[2000:2100]%100>=40)&(i[2000:2100]%100<90),5.,0.)
            data[:,4]=5-data[:,3]
        path=self.fixture.root/('rx-'+str(len(self.fixture.items))+'.csv');np.savetxt(path,data,delimiter=',',header='Time_s,A,-A,B,-B,Z,-Z',comments='')
        from engine import Recording
        r=Recording(path,{'slew_mode':'none','receiver_mode':'online','low':2.5,'high':2.5,'window':1})
        self.fixture.items.append(r);r.run();self.assertIsNone(r.error,r.error)
        return r
    def test_common_mode_raw_glitch_is_not_receiver_fault(self):
        r=self.recording(common_glitch=True)
        raw=r.faults(r.start,r.end)
        self.assertTrue(any(e['kind']=='short raw pulse candidate' for e in raw['events']))
        self.assertEqual(r.faults(r.start,r.end,signal_domain='receiver')['count'],0)
        self.assertEqual(r.plot(r.start,r.end)['receiver_faults']['count'],0)
        self.assertTrue(all(e['signal_domain']=='raw' for e in raw['events']))
    def test_phase_fault_measured_after_receiver_remains_visible(self):
        r=self.recording(phase_jump=True)
        events=r.plot(r.start,r.end)['receiver_faults']['events']
        self.assertTrue(any(e['kind']=='A/B phase outlier' for e in events))
        self.assertTrue(all(e['signal_domain']=='receiver' for e in events))
    def test_raw_fault_limit_does_not_hide_receiver_faults(self):
        r=self.recording()
        with closing(sqlite3.connect(r.dir/'faults.sqlite')) as db:
            raw={'time':r.start,'sample':0,'channel':'A','pulse':1,'kind':'short raw pulse candidate','signal_domain':'raw'}
            db.executemany('INSERT INTO faults(time,payload) VALUES (?,?)',[(r.start,json.dumps(raw))]*600)
            rx=dict(raw,kind='A/B phase outlier',signal_domain='receiver',time=r.end)
            db.execute('INSERT INTO faults(time,payload) VALUES (?,?)',(r.end,json.dumps(rx)))
            db.commit()
        self.assertFalse(any(e['signal_domain']=='receiver' for e in r.faults(r.start,r.end)['events']))
        receiver=r.faults(r.start,r.end,signal_domain='receiver')
        self.assertEqual(receiver['count'],1);self.assertEqual(len(receiver['events']),1)

if __name__=='__main__':unittest.main()
