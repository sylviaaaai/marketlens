"""Windows current-user DPAPI cache. No plaintext files or credential logging."""
import ctypes
from ctypes import wintypes
import json
import os
from pathlib import Path
from uuid import uuid4


class AccessCacheError(RuntimeError):
    pass


def atomic_write(path: Path, data: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_name(path.name + "." + uuid4().hex + ".tmp")
    try:
        with temp.open("xb") as handle:
            handle.write(data)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temp, path)
    finally:
        if temp.exists():
            temp.unlink()


class DataBlob(ctypes.Structure):
    _fields_ = [("size", wintypes.DWORD), ("data", ctypes.POINTER(ctypes.c_ubyte))]


def dpapi(data: bytes, entropy: bytes, *, decrypt: bool = False) -> bytes:
    if os.name != "nt":
        raise AccessCacheError("WindowsDPAPIRequired")
    crypt = ctypes.WinDLL("crypt32", use_last_error=True)
    kernel = ctypes.WinDLL("kernel32", use_last_error=True)
    fn = crypt.CryptUnprotectData if decrypt else crypt.CryptProtectData
    fn.argtypes = [ctypes.POINTER(DataBlob), ctypes.c_void_p, ctypes.POINTER(DataBlob),
                   ctypes.c_void_p, ctypes.c_void_p, wintypes.DWORD, ctypes.POINTER(DataBlob)]
    fn.restype = wintypes.BOOL
    kernel.LocalFree.argtypes = [ctypes.c_void_p]
    kernel.LocalFree.restype = ctypes.c_void_p
    buf, salt = ctypes.create_string_buffer(data), ctypes.create_string_buffer(entropy)
    src = DataBlob(len(data), ctypes.cast(buf, ctypes.POINTER(ctypes.c_ubyte)))
    extra = DataBlob(len(entropy), ctypes.cast(salt, ctypes.POINTER(ctypes.c_ubyte)))
    output = DataBlob()
    # UI forbidden; deliberately NOT CRYPTPROTECT_LOCAL_MACHINE.
    if not fn(ctypes.byref(src), None, ctypes.byref(extra), None, None, 1, ctypes.byref(output)):
        raise AccessCacheError("DPAPIProtectionFailed")
    try:
        return ctypes.string_at(output.data, output.size)
    finally:
        kernel.LocalFree(output.data)


class AccessCache:
    def __init__(self, directory: Path, database: Path):
        self.directory = Path(directory)
        self.context = str(Path(database).resolve())

    def _entropy(self, run: int, attempt: int) -> bytes:
        return f"MarketLens/v1/{self.context}/{run}/{attempt}".encode("utf-8")

    def verify_available(self) -> None:
        data = b"MarketLens DPAPI availability check"
        if dpapi(dpapi(data, b"check"), b"check", decrypt=True) != data:
            raise AccessCacheError("DPAPIRoundTripFailed")

    def save(self, run: int, attempt: int, access: dict[str, str]) -> None:
        # Separate immutable per-attempt envelopes; commit before DB evidence commit.
        data = json.dumps({"version": 1, "run": run, "attempt": attempt, "access": access}).encode("utf-8")
        try:
            atomic_write(self.directory / f"run_{run}_attempt_{attempt}.dpapi",
                         dpapi(data, self._entropy(run, attempt)))
        except (OSError, AccessCacheError):
            raise AccessCacheError("AccessCacheSaveFailed") from None

    def load(self, run: int, attempts: list[int]) -> dict[str, str]:
        access = {}
        try:
            for attempt in attempts:
                path = self.directory / f"run_{run}_attempt_{attempt}.dpapi"
                value = json.loads(dpapi(path.read_bytes(), self._entropy(run, attempt), decrypt=True))
                if value.get("version") != 1 or value.get("run") != run or value.get("attempt") != attempt:
                    raise ValueError()
                mapping = value["access"]
                if not isinstance(mapping, dict) or any(not isinstance(k, str) or not isinstance(v, str)
                                                        or not k or not v for k, v in mapping.items()):
                    raise ValueError()
                access.update(mapping)
        except (OSError, ValueError, KeyError, TypeError, AccessCacheError):
            raise AccessCacheError("AccessCacheUnavailable;no_automatic_search_refresh") from None
        return access
