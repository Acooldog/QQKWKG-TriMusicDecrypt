from __future__ import annotations

import logging
import pathlib
import shutil
import time
from dataclasses import dataclass, field

from src.Infrastructure.process_utils import find_process_by_substring
from src.Infrastructure.transcoder import detect_audio_container


SUPPORTED_SUFFIXES = {".mflac", ".mgg", ".mmp4"}
DEFAULT_RULES = {"mflac": "flac", "mgg": "m4a", "mmp4": "m4a"}
RAW_CONTAINER_RULES = {"mflac": "flac", "mgg": "ogg", "mmp4": "m4a"}
WHITELIST = {"flac", "m4a", "mp3", "wav"}
logger = logging.getLogger("qkkdecrypt.infrastructure.platforms.qq")


@dataclass(slots=True)
class QQPlatformAdapter:
    platform_id: str = "qq"
    display_name: str = "QQ音乐"
    _gateway: FridaDecryptGateway | None = field(default=None, init=False, repr=False)
    _internal_direct: QQInternalDirectDecryptService | None = field(default=None, init=False, repr=False)
    _fallback: QQExportFallbackService | None = field(default=None, init=False, repr=False)

    def _load_runtime(self):
        from src.Infrastructure.platforms.qq.runtime.frida_decrypt_gateway import FridaDecryptGateway
        from src.Infrastructure.platforms.qq.runtime.qqmusic_decrypt import pick_safe_tmp_dir

        return FridaDecryptGateway, pick_safe_tmp_dir

    def _ensure_fallback(self) -> QQExportFallbackService:
        if self._fallback is None:
            from src.Infrastructure.platforms.qq.export_fallback import QQExportFallbackService

            self._fallback = QQExportFallbackService()
        return self._fallback

    def _ensure_internal_direct(self) -> QQInternalDirectDecryptService:
        if self._internal_direct is None:
            from src.Infrastructure.platforms.qq.internal_direct import QQInternalDirectDecryptService

            self._internal_direct = QQInternalDirectDecryptService()
        return self._internal_direct

    @staticmethod
    def _format_internal_direct_status(status: str, message: str) -> str:
        mapping = {
            "qq_not_running": "qq_internal_direct_not_running: QQ音乐未运行，无法触发内部直解",
            "timeout": "qq_internal_direct_timeout: QQ 内部直解未在限定时间内触发",
            "attach_failed": "qq_internal_direct_attach_failed: QQ 内部直解附加失败",
            "hook_error": "qq_internal_direct_hook_error: QQ 内部直解 Hook 失败",
            "invoke_failed": "qq_internal_direct_invoke_failed: QQ 内部直解返回失败",
            "output_missing": "qq_internal_direct_output_missing: QQ 内部直解已触发，但未生成输出文件",
        }
        if status in mapping:
            return mapping[status]
        if message:
            return f"qq_internal_direct_failed: {message}"
        return "qq_internal_direct_failed: QQ 内部直解失败"

    def _try_stage_fallback_chain(
        self,
        input_path: pathlib.Path,
        stage_path: pathlib.Path,
        *,
        reason: str,
    ) -> tuple[str, dict[str, str]]:
        internal_direct = self._ensure_internal_direct()
        fallback = self._ensure_fallback()

        direct_result = internal_direct.stage_internal_flac(str(input_path), str(stage_path))
        if direct_result.status == "staged":
            logger.warning(
                "QQ internal direct decrypt engaged: %s | reason=%s source_cache=%s staged=%s",
                input_path,
                reason,
                direct_result.source_cache_path or "",
                direct_result.staged_path or "",
            )
            return "qq-internal-direct-flac", {
                "fallback_mode": "qq_internal_direct",
                "fallback_source_input": str(input_path),
                "source_cache_path": str(direct_result.source_cache_path or ""),
                "original_output_path": str(direct_result.original_output_path or ""),
                "cover_path": str(direct_result.cover_path or ""),
            }

        export_result = fallback.stage_exported_flac(input_path, stage_path)
        if export_result.status == "staged":
            logger.warning(
                "QQ export fallback engaged: %s | reason=%s export=%s",
                input_path,
                reason,
                export_result.exported_path or "",
            )
            return "qq-export-flac", {
                "fallback_mode": "qq_export_flac",
                "fallback_source_input": str(input_path),
                "fallback_export_path": str(export_result.exported_path or ""),
            }

        raise RuntimeError(
            f"{self._format_internal_direct_status(direct_result.status, direct_result.message)}; "
            f"qq_export_flac_not_found: 未找到 QQ 导出的 FLAC"
        )

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
        prefer_export_fallback = bool(settings.get("qq_prefer_export_fallback", False))
        source_suffix = input_path.suffix.lower().lstrip(".")
        default_ext = RAW_CONTAINER_RULES.get(source_suffix, "flac")
        safe_tmp_root = pathlib.Path(pick_safe_tmp_dir(str(work_dir))).resolve()
        safe_tmp_root.mkdir(parents=True, exist_ok=True)
        safe_output = safe_tmp_root / f"qq_{time.time_ns()}.{default_ext}"
        final_work_path = work_dir / f"{input_path.stem}.{default_ext}"
        backend = "frida:qqmusic"
        fallback_detail: dict[str, str] = {}
        decrypt_exception: Exception | None = None

        if prefer_export_fallback:
            backend, fallback_detail = self._try_stage_fallback_chain(
                input_path,
                final_work_path,
                reason="retry_from_source",
            )
        else:
            try:
                ok = self._gateway.decrypt_file(str(input_path), str(safe_output))
            except Exception as exc:  # pragma: no cover - runtime-specific failure
                decrypt_exception = exc
                ok = False
            if ok and safe_output.exists() and safe_output.stat().st_size > 1024:
                final_work_path.parent.mkdir(parents=True, exist_ok=True)
                if final_work_path.exists():
                    final_work_path.unlink()
                shutil.move(str(safe_output), str(final_work_path))
            else:
                safe_output.unlink(missing_ok=True)
                backend, fallback_detail = self._try_stage_fallback_chain(
                    input_path,
                    final_work_path,
                    reason="decrypt_gateway_exception" if decrypt_exception is not None else "decrypt_gateway_failed",
                )

        detected_container, recognition_stage = detect_audio_container(final_work_path)
        if detected_container == "bin":
            backend, fallback_detail = self._try_stage_fallback_chain(
                input_path,
                final_work_path,
                reason="unrecognized_audio_container",
            )
            detected_container, recognition_stage = detect_audio_container(final_work_path)

        elapsed = round(time.perf_counter() - started, 6)
        detail = {
            "output_path": str(final_work_path),
            "detected_container": detected_container,
            "final_extension": detected_container,
            "recognition_stage": recognition_stage,
            "backend": backend,
            "decoded_bytes": final_work_path.stat().st_size,
            "timing": {
                "header_parse_sec": 0.0,
                "key_material_sec": 0.0,
                "stream_decode_sec": elapsed,
                "publish_sec": 0.0,
                "total_sec": elapsed,
            },
        }
        if fallback_detail:
            detail.update(fallback_detail)
        return detail
