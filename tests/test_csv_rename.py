import unittest
from pathlib import Path
import csv,io
import numpy as np
from csv_export import layout,write_csv
from server import Handler
from tests import test_csv_layout as layouts
from tests import test_csv_channels as channels

class RenameCsvTests(unittest.TestCase):
    def setUp(self):self.fixture=layouts.CsvLayoutTests();self.fixture.setUp()
    def tearDown(self):self.fixture.tearDown()
    def test_original_body_units_and_quoted_unicode_headers(self):
        r,v=self.fixture.load('Time_ms;A;B',channels=2,delimiter=';')
        original=r.source.read_bytes();target=r.dir/'rename.csv'
        names=['Elapsed ms','Signal; positive','Inverse "signal" µV']
        write_csv(r,target,names)
        self.assertEqual(next(csv.reader(io.StringIO(target.read_text(encoding='utf-8')),delimiter=';')),names)
        self.assertEqual(target.read_bytes().split(b'\n',1)[1],original.split(b'\n',1)[1])
        self.assertEqual(r.source.read_bytes(),original)
        self.assertEqual(r.headers,['Time_ms','A','B'])
    def test_headerless_first_sample_preserved(self):
        r,v=self.fixture.load(None,channels=2)
        original=r.source.read_bytes();target=r.dir/'rename.csv'
        write_csv(r,target,['Time_s','A','B'])
        self.assertEqual(target.read_bytes().split(b'\n',1)[1],original)
        np.testing.assert_array_equal(np.loadtxt(target,delimiter=',',skiprows=1),v)
    def test_headerless_bom_preserved_as_valid_numeric_data(self):
        r,v=self.fixture.load(None,channels=1)
        r.source.write_bytes(b'\xef\xbb\xbf'+r.source.read_bytes())
        target=r.dir/'rename.csv';write_csv(r,target,['Time_s','A'])
        np.testing.assert_array_equal(np.loadtxt(target,delimiter=',',skiprows=1),v)
    def test_reject_invalid_names_and_leave_no_export(self):
        r,v=self.fixture.load('Time_s,A,B',channels=2)
        for names in (None,['A'],['Time_s','','B'],['Time_s','a','A'],['Time_s','A\nB','B'],['Time_s','x'*129,'B']):
            target=r.dir/'invalid.csv'
            with self.assertRaises(ValueError):write_csv(r,target,names)
            self.assertFalse(target.exists())
    def test_prepared_export_uses_custom_header(self):
        r,v=self.fixture.load('x,c1,c2',channels=2)
        target,kind=Handler.prepare_export(r,{'kind':'csv','csv_headers':['Time_s','A','B']})
        self.assertEqual(kind,'csv');self.assertTrue(target.name.startswith('renamed-export-'))
        self.assertEqual(target.read_text().splitlines()[0],'Time_s,A,B')
    def test_combined_selected_channels(self):
        fixture=channels.CsvChannelTests();fixture.setUp()
        try:
            a,_=fixture.channel('A');b,_=fixture.channel('B')
            r=fixture.recording({'A':a,'B':b});r.run();self.assertIsNone(r.error)
            self.assertFalse(layout(r)['source_layout'])
            target=r.dir/'rename.csv';names=['Seconds','one','two','three','four','five','six'];write_csv(r,target,names)
            self.assertEqual(next(csv.reader(io.StringIO(target.read_text()))),names)
            np.testing.assert_allclose(np.loadtxt(target,delimiter=',',skiprows=1),r.data)
        finally:fixture.tearDown()

if __name__=='__main__':unittest.main()
