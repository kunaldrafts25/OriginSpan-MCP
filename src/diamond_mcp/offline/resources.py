"""Single-host process limits without optional telemetry dependencies."""
from __future__ import annotations

from pathlib import Path
import os


def process_memory(process):
    try:
        if os.name == 'nt':
            import ctypes
            from ctypes import wintypes
            class Counters(ctypes.Structure):
                _fields_ = [('cb', wintypes.DWORD), ('faults', wintypes.DWORD)] + [
                    (name, ctypes.c_size_t) for name in ('peak', 'working', 'peak_paged', 'paged', 'peak_nonpaged', 'nonpaged', 'pagefile', 'peak_pagefile')]
            counters = Counters(); counters.cb = ctypes.sizeof(counters)
            call = ctypes.WinDLL('psapi').GetProcessMemoryInfo
            call.argtypes = [wintypes.HANDLE, ctypes.c_void_p, wintypes.DWORD]
            call.restype = wintypes.BOOL
            if call(int(process._handle), ctypes.byref(counters), counters.cb):
                return dict(rss_mib=round(counters.working/1048576, 1), peak_rss_mib=round(counters.peak/1048576, 1))
        else:
            data = {}
            for line in Path(f'/proc/{process.pid}/status').read_text().splitlines():
                key, _, value = line.partition(':')
                if key in {'VmRSS', 'VmHWM'}: data[key] = int(value.split()[0])/1024
            if 'VmRSS' in data: return dict(rss_mib=round(data['VmRSS'], 1), peak_rss_mib=round(data.get('VmHWM', data['VmRSS']), 1))
    except (OSError, ValueError, AttributeError):
        pass
    return dict(rss_mib=None, peak_rss_mib=None)


class InferenceLock:
    def __init__(self, state): self.path = Path(state)/'offline-models/inference.lock'
    def __enter__(self):
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.file = self.path.open('a+b')
        self.file.seek(0); self.file.write(b'0'); self.file.flush(); self.file.seek(0)
        try:
            if os.name == 'nt':
                import msvcrt
                msvcrt.locking(self.file.fileno(), msvcrt.LK_NBLCK, 1)
            else:
                import fcntl
                fcntl.flock(self.file.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError as exc:
            self.file.close()
            raise ValueError('Another offline inference run owns this state directory') from exc
        return self
    def __exit__(self, *_):
        self.file.seek(0)
        if os.name == 'nt':
            import msvcrt
            msvcrt.locking(self.file.fileno(), msvcrt.LK_UNLCK, 1)
        else:
            import fcntl
            fcntl.flock(self.file.fileno(), fcntl.LOCK_UN)
        self.file.close()
