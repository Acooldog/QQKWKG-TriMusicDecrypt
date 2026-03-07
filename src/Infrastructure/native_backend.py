from __future__ import annotations

import ctypes
import pathlib
from functools import lru_cache

from src.Infrastructure.runtime_paths import RuntimePaths


class NativeBackendError(RuntimeError):
    pass


class NativeKudogBackend:
    def __init__(self, dll_path: pathlib.Path | None) -> None:
        self.dll_path = dll_path
        self.available = False
        self.reason = "dll_not_found"
        self._dll = None
        self._buffer_cache: dict[tuple[str, int], ctypes.Array] = {}
        if dll_path is None or not dll_path.exists():
            return
        try:
            self._dll = ctypes.CDLL(str(dll_path))
            self._bind()
            self.available = True
            self.reason = "ok"
        except Exception as exc:  # pragma: no cover - runtime environment dependent
            self._dll = None
            self.reason = f"load_failed:{exc}"

    def _bind(self) -> None:
        assert self._dll is not None
        self._dll.kudog_decode_v3.argtypes = [
            ctypes.POINTER(ctypes.c_uint8),
            ctypes.c_size_t,
            ctypes.POINTER(ctypes.c_uint8),
            ctypes.c_size_t,
            ctypes.POINTER(ctypes.c_uint8),
            ctypes.c_size_t,
            ctypes.c_ulonglong,
        ]
        self._dll.kudog_decode_v3.restype = ctypes.c_int
        self._dll.kudog_qmc_map_decrypt.argtypes = [
            ctypes.POINTER(ctypes.c_uint8),
            ctypes.c_size_t,
            ctypes.POINTER(ctypes.c_uint8),
            ctypes.c_size_t,
            ctypes.c_ulonglong,
        ]
        self._dll.kudog_qmc_map_decrypt.restype = ctypes.c_int
        self._dll.kudog_qmc_rc4_decrypt.argtypes = [
            ctypes.POINTER(ctypes.c_uint8),
            ctypes.c_size_t,
            ctypes.POINTER(ctypes.c_uint8),
            ctypes.c_size_t,
            ctypes.c_ulonglong,
        ]
        self._dll.kudog_qmc_rc4_decrypt.restype = ctypes.c_int

    def _cached_const_buffer(self, value: bytes, label: str) -> ctypes.Array:
        cache_key = (label, id(value))
        cached = self._buffer_cache.get(cache_key)
        if cached is not None:
            return cached
        array_type = ctypes.c_uint8 * len(value)
        buffer = array_type.from_buffer_copy(value)
        self._buffer_cache[cache_key] = buffer
        return buffer

    @staticmethod
    def _mutable_buffer(buffer: bytearray, length: int) -> ctypes.Array:
        array_type = ctypes.c_uint8 * length
        return array_type.from_buffer(buffer)

    def decode_v3_inplace(self, buffer: bytearray, length: int, own_key: bytes, pub_key: bytes, start_pos: int) -> None:
        if not self.available or self._dll is None:
            raise NativeBackendError(self.reason)
        rc = self._dll.kudog_decode_v3(
            self._mutable_buffer(buffer, length),
            length,
            self._cached_const_buffer(own_key, "own"),
            len(own_key),
            self._cached_const_buffer(pub_key, "pub"),
            len(pub_key),
            start_pos,
        )
        if rc != 0:
            raise NativeBackendError(f"kudog_decode_v3 rc={rc}")

    def map_decrypt_inplace(self, buffer: bytearray, length: int, key: bytes, start_pos: int) -> None:
        if not self.available or self._dll is None:
            raise NativeBackendError(self.reason)
        rc = self._dll.kudog_qmc_map_decrypt(
            self._mutable_buffer(buffer, length),
            length,
            self._cached_const_buffer(key, "map"),
            len(key),
            start_pos,
        )
        if rc != 0:
            raise NativeBackendError(f"kudog_qmc_map_decrypt rc={rc}")

    def rc4_decrypt_inplace(self, buffer: bytearray, length: int, key: bytes, start_pos: int) -> None:
        if not self.available or self._dll is None:
            raise NativeBackendError(self.reason)
        rc = self._dll.kudog_qmc_rc4_decrypt(
            self._mutable_buffer(buffer, length),
            length,
            self._cached_const_buffer(key, "rc4"),
            len(key),
            start_pos,
        )
        if rc != 0:
            raise NativeBackendError(f"kudog_qmc_rc4_decrypt rc={rc}")


def resolve_native_dll(paths: RuntimePaths) -> pathlib.Path | None:
    candidates = [
        paths.assets_dir / "kudog_native.dll",
        paths.root_dir / "kudog_native.dll",
        pathlib.Path.cwd() / "assets" / "kudog_native.dll",
        pathlib.Path.cwd() / "kudog_native.dll",
    ]
    for candidate in candidates:
        if candidate.exists() and candidate.is_file():
            return candidate
    return None


@lru_cache(maxsize=1)
def get_native_backend() -> NativeKudogBackend:
    paths = RuntimePaths.discover()
    return NativeKudogBackend(resolve_native_dll(paths))
