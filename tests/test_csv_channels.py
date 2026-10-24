import tempfile
import unittest
from pathlib import Path
import numpy as np
import engine
from engine import Recording
from cache_control import CacheManager
from server import Handler

class CsvChannelTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory(dir=engine.CACHE)
        self.folder=Path(self.tmp.name)
        self.items=[]
    def tearDown(self):
        CacheManager.close_recordings(self.items)
        for r in self.items:
            import shutil
            shutil.rmtree(r.dir)
        self.tmp.cleanup()
    def channel(self,name,count=2000,unit='s',offset=0,timed=True):
        p=self.folder/(name+'.csv')
        t=np.arange(count)*.0001+offset
        v=np.where((np.arange(count)+(25 if name=='B' else 0))%100<50,5.,0.)
        np.savetxt(p,np.column_stack([t*(1000 if unit=='ms' else 1),v]) if timed else v,delimiter=',',header='Time_'+unit+',Voltage' if timed else 'Voltage',comments='')
        return p,v
    def recording(self,paths):
        r=Recording(next(iter(paths.values())),{'csv_channel_paths':{k:str(v) for k,v in paths.items()},'slew_mode':'none'})
        self.items.append(r)
        return r
    def test_import_progress_across_channels_and_combining(self):
        from unittest.mock import patch
        a,_=self.channel('A',count=1000);b,_=self.channel('B',count=1000)
        r=self.recording({'A':a,'B':b})
        states=[]
        def observe():states.append((r.stage,r.progress))
        r.check=observe
        with patch.object(engine,'BLOCK',64):r.ingest()
        readings=[v for stage,v in states if stage.startswith('Reading CSV channel')]
        self.assertTrue(any(0<v<.16 for v in readings))
        self.assertTrue(any(.16<v<.32 for v in readings))
        self.assertTrue(any(stage.startswith('Checking timelines') and .32<v<.4 for stage,v in states))
        values=[v for _,v in states]
        self.assertTrue(all(b>=a for a,b in zip(values,values[1:])))
        self.assertAlmostEqual(r.progress,.4)
    def test_cancel_during_second_channel(self):
        a,_=self.channel('A');b,_=self.channel('B')
        r=self.recording({'A':a,'B':b})
        def cancel():
            if 'channel B' in r.stage:raise ValueError('Analysis cancelled')
        r.check=cancel
        with self.assertRaisesRegex(ValueError,'cancelled'):r.ingest()

    def test_subset_analysis_and_export(self):
        a,av=self.channel('A');b,bv=self.channel('B')
        originals={p:p.read_bytes() for p in [a,b]}
        r=self.recording({'A':a,'B':b});r.run()
        self.assertIsNone(r.error,r.error);self.assertTrue(r.ready)
        self.assertEqual(r.present,[0,2]);np.testing.assert_array_equal(r.data[:,1],av);np.testing.assert_array_equal(r.data[:,3],bv)
        target,_=Handler.prepare_export(r,{'kind':'csv'})
        try:
            data=np.loadtxt(target,delimiter=',',skiprows=1)
            np.testing.assert_allclose(data,r.data)
        finally:target.unlink()
        for p,content in originals.items():self.assertEqual(p.read_bytes(),content)
    def test_all_six(self):
        paths={n:self.channel(n)[0] for n in engine.NAMES}
        r=self.recording(paths);r.run();self.assertIsNone(r.error,r.error);self.assertEqual(r.present,list(range(6)))
    def test_no_time_single_channel(self):
        a,av=self.channel('A',timed=False)
        r=self.recording({'A':a});r.run();self.assertIsNone(r.error,r.error);self.assertFalse(r.seconds);np.testing.assert_array_equal(r.data[:,0],np.arange(len(av)))
    def test_normalized_time_units(self):
        a,_=self.channel('A');b,_=self.channel('B',unit='ms')
        r=self.recording({'A':a,'B':b});r.run();self.assertIsNone(r.error,r.error)
    def test_count_and_time_mismatch(self):
        for kwargs in ({'count':1999},{'offset':.0001},{'timed':False}):
            a,_=self.channel('A');b,_=self.channel('B',**kwargs)
            r=self.recording({'A':a,'B':b});r.run();self.assertIsNotNone(r.error)
    def test_reject_multiple_signal_columns_and_duplicate_files(self):
        a,_=self.channel('A');a.write_text('Time_s,CH1,CH2\n0,0,5\n1,5,0\n')
        r=self.recording({'A':a});r.run();self.assertIn('exactly one signal',r.error)
        r=self.recording({'A':a,'B':a});r.run();self.assertIn('different CSV',r.error)

if __name__=='__main__':unittest.main()
