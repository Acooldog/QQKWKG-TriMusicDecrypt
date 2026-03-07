from __future__ import annotations

import argparse
import base64
import ctypes
import hashlib
import json
import lzma
import os
import pathlib
import sqlite3
import struct
import tempfile
import time
from dataclasses import dataclass
from functools import lru_cache
from typing import BinaryIO

try:
    import numpy as np
except Exception:  # pragma: no cover - optional acceleration
    np = None

from src.Infrastructure.native_backend import NativeBackendError, get_native_backend
from src.Infrastructure.runtime_paths import RuntimePaths
from src.Infrastructure.transcoder import probe_audio_container

PATHS = RuntimePaths.discover()
DEFAULT_KEY_PATH = PATHS.assets_dir / "kugou_key.xz"
DEFAULT_OUTPUT_DIR = PATHS.output_dir
DEFAULT_KGG_DB_PATH = pathlib.Path(os.environ.get("APPDATA", "")) / "KuGou8" / "KGMusicV3.db"

HEADER_LEN = 1024
KGM_MAGIC = bytes([
    0x7C, 0xD5, 0x32, 0xEB, 0x86, 0x02, 0x7F, 0x4B,
    0xA8, 0xAF, 0xA6, 0x8E, 0x0F, 0xFF, 0x99, 0x14,
])
VPR_MAGIC = bytes([
    0x05, 0x28, 0xBC, 0x96, 0xE9, 0xE4, 0x5A, 0x43,
    0x91, 0xAA, 0xBD, 0xD0, 0x7A, 0xF5, 0x36, 0x31,
])
SUPPORTED_SUFFIXES = {".kgm", ".kgma", ".kgg", ".vpr", ".kgm.flac", ".vpr.flac"}
OUTPUT_AUDIO_EXTENSIONS = {".flac", ".ogg", ".wav", ".mp3", ".m4a", ".bin"}
V3_STREAM_CHUNK_SIZE = 16 * 1024 * 1024
V5_STREAM_CHUNK_SIZE = 4 * 1024 * 1024
OWN_KEY_LEN = 17
PUB_KEY_LEN = 1170494464
PUB_KEY_LEN_MAGNIFICATION = 16
PUB_KEY_MEND = bytes([
    0xB8, 0xD5, 0x3D, 0xB2, 0xE9, 0xAF, 0x78, 0x8C, 0x83, 0x33, 0x71, 0x51, 0x76, 0xA0,
    0xCD, 0x37, 0x2F, 0x3E, 0x35, 0x8D, 0xA9, 0xBE, 0x98, 0xB7, 0xE7, 0x8C, 0x22, 0xCE,
    0x5A, 0x61, 0xDF, 0x68, 0x69, 0x89, 0xFE, 0xA5, 0xB6, 0xDE, 0xA9, 0x77, 0xFC, 0xC8,
    0xBD, 0xBD, 0xE5, 0x6D, 0x3E, 0x5A, 0x36, 0xEF, 0x69, 0x4E, 0xBE, 0xE1, 0xE9, 0x66,
    0x1C, 0xF3, 0xD9, 0x02, 0xB6, 0xF2, 0x12, 0x9B, 0x44, 0xD0, 0x6F, 0xB9, 0x35, 0x89,
    0xB6, 0x46, 0x6D, 0x73, 0x82, 0x06, 0x69, 0xC1, 0xED, 0xD7, 0x85, 0xC2, 0x30, 0xDF,
    0xA2, 0x62, 0xBE, 0x79, 0x2D, 0x62, 0x62, 0x3D, 0x0D, 0x7E, 0xBE, 0x48, 0x89, 0x23,
    0x02, 0xA0, 0xE4, 0xD5, 0x75, 0x51, 0x32, 0x02, 0x53, 0xFD, 0x16, 0x3A, 0x21, 0x3B,
    0x16, 0x0F, 0xC3, 0xB2, 0xBB, 0xB3, 0xE2, 0xBA, 0x3A, 0x3D, 0x13, 0xEC, 0xF6, 0x01,
    0x45, 0x84, 0xA5, 0x70, 0x0F, 0x93, 0x49, 0x0C, 0x64, 0xCD, 0x31, 0xD5, 0xCC, 0x4C,
    0x07, 0x01, 0x9E, 0x00, 0x1A, 0x23, 0x90, 0xBF, 0x88, 0x1E, 0x3B, 0xAB, 0xA6, 0x3E,
    0xC4, 0x73, 0x47, 0x10, 0x7E, 0x3B, 0x5E, 0xBC, 0xE3, 0x00, 0x84, 0xFF, 0x09, 0xD4,
    0xE0, 0x89, 0x0F, 0x5B, 0x58, 0x70, 0x4F, 0xFB, 0x65, 0xD8, 0x5C, 0x53, 0x1B, 0xD3,
    0xC8, 0xC6, 0xBF, 0xEF, 0x98, 0xB0, 0x50, 0x4F, 0x0F, 0xEA, 0xE5, 0x83, 0x58, 0x8C,
    0x28, 0x2C, 0x84, 0x67, 0xCD, 0xD0, 0x9E, 0x47, 0xDB, 0x27, 0x50, 0xCA, 0xF4, 0x63,
    0x63, 0xE8, 0x97, 0x7F, 0x1B, 0x4B, 0x0C, 0xC2, 0xC1, 0x21, 0x4C, 0xCC, 0x58, 0xF5,
    0x94, 0x52, 0xA3, 0xF3, 0xD3, 0xE0, 0x68, 0xF4, 0x00, 0x23, 0xF3, 0x5E, 0x0A, 0x7B,
    0x93, 0xDD, 0xAB, 0x12, 0xB2, 0x13, 0xE8, 0x84, 0xD7, 0xA7, 0x9F, 0x0F, 0x32, 0x4C,
    0x55, 0x1D, 0x04, 0x36, 0x52, 0xDC, 0x03, 0xF3, 0xF9, 0x4E, 0x42, 0xE9, 0x3D, 0x61,
    0xEF, 0x7C, 0xB6, 0xB3, 0x93, 0x50,
])

PAGE_SIZE = 0x400
SQLITE_HEADER = b"SQLite format 3\x00"
DEFAULT_MASTER_KEY = bytes([
    0x1D, 0x61, 0x31, 0x45, 0xB2, 0x47, 0xBF, 0x7F, 0x3D, 0x18, 0x96, 0x72, 0x14, 0x4F, 0xE4, 0xBF,
    0x00, 0x00, 0x00, 0x00, 0x73, 0x41, 0x6C, 0x54,
])


class DecodeError(RuntimeError):
    pass


class UnrecognizedAudioContainerError(DecodeError):
    def __init__(self, summary: dict):
        super().__init__("unrecognized_audio_container")
        self.summary = summary


@dataclass(slots=True)
class KugouHeader:
    magic_header: bytes
    audio_offset: int
    crypto_version: int
    crypto_slot: int
    crypto_test_data: bytes
    crypto_key: bytes
    audio_hash: str = ""
    declared_extension: str = ""


class StreamCipher:
    def decrypt(self, data: bytearray, offset: int) -> None:
        raise NotImplementedError


class StaticCipher(StreamCipher):
    BOX = bytes([
        0x77, 0x48, 0x32, 0x73, 0xDE, 0xF2, 0xC0, 0xC8, 0x95, 0xEC, 0x30, 0xB2, 0x51, 0xC3, 0xE1, 0xA0,
        0x9E, 0xE6, 0x9D, 0xCF, 0xFA, 0x7F, 0x14, 0xD1, 0xCE, 0xB8, 0xDC, 0xC3, 0x4A, 0x67, 0x93, 0xD6,
        0x28, 0xC2, 0x91, 0x70, 0xCA, 0x8D, 0xA2, 0xA4, 0xF0, 0x08, 0x61, 0x90, 0x7E, 0x6F, 0xA2, 0xE0,
        0xEB, 0xAE, 0x3E, 0xB6, 0x67, 0xC7, 0x92, 0xF4, 0x91, 0xB5, 0xF6, 0x6C, 0x5E, 0x84, 0x40, 0xF7,
        0xF3, 0x1B, 0x02, 0x7F, 0xD5, 0xAB, 0x41, 0x89, 0x28, 0xF4, 0x25, 0xCC, 0x52, 0x11, 0xAD, 0x43,
        0x68, 0xA6, 0x41, 0x8B, 0x84, 0xB5, 0xFF, 0x2C, 0x92, 0x4A, 0x26, 0xD8, 0x47, 0x6A, 0x7C, 0x95,
        0x61, 0xCC, 0xE6, 0xCB, 0xBB, 0x3F, 0x47, 0x58, 0x89, 0x75, 0xC3, 0x75, 0xA1, 0xD9, 0xAF, 0xCC,
        0x08, 0x73, 0x17, 0xDC, 0xAA, 0x9A, 0xA2, 0x16, 0x41, 0xD8, 0xA2, 0x06, 0xC6, 0x8B, 0xFC, 0x66,
        0x34, 0x9F, 0xCF, 0x18, 0x23, 0xA0, 0x0A, 0x74, 0xE7, 0x2B, 0x27, 0x70, 0x92, 0xE9, 0xAF, 0x37,
        0xE6, 0x8C, 0xA7, 0xBC, 0x62, 0x65, 0x9C, 0xC2, 0x08, 0xC9, 0x88, 0xB3, 0xF3, 0x43, 0xAC, 0x74,
        0x2C, 0x0F, 0xD4, 0xAF, 0xA1, 0xC3, 0x01, 0x64, 0x95, 0x4E, 0x48, 0x9F, 0xF4, 0x35, 0x78, 0x95,
        0x7A, 0x39, 0xD6, 0x6A, 0xA0, 0x6D, 0x40, 0xE8, 0x4F, 0xA8, 0xEF, 0x11, 0x1D, 0xF3, 0x1B, 0x3F,
        0x3F, 0x07, 0xDD, 0x6F, 0x5B, 0x19, 0x30, 0x19, 0xFB, 0xEF, 0x0E, 0x37, 0xF0, 0x0E, 0xCD, 0x16,
        0x49, 0xFE, 0x53, 0x47, 0x13, 0x1A, 0xBD, 0xA4, 0xF1, 0x40, 0x19, 0x60, 0x0E, 0xED, 0x68, 0x09,
        0x06, 0x5F, 0x4D, 0xCF, 0x3D, 0x1A, 0xFE, 0x20, 0x77, 0xE4, 0xD9, 0xDA, 0xF9, 0xA4, 0x2B, 0x76,
        0x1C, 0x71, 0xDB, 0x00, 0xBC, 0xFD, 0x0C, 0x6C, 0xA5, 0x47, 0xF7, 0xF6, 0x00, 0x79, 0x4A, 0x11,
    ])

    def decrypt(self, data: bytearray, offset: int) -> None:
        for i in range(len(data)):
            pos = offset + i
            if pos > 0x7FFF:
                pos %= 0x7FFF
            idx = (pos * pos + 27) & 0xFF
            data[i] ^= self.BOX[idx]


class MapCipher(StreamCipher):
    def __init__(self, key: bytes) -> None:
        if not key:
            raise DecodeError("qmc map cipher key is empty")
        self.key = key
        self.size = len(key)
        self.native_backend = get_native_backend()

    @staticmethod
    def _rotate(value: int, bits: int) -> int:
        rotate = (bits + 4) % 8
        return ((value << rotate) & 0xFF) | (value >> rotate)

    def _mask(self, offset: int) -> int:
        if offset > 0x7FFF:
            offset %= 0x7FFF
        idx = (offset * offset + 71214) % self.size
        return self._rotate(self.key[idx], idx & 0x7)

    def decrypt(self, data: bytearray, offset: int) -> None:
        if self.native_backend.available:
            try:
                self.native_backend.map_decrypt_inplace(data, len(data), self.key, offset)
                return
            except NativeBackendError:
                pass
        for i in range(len(data)):
            data[i] ^= self._mask(offset + i)


class RC4Cipher(StreamCipher):
    SEGMENT_SIZE = 5120
    FIRST_SEGMENT_SIZE = 128

    def __init__(self, key: bytes) -> None:
        if not key:
            raise DecodeError("qmc rc4 cipher key is empty")
        self.key = key
        self.n = len(key)
        self.native_backend = get_native_backend()
        self.box = [i & 0xFF for i in range(self.n)]
        j = 0
        for i in range(self.n):
            j = (j + self.box[i] + key[i % self.n]) % self.n
            self.box[i], self.box[j] = self.box[j], self.box[i]
        self.hash_base = 1
        for value in key:
            if value == 0:
                continue
            next_hash = (self.hash_base * value) & 0xFFFFFFFF
            if next_hash == 0 or next_hash <= self.hash_base:
                break
            self.hash_base = next_hash

    def _get_segment_skip(self, idx: int) -> int:
        seed = self.key[idx % self.n]
        value = int(float(self.hash_base) / float((idx + 1) * seed) * 100.0)
        return value % self.n

    def _decrypt_first_segment(self, data: bytearray, offset: int) -> None:
        for i in range(len(data)):
            data[i] ^= self.key[self._get_segment_skip(offset + i)]

    def _decrypt_segment(self, data: bytearray, offset: int) -> None:
        box = self.box[:]
        j = 0
        k = 0
        skip = (offset % self.SEGMENT_SIZE) + self._get_segment_skip(offset // self.SEGMENT_SIZE)
        for i in range(-skip, len(data)):
            j = (j + 1) % self.n
            k = (box[j] + k) % self.n
            box[j], box[k] = box[k], box[j]
            if i >= 0:
                data[i] ^= box[(box[j] + box[k]) % self.n]

    def decrypt(self, data: bytearray, offset: int) -> None:
        if self.native_backend.available:
            try:
                self.native_backend.rc4_decrypt_inplace(data, len(data), self.key, offset)
                return
            except NativeBackendError:
                pass
        view_offset = offset
        processed = 0
        to_process = len(data)
        if view_offset < self.FIRST_SEGMENT_SIZE:
            block = min(to_process, self.FIRST_SEGMENT_SIZE - view_offset)
            chunk = data[:block]
            self._decrypt_first_segment(chunk, view_offset)
            data[:block] = chunk
            view_offset += block
            processed += block
            to_process -= block
        if to_process <= 0:
            return
        if view_offset % self.SEGMENT_SIZE != 0:
            block = min(to_process, self.SEGMENT_SIZE - (view_offset % self.SEGMENT_SIZE))
            chunk = data[processed:processed + block]
            self._decrypt_segment(chunk, view_offset)
            data[processed:processed + block] = chunk
            view_offset += block
            processed += block
            to_process -= block
        while to_process > self.SEGMENT_SIZE:
            chunk = data[processed:processed + self.SEGMENT_SIZE]
            self._decrypt_segment(chunk, view_offset)
            data[processed:processed + self.SEGMENT_SIZE] = chunk
            view_offset += self.SEGMENT_SIZE
            processed += self.SEGMENT_SIZE
            to_process -= self.SEGMENT_SIZE
        if to_process > 0:
            chunk = data[processed:]
            self._decrypt_segment(chunk, view_offset)
            data[processed:] = chunk


class _AesCbcNoPadding:
    def __init__(self) -> None:
        self.bcrypt = ctypes.WinDLL("bcrypt")
        self.alg_handle = ctypes.c_void_p()
        self.object_length = ctypes.c_ulong()
        self.block_length = ctypes.c_ulong()
        self._open_provider()

    def _check(self, status: int, action: str) -> None:
        if status != 0:
            raise DecodeError(f"bcrypt {action} failed: 0x{status & 0xFFFFFFFF:08x}")

    def _open_provider(self) -> None:
        result = ctypes.c_ulong()
        status = self.bcrypt.BCryptOpenAlgorithmProvider(
            ctypes.byref(self.alg_handle),
            ctypes.c_wchar_p("AES"),
            None,
            0,
        )
        self._check(status, "open algorithm provider")
        mode = ctypes.create_unicode_buffer("ChainingModeCBC")
        status = self.bcrypt.BCryptSetProperty(
            self.alg_handle,
            ctypes.c_wchar_p("ChainingMode"),
            ctypes.cast(mode, ctypes.POINTER(ctypes.c_ubyte)),
            ctypes.sizeof(mode),
            0,
        )
        self._check(status, "set chaining mode")
        status = self.bcrypt.BCryptGetProperty(
            self.alg_handle,
            ctypes.c_wchar_p("ObjectLength"),
            ctypes.byref(self.object_length),
            ctypes.sizeof(self.object_length),
            ctypes.byref(result),
            0,
        )
        self._check(status, "get object length")
        status = self.bcrypt.BCryptGetProperty(
            self.alg_handle,
            ctypes.c_wchar_p("BlockLength"),
            ctypes.byref(self.block_length),
            ctypes.sizeof(self.block_length),
            ctypes.byref(result),
            0,
        )
        self._check(status, "get block length")

    def decrypt(self, buffer: bytes | bytearray, key: bytes, iv: bytes) -> bytes:
        if len(key) != 16:
            raise DecodeError(f"invalid AES key size: {len(key)}")
        if len(iv) != int(self.block_length.value):
            raise DecodeError(f"invalid AES IV size: {len(iv)}")
        if len(buffer) % int(self.block_length.value) != 0:
            raise DecodeError("AES CBC buffer must align to block size")

        key_handle = ctypes.c_void_p()
        key_object = (ctypes.c_ubyte * int(self.object_length.value))()
        key_bytes = (ctypes.c_ubyte * len(key)).from_buffer_copy(key)
        status = self.bcrypt.BCryptGenerateSymmetricKey(
            self.alg_handle,
            ctypes.byref(key_handle),
            key_object,
            len(key_object),
            key_bytes,
            len(key),
            0,
        )
        self._check(status, "generate symmetric key")
        try:
            iv_bytes = (ctypes.c_ubyte * len(iv)).from_buffer_copy(iv)
            src = (ctypes.c_ubyte * len(buffer)).from_buffer_copy(bytes(buffer))
            dst = (ctypes.c_ubyte * len(buffer))()
            out_len = ctypes.c_ulong()
            status = self.bcrypt.BCryptDecrypt(
                key_handle,
                src,
                len(buffer),
                None,
                iv_bytes,
                len(iv),
                dst,
                len(buffer),
                ctypes.byref(out_len),
                0,
            )
            self._check(status, "decrypt")
            return bytes(dst[: out_len.value])
        finally:
            self.bcrypt.BCryptDestroyKey(key_handle)

    def close(self) -> None:
        if self.alg_handle:
            self.bcrypt.BCryptCloseAlgorithmProvider(self.alg_handle, 0)
            self.alg_handle = ctypes.c_void_p()


_AES_CBC = _AesCbcNoPadding()


def _rotate_byte(value: int, bits: int) -> int:
    return ((value << bits) & 0xFF) | (value >> (8 - bits))


def detect_extension(head: bytes, fallback: str = "bin") -> str:
    if head.startswith(b"fLaC"):
        return "flac"
    if head.startswith(b"OggS"):
        return "ogg"
    if head.startswith(b"RIFF") and head[8:12] == b"WAVE":
        return "wav"
    if head.startswith(b"ID3"):
        return "mp3"
    if len(head) >= 2 and head[0] == 0xFF and head[1] in (0xFB, 0xF3, 0xF2):
        return "mp3"
    if b"ftyp" in head[:64]:
        return "m4a"
    return fallback


def output_basename(input_path: pathlib.Path) -> str:
    name = input_path.name
    lower_name = name.lower()
    for suffix in sorted(SUPPORTED_SUFFIXES, key=len, reverse=True):
        if lower_name.endswith(suffix):
            return name[: -len(suffix)]
    return input_path.stem


def _simple_make_key(salt: int, length: int) -> bytes:
    import math

    return bytes(int(abs(math.tan(float(salt) + float(i) * 0.1)) * 100.0) & 0xFF for i in range(length))


def _tea_decrypt_block(block: bytes, key: bytes, rounds: int = 32) -> bytes:
    if len(block) != 8 or len(key) != 16:
        raise DecodeError("invalid TEA block or key size")
    v0, v1 = struct.unpack(">2I", block)
    k0, k1, k2, k3 = struct.unpack(">4I", key)
    delta = 0x9E3779B9
    total = (delta * (rounds // 2)) & 0xFFFFFFFF
    for _ in range(rounds // 2):
        v1 = (v1 - ((((v0 << 4) + k2) & 0xFFFFFFFF) ^ ((v0 + total) & 0xFFFFFFFF) ^ (((v0 >> 5) + k3) & 0xFFFFFFFF))) & 0xFFFFFFFF
        v0 = (v0 - ((((v1 << 4) + k0) & 0xFFFFFFFF) ^ ((v1 + total) & 0xFFFFFFFF) ^ (((v1 >> 5) + k1) & 0xFFFFFFFF))) & 0xFFFFFFFF
        total = (total - delta) & 0xFFFFFFFF
    return struct.pack(">2I", v0, v1)


def _xor8(a: bytes, b: bytes) -> bytes:
    return bytes(x ^ y for x, y in zip(a, b))


def _decrypt_tencent_tea(buffer: bytes, key: bytes) -> bytes:
    if len(buffer) % 8 != 0:
        raise DecodeError("Tencent TEA input size must align to 8 bytes")
    if len(buffer) < 16:
        raise DecodeError("Tencent TEA input size too small")
    first = _tea_decrypt_block(buffer[:8], key)
    pad_len = first[0] & 0x7
    out_len = len(buffer) - 1 - pad_len - 2 - 7
    out = bytearray(out_len)
    iv_prev = b"\x00" * 8
    iv_cur = buffer[:8]
    in_pos = 8
    dest = bytearray(first)
    dest_idx = 1 + pad_len

    def crypt_block() -> tuple[bytearray, bytes, bytes, int]:
        nonlocal in_pos, dest_idx, iv_prev, iv_cur, dest
        iv_prev = iv_cur
        iv_cur = buffer[in_pos:in_pos + 8]
        xored = _xor8(dest, iv_cur)
        dest = bytearray(_tea_decrypt_block(xored, key))
        in_pos += 8
        dest_idx = 0
        return dest, iv_prev, iv_cur, dest_idx

    consumed_salt = 0
    while consumed_salt < 2:
        if dest_idx < 8:
            dest_idx += 1
            consumed_salt += 1
        else:
            crypt_block()

    out_pos = 0
    while out_pos < out_len:
        if dest_idx < 8:
            out[out_pos] = dest[dest_idx] ^ iv_prev[dest_idx]
            dest_idx += 1
            out_pos += 1
        else:
            crypt_block()

    for _ in range(7):
        if dest_idx == 8:
            crypt_block()
        if (dest[dest_idx] ^ iv_prev[dest_idx]) != 0:
            raise DecodeError("Tencent TEA zero check failed")
        dest_idx += 1

    return bytes(out)


def _derive_key(raw_key: bytes) -> bytes:
    raw = base64.b64decode(raw_key)
    prefix = b"QQMusic EncV2,Key:"
    if raw.startswith(prefix):
        raw = _derive_key_v2(raw[len(prefix):])
    return _derive_key_v1(raw)


def _derive_key_v1(raw: bytes) -> bytes:
    if len(raw) < 16:
        raise DecodeError("qmc raw key too short")
    simple = _simple_make_key(106, 8)
    tea_key = bytearray(16)
    for i in range(8):
        tea_key[i * 2] = simple[i]
        tea_key[i * 2 + 1] = raw[i]
    tail = _decrypt_tencent_tea(raw[8:], bytes(tea_key))
    return raw[:8] + tail


def _derive_key_v2(raw: bytes) -> bytes:
    key1 = bytes([0x33, 0x38, 0x36, 0x5A, 0x4A, 0x59, 0x21, 0x40, 0x23, 0x2A, 0x24, 0x25, 0x5E, 0x26, 0x29, 0x28])
    key2 = bytes([0x2A, 0x2A, 0x23, 0x21, 0x28, 0x23, 0x24, 0x25, 0x26, 0x5E, 0x61, 0x31, 0x63, 0x5A, 0x2C, 0x54])
    step1 = _decrypt_tencent_tea(raw, key1)
    step2 = _decrypt_tencent_tea(step1, key2)
    return base64.b64decode(step2)


def _new_qmc_cipher_from_ekey(ekey: str | bytes) -> StreamCipher:
    raw = ekey.encode("utf-8") if isinstance(ekey, str) else ekey
    key = _derive_key(raw)
    if len(key) > 300:
        return RC4Cipher(key)
    if key:
        return MapCipher(key)
    return StaticCipher()


@lru_cache(maxsize=4)
def _load_public_key_cached(cache_key: tuple[str, int, int]) -> bytes:
    return lzma.decompress(pathlib.Path(cache_key[0]).read_bytes())


def load_public_key(path: pathlib.Path) -> bytes:
    stat = path.stat()
    cache_key = (str(path.resolve()), stat.st_size, stat.st_mtime_ns)
    return _load_public_key_cached(cache_key)


@lru_cache(maxsize=32)
def _build_own_transform_tables(own_key: bytes) -> tuple[bytes, ...]:
    tables: list[bytes] = []
    for key_byte in own_key:
        table = bytearray(256)
        for src in range(256):
            value = src ^ key_byte
            value ^= (value & 0x0F) << 4
            table[src] = value
        tables.append(bytes(table))
    return tuple(tables)


@lru_cache(maxsize=1)
def _build_pub_transform_tables() -> tuple[bytes, ...]:
    tables: list[bytes] = []
    for mend_byte in PUB_KEY_MEND:
        table = bytearray(256)
        for pub_byte in range(256):
            value = mend_byte ^ pub_byte
            value ^= (value & 0x0F) << 4
            table[pub_byte] = value
        tables.append(bytes(table))
    return tuple(tables)


@lru_cache(maxsize=32)
def _build_v3_block_phase_tables(own_key: bytes) -> tuple[tuple[tuple[bytes, ...], tuple[bytes, ...]], ...]:
    own_tables = _build_own_transform_tables(own_key)
    pub_tables = _build_pub_transform_tables()
    phases: list[tuple[tuple[bytes, ...], tuple[bytes, ...]]] = []
    for phase in range(17):
        phase_start = phase * 16
        own_phase = tuple(own_tables[(phase_start + i) % OWN_KEY_LEN] for i in range(16))
        pub_phase = tuple(pub_tables[phase_start + i] for i in range(16))
        phases.append((own_phase, pub_phase))
    return tuple(phases)


@lru_cache(maxsize=32)
def _build_v3_numpy_lut(own_key: bytes):
    if np is None:
        return None
    own_tables = np.frombuffer(b"".join(_build_own_transform_tables(own_key)), dtype=np.uint8).reshape(OWN_KEY_LEN, 256)
    pub_tables = np.frombuffer(b"".join(_build_pub_transform_tables()), dtype=np.uint8).reshape(len(PUB_KEY_MEND), 256)
    lut = np.empty((17, PUB_KEY_LEN_MAGNIFICATION, 256, 256), dtype=np.uint8)
    for phase in range(17):
        phase_start = phase * PUB_KEY_LEN_MAGNIFICATION
        for column in range(PUB_KEY_LEN_MAGNIFICATION):
            own_row = own_tables[(phase_start + column) % OWN_KEY_LEN]
            pub_row = pub_tables[phase_start + column]
            lut[phase, column] = own_row[np.newaxis, :] ^ pub_row[:, np.newaxis]
    return lut


def _decode_v3_chunk(
    data: bytearray,
    own_key: bytes,
    pub_key: bytes,
    start_pos: int,
    data_len: int | None = None,
) -> tuple[bool, str | None]:
    length = len(data) if data_len is None else data_len
    native_backend = get_native_backend()
    fallback_reason = None
    if native_backend.available:
        try:
            native_backend.decode_v3_inplace(data, length, own_key, pub_key, start_pos)
            return True, None
        except NativeBackendError as exc:
            fallback_reason = str(exc)
    own_tables = _build_own_transform_tables(own_key)
    pub_tables = _build_pub_transform_tables()
    phase_tables = _build_v3_block_phase_tables(own_key)
    numpy_lut = _build_v3_numpy_lut(own_key)
    own_len = len(own_tables)
    pub_len = len(pub_key)
    mend_len = len(pub_tables)
    total = length
    data_view = memoryview(data)
    offset = 0
    pos = start_pos
    while offset < total and (pos & 0x0F) != 0:
        pub_index = pos // PUB_KEY_LEN_MAGNIFICATION
        if pub_index >= pub_len:
            raise DecodeError("public key exhausted for current file size")
        pub_value = pub_key[pub_index]
        own_table = own_tables[pos % own_len]
        pub_value_table = pub_tables[pos % mend_len]
        data_view[offset] = own_table[data_view[offset]] ^ pub_value_table[pub_value]
        offset += 1
        pos += 1

    block_index = pos // PUB_KEY_LEN_MAGNIFICATION
    if numpy_lut is not None:
        full_blocks = (total - offset) // PUB_KEY_LEN_MAGNIFICATION
        if block_index + full_blocks > pub_len:
            raise DecodeError("public key exhausted for current file size")
        if full_blocks > 0:
            data_arr = np.frombuffer(data, dtype=np.uint8, count=full_blocks * PUB_KEY_LEN_MAGNIFICATION, offset=offset)
            block_view = data_arr.reshape(-1, PUB_KEY_LEN_MAGNIFICATION)
            pub_arr = np.frombuffer(pub_key, dtype=np.uint8)
            phase_base = block_index % 17
            for phase in range(17):
                row_start = (phase - phase_base) % 17
                if row_start >= full_blocks:
                    continue
                rows = block_view[row_start::17]
                pub_seq = pub_arr[block_index + row_start:block_index + full_blocks:17]
                phase_lut = numpy_lut[phase]
                for column in range(PUB_KEY_LEN_MAGNIFICATION):
                    rows[:, column] = phase_lut[column][pub_seq, rows[:, column]]
            offset += full_blocks * PUB_KEY_LEN_MAGNIFICATION
            pos += full_blocks * PUB_KEY_LEN_MAGNIFICATION
            block_index += full_blocks

    while offset + PUB_KEY_LEN_MAGNIFICATION <= total:
        if block_index >= pub_len:
            raise DecodeError("public key exhausted for current file size")
        phase = block_index % 17
        own_phase, pub_phase = phase_tables[phase]
        pub_value = pub_key[block_index]

        own0, own1, own2, own3, own4, own5, own6, own7, own8, own9, own10, own11, own12, own13, own14, own15 = own_phase
        pub0, pub1, pub2, pub3, pub4, pub5, pub6, pub7, pub8, pub9, pub10, pub11, pub12, pub13, pub14, pub15 = pub_phase

        data_view[offset + 0] = own0[data_view[offset + 0]] ^ pub0[pub_value]
        data_view[offset + 1] = own1[data_view[offset + 1]] ^ pub1[pub_value]
        data_view[offset + 2] = own2[data_view[offset + 2]] ^ pub2[pub_value]
        data_view[offset + 3] = own3[data_view[offset + 3]] ^ pub3[pub_value]
        data_view[offset + 4] = own4[data_view[offset + 4]] ^ pub4[pub_value]
        data_view[offset + 5] = own5[data_view[offset + 5]] ^ pub5[pub_value]
        data_view[offset + 6] = own6[data_view[offset + 6]] ^ pub6[pub_value]
        data_view[offset + 7] = own7[data_view[offset + 7]] ^ pub7[pub_value]
        data_view[offset + 8] = own8[data_view[offset + 8]] ^ pub8[pub_value]
        data_view[offset + 9] = own9[data_view[offset + 9]] ^ pub9[pub_value]
        data_view[offset + 10] = own10[data_view[offset + 10]] ^ pub10[pub_value]
        data_view[offset + 11] = own11[data_view[offset + 11]] ^ pub11[pub_value]
        data_view[offset + 12] = own12[data_view[offset + 12]] ^ pub12[pub_value]
        data_view[offset + 13] = own13[data_view[offset + 13]] ^ pub13[pub_value]
        data_view[offset + 14] = own14[data_view[offset + 14]] ^ pub14[pub_value]
        data_view[offset + 15] = own15[data_view[offset + 15]] ^ pub15[pub_value]

        offset += PUB_KEY_LEN_MAGNIFICATION
        pos += PUB_KEY_LEN_MAGNIFICATION
        block_index += 1

    while offset < total:
        pub_index = pos // PUB_KEY_LEN_MAGNIFICATION
        if pub_index >= pub_len:
            raise DecodeError("public key exhausted for current file size")
        pub_value = pub_key[pub_index]
        own_table = own_tables[pos % own_len]
        pub_value_table = pub_tables[pos % mend_len]
        data_view[offset] = own_table[data_view[offset]] ^ pub_value_table[pub_value]
        offset += 1
        pos += 1
    return False, fallback_reason


def parse_header_bytes(header: bytes) -> KugouHeader:
    if len(header) != HEADER_LEN:
        raise DecodeError("header length mismatch")
    magic = header[:16]
    if magic not in {KGM_MAGIC, VPR_MAGIC}:
        raise DecodeError("unsupported kugou file header")
    audio_offset, crypto_version, crypto_slot = struct.unpack_from("<III", header, 0x10)
    crypto_test_data = header[0x1C:0x2C]
    crypto_key = header[0x2C:0x3C]
    audio_hash = ""
    declared_ext = ""
    if crypto_version == 5:
        pos = 0x44
        if pos + 4 > len(header):
            raise DecodeError("kgg header missing audio hash length")
        audio_hash_len = struct.unpack_from("<I", header, pos)[0]
        pos += 4
        if audio_hash_len <= 0 or pos + audio_hash_len > len(header):
            raise DecodeError("invalid kgg audio hash length")
        audio_hash = header[pos:pos + audio_hash_len].decode("ascii", "ignore")
    return KugouHeader(
        magic_header=magic,
        audio_offset=audio_offset,
        crypto_version=crypto_version,
        crypto_slot=crypto_slot,
        crypto_test_data=crypto_test_data,
        crypto_key=crypto_key,
        audio_hash=audio_hash,
        declared_extension=declared_ext,
    )


def parse_header_file(path: pathlib.Path) -> KugouHeader:
    with path.open("rb") as fp:
        return parse_header_bytes(fp.read(HEADER_LEN))


def _decode_v3_stream(
    src: BinaryIO,
    dst: BinaryIO,
    own_key: bytes,
    pub_key: bytes,
    *,
    chunk_size: int = V3_STREAM_CHUNK_SIZE,
    compute_hash: bool = False,
) -> dict:
    pos = 0
    sha256 = hashlib.sha256() if compute_hash else None
    head = bytearray()
    decoded_bytes = 0
    chunk_count = 0
    native_chunk_count = 0
    native_fallback_chunks = 0
    native_fallback_reason = None
    buffer = bytearray(chunk_size)
    view = memoryview(buffer)
    while True:
        read_len = src.readinto(buffer)
        if not read_len:
            break
        chunk_count += 1
        used_native, fallback_reason = _decode_v3_chunk(buffer, own_key, pub_key, pos, read_len)
        if used_native:
            native_chunk_count += 1
        elif fallback_reason is not None:
            native_fallback_chunks += 1
            if native_fallback_reason is None:
                native_fallback_reason = fallback_reason
        chunk = view[:read_len]
        if len(head) < 8192:
            head.extend(chunk[: 8192 - len(head)])
        dst.write(chunk)
        if sha256 is not None:
            sha256.update(chunk)
        decoded_bytes += read_len
        pos += read_len
    result = {
        "decoded_bytes": decoded_bytes,
        "head_hex": bytes(head[:64]).hex(),
        "detected_container": detect_extension(bytes(head), "bin"),
        "chunk_count": chunk_count,
        "native_chunk_count": native_chunk_count,
        "native_fallback_chunks": native_fallback_chunks,
        "native_fallback_reason": native_fallback_reason,
        "bytes_per_chunk_avg": round(decoded_bytes / chunk_count, 2) if chunk_count else 0.0,
    }
    if sha256 is not None:
        result["sha256"] = sha256.hexdigest()
    return result


def _decode_v5_stream(
    src: BinaryIO,
    dst: BinaryIO,
    cipher: StreamCipher,
    *,
    chunk_size: int = V5_STREAM_CHUNK_SIZE,
    compute_hash: bool = False,
) -> dict:
    pos = 0
    sha256 = hashlib.sha256() if compute_hash else None
    head = bytearray()
    decoded_bytes = 0
    buffer = bytearray(chunk_size)
    while True:
        read_len = src.readinto(buffer)
        if not read_len:
            break
        chunk = buffer[:read_len]
        cipher.decrypt(chunk, pos)
        if len(head) < 8192:
            head.extend(chunk[: 8192 - len(head)])
        dst.write(chunk)
        if sha256 is not None:
            sha256.update(chunk)
        decoded_bytes += read_len
        pos += read_len
    result = {
        "decoded_bytes": decoded_bytes,
        "head_hex": bytes(head[:64]).hex(),
        "detected_container": detect_extension(bytes(head), "bin"),
    }
    if sha256 is not None:
        result["sha256"] = sha256.hexdigest()
    return result


def _derive_iv_seed(seed: int) -> int:
    left = (seed * 0x9EF4) & 0xFFFFFFFF
    right = (seed // 0xCE26) * 0x7FFFFF07
    value = (left - right) & 0xFFFFFFFF
    if (value & 0x80000000) == 0:
        return value
    return (value + 0x7FFFFF07) & 0xFFFFFFFF


def _derive_page_iv(page: int) -> bytes:
    iv = bytearray(16)
    page += 1
    for offset in range(0, 16, 4):
        page = _derive_iv_seed(page)
        struct.pack_into("<I", iv, offset, page)
    return hashlib.md5(iv).digest()


def _derive_page_key(page: int) -> bytes:
    master_key = bytearray(DEFAULT_MASTER_KEY)
    struct.pack_into("<I", master_key, 0x10, page)
    return hashlib.md5(master_key).digest()


def _validate_first_page_header(header: bytes) -> None:
    o10 = struct.unpack_from("<I", header, 0x10)[0]
    o14 = struct.unpack_from("<I", header, 0x14)[0]
    v6 = ((o10 & 0xFF) << 8) | ((o10 & 0xFF00) << 16)
    ok = o14 == 0x20204000 and (v6 - 0x200) <= 0xFE00 and ((v6 - 1) & v6) == 0
    if not ok:
        raise DecodeError("invalid encrypted sqlite page 1 header")


def _decrypt_database(buffer: bytearray) -> bytearray:
    if bytes(buffer[: len(SQLITE_HEADER)]) == SQLITE_HEADER:
        return buffer
    if not buffer or len(buffer) % PAGE_SIZE != 0:
        raise DecodeError(f"invalid encrypted database size: {len(buffer)}")
    first_page = bytearray(buffer[:PAGE_SIZE])
    _validate_first_page_header(first_page)
    expected_header = bytes(first_page[0x10:0x18])
    first_page[0x10:0x18] = first_page[0x08:0x10]
    decrypted_first = _AES_CBC.decrypt(bytes(first_page[0x10:]), _derive_page_key(1), _derive_page_iv(1))
    first_page[0x10:] = decrypted_first
    if bytes(first_page[0x10:0x18]) != expected_header:
        raise DecodeError("decrypt page 1 failed")
    first_page[:0x10] = SQLITE_HEADER
    buffer[:PAGE_SIZE] = first_page
    for page_number in range(2, len(buffer) // PAGE_SIZE + 1):
        start = (page_number - 1) * PAGE_SIZE
        end = start + PAGE_SIZE
        buffer[start:end] = _AES_CBC.decrypt(bytes(buffer[start:end]), _derive_page_key(page_number), _derive_page_iv(page_number))
    return buffer


def _extract_key_mapping(decrypted_db: bytes) -> dict[str, str]:
    with tempfile.TemporaryDirectory(prefix="kgg-db-") as tmpdir:
        tmp_path = pathlib.Path(tmpdir) / "KGMusicV3.db"
        tmp_path.write_bytes(decrypted_db)
        conn = sqlite3.connect(str(tmp_path))
        try:
            rows = conn.execute(
                "select EncryptionKeyId, EncryptionKey from ShareFileItems where EncryptionKey != '' and EncryptionKey is not null"
            ).fetchall()
        finally:
            conn.close()
    return {str(key_id): str(key) for key_id, key in rows}


@lru_cache(maxsize=4)
def _load_kgg_key_mapping_cached(cache_key: tuple[str, int, int]) -> dict[str, str]:
    db_path = pathlib.Path(cache_key[0])
    buffer = bytearray(db_path.read_bytes())
    decrypted = _decrypt_database(buffer)
    return _extract_key_mapping(bytes(decrypted))


def load_kgg_key_mapping(db_path: pathlib.Path) -> dict[str, str]:
    stat = db_path.stat()
    cache_key = (str(db_path.resolve()), stat.st_size, stat.st_mtime_ns)
    return _load_kgg_key_mapping_cached(cache_key)


def ensure_output_path(input_path: pathlib.Path, output_dir: pathlib.Path, extension: str) -> pathlib.Path:
    output_dir.mkdir(parents=True, exist_ok=True)
    return output_dir / f"{output_basename(input_path)}.{extension}"


def create_temp_output_path(input_path: pathlib.Path, output_dir: pathlib.Path) -> pathlib.Path:
    output_dir.mkdir(parents=True, exist_ok=True)
    stamp = f"{time.time_ns()}_{os.getpid()}"
    return output_dir / f".{output_basename(input_path)}.{stamp}.tmp"


def create_failed_raw_output_path(input_path: pathlib.Path, failed_raw_dir: pathlib.Path, attempt: str) -> pathlib.Path:
    failed_raw_dir.mkdir(parents=True, exist_ok=True)
    stamp = f"{time.time_ns()}_{os.getpid()}_{attempt}"
    return failed_raw_dir / f"{output_basename(input_path)}.{stamp}.bin"


def cleanup_stale_bin(input_path: pathlib.Path, output_dir: pathlib.Path, final_ext: str) -> None:
    if final_ext == "bin":
        return
    stale_bin = output_dir / f"{output_basename(input_path)}.bin"
    if stale_bin.exists() and stale_bin.is_file():
        try:
            stale_bin.unlink()
        except OSError:
            pass


def decode_file(
    input_path: pathlib.Path,
    output_dir: pathlib.Path,
    *,
    key_path: pathlib.Path = DEFAULT_KEY_PATH,
    kgg_db_path: pathlib.Path = DEFAULT_KGG_DB_PATH,
    failed_raw_dir: pathlib.Path | None = None,
    publish_unrecognized_to_output: bool = True,
    attempt: str = "initial",
) -> dict:
    started_perf = time.perf_counter()
    native_backend = get_native_backend()
    timing = {
        "header_parse_sec": 0.0,
        "key_material_sec": 0.0,
        "stream_decode_sec": 0.0,
        "publish_sec": 0.0,
        "total_sec": 0.0,
    }
    input_path = input_path.expanduser().resolve()
    output_dir = output_dir.expanduser().resolve()
    key_path = key_path.expanduser().resolve()
    kgg_db_path = kgg_db_path.expanduser().resolve() if str(kgg_db_path) else kgg_db_path

    header_started = time.perf_counter()
    header = parse_header_file(input_path)
    timing["header_parse_sec"] = round(time.perf_counter() - header_started, 6)
    temp_output = create_temp_output_path(input_path, output_dir)

    try:
        if header.crypto_version == 5:
            key_started = time.perf_counter()
            if not kgg_db_path or not kgg_db_path.exists():
                raise DecodeError(f"missing KGMusicV3.db: {kgg_db_path}")
            mapping = load_kgg_key_mapping(kgg_db_path)
            ekey = mapping.get(header.audio_hash)
            if not ekey:
                raise DecodeError(f"ekey missing for audio_hash={header.audio_hash}")
            cipher = _new_qmc_cipher_from_ekey(ekey)
            timing["key_material_sec"] = round(time.perf_counter() - key_started, 6)
            decode_started = time.perf_counter()
            with input_path.open("rb", buffering=V5_STREAM_CHUNK_SIZE) as src, temp_output.open("wb", buffering=V5_STREAM_CHUNK_SIZE) as dst:
                src.seek(header.audio_offset)
                summary = _decode_v5_stream(src, dst, cipher, chunk_size=V5_STREAM_CHUNK_SIZE, compute_hash=False)
            timing["stream_decode_sec"] = round(time.perf_counter() - decode_started, 6)
            summary["audio_hash"] = header.audio_hash
            summary["declared_extension"] = header.declared_extension
            summary["crypto_mode"] = "v5"
            summary["kgg_db_path"] = str(kgg_db_path)
        else:
            key_started = time.perf_counter()
            pub_key = load_public_key(key_path)
            own_key = bytearray(OWN_KEY_LEN)
            own_key[:16] = header.crypto_test_data
            timing["key_material_sec"] = round(time.perf_counter() - key_started, 6)
            decode_started = time.perf_counter()
            with input_path.open("rb", buffering=V3_STREAM_CHUNK_SIZE) as src, temp_output.open("wb", buffering=V3_STREAM_CHUNK_SIZE) as dst:
                src.seek(header.audio_offset)
                summary = _decode_v3_stream(src, dst, bytes(own_key), pub_key, chunk_size=V3_STREAM_CHUNK_SIZE, compute_hash=False)
            timing["stream_decode_sec"] = round(time.perf_counter() - decode_started, 6)
            summary["own_key_hex"] = bytes(own_key).hex()
            summary["crypto_mode"] = "v3"
            summary["key_path"] = str(key_path)

        publish_started = time.perf_counter()
        fast_container = str(summary.get("detected_container", "bin")).strip().lower() or "bin"
        probed_container = None
        if fast_container == "bin":
            probed_container = probe_audio_container(temp_output)
        detected_container = probed_container or fast_container
        final_ext = detected_container
        if final_ext == "bin" and not publish_unrecognized_to_output:
            failed_raw_path = None
            if failed_raw_dir is not None:
                failed_raw_path = create_failed_raw_output_path(input_path, failed_raw_dir, attempt)
                if failed_raw_path.exists():
                    failed_raw_path.unlink()
                temp_output.replace(failed_raw_path)
            timing["publish_sec"] = round(time.perf_counter() - publish_started, 6)
            timing["total_sec"] = round(time.perf_counter() - started_perf, 6)
            summary.update(
                {
                    "input_path": str(input_path),
                    "output_path": None,
                    "failed_raw_path": str(failed_raw_path) if failed_raw_path is not None else None,
                    "magic_header_hex": header.magic_header.hex(),
                    "audio_offset": header.audio_offset,
                    "crypto_version": header.crypto_version,
                    "crypto_slot": header.crypto_slot,
                    "crypto_test_data_hex": header.crypto_test_data.hex(),
                    "crypto_key_hex": header.crypto_key.hex(),
                    "detected_container": detected_container,
                    "final_extension": None,
                    "recognition_stage": "fast_probe_failed",
                    "backend": f"native-c:{native_backend.dll_path.name}" if native_backend.available and native_backend.dll_path else "python",
                    "timing": timing,
                }
            )
            raise UnrecognizedAudioContainerError(summary)
        final_output = ensure_output_path(input_path, output_dir, final_ext)
        if final_output.exists():
            final_output.unlink()
        temp_output.replace(final_output)
        cleanup_stale_bin(input_path, output_dir, final_ext)
        timing["publish_sec"] = round(time.perf_counter() - publish_started, 6)
        timing["total_sec"] = round(time.perf_counter() - started_perf, 6)
        summary.update(
            {
                "input_path": str(input_path),
                "output_path": str(final_output),
                "magic_header_hex": header.magic_header.hex(),
                "audio_offset": header.audio_offset,
                "crypto_version": header.crypto_version,
                "crypto_slot": header.crypto_slot,
                "crypto_test_data_hex": header.crypto_test_data.hex(),
                "crypto_key_hex": header.crypto_key.hex(),
                "detected_container": detected_container,
                "final_extension": final_ext,
                "recognition_stage": "fast" if detected_container == fast_container else "ffmpeg_probe",
                "backend": f"native-c:{native_backend.dll_path.name}" if native_backend.available and native_backend.dll_path else "python",
                "timing": timing,
            }
        )
        return summary
    finally:
        if temp_output.exists():
            try:
                temp_output.unlink()
            except OSError:
                pass


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Offline decoder for KuGou kgma/kgm/kgg/vpr files.")
    parser.add_argument("--input", required=True, help="Path to Kugou encrypted file")
    parser.add_argument("--output-dir", default=str(DEFAULT_OUTPUT_DIR), help="Output directory")
    parser.add_argument("--key-file", default=str(DEFAULT_KEY_PATH), help="Path to kugou_key.xz for v3 files")
    parser.add_argument("--kgg-db", default=str(DEFAULT_KGG_DB_PATH), help="Path to KGMusicV3.db for kgg files")
    parser.add_argument("--json", action="store_true", help="Print JSON summary")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    summary = decode_file(
        pathlib.Path(args.input),
        pathlib.Path(args.output_dir),
        key_path=pathlib.Path(args.key_file),
        kgg_db_path=pathlib.Path(args.kgg_db),
    )
    if args.json:
        print(json.dumps(summary, ensure_ascii=False, indent=2))
    else:
        for key in (
            "input_path",
            "output_path",
            "detected_container",
            "final_extension",
            "decoded_bytes",
            "sha256",
            "crypto_mode",
            "audio_hash",
            "declared_extension",
            "head_hex",
        ):
            value = summary.get(key)
            if value:
                print(f"{key}={value}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
