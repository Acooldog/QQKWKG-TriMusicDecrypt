from __future__ import annotations

import pathlib
from dataclasses import dataclass

from src.Infrastructure.config_repository import auto_find_kgg_db_path, auto_find_kugou_key
from src.Infrastructure.file_catalog import iter_supported_files, file_requires_kgg_db
from src.Infrastructure.kugou_decoder import decode_file, output_basename
from src.Infrastructure.runtime_paths import RuntimePaths


@dataclass(slots=True)
class KugouPlatformAdapter:
    platform_id: str = "kugou"
    display_name: str = "酷狗音乐"

    def requires_running_process(self) -> bool:
        return False

    def validate_runtime(self, settings: dict) -> tuple[bool, str | None]:
        paths = RuntimePaths.discover()
        configured = str(settings.get("key_file", "") or "").strip()
        key_file = pathlib.Path(configured) if configured else auto_find_kugou_key(paths)
        if key_file is None or not key_file.exists():
            return False, "未找到可用的 kugou_key.xz"
        return True, None

    def collect_files(self, input_path: pathlib.Path, recursive: bool) -> list[pathlib.Path]:
        return iter_supported_files(input_path, recursive)

    def output_basename(self, input_path: pathlib.Path) -> str:
        return output_basename(input_path)

    def predicted_extension(self, input_path: pathlib.Path, settings: dict) -> str | None:
        if file_requires_kgg_db(input_path):
            value = str(settings.get("target_format_kgg", "auto") or "auto").strip().lower().lstrip(".")
        else:
            value = str(settings.get("target_format_kgma", "auto") or "auto").strip().lower().lstrip(".")
        if value == "ogg":
            value = "m4a"
        return None if value == "auto" else value

    def desired_target_format(self, input_path: pathlib.Path, settings: dict) -> str:
        value = self.predicted_extension(input_path, settings) or "auto"
        return "m4a" if value == "ogg" else value

    def decrypt_one(self, input_path: pathlib.Path, work_dir: pathlib.Path, settings: dict, *, log_dir: pathlib.Path) -> dict:
        paths = RuntimePaths.discover()
        configured_key = str(settings.get("key_file", "") or "").strip()
        key_file = pathlib.Path(configured_key) if configured_key else (auto_find_kugou_key(paths) or (paths.assets_dir / "kugou_key.xz"))
        db_value = str(settings.get("kgg_db_path", "") or "").strip()
        kgg_db_path = pathlib.Path(db_value) if db_value else (auto_find_kgg_db_path() or pathlib.Path())
        return decode_file(
            input_path,
            work_dir,
            key_path=key_file,
            kgg_db_path=kgg_db_path,
            failed_raw_dir=log_dir / "failed_raw",
            publish_unrecognized_to_output=False,
            attempt="initial",
        )
