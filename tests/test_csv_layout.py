import unittest
import tempfile
from pathlib import Path
import numpy as np
import engine
from engine import Recording
from cache_control import CacheManager

class CsvLayoutTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory(dir=engine.CACHE);self.root=Path(self.tmp.name);self.items=[]
    def tearDown(self):
        import shutil
        CacheManager.close_recordings(self.items)
        for r in self.items:shutil.rmtree(r.dir)
        self.tmp.cleanup()
    def load(self,header,channels=6,options=None,delimiter=','):
        p=self.root/(str(len(self.items))+'.csv');i=np.arange(2000)
        values=np.column_stack([i*.0001]+[np.where((i+c*25)%100<50,5.,0.) for c in range(channels)])
        np.savetxt(p,values,delimiter=delimiter,header=header if header is not None else '',comments='')
        r=Recording(p,dict(slew_mode='none',**(options or {})));self.items.append(r);r.run()
        return r,values
    def test_unrecognised_blank_partial_and_headerless_columns(self):
        for header in ('x,C1,C2,C3,C4,C5,C6',',,,,,,','Time_s,A,nonsense,B,wrong,Z,unknown',None):
            r,v=self.load(header)
            self.assertIsNone(r.error,r.error);self.assertEqual(r.count,len(v));self.assertEqual(r.present,list(range(6)));np.testing.assert_array_equal(r.data,v)
            self.assertTrue(r.csv_columns['assumed']);self.assertTrue(r.seconds)
    def test_single_channel_generic_and_headerless(self):
        for header in ('unknown,anything',None):
            r,v=self.load(header,channels=1,options={'single_csv_channel':2})
            self.assertIsNone(r.error,r.error);self.assertEqual(r.present,[2]);np.testing.assert_array_equal(r.data[:,3],v[:,1]);self.assertEqual(r.count,len(v))
    def test_recognised_sparse_channels_preserved(self):
        r,v=self.load('Time_s,A,B',channels=2)
        self.assertIsNone(r.error,r.error);self.assertEqual(r.present,[0,2]);self.assertFalse(hasattr(r,'csv_columns'))
    def test_explicit_units_and_delimiters(self):
        r,v=self.load('Time_ms;CH1;CH2',channels=2,delimiter=';')
        self.assertIsNone(r.error,r.error);np.testing.assert_allclose(r.data[:,0],v[:,0]/1000)
    def test_generic_merge_role(self):
        r,v=self.load('x,V1,V2,V3,V4',channels=4,options={'capture_role':'B'})
        self.assertIsNone(r.error,r.error);self.assertEqual(r.present,[2,3,4,5]);np.testing.assert_array_equal(r.data[:,3:],v[:,1:])
    def test_too_many_columns_rejected(self):
        r,v=self.load('x,c1,c2,c3,c4,c5,c6,c7',channels=7)
        self.assertIn('one to six',r.error)

if __name__=='__main__':unittest.main()
