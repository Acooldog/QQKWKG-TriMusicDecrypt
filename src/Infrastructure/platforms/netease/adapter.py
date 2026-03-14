from __future__ import annotations

import pathlib
import time
from dataclasses import dataclass, field

from ncmdump import NeteaseCloudMusicFile

from src.Infrastructure.transcoder import detect_audio_container


SUPPORTED_SUFFIXES = {".ncm"}
WHITELIST = {"flac", "m4a", "mp3", "wav"}


@dataclass(slots=True)
class NeteasePlatformAdapter:
    platform_id: str = "netease"
    display_name: str = "网易云音乐"
    _raw_format_cache: dict[str, str] = field(default_factory=dict, init=False, repr=False)

    def requires_running_process(self) -> bool:
        return False

    def validate_runtime(self, settings: dict) -> tuple[bool, str | None]:
        return True, None

    def collect_files(self, input_path: pathlib.Path, recursive: bool) -> list[pathlib.Path]:
        if input_path.is_file():
            return [input_path] if input_path.suffix.lower() in SUPPORTED_SUFFIXES else []
        pattern = "**/*" if recursive else "*"
        return sorted(
            candidate
            for candidate in input_path.glob(pattern)
            if candidate.is_file() and candidate.suffix.lower() in SUPPORTED_SUFFIXES
        )

    def output_basename(self, input_path: pathlib.Path) -> str:
        return input_path.stem

    def _raw_format(self, input_path: pathlib.Path) -> str:
        cache_key = str(input_path.resolve()).lower()
        cached = self._raw_format_cache.get(cache_key)
        if cached:
            return cached
        try:
            ncm = NeteaseCloudMusicFile(input_path).decrypt()
            raw_format = str(getattr(ncm.music_metadata, "format", "mp3") or "mp3").strip().lower()
        except Exception:
            raw_format = "mp3"
        if raw_format == "ogg":
            raw_format = "m4a"
        if raw_format not in WHITELIST:
            raw_format = "mp3"
        self._raw_format_cache[cache_key] = raw_format
        return raw_format

    def predicted_extension(self, input_path: pathlib.Path, settings: dict) -> str | None:
        target = str(settings.get("target_format_ncm", "auto") or "auto").strip().lower()
        return self._raw_format(input_path) if target == "auto" else target

    def desired_target_format(self, input_path: pathlib.Path, settings: dict) -> str:
        target = str(settings.get("target_format_ncm", "auto") or "auto").strip().lower()
        return self._raw_format(input_path) if target == "auto" else target

    def decrypt_one(self, input_path: pathlib.Path, work_dir: pathlib.Path, settings: dict, *, log_dir: pathlib.Path) -> dict:
        started = time.perf_counter()
        ncm = NeteaseCloudMusicFile(input_path).decrypt()
        raw_format = self._raw_format(input_path)
        output_hint = work_dir / input_path.stem
        dumped = ncm.dump_music(output_hint)
        final_work_path = pathlib.Path(dumped)
        detected_container, recognition_stage = detect_audio_container(final_work_path)
        elapsed = round(time.perf_counter() - started, 6)
        return {
            "output_path": str(final_work_path),
            "detected_container": detected_container,
            "final_extension": detected_container,
            "recognition_stage": recognition_stage,
            "backend": "python:ncmdump-py",
            "decoded_bytes": final_work_path.stat().st_size if final_work_path.exists() else 0,
            "timing": {
                "header_parse_sec": 0.0,
                "key_material_sec": 0.0,
                "stream_decode_sec": elapsed,
                "publish_sec": 0.0,
                "total_sec": elapsed,
            },
        }
