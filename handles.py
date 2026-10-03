"""Read-only diagnostic: which single-instance guards does a running Roblox hold?

list_singleton_handles(pid) -> [(handle_value, type_name, object_name)] for lock-type handles named like *singleton*

It only looks (like the Handles view in Process Explorer); it never closes or changes anything.
"""
import ctypes
import threading
from ctypes import wintypes

ntdll = ctypes.WinDLL("ntdll")
k32 = ctypes.WinDLL("kernel32", use_last_error=True)

PROCESS_DUP_HANDLE = 0x0040
DUPLICATE_SAME_ACCESS = 0x2
SystemExtendedHandleInformation = 64
STATUS_INFO_LENGTH_MISMATCH = 0xC0000004

k32.OpenProcess.restype = wintypes.HANDLE
k32.GetCurrentProcess.restype = wintypes.HANDLE
k32.DuplicateHandle.argtypes = [wintypes.HANDLE, wintypes.HANDLE, wintypes.HANDLE, ctypes.POINTER(wintypes.HANDLE),
                                wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
k32.CloseHandle.argtypes = [wintypes.HANDLE]


class HandleEntry(ctypes.Structure):
    _fields_ = [("Object", ctypes.c_void_p), ("UniqueProcessId", ctypes.c_size_t), ("HandleValue", ctypes.c_size_t),
                ("GrantedAccess", wintypes.ULONG), ("CreatorBackTraceIndex", wintypes.USHORT),
                ("ObjectTypeIndex", wintypes.USHORT), ("HandleAttributes", wintypes.ULONG), ("Reserved", wintypes.ULONG)]


class UnicodeString(ctypes.Structure):
    _fields_ = [("Length", wintypes.USHORT), ("MaximumLength", wintypes.USHORT), ("Buffer", ctypes.c_void_p)]


def _system_handles():
    size = 1 << 22
    while True:
        buf = ctypes.create_string_buffer(size)
        ret = wintypes.ULONG()
        status = ntdll.NtQuerySystemInformation(SystemExtendedHandleInformation, buf, size, ctypes.byref(ret))
        if status & 0xFFFFFFFF == STATUS_INFO_LENGTH_MISMATCH:
            size = max(size * 2, ret.value + (1 << 20))
            continue
        if status != 0:
            raise OSError(f"NtQuerySystemInformation failed: 0x{status & 0xFFFFFFFF:x}")
        count = ctypes.c_size_t.from_buffer(buf).value
        base = ctypes.addressof(buf) + 2 * ctypes.sizeof(ctypes.c_size_t)
        return buf, (HandleEntry * count).from_address(base)


def _query_name(handle, info_class):
    """ObjectTypeInformation (2) or ObjectNameInformation (1) -> string."""
    buf = ctypes.create_string_buffer(2048)
    ret = wintypes.ULONG()
    if ntdll.NtQueryObject(handle, info_class, buf, 2048, ctypes.byref(ret)) != 0:
        return ""
    us = UnicodeString.from_buffer(buf)
    if not us.Buffer or not us.Length:
        return ""
    return ctypes.wstring_at(us.Buffer, us.Length // 2)


def _named(handle, result):
    result.append(_query_name(handle, 1))


def list_singleton_handles(pid, all_named_locks=False):
    """Lock-type (Mutant/Event/Semaphore) handles inside `pid` whose name contains 'singleton'."""
    hproc = k32.OpenProcess(PROCESS_DUP_HANDLE, False, pid)
    if not hproc:
        raise PermissionError(f"can't open process {pid} for handle access (error {ctypes.get_last_error()}) - "
                              "run the sentinel as administrator")
    found = []
    try:
        keep, entries = _system_handles()
        me = k32.GetCurrentProcess()
        for e in entries:
            if e.UniqueProcessId != pid:
                continue
            dup = wintypes.HANDLE()
            if not k32.DuplicateHandle(hproc, e.HandleValue, me, ctypes.byref(dup), 0, False, DUPLICATE_SAME_ACCESS):
                continue
            try:
                tname = _query_name(dup, 2)
                if tname not in ("Mutant", "Event", "Semaphore"):
                    continue
                res = []
                t = threading.Thread(target=_named, args=(dup, res), daemon=True)
                t.start()
                t.join(0.5)                             # some handles block forever when asked for a name
                name = res[0] if res else ""
                if all_named_locks or "singleton" in name.lower():
                    found.append((e.HandleValue, tname, name))
            finally:
                k32.CloseHandle(dup)
    finally:
        k32.CloseHandle(hproc)
    return found


