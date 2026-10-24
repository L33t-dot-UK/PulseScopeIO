import ctypes
import sys
import unittest
from unittest.mock import Mock, patch
import file_picker

class PickerTests(unittest.TestCase):
    def test_filter_modes(self):
        self.assertEqual(file_picker.file_filter('bin')[2],'*.bin;*.BIN')
        self.assertNotIn('wav',file_picker.file_filter('csv-channel')[2])
        self.assertIn('wav',file_picker.file_filter(None)[2])
    def test_dispatch_without_importing_tk_on_windows(self):
        with patch.object(sys,'platform','win32'),patch('file_picker.windows_file',return_value='a.csv') as choose,patch('file_picker.tk_file') as tk:
            self.assertEqual(file_picker.choose_file('csv-channel'),'a.csv');choose.assert_called_once();tk.assert_not_called()
    @unittest.skipUnless(sys.platform=='win32','Windows native API')
    def test_windows_selection_cancel_and_error(self):
        common=Mock();user=Mock();user.GetForegroundWindow.return_value=None
        def select(pointer):
            obj=pointer._obj
            self.assertEqual(obj.nFilterIndex,1)
            self.assertEqual(obj.lpstrTitle,'Choose single-channel CSV recording')
            self.assertTrue(obj.Flags & 8)
            field=type(obj).lpstrFile.offset
            address=ctypes.c_void_p.from_address(ctypes.addressof(obj)+field).value
            value=ctypes.create_unicode_buffer('C:\\captures\\signal.csv')
            ctypes.memmove(address,value,ctypes.sizeof(value))
            return 1
        common.GetOpenFileNameW.side_effect=select
        with patch('ctypes.WinDLL',side_effect=[user,common]):
            self.assertEqual(file_picker.windows_file(file_picker.file_filter('csv-channel')),'C:\\captures\\signal.csv')
        common.GetOpenFileNameW.side_effect=None;common.GetOpenFileNameW.return_value=0
        common.CommDlgExtendedError.return_value=0
        with patch('ctypes.WinDLL',side_effect=[user,common]):self.assertEqual(file_picker.windows_file(file_picker.file_filter('bin')),'')
        common.CommDlgExtendedError.return_value=2
        with patch('ctypes.WinDLL',side_effect=[user,common]),self.assertRaisesRegex(RuntimeError,'0x2'):
            file_picker.windows_file(file_picker.file_filter('bin'))
    def test_linux_picker_filter_and_cleanup(self):
        root=Mock()
        with patch('tkinter.Tk',return_value=root),patch('tkinter.StringVar') as var,patch('tkinter.filedialog.askopenfilename',return_value='a.csv') as choose:
            self.assertEqual(file_picker.tk_file(file_picker.file_filter('csv-channel')),'a.csv')
            var.assert_called_once_with(root,value='CSV channel files')
            self.assertEqual(choose.call_args.kwargs['filetypes'][0],('CSV channel files','*.csv *.tsv *.txt'))
            root.destroy.assert_called_once()

if __name__=='__main__':unittest.main()
