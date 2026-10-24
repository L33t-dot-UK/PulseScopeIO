import unittest
from report import engineering

class ReportUnitsTests(unittest.TestCase):
    def test_physical_units(self):
        for value,unit,expected in [(1.25e-6,'s','1.25 µs'),(2.5e-3,'s','2.5 ms'),(8e-9,'s','8 ns'),(8192,'Hz','8.192 kHz'),(3333333,'Hz','3.3333 MHz'),(-2.5e-6,'s','-2.5 µs'),(.9999999,'s','1 s'),(0,'s','0 s')]:
            self.assertEqual(engineering(value,unit),expected)
    def test_unknown_units_and_unavailable(self):
        self.assertEqual(engineering(12,'samples'),'12 samples')
        self.assertEqual(engineering(None,'Hz'),'-')
        self.assertEqual(engineering(float('nan'),'s'),'-')
