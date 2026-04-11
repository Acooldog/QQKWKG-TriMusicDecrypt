from __future__ import annotations

import hashlib
import lzma
import os
import pathlib
import tempfile
import time
import urllib.error
import urllib.request
from dataclasses import dataclass

from src.Infrastructure.runtime_paths import RuntimePaths


USER_AGENT = "QKKDecrypt/refresh-kugou-key"
DEFAULT_TIMEOUT_SEC = 10
DEFAULT_BRANCH_CANDIDATES = ("main-ui", "main")
REFRESHED_KEY_FILENAME = "kugou_key_refreshed.xz"
LEGACY_KEY_FILENAME = "kugou_key.xz"
LOCAL_SCAN_FILENAMES = (
    REFRESHED_KEY_FILENAME,
    LEGACY_KEY_FILENAME,
    "kg_key.xz",
    "kugoukey.xz",
)
LOCAL_SCAN_DIR_PATTERNS = (
    "KuGou8",
    "KuGou",
    "KG",
)
LOCAL_SCAN_EXTRA_EXTENSIONS = ("*.xz",)
LOCAL_CONTAINER_HINTS = ("AppStore", "OfflinePackage")
MAX_LOCAL_SCAN_DEPTH = 3
MAX_LOCAL_CONTAINER_FILES = 12
MAX_LOCAL_CONTAINER_SIZE = 16 * 1024 * 1024
LOCAL_SOURCE_TIME_BUDGET_SEC = 5.0
XZ_MAGIC = bytes.fromhex("FD377A585A00")


@dataclass(frozen=True, slots=True)
class KugouKeyRefreshResult:
    output_path: pathlib.Path
    source_url: str
    file_size: int
    sha256: str
    validation_size: int


def _candidate_urls(branch_candidates: tuple[str, ...]) -> list[str]:
    urls: list[str] = []
    seen: set[str] = set()
    for branch in branch_candidates:
        for template in (
            "https://gitee.com/daoges_x/QQKWKG-TriMusicDecrypt/raw/{branch}/assets/kugou_key.xz",
            "https://raw.githubusercontent.com/Acooldog/QQKWKG-TriMusicDecrypt/{branch}/assets/kugou_key.xz",
        ):
            url = template.format(branch=branch)
            if url not in seen:
                seen.add(url)
                urls.append(url)
    return urls


def default_refreshed_kugou_key_path(paths: RuntimePaths) -> pathlib.Path:
    return (paths.root_dir / "assets" / REFRESHED_KEY_FILENAME).resolve()


def _iter_local_kugou_key_candidates(paths: RuntimePaths, destination: pathlib.Path) -> list[pathlib.Path]:
    candidates: list[pathlib.Path] = []
    seen: set[str] = set()

    def add(path: pathlib.Path | None) -> None:
        if path is None:
            return
        try:
            resolved = path.expanduser().resolve()
        except OSError:
            return
        key = str(resolved).lower()
        if key in seen or not resolved.is_file() or resolved == destination:
            return
        seen.add(key)
        candidates.append(resolved)

    for base_dir in _iter_local_base_dirs():
        for filename in LOCAL_SCAN_FILENAMES:
            add(base_dir / filename)
        _collect_local_candidates(base_dir, add)
    return candidates


def _iter_local_container_candidates() -> list[pathlib.Path]:
    candidates: list[pathlib.Path] = []
    seen: set[str] = set()

    def add(path: pathlib.Path | None) -> None:
        if path is None:
            return
        try:
            resolved = path.expanduser().resolve()
        except OSError:
            return
        if not resolved.is_file():
            return
        try:
            size = resolved.stat().st_size
        except OSError:
            return
        if size <= 0 or size > MAX_LOCAL_CONTAINER_SIZE:
            return
        key = str(resolved).lower()
        if key in seen:
            return
        seen.add(key)
        candidates.append(resolved)

    for base_dir in _iter_local_base_dirs():
        if not base_dir.exists() or not base_dir.is_dir():
            continue
        direct_candidates = [
            base_dir / 'AppStore' / 'webgl' / 'v3.4' / 'snapshot_blob.bin',
            base_dir / 'AppStore' / 'webgl' / 'v3.4' / 'v8_context_snapshot.bin',
            base_dir / 'AppStore' / 'webgl' / 'v3.4' / 'icudtl.dat',
            base_dir / 'AppStore' / 'webgl' / 'v3.4' / 'external.bin',
            base_dir / 'AppStore' / 'webgl' / 'v3.4' / 'desktop_manager' / '32' / 'icudtl_infra.dat',
            base_dir / 'AppStore' / 'webgl' / 'v3.4' / 'desktop_manager' / '64' / 'icudtl_infra.dat',
        ]
        for candidate in direct_candidates:
            add(candidate)
    return candidates[:MAX_LOCAL_CONTAINER_FILES]


def _iter_local_base_dirs() -> list[pathlib.Path]:
    roots: list[pathlib.Path] = []
    seen: set[str] = set()
    for raw_root in (os.environ.get("APPDATA"), os.environ.get("LOCALAPPDATA"), os.environ.get("PROGRAMDATA")):
        if not raw_root:
            continue
        root = pathlib.Path(raw_root)
        for pattern in LOCAL_SCAN_DIR_PATTERNS:
            direct = root / pattern
            wildcard_parent = root
            if direct.exists():
                key = str(direct.resolve()).lower()
                if key not in seen:
                    seen.add(key)
                    roots.append(direct.resolve())
            try:
                for matched in wildcard_parent.glob(f"{pattern}*"):
                    if matched.exists() and matched.is_dir():
                        key = str(matched.resolve()).lower()
                        if key not in seen:
                            seen.add(key)
                            roots.append(matched.resolve())
            except OSError:
                pass
    return roots


def _collect_local_candidates(base_dir: pathlib.Path, add) -> None:
    if not base_dir.exists() or not base_dir.is_dir():
        return
    for ext_pattern in LOCAL_SCAN_EXTRA_EXTENSIONS:
        try:
            for path in base_dir.glob(ext_pattern):
                add(path)
        except OSError:
            pass
    frontier = [(base_dir, 0)]
    while frontier:
        current, depth = frontier.pop(0)
        if depth >= MAX_LOCAL_SCAN_DEPTH:
            continue
        try:
            children = list(current.iterdir())
        except OSError:
            continue
        for child in children:
            if child.is_dir():
                frontier.append((child, depth + 1))
                for filename in LOCAL_SCAN_FILENAMES:
                    add(child / filename)
                for ext_pattern in LOCAL_SCAN_EXTRA_EXTENSIONS:
                    try:
                        for path in child.glob(ext_pattern):
                            add(path)
                    except OSError:
                        pass


def _extract_valid_xz_stream(payload: bytes) -> tuple[bytes, int]:
    if not payload:
        raise RuntimeError("Empty payload")
    decompressor = lzma.LZMADecompressor()
    try:
        plain = decompressor.decompress(payload)
    except lzma.LZMAError as exc:
        raise RuntimeError("Payload is not a valid xz stream") from exc
    if not decompressor.eof:
        raise RuntimeError("Payload does not contain a complete xz stream")
    validation_head = plain[:4096]
    if not validation_head:
        raise RuntimeError("Payload does not decompress into usable content")
    stream_size = len(payload) - len(decompressor.unused_data)
    if stream_size <= 0:
        raise RuntimeError("Resolved xz stream size is invalid")
    return payload[:stream_size], len(validation_head)


def _validate_xz_file(path: pathlib.Path) -> tuple[bytes, int, str, int]:
    payload = path.read_bytes()
    stream_payload, validation_size = _extract_valid_xz_stream(payload)
    return stream_payload, len(stream_payload), hashlib.sha256(stream_payload).hexdigest(), validation_size


def _try_extract_embedded_xz(path: pathlib.Path) -> tuple[bytes, int, str, int, int] | None:
    try:
        payload = path.read_bytes()
    except OSError:
        return None
    start = 0
    attempts = 0
    while attempts < 4:
        offset = payload.find(XZ_MAGIC, start)
        if offset < 0:
            return None
        attempts += 1
        try:
            stream_payload, validation_size = _extract_valid_xz_stream(payload[offset:])
            return stream_payload, len(stream_payload), hashlib.sha256(stream_payload).hexdigest(), validation_size, offset
        except RuntimeError:
            start = offset + 1
    return None


def refresh_kugou_key(
    paths: RuntimePaths,
    *,
    destination: pathlib.Path | None = None,
    branch_candidates: tuple[str, ...] = DEFAULT_BRANCH_CANDIDATES,
    timeout_sec: int = DEFAULT_TIMEOUT_SEC,
) -> KugouKeyRefreshResult:
    paths.ensure_runtime_dirs()
    output_path = (destination or default_refreshed_kugou_key_path(paths)).expanduser().resolve()
    output_path.parent.mkdir(parents=True, exist_ok=True)
    temp_dir = pathlib.Path(tempfile.mkdtemp(prefix="kugou_key_refresh_", dir=str(paths.log_dir)))
    errors: list[str] = []

    try:
        local_started = time.perf_counter()
        for local_path in _iter_local_kugou_key_candidates(paths, output_path):
            try:
                stream_payload, file_size, sha256, validation_size = _validate_xz_file(local_path)
                output_path.write_bytes(stream_payload)
                return KugouKeyRefreshResult(
                    output_path=output_path,
                    source_url=str(local_path),
                    file_size=file_size,
                    sha256=sha256,
                    validation_size=validation_size,
                )
            except (OSError, RuntimeError, lzma.LZMAError, EOFError) as exc:
                errors.append(f"{local_path} -> {exc}")

        for container_path in _iter_local_container_candidates():
            if time.perf_counter() - local_started >= LOCAL_SOURCE_TIME_BUDGET_SEC:
                errors.append("Local embedded scan time budget exceeded")
                break
            try:
                extracted = _try_extract_embedded_xz(container_path)
            except OSError as exc:
                errors.append(f"{container_path} -> {exc}")
                continue
            if extracted is None:
                continue
            stream_payload, file_size, sha256, validation_size, offset = extracted
            output_path.write_bytes(stream_payload)
            return KugouKeyRefreshResult(
                output_path=output_path,
                source_url=f"embedded:{container_path}#offset={offset}",
                file_size=file_size,
                sha256=sha256,
                validation_size=validation_size,
            )

        for url in _candidate_urls(branch_candidates):
            temp_path = temp_dir / f"{int(time.time() * 1000)}.xz"
            try:
                request = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
                with urllib.request.urlopen(request, timeout=timeout_sec) as response:
                    payload = response.read()
                if not payload:
                    raise RuntimeError("Downloaded payload is empty")
                temp_path.write_bytes(payload)
                stream_payload, validation_size = _extract_valid_xz_stream(payload)
                output_path.write_bytes(stream_payload)
                return KugouKeyRefreshResult(
                    output_path=output_path,
                    source_url=url,
                    file_size=len(stream_payload),
                    sha256=hashlib.sha256(stream_payload).hexdigest(),
                    validation_size=validation_size,
                )
            except (urllib.error.URLError, urllib.error.HTTPError, TimeoutError, OSError, RuntimeError, ValueError, lzma.LZMAError, EOFError) as exc:
                errors.append(f"{url} -> {exc}")
                try:
                    temp_path.unlink(missing_ok=True)
                except Exception:
                    pass
        joined = "; ".join(errors) if errors else "No usable local or remote Kugou key source matched"
        raise RuntimeError(f"Failed to refresh kugou_key.xz: {joined}")
    finally:
        try:
            temp_dir.rmdir()
        except OSError:
            pass
