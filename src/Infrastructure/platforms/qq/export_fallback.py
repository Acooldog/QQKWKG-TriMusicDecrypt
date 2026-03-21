from __future__ import annotations

import logging
import pathlib
import re
import shutil
import time
from dataclasses import dataclass


logger = logging.getLogger("qkkdecrypt.infrastructure.qq_export_fallback")


@dataclass(slots=True)
class QQExportFallbackResult:
    status: str
    exported_path: str | None = None
    staged_path: str | None = None
    message: str = ""


class QQExportFallbackService:
    """Reuse QQMusic's exported FLAC result when direct Frida decrypt is unresolved."""

    QUALITY_SUFFIX_RE = re.compile(r"_([A-Za-z0-9]{1,8})$")

    def __init__(self, export_dir: str | pathlib.Path | None = None):
        self.export_dir = pathlib.Path(export_dir) if export_dir else pathlib.Path.home() / "Music"

    def stage_exported_flac(
        self,
        source_file_path: pathlib.Path,
        stage_path: pathlib.Path,
        *,
        wait_seconds: float = 0.0,
    ) -> QQExportFallbackResult:
        candidate = self._wait_for_candidate(source_file_path, wait_seconds=wait_seconds)
        if candidate is None:
            return QQExportFallbackResult(
                status="not_found",
                message=f"no QQ exported FLAC was found under {self.export_dir}",
            )
        stage_path.parent.mkdir(parents=True, exist_ok=True)
        if stage_path.exists():
            stage_path.unlink()
        shutil.copy2(candidate, stage_path)
        logger.info("QQ export fallback staged: %s -> %s", candidate, stage_path)
        return QQExportFallbackResult(
            status="staged",
            exported_path=str(candidate),
            staged_path=str(stage_path),
            message="reused QQ exported FLAC",
        )

    def _wait_for_candidate(self, source_file_path: pathlib.Path, *, wait_seconds: float) -> pathlib.Path | None:
        deadline = time.time() + max(wait_seconds, 0.0)
        while True:
            candidate = self._find_candidate(source_file_path)
            if candidate is not None:
                return candidate
            if time.time() >= deadline:
                return None
            time.sleep(0.5)

    def _find_candidate(self, source_file_path: pathlib.Path) -> pathlib.Path | None:
        if not self.export_dir.exists():
            return None
        stems = self._candidate_stems(source_file_path.stem)
        candidates: list[pathlib.Path] = []
        for stem in stems:
            exact = self.export_dir / f"{stem}.flac"
            if exact.exists() and exact.is_file() and exact.stat().st_size > 1024 and self._is_stable_file(exact):
                candidates.append(exact)
            for matched in self.export_dir.glob(f"{stem}*.flac"):
                if matched.is_file() and matched.stat().st_size > 1024 and self._is_stable_file(matched):
                    candidates.append(matched)
        if not candidates:
            return None
        deduped: dict[str, pathlib.Path] = {}
        for candidate in candidates:
            deduped[str(candidate).lower()] = candidate
        ranked = sorted(
            deduped.values(),
            key=lambda item: (
                0 if item.stem == stems[0] else 1,
                -item.stat().st_mtime,
            ),
        )
        return ranked[0] if ranked else None

    def _candidate_stems(self, stem: str) -> list[str]:
        raw = (stem or "").strip()
        normalized = self.QUALITY_SUFFIX_RE.sub("", raw)
        values: list[str] = []
        for candidate in (normalized, raw):
            if candidate and candidate not in values:
                values.append(candidate)
        return values

    @staticmethod
    def _is_stable_file(path: pathlib.Path) -> bool:
        try:
            first = path.stat()
            time.sleep(0.2)
            second = path.stat()
            return first.st_size == second.st_size and first.st_mtime == second.st_mtime
        except OSError:
            return False
