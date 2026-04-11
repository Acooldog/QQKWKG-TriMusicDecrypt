from __future__ import annotations

import hashlib
import lzma
import os
import pathlib
import shutil
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
MAX_LOCAL_SCAN_DEPTH = 3


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

    env_roots = [
        os.environ.get("APPDATA"),
        os.environ.get("LOCALAPPDATA"),
        os.environ.get("PROGRAMDATA"),
    ]
    for raw_root in env_roots:
        if not raw_root:
            continue
        root = pathlib.Path(raw_root)
        for pattern in LOCAL_SCAN_DIR_PATTERNS:
            direct = root / pattern
            wildcard = root.glob(f"{pattern}*")
            if direct.exists():
                _collect_local_candidates(direct, add)
            for matched in wildcard:
                _collect_local_candidates(matched, add)
    return candidates


def _collect_local_candidates(base_dir: pathlib.Path, add) -> None:
    if not base_dir.exists() or not base_dir.is_dir():
        return
    for filename in LOCAL_SCAN_FILENAMES:
        add(base_dir / filename)
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


def _validate_xz_file(path: pathlib.Path) -> tuple[int, str, int]:
    payload = path.read_bytes()
    if not payload:
        raise RuntimeError("Local key file is empty")
    with lzma.open(path, "rb") as handle:
        validation_head = handle.read(4096)
    if not validation_head:
        raise RuntimeError("Local key file is not a valid xz payload")
    return len(payload), hashlib.sha256(payload).hexdigest(), len(validation_head)


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
        for local_path in _iter_local_kugou_key_candidates(paths, output_path):
            try:
                file_size, sha256, validation_size = _validate_xz_file(local_path)
                if local_path != output_path:
                    shutil.copy2(local_path, output_path)
                return KugouKeyRefreshResult(
                    output_path=output_path,
                    source_url=str(local_path),
                    file_size=file_size,
                    sha256=sha256,
                    validation_size=validation_size,
                )
            except (OSError, RuntimeError, lzma.LZMAError, EOFError) as exc:
                errors.append(f"{local_path} -> {exc}")

        for url in _candidate_urls(branch_candidates):
            temp_path = temp_dir / f"{int(time.time() * 1000)}.xz"
            try:
                request = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
                with urllib.request.urlopen(request, timeout=timeout_sec) as response:
                    payload = response.read()
                if not payload:
                    raise RuntimeError("Downloaded payload is empty")
                temp_path.write_bytes(payload)
                with lzma.open(temp_path, "rb") as handle:
                    validation_head = handle.read(4096)
                if not validation_head:
                    raise RuntimeError("Downloaded payload is not a valid xz payload")
                temp_path.replace(output_path)
                return KugouKeyRefreshResult(
                    output_path=output_path,
                    source_url=url,
                    file_size=len(payload),
                    sha256=hashlib.sha256(payload).hexdigest(),
                    validation_size=len(validation_head),
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
