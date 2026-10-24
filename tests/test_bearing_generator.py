import unittest,tempfile
from pathlib import Path
import numpy as np
import engine
from synthetic import generate_capture
from engine import Recording
from cache_control import CacheManager

class BearingGeneratorTests(unittest.TestCase):
    def setUp(self):self.tmp=tempfile.TemporaryDirectory(dir=engine.CACHE);self.folder=Path(self.tmp.name);self.items=[]
    def tearDown(self):
        import shutil
        CacheManager.close_recordings(self.items)
        for r in self.items:shutil.rmtree(r.dir)
        self.tmp.cleanup()
    def test_bounds_variation_and_seed(self):
        errors=[]
        for seed in range(10):
            d=generate_capture(self.folder/'bearing.csv','bearing',ppr=1024,seed=seed)
            b=d['bearing_error'];errors.append(b['error_degrees'])
            self.assertGreaterEqual(b['error_degrees'],1);self.assertLessEqual(b['error_degrees'],5)
            self.assertAlmostEqual(b['error_degrees'],360*b['missing_cycles']/1024)
            self.assertGreater(b['start_time'],1);self.assertLess(b['end_time'],2)
        self.assertGreater(len(set(errors)),1)
        p=self.folder/'bearing.csv';generate_capture(p,'bearing',ppr=1024,seed=7);first=p.read_bytes();generate_capture(p,'bearing',ppr=1024,seed=7);self.assertEqual(p.read_bytes(),first)
    def test_counted_bearing_lag_and_index_reset(self):
        records={};metadata={}
        for kind in ['clean','bearing']:
            path=self.folder/(kind+'.csv');d=generate_capture(path,kind,ppr=1024,seed=9);metadata[kind]=d
            r=Recording(path,{'low':2.5,'high':2.5,'rpm':'60','reference':True,'ppr':1024,'slew_mode':'none'})
            self.items.append(r);r.run();self.assertIsNone(r.error,r.error);records[kind]=r
        bad=metadata['bearing']['bearing_error'];lost=bad['missing_cycles'];t=min(1.95,bad['end_time']+.1)
        clean=records['clean'];bearing=records['bearing']
        self.assertEqual(clean.position(t)['count']-bearing.position(t)['count'],lost)
        self.assertAlmostEqual((clean.position(t)['count']-bearing.position(t)['count'])*360/1024,bad['error_degrees'])
        self.assertEqual(clean.position(2.2)['count'],bearing.position(2.2)['count'])
        self.assertEqual(clean.advanced_analysis['quadrature']['valid_transitions']-bearing.advanced_analysis['quadrature']['valid_transitions'],4*lost)
        self.assertEqual(bearing.advanced_analysis['quadrature']['simultaneous'],0)
        missing=bearing.pulse_counts()['missing_by_channel'];self.assertEqual(missing['A'],lost);self.assertEqual(missing['B'],lost)
    def test_small_resolution_rejected(self):
        with self.assertRaisesRegex(ValueError,'72'):generate_capture(self.folder/'small.csv','bearing',ppr=32)

if __name__=='__main__':unittest.main()
