from __future__ import annotations

import hashlib
import lzma
import pathlib
import tempfile
import time
import urllib.error
import urllib.request
from dataclasses import dataclass

from src.Infrastructure.runtime_paths import RuntimePaths


USER_AGENT = "QKKDecrypt/refresh-kugou-key"
DEFAULT_TIMEOUT_SEC = 10
DEFAULT_BRANCH_CANDIDATES = ("main", "main-ui")
REFRESHED_KEY_FILENAME = "kugou_key_refreshed.xz"


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
        for url in _candidate_urls(branch_candidates):
            temp_path = temp_dir / f"{int(time.time() * 1000)}.xz"
            try:
                request = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
                with urllib.request.urlopen(request, timeout=timeout_sec) as response:
                    payload = response.read()
                if not payload:
                    raise RuntimeError("下载结果为空")
                temp_path.write_bytes(payload)
                with lzma.open(temp_path, "rb") as handle:
                    validation_head = handle.read(4096)
                if not validation_head:
                    raise RuntimeError("下载结果无法解压出有效公钥")
                temp_path.replace(output_path)
                return KugouKeyRefreshResult(
                    output_path=output_path,
                    source_url=url,
                    file_size=len(payload),
                    sha256=hashlib.sha256(payload).hexdigest(),
                    validation_size=len(validation_head),
                )
            except (urllib.error.URLError, urllib.error.HTTPError, TimeoutError, OSError, RuntimeError, ValueError) as exc:
                errors.append(f"{url} -> {exc}")
                try:
                    temp_path.unlink(missing_ok=True)
                except Exception:
                    pass
        joined = "；".join(errors) if errors else "未命中任何可用下载源"
        raise RuntimeError(f"抓取 kugou_key.xz 失败：{joined}")
    finally:
        try:
            temp_dir.rmdir()
        except OSError:
            pass
