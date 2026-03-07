from __future__ import annotations

import pathlib
import shutil
import time
from dataclasses import dataclass, field

from src.Infrastructure.process_utils import find_process_by_substring
from src.Infrastructure.transcoder import detect_audio_container


SUPPORTED_SUFFIXES = {".mflac", ".mgg", ".mmp4"}
DEFAULT_RULES = {"mflac": "flac", "mgg": "ogg", "mmp4": "m4a"}
WHITELIST = {"flac", "ogg", "m4a", "mp3", "wav"}


@dataclass(slots=True)
class QQPlatformAdapter:
    platform_id: str = "qq"
    display_name: str = "QQ音乐"
    _gateway: FridaDecryptGateway | None = field(default=None, init=False, repr=False)

    def _load_runtime(self):
        from src.Infrastructure.platforms.qq.runtime.frida_decrypt_gateway import FridaDecryptGateway
        from src.Infrastructure.platforms.qq.runtime.qqmusic_decrypt import pick_safe_tmp_dir

        return FridaDecryptGateway, pick_safe_tmp_dir

    def requires_running_process(self) -> bool:
        return True

    def validate_runtime(self, settings: dict) -> tuple[bool, str | None]:
        process_match = str(settings.get("process_match", "qqmusic") or "qqmusic")
        info = find_process_by_substring(process_match)
        return (info is not None, None if info is not None else "QQ音乐未运行")

    def collect_files(self, input_path: pathlib.Path, recursive: bool) -> list[pathlib.Path]:
        if input_path.is_file():
            return [input_path] if input_path.suffix.lower() in SUPPORTED_SUFFIXES else []
        pattern = "**/*" if recursive else "*"
        return sorted(candidate for candidate in input_path.glob(pattern) if candidate.is_file() and candidate.suffix.lower() in SUPPORTED_SUFFIXES)

    def output_basename(self, input_path: pathlib.Path) -> str:
        return input_path.stem

    def _normalized_rules(self, settings: dict) -> dict[str, str]:
        merged = dict(DEFAULT_RULES)
        raw = settings.get("format_rules") or {}
        if isinstance(raw, dict):
            for key, value in raw.items():
                source = str(key or "").strip().lower().lstrip(".")
                target = str(value or "").strip().lower().lstrip(".")
                if source in merged and target in WHITELIST:
                    merged[source] = target
        return merged

    def predicted_extension(self, input_path: pathlib.Path, settings: dict) -> str | None:
        source = input_path.suffix.lower().lstrip(".")
        return self._normalized_rules(settings).get(source)

    def desired_target_format(self, input_path: pathlib.Path, settings: dict) -> str:
        return self.predicted_extension(input_path, settings) or "auto"

    def decrypt_one(self, input_path: pathlib.Path, work_dir: pathlib.Path, settings: dict, *, log_dir: pathlib.Path) -> dict:
        started = time.perf_counter()
        FridaDecryptGateway, pick_safe_tmp_dir = self._load_runtime()
        if self._gateway is None:
            self._gateway = FridaDecryptGateway()
        default_ext = DEFAULT_RULES.get(input_path.suffix.lower().lstrip("."), "flac")
        safe_tmp_root = pathlib.Path(pick_safe_tmp_dir(str(work_dir))).resolve()
        safe_tmp_root.mkdir(parents=True, exist_ok=True)
        safe_output = safe_tmp_root / f"qq_{time.time_ns()}.{default_ext}"
        final_work_path = work_dir / f"{input_path.stem}.{default_ext}"
        ok = self._gateway.decrypt_file(str(input_path), str(safe_output))
        if not ok or not safe_output.exists() or safe_output.stat().st_size <= 1024:
            raise RuntimeError("qq_decrypt_failed")
        final_work_path.parent.mkdir(parents=True, exist_ok=True)
        if final_work_path.exists():
            final_work_path.unlink()
        shutil.move(str(safe_output), str(final_work_path))
        detected_container, recognition_stage = detect_audio_container(final_work_path)
        elapsed = round(time.perf_counter() - started, 6)
        return {
            "output_path": str(final_work_path),
            "detected_container": detected_container,
            "final_extension": detected_container,
            "recognition_stage": recognition_stage,
            "backend": "frida:qqmusic",
            "decoded_bytes": final_work_path.stat().st_size,
            "timing": {
                "header_parse_sec": 0.0,
                "key_material_sec": 0.0,
                "stream_decode_sec": elapsed,
                "publish_sec": 0.0,
                "total_sec": elapsed,
            },
        }
