"""Docker CLI CPU accounting on Windows, using the still-open Popen handle."""
import os

def child_cpu_seconds(process):
    if os.name!='nt':return None
    import ctypes
    from ctypes import wintypes
    class FILETIME(ctypes.Structure):
        _fields_=[('low',wintypes.DWORD),('high',wintypes.DWORD)]
    fn=ctypes.WinDLL('kernel32',use_last_error=True).GetProcessTimes
    fn.argtypes=[wintypes.HANDLE]+[ctypes.POINTER(FILETIME)]*4
    fn.restype=wintypes.BOOL
    creation,exit_time,kernel,user=(FILETIME() for _ in range(4))
    if not fn(wintypes.HANDLE(int(process._handle)),ctypes.byref(creation),ctypes.byref(exit_time),ctypes.byref(kernel),ctypes.byref(user)):
        raise ctypes.WinError(ctypes.get_last_error())
    return ((kernel.high<<32)+kernel.low+(user.high<<32)+user.low)/10_000_000
