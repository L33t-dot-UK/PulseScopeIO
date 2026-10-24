"""Local file selection, without a Tcl/Tk dependency on Windows."""
import sys


def file_filter(kind):
    if kind == "bin":
        return "Choose BIN channel recording", "BIN files", "*.bin;*.BIN", ".bin"
    if kind == "csv-channel":
        return "Choose single-channel CSV recording", "CSV channel files", "*.csv;*.tsv;*.txt", ".csv"
    return "Choose encoder recording", "Encoder recordings", "*.csv;*.tsv;*.txt;*.wav", ".csv"


def choose_file(kind=None):
    settings = file_filter(kind)
    return windows_file(settings) if sys.platform == "win32" else tk_file(settings)


def windows_file(settings):
    import ctypes
    from ctypes import wintypes

    class OpenFileName(ctypes.Structure):
        _fields_ = [
            ("lStructSize", wintypes.DWORD),
            ("hwndOwner", wintypes.HWND),
            ("hInstance", wintypes.HINSTANCE),
            ("lpstrFilter", wintypes.LPCWSTR),
            ("lpstrCustomFilter", wintypes.LPWSTR),
            ("nMaxCustFilter", wintypes.DWORD),
            ("nFilterIndex", wintypes.DWORD),
            ("lpstrFile", wintypes.LPWSTR),
            ("nMaxFile", wintypes.DWORD),
            ("lpstrFileTitle", wintypes.LPWSTR),
            ("nMaxFileTitle", wintypes.DWORD),
            ("lpstrInitialDir", wintypes.LPCWSTR),
            ("lpstrTitle", wintypes.LPCWSTR),
            ("Flags", wintypes.DWORD),
            ("nFileOffset", wintypes.WORD),
            ("nFileExtension", wintypes.WORD),
            ("lpstrDefExt", wintypes.LPCWSTR),
            ("lCustData", ctypes.c_ssize_t),
            ("lpfnHook", ctypes.c_void_p),
            ("lpTemplateName", wintypes.LPCWSTR),
            ("pvReserved", ctypes.c_void_p),
            ("dwReserved", wintypes.DWORD),
            ("FlagsEx", wintypes.DWORD),
        ]

    title, label, patterns, extension = settings
    path = ctypes.create_unicode_buffer(32768)
    dialog = OpenFileName()
    dialog.lStructSize = ctypes.sizeof(dialog)
    dialog.lpstrFilter = label + "\0" + patterns + "\0All files\0*.*\0\0"
    dialog.nFilterIndex = 1
    dialog.lpstrFile = ctypes.cast(path, wintypes.LPWSTR)
    dialog.nMaxFile = len(path)
    dialog.lpstrTitle = title
    dialog.lpstrDefExt = extension.lstrip(".")
    # Explorer picker; existing file/path only; preserve the server working directory.
    dialog.Flags = 0x00080000 | 0x00001000 | 0x00000800 | 0x00000008 | 0x02000000
    user32 = ctypes.WinDLL("user32", use_last_error=True)
    user32.GetForegroundWindow.restype = wintypes.HWND
    dialog.hwndOwner = user32.GetForegroundWindow()
    common = ctypes.WinDLL("comdlg32", use_last_error=True)
    common.GetOpenFileNameW.argtypes = [ctypes.POINTER(OpenFileName)]
    common.GetOpenFileNameW.restype = wintypes.BOOL
    common.CommDlgExtendedError.restype = wintypes.DWORD
    if common.GetOpenFileNameW(ctypes.byref(dialog)):
        return path.value
    error = common.CommDlgExtendedError()
    if error:
        raise RuntimeError(f"Windows file dialog failed (code {error:#x})")
    return ""


def tk_file(settings):
    import tkinter as tk
    from tkinter import filedialog

    title, label, patterns, extension = settings
    root = tk.Tk()
    root.withdraw()
    root.attributes("-topmost", True)
    try:
        return filedialog.askopenfilename(
            parent=root, title=title, defaultextension=extension,
            typevariable=tk.StringVar(root, value=label),
            filetypes=[(label, patterns.replace(";", " ")), ("All files", "*.*")],
        )
    finally:
        root.destroy()
