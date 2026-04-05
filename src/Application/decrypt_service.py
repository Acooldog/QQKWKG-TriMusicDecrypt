from __future__ import annotations

import logging
import pathlib
import shutil
import time
from dataclasses import dataclass
from typing import Any

from src.Application.models import BatchRunConfig, BatchSummary, FileResult, PlatformAdapter, TIMING_STAGE_KEYS
from src.Infrastructure.cover_art_service import CoverArtService
from src.Infrastructure.output_manifest_repository import OutputManifestRepository
from src.Infrastructure.runtime_logging import setup_logger, timing_text, write_batch_reports
from src.Infrastructure.runtime_paths import RuntimePaths
from src.Infrastructure.transcoder import (
    normalize_target_format,
    probe_media_summary,
    summary_to_log,
    transcode_file,
)


AUDIO_OUTPUT_EXTS = {".flac", ".wav", ".mp3", ".m4a"}


@dataclass(slots=True)
class _PreparedArtifact:
    index: int
    total_count: int
    input_path: pathlib.Path
    basename: str
    desired_target: str
    file_started: float
    working_path: pathlib.Path
    detected_container: str
    detail: dict[str, Any]
    decrypt_detail_timing: dict[str, float]
    file_timing: dict[str, float]


def _new_timing() -> dict[str, float]:
    return {key: 0.0 for key in TIMING_STAGE_KEYS}


def _copy_timing(source: dict[str, float]) -> dict[str, float]:
    return {key: round(float(source.get(key, 0.0)), 6) for key in TIMING_STAGE_KEYS}


def _accumulate(total: dict[str, float], single: dict[str, float]) -> None:
    for key in TIMING_STAGE_KEYS:
        total[key] = round(float(total.get(key, 0.0)) + float(single.get(key, 0.0)), 6)


def _artifact_timing(detail: dict[str, Any]) -> dict[str, float]:
    timing = detail.get("timing") or detail.get("decrypt_detail_timing") or {}
    if timing:
        return {k: float(v) for k, v in timing.items() if isinstance(v, (int, float))}
    total = float(detail.get("elapsed_sec", 0.0))
    return {
        "header_parse_sec": 0.0,
        "key_material_sec": 0.0,
        "stream_decode_sec": total,
        "publish_sec": 0.0,
        "total_sec": total,
    }


def _log_media_summary(logger: logging.Logger, label: str, path: pathlib.Path) -> dict[str, Any]:
    summary = probe_media_summary(path)
    logger.info("%s: %s | %s", label, path.name, summary_to_log(summary))
    return summary


def _validate_summary(logger: logging.Logger, label: str, path: pathlib.Path, summary: dict[str, Any]) -> str | None:
    container = str(summary.get("container") or "bin")
    if container == "bin":
        logger.warning("%s produced an unrecognized audio container: %s", label, path)
        return "unrecognized_audio_container"
    return None


def _cover_art_enabled(settings: dict[str, Any]) -> bool:
    value = settings.get("embed_cover_art", True)
    if isinstance(value, str):
        return value.strip().lower() in {"1", "true", "yes", "y", "on"}
    return bool(value)


def _album_metadata_enabled(settings: dict[str, Any]) -> bool:
    value = settings.get("supplement_album_metadata", False)
    if isinstance(value, str):
        return value.strip().lower() in {"1", "true", "yes", "y", "on"}
    return bool(value)


def _transcode_enabled(settings: dict[str, Any]) -> bool:
    value = settings.get("transcode_enabled", True)
    if isinstance(value, str):
        return value.strip().lower() in {"1", "true", "yes", "y", "on"}
    return bool(value)


def _maybe_attach_cover(
    logger: logging.Logger,
    config: BatchRunConfig,
    cover_service: CoverArtService,
    source_path: pathlib.Path,
    output_path: pathlib.Path,
    *,
    index: int,
    total_count: int,
) -> None:
    if output_path.suffix.lower() not in {".m4a", ".mp3", ".flac"}:
        return
    if not _cover_art_enabled(config.settings):
        logger.info("cover_skipped: %s reason=disabled_by_user", output_path.name)
        _emit_event(
            config,
            "cover_finished",
            {
                "platform_id": config.platform_id,
                "index": index,
                "total": total_count,
                "input_path": str(source_path),
                "output_path": str(output_path),
                "status": "disabled",
                "message": "已按设置跳过封面补写",
            },
        )
        return
    logger.info("covering: %s source=%s mode=local_first cache_then_network may_be_slow=true", output_path.name, source_path.name)
    _emit_event(
        config,
        "cover_started",
        {
            "platform_id": config.platform_id,
            "index": index,
            "total": total_count,
            "input_path": str(source_path),
            "output_path": str(output_path),
            "message": "正在补封面（本地优先，可能会变慢）",
        },
    )
    summary_before = probe_media_summary(output_path)
    result = cover_service.supplement_cover(str(output_path), str(source_path), summary_before)
    if result.status == "embedded":
        logger.info(
            "Cover attached: %s source=%s image=%s | %s",
            output_path.name,
            result.source or "",
            result.image_path or "",
            summary_to_log(probe_media_summary(output_path)),
        )
    elif result.status not in {"already_present", "unsupported"}:
        logger.info(
            "Cover not attached: %s status=%s message=%s",
            output_path.name,
            result.status,
            result.message,
        )
    _emit_event(
        config,
        "cover_finished",
        {
            "platform_id": config.platform_id,
            "index": index,
            "total": total_count,
            "input_path": str(source_path),
            "output_path": str(output_path),
            "status": result.status,
            "source": result.source or "",
            "message": result.message,
        },
    )


def _maybe_supplement_album_metadata(
    logger: logging.Logger,
    config: BatchRunConfig,
    cover_service: CoverArtService,
    source_path: pathlib.Path,
    output_path: pathlib.Path,
    *,
    index: int,
    total_count: int,
) -> None:
    if output_path.suffix.lower() not in {".m4a", ".wav"}:
        return
    if not _album_metadata_enabled(config.settings):
        logger.info("album_metadata_skipped: %s reason=disabled_by_user", output_path.name)
        _emit_event(
            config,
            "metadata_finished",
            {
                "platform_id": config.platform_id,
                "index": index,
                "total": total_count,
                "input_path": str(source_path),
                "output_path": str(output_path),
                "status": "disabled",
                "message": "已按设置跳过专辑信息补全",
            },
        )
        return
    logger.info(
        "metadata_supplementing: %s source=%s mode=local_first cache_then_network may_be_slow=true",
        output_path.name,
        source_path.name,
    )
    _emit_event(
        config,
        "metadata_started",
        {
            "platform_id": config.platform_id,
            "index": index,
            "total": total_count,
            "input_path": str(source_path),
            "output_path": str(output_path),
            "message": "正在补专辑信息（本地优先，可能会变慢）",
        },
    )
    summary_before = probe_media_summary(output_path)
    result = cover_service.supplement_album_metadata(str(output_path), str(source_path), summary_before)
    if result.status == "embedded":
        logger.info(
            "Album metadata supplemented: %s source=%s fields=%s | %s",
            output_path.name,
            result.source or "",
            ",".join(result.updated_fields),
            summary_to_log(probe_media_summary(output_path)),
        )
    elif result.status not in {"already_present", "unsupported"}:
        logger.info(
            "Album metadata not supplemented: %s status=%s message=%s",
            output_path.name,
            result.status,
            result.message,
        )
    _emit_event(
        config,
        "metadata_finished",
        {
            "platform_id": config.platform_id,
            "index": index,
            "total": total_count,
            "input_path": str(source_path),
            "output_path": str(output_path),
            "status": result.status,
            "source": result.source or "",
            "updated_fields": list(result.updated_fields),
            "message": result.message,
        },
    )


def _throughput_mib(detail: dict[str, Any], decrypt_timing: dict[str, float]) -> float:
    decoded_bytes = int(detail.get("decoded_bytes", 0) or 0)
    stream_decode = float(decrypt_timing.get("stream_decode_sec", 0.0))
    if decoded_bytes <= 0 or stream_decode <= 0.0:
        return 0.0
    return decoded_bytes / (1024.0 * 1024.0) / stream_decode


def _log_decrypt_detail(logger: logging.Logger, platform_id: str, index: int, total_count: int, file_name: str, detail: dict[str, Any], decrypt_timing: dict[str, float]) -> None:
    logger.info(
        "[timing] decrypt_detail [%d/%d] %s platform=%s backend=%s header_parse=%.3fs key_material=%.3fs stream_decode=%.3fs publish=%.3fs total=%.3fs decoded_bytes=%d throughput=%.2fMiB/s",
        index,
        total_count,
        file_name,
        platform_id,
        detail.get("backend", "unknown"),
        float(decrypt_timing.get("header_parse_sec", 0.0)),
        float(decrypt_timing.get("key_material_sec", 0.0)),
        float(decrypt_timing.get("stream_decode_sec", 0.0)),
        float(decrypt_timing.get("publish_sec", 0.0)),
        float(decrypt_timing.get("total_sec", 0.0)),
        int(detail.get("decoded_bytes", 0) or 0),
        _throughput_mib(detail, decrypt_timing),
    )


def _default_collision_choice(base_name: str, extension: str, existing_platform: str | None, config: BatchRunConfig) -> str:
    if not config.interactive or config.collision_resolver is None:
        return "suffix"
    return config.collision_resolver(base_name, extension, existing_platform)


def _publish_base_name(platform_id: str, input_path: pathlib.Path, base_name: str) -> str:
    source_ext = input_path.suffix.lower().lstrip(".")
    if platform_id == "kugou" and source_ext in {"kgg", "kgma", "kgm", "vpr"}:
        return f"{base_name}.{source_ext}"
    return base_name


def _resolve_publish_target(
    *,
    base_name: str,
    input_path: pathlib.Path,
    extension: str,
    platform_id: str,
    output_dir: pathlib.Path,
    manifest_repo: OutputManifestRepository,
    config: BatchRunConfig,
) -> tuple[pathlib.Path, str, str | None]:
    publish_name = _publish_base_name(platform_id, input_path, base_name)
    target = output_dir / f"{publish_name}.{extension}"
    if not target.exists():
        return target, "direct", None
    existing_platform = manifest_repo.get_platform(target)
    if existing_platform in {None, platform_id}:
        return target, "existing_same_platform", existing_platform
    choice = _default_collision_choice(base_name, extension, existing_platform, config)
    if choice == "overwrite":
        return target, "overwrite", existing_platform
    if choice == "subdir":
        sub_target = output_dir / platform_id / f"{publish_name}.{extension}"
        return sub_target, "subdir", existing_platform
    suffix_target = output_dir / f"{publish_name}.{platform_id}.{extension}"
    return suffix_target, "suffix", existing_platform


def _publish_file(source_path: pathlib.Path, target_path: pathlib.Path) -> pathlib.Path:
    target_path.parent.mkdir(parents=True, exist_ok=True)
    temp_target = target_path.with_name(f".{target_path.stem}.publish.{time.time_ns()}{target_path.suffix}")
    if target_path.exists():
        target_path.unlink()
    try:
        source_path.replace(target_path)
    except OSError:
        if temp_target.exists():
            temp_target.unlink()
        shutil.copy2(str(source_path), str(temp_target))
        temp_target.replace(target_path)
        source_path.unlink(missing_ok=True)
    finally:
        temp_target.unlink(missing_ok=True)
    return target_path


def _maybe_transcode(logger: logging.Logger, input_path: pathlib.Path, target_format: str, current_path: pathlib.Path, detected_container: str, file_timing: dict[str, float]) -> tuple[pathlib.Path, str, dict[str, Any] | None]:
    target_format = normalize_target_format(target_format)
    if target_format == "auto" or detected_container == "bin" or target_format == detected_container:
        return current_path, detected_container, None
    started = time.perf_counter()
    target_path = current_path.with_suffix(f".{target_format}")
    logger.info("transcoding: %s -> %s", current_path.name, target_path.suffix)
    meta = transcode_file(current_path, target_path, target_format)
    logger.info("transcoding_ffmpeg: %s", meta.get("ffmpeg_path", ""))
    if current_path.exists():
        current_path.unlink()
    file_timing["transcode_sec"] = round(float(file_timing.get("transcode_sec", 0.0)) + (time.perf_counter() - started), 6)
    return target_path, target_format, meta


def _normalize_final_target(desired_target: str, detected_container: str, *, transcode_enabled: bool) -> str:
    if not transcode_enabled:
        return str(detected_container or "bin").lower()
    normalized = normalize_target_format(desired_target)
    if normalized == "ogg":
        return "m4a"
    if normalized == "auto" and str(detected_container).lower() == "ogg":
        return "m4a"
    return normalized


def _emit_event(config: BatchRunConfig, event_name: str, payload: dict[str, Any]) -> None:
    if config.event_sink is None:
        return
    try:
        config.event_sink(event_name, payload)
    except Exception:
        # UI / observer failures must not break the decrypt pipeline.
        pass


def _is_stop_requested(config: BatchRunConfig) -> bool:
    if config.stop_requested is None:
        return False
    try:
        return bool(config.stop_requested())
    except Exception:
        return False


def _cleanup_working_path(path: pathlib.Path | None) -> None:
    if path is None:
        return
    try:
        if path.exists():
            path.unlink()
    except Exception:
        pass


def _auto_transcode_after_decode(settings: dict[str, Any]) -> bool:
    value = settings.get("auto_transcode_after_decode", False)
    if isinstance(value, str):
        return value.strip().lower() in {"1", "true", "yes", "y", "on"}
    return bool(value)


def _artifact_needs_transcode(desired_target: str, detected_container: str) -> bool:
    target_format = normalize_target_format(desired_target)
    return not (target_format == "auto" or detected_container == "bin" or target_format == detected_container)


def _resolve_batch_transcode_choice(
    logger: logging.Logger,
    config: BatchRunConfig,
    prepared_artifacts: list[_PreparedArtifact],
    *,
    failed_count: int,
    stopped_early: bool,
) -> tuple[bool, list[_PreparedArtifact]]:
    pending = [item for item in prepared_artifacts if _artifact_needs_transcode(item.desired_target, item.detected_container)]
    if failed_count > 0 or stopped_early:
        logger.info(
            "batch_transcode_skipped: pending=%d failed=%d stopped=%s",
            len(pending),
            failed_count,
            stopped_early,
        )
        return False, pending

    payload = {
        "platform_id": config.platform_id,
        "ready_count": len(prepared_artifacts),
        "pending_count": len(pending),
        "has_pending_transcode": bool(pending),
        "transcode_enabled_setting": _transcode_enabled(config.settings),
        "pending_files": [item.input_path.name for item in pending],
        "pending_targets": [
            {
                "input_path": str(item.input_path),
                "detected_container": item.detected_container,
                "desired_target": normalize_target_format(item.desired_target),
            }
            for item in pending
        ],
    }
    _emit_event(config, "batch_decode_finished", dict(payload))
    if pending and _auto_transcode_after_decode(config.settings):
        logger.info("batch_transcode_auto_enabled: pending=%d", len(pending))
        _emit_event(
            config,
            "batch_transcode_decided",
            {
                **payload,
                "should_transcode": True,
                "decision_mode": "auto",
                "remember_choice": True,
            },
        )
        return True, pending

    resolver = config.transcode_confirmation_resolver
    if resolver is None:
        logger.info("batch_transcode_prompt_unavailable: pending=%d", len(pending))
        _emit_event(
            config,
            "batch_transcode_decided",
            {
                **payload,
                "should_transcode": False,
                "decision_mode": "unavailable",
                "remember_choice": False,
            },
        )
        return False, pending

    _emit_event(config, "batch_transcode_confirmation_needed", dict(payload))
    response = resolver(dict(payload))
    should_transcode = bool(response[0]) if response and pending else False
    remember_choice = bool(response[1]) if response else False
    logger.info(
        "batch_transcode_prompt_result: pending=%d should_transcode=%s remember=%s",
        len(pending),
        should_transcode,
        remember_choice,
    )
    _emit_event(
        config,
        "batch_transcode_decided",
        {
            **payload,
            "should_transcode": should_transcode,
            "decision_mode": "prompt",
            "remember_choice": remember_choice,
        },
    )
    return should_transcode, pending


def _finalize_prepared_artifact(
    logger: logging.Logger,
    config: BatchRunConfig,
    cover_service: CoverArtService,
    manifest_repo: OutputManifestRepository,
    prepared: _PreparedArtifact,
    *,
    should_transcode: bool,
    file_started: float,
) -> tuple[str, FileResult]:
    working_path = prepared.working_path
    final_extension = prepared.detected_container
    transcode_meta: dict[str, Any] | None = None
    try:
        if should_transcode and _artifact_needs_transcode(prepared.desired_target, prepared.detected_container):
            working_path, final_extension, transcode_meta = _maybe_transcode(
                logger,
                prepared.input_path,
                prepared.desired_target,
                working_path,
                prepared.detected_container,
                prepared.file_timing,
            )
            _emit_event(
                config,
                "batch_transcode_progress",
                {
                    "platform_id": config.platform_id,
                    "index": prepared.index,
                    "total": prepared.total_count,
                    "input_path": str(prepared.input_path),
                    "output_path": str(working_path),
                    "target_format": final_extension,
                    "message": f"正在统一转码：{prepared.input_path.name}",
                },
            )

        if _is_stop_requested(config):
            _cleanup_working_path(working_path)
            raise RuntimeError("stopped_by_user")

        _maybe_attach_cover(
            logger,
            config,
            cover_service,
            prepared.input_path,
            working_path,
            index=prepared.index,
            total_count=prepared.total_count,
        )
        if _is_stop_requested(config):
            _cleanup_working_path(working_path)
            raise RuntimeError("stopped_by_user")

        _maybe_supplement_album_metadata(
            logger,
            config,
            cover_service,
            prepared.input_path,
            working_path,
            index=prepared.index,
            total_count=prepared.total_count,
        )
        if _is_stop_requested(config):
            _cleanup_working_path(working_path)
            raise RuntimeError("stopped_by_user")

        final_summary = _log_media_summary(logger, "Final media summary", working_path)
        summary_error = _validate_summary(logger, "Final publish", working_path, final_summary)
        if summary_error:
            raise RuntimeError(summary_error)

        publish_started = time.perf_counter()
        publish_hint = _resolve_publish_target(
            base_name=prepared.basename,
            input_path=prepared.input_path,
            extension=final_extension,
            platform_id=config.platform_id,
            output_dir=config.output_dir,
            manifest_repo=manifest_repo,
            config=config,
        )
        final_target, publish_mode, existing_platform = publish_hint
        if final_target.exists() and publish_mode == "existing_same_platform":
            _cleanup_working_path(working_path)
            prepared.file_timing["publish_sec"] = round(time.perf_counter() - publish_started, 6)
            prepared.file_timing["total_sec"] = round(time.perf_counter() - file_started, 6)
            logger.info("skip_duplicate_after_decode: %s -> %s", prepared.input_path.name, final_target)
            logger.info(
                "[timing] file_done [%d/%d] %s reason=already_decrypted %s",
                prepared.index,
                prepared.total_count,
                prepared.input_path.name,
                timing_text(prepared.file_timing),
            )
            result = FileResult(
                ok=True,
                skipped=True,
                platform_id=config.platform_id,
                input_path=str(prepared.input_path),
                output_path=str(final_target),
                reason="already_decrypted",
                timing=_copy_timing(prepared.file_timing),
                decrypt_detail_timing=prepared.decrypt_detail_timing,
                payload=dict(prepared.detail),
            )
            _emit_event(
                config,
                "file_finished",
                {
                    "platform_id": config.platform_id,
                    "index": prepared.index,
                    "total": prepared.total_count,
                    "result": "already_decrypted",
                    "output_path": str(final_target),
                    "timing": dict(result.timing),
                    "decrypt_detail_timing": dict(result.decrypt_detail_timing),
                },
            )
            return "already_decrypted", result

        published = _publish_file(working_path, final_target)
        prepared.file_timing["publish_sec"] = round(time.perf_counter() - publish_started, 6)
        prepared.file_timing["total_sec"] = round(time.perf_counter() - file_started, 6)
        manifest_repo.set_platform(published, config.platform_id)
        payload = dict(prepared.detail)
        payload.update(
            {
                "detected_container": prepared.detected_container,
                "final_extension": final_extension,
                "publish_mode": publish_mode,
                "existing_platform": existing_platform,
                "transcode_mode": "batch_post_decode" if should_transcode else "raw_publish",
            }
        )
        if transcode_meta is not None:
            payload["transcode"] = transcode_meta
        logger.info("success: %s -> %s", prepared.input_path.name, published)
        logger.info(
            "[timing] file_done [%d/%d] %s reason=success %s",
            prepared.index,
            prepared.total_count,
            prepared.input_path.name,
            timing_text(prepared.file_timing),
        )
        result = FileResult(
            ok=True,
            skipped=False,
            platform_id=config.platform_id,
            input_path=str(prepared.input_path),
            output_path=str(published),
            timing=_copy_timing(prepared.file_timing),
            decrypt_detail_timing=prepared.decrypt_detail_timing,
            payload=payload,
        )
        _emit_event(
            config,
            "file_finished",
            {
                "platform_id": config.platform_id,
                "index": prepared.index,
                "total": prepared.total_count,
                "result": "success",
                "output_path": str(published),
                "timing": dict(result.timing),
                "decrypt_detail_timing": dict(result.decrypt_detail_timing),
                "payload": dict(payload),
            },
        )
        return "success", result
    except Exception as exc:
        _cleanup_working_path(working_path)
        prepared.file_timing["total_sec"] = round(time.perf_counter() - file_started, 6)
        logger.warning("failed: %s reason=%s", prepared.input_path.name, exc)
        logger.info(
            "[timing] file_done [%d/%d] %s reason=%s %s",
            prepared.index,
            prepared.total_count,
            prepared.input_path.name,
            exc,
            timing_text(prepared.file_timing),
        )
        result = FileResult(
            ok=False,
            skipped=False,
            platform_id=config.platform_id,
            input_path=str(prepared.input_path),
            reason=str(exc),
            timing=_copy_timing(prepared.file_timing),
        )
        _emit_event(
            config,
            "file_finished",
            {
                "platform_id": config.platform_id,
                "index": prepared.index,
                "total": prepared.total_count,
                "result": "failed",
                "input_path": str(prepared.input_path),
                "reason": str(exc),
                "timing": dict(result.timing),
            },
        )
        return "failed", result


def run_batch(config: BatchRunConfig, adapter: PlatformAdapter) -> int:
    paths = RuntimePaths.discover()
    paths.ensure_runtime_dirs()
    logger, log_path, log_dir = setup_logger(paths)
    cover_service = CoverArtService()
    manifest_repo = OutputManifestRepository(paths.output_manifest)
    batch_started = time.perf_counter()
    work_dir = log_dir / "work" / f"{config.platform_id}_{int(batch_started)}"
    work_dir.mkdir(parents=True, exist_ok=True)

    logger.info("runtime_dir: %s", paths.root_dir)
    logger.info("plugins_config: %s", paths.plugins_config)
    logger.info("log_file: %s", log_path)
    logger.info("platform: %s", config.platform_id)
    logger.info("input_path: %s", config.input_path)
    logger.info("output_dir: %s", config.output_dir)
    logger.info("recursive: %s", config.recursive)

    files = adapter.collect_files(config.input_path, config.recursive)
    logger.info("candidate_files: %d", len(files))
    _emit_event(
        config,
        "batch_started",
        {
            "platform_id": config.platform_id,
            "candidate_count": len(files),
            "input_path": str(config.input_path),
            "output_dir": str(config.output_dir),
        },
    )

    timing_batch_total = _new_timing()
    results: list[FileResult] = []
    prepared_artifacts: list[_PreparedArtifact] = []
    success_count = 0
    skipped_count = 0
    failed_count = 0
    stopped_early = False
    transcode_enabled = _transcode_enabled(config.settings)

    for index, file_path in enumerate(files, start=1):
        if _is_stop_requested(config):
            stopped_early = True
            logger.info("stop_requested_before_file: index=%d total=%d", index, len(files))
            break
        file_started = time.perf_counter()
        file_timing = _new_timing()
        scan_started = time.perf_counter()
        logger.info("[%d/%d] decrypting: %s", index, len(files), file_path)
        _emit_event(
            config,
            "file_started",
            {
                "platform_id": config.platform_id,
                "index": index,
                "total": len(files),
                "input_path": str(file_path),
            },
        )
        file_timing["scan_sec"] = round(time.perf_counter() - scan_started, 6)

        basename = adapter.output_basename(file_path)
        predicted_ext = adapter.predicted_extension(file_path, config.settings)
        desired_target = adapter.desired_target_format(file_path, config.settings)

        dedupe_started = time.perf_counter()
        if predicted_ext and not transcode_enabled:
            hinted_target, hinted_mode, _ = _resolve_publish_target(
                base_name=basename,
                input_path=file_path,
                extension=predicted_ext,
                platform_id=config.platform_id,
                output_dir=config.output_dir,
                manifest_repo=manifest_repo,
                config=config,
            )
            if hinted_target.exists() and hinted_mode == "existing_same_platform":
                skipped_count += 1
                file_timing["dedupe_sec"] = round(time.perf_counter() - dedupe_started, 6)
                file_timing["total_sec"] = round(time.perf_counter() - file_started, 6)
                _accumulate(timing_batch_total, file_timing)
                logger.info("skip_duplicate: %s -> %s", file_path.name, hinted_target)
                logger.info("[timing] file_done [%d/%d] %s reason=already_decrypted %s", index, len(files), file_path.name, timing_text(file_timing))
                result = FileResult(ok=True, skipped=True, platform_id=config.platform_id, input_path=str(file_path), output_path=str(hinted_target), reason="already_decrypted", timing=_copy_timing(file_timing))
                results.append(result)
                _emit_event(
                    config,
                    "file_finished",
                    {
                        "platform_id": config.platform_id,
                        "index": index,
                        "total": len(files),
                        "result": "already_decrypted",
                        "output_path": str(hinted_target),
                        "timing": dict(result.timing),
                    },
                )
                continue
        file_timing["dedupe_sec"] = round(time.perf_counter() - dedupe_started, 6)

        working_path: pathlib.Path | None = None
        try:
            decrypt_settings = dict(config.settings)
            if config.platform_id == "qq":
                decrypt_settings["qq_variant_notifier"] = lambda payload, *, _index=index, _total=len(files), _path=file_path: _emit_event(
                    config,
                    "variant_started",
                    {
                        "platform_id": config.platform_id,
                        "index": _index,
                        "total": _total,
                        "input_path": str(payload.get("input_path") or _path),
                        "variant_mode": str(payload.get("variant_mode") or "path_sensitive_mflac"),
                        "variant_label": str(payload.get("variant_label") or "路径敏感型 mflac 变体"),
                        "message": str(payload.get("message") or f"正在执行变体转换：{str(payload.get('variant_label') or '路径敏感型 mflac 变体')}"),
                    },
                )
            decrypt_started = time.perf_counter()
            detail = adapter.decrypt_one(file_path, work_dir, decrypt_settings, log_dir=log_dir)
            file_timing["decrypt_sec"] = round(time.perf_counter() - decrypt_started, 6)
            decrypt_detail_timing = _artifact_timing(detail)
            _log_decrypt_detail(logger, config.platform_id, index, len(files), file_path.name, detail, decrypt_detail_timing)

            working_path = pathlib.Path(str(detail["output_path"]))
            if _is_stop_requested(config):
                stopped_early = True
                logger.info("stop_requested_after_decrypt: %s", file_path.name)
                _cleanup_working_path(working_path)
                break

            detected_container = str(detail.get("detected_container") or detail.get("final_extension") or "bin").lower()
            decrypt_summary = _log_media_summary(logger, "Decrypt media summary", working_path)
            summary_error = _validate_summary(logger, "Decrypt", working_path, decrypt_summary)
            if summary_error:
                raise RuntimeError(str(detail.get("reason") or summary_error))

            normalized_target = _normalize_final_target(
                desired_target,
                detected_container,
                transcode_enabled=transcode_enabled,
            )
            prepared_artifacts.append(
                _PreparedArtifact(
                    index=index,
                    total_count=len(files),
                    input_path=file_path,
                    basename=basename,
                    desired_target=normalized_target,
                    file_started=file_started,
                    working_path=working_path,
                    detected_container=detected_container,
                    detail=detail,
                    decrypt_detail_timing=decrypt_detail_timing,
                    file_timing=file_timing,
                )
            )
            logger.info(
                "decoded_ready: %s container=%s target=%s needs_transcode=%s",
                file_path.name,
                detected_container,
                normalized_target,
                _artifact_needs_transcode(normalized_target, detected_container),
            )
            _emit_event(
                config,
                "file_decrypted",
                {
                    "platform_id": config.platform_id,
                    "index": index,
                    "total": len(files),
                    "input_path": str(file_path),
                    "working_path": str(working_path),
                    "detected_container": detected_container,
                    "desired_target": normalized_target,
                    "needs_transcode": _artifact_needs_transcode(normalized_target, detected_container),
                    "timing": dict(file_timing),
                    "decrypt_detail_timing": dict(decrypt_detail_timing),
                    "payload": dict(detail),
                },
            )
        except Exception as exc:
            _cleanup_working_path(working_path)
            file_timing["total_sec"] = round(time.perf_counter() - file_started, 6)
            _accumulate(timing_batch_total, file_timing)
            logger.warning("failed: %s reason=%s", file_path.name, exc)
            logger.info("[timing] file_done [%d/%d] %s reason=%s %s", index, len(files), file_path.name, exc, timing_text(file_timing))
            result = FileResult(ok=False, skipped=False, platform_id=config.platform_id, input_path=str(file_path), reason=str(exc), timing=_copy_timing(file_timing))
            results.append(result)
            _emit_event(
                config,
                "file_finished",
                {
                    "platform_id": config.platform_id,
                    "index": index,
                    "total": len(files),
                    "result": "failed",
                    "input_path": str(file_path),
                    "reason": str(exc),
                    "timing": dict(result.timing),
                },
            )
            failed_count += 1

    should_transcode = False
    if prepared_artifacts:
        should_transcode, pending_transcode = _resolve_batch_transcode_choice(
            logger,
            config,
            prepared_artifacts,
            failed_count=failed_count,
            stopped_early=stopped_early,
        )
        if should_transcode and pending_transcode:
            logger.info("batch_transcode_started: pending=%d", len(pending_transcode))
            _emit_event(
                config,
                "batch_transcode_started",
                {
                    "platform_id": config.platform_id,
                    "pending_count": len(pending_transcode),
                    "pending_files": [item.input_path.name for item in pending_transcode],
                },
            )

    finalized_count = 0
    for prepared in prepared_artifacts:
        status, result = _finalize_prepared_artifact(
            logger,
            config,
            cover_service,
            manifest_repo,
            prepared,
            should_transcode=should_transcode,
            file_started=prepared.file_started,
        )
        finalized_count += 1
        results.append(result)
        _accumulate(timing_batch_total, result.timing)
        if status == "success":
            success_count += 1
        elif status == "already_decrypted":
            skipped_count += 1
        elif result.reason == "stopped_by_user":
            stopped_early = True
            break
        else:
            failed_count += 1
        if _is_stop_requested(config):
            stopped_early = True
            break

    if finalized_count < len(prepared_artifacts):
        for leftover in prepared_artifacts[finalized_count:]:
            _cleanup_working_path(leftover.working_path)

    timed_file_count = len(results) if results else 1
    timing_batch_avg = {key: round(float(timing_batch_total.get(key, 0.0)) / float(timed_file_count), 6) for key in TIMING_STAGE_KEYS}
    hotspot_candidates = {key: value for key, value in timing_batch_total.items() if key != "total_sec"}
    hotspot_stage = max(hotspot_candidates, key=hotspot_candidates.get) if hotspot_candidates else None
    timing_hotspot_stage = {
        "stage": hotspot_stage,
        "total_sec": round(float(hotspot_candidates.get(hotspot_stage, 0.0)), 6) if hotspot_stage else 0.0,
        "ratio_of_total": round(float(hotspot_candidates.get(hotspot_stage, 0.0)) / float(timing_batch_total.get("total_sec", 0.0)), 6) if hotspot_stage and float(timing_batch_total.get("total_sec", 0.0)) > 0.0 else 0.0,
        "batch_wall_sec": round(time.perf_counter() - batch_started, 6),
    }
    result_code = 3 if stopped_early else (0 if failed_count == 0 else 2)
    summary = BatchSummary(
        result_code=result_code,
        platform_id=config.platform_id,
        input_path=str(config.input_path),
        output_dir=str(config.output_dir),
        success_count=success_count,
        skipped_count=skipped_count,
        failed_count=failed_count,
        candidate_count=len(files),
        timing_batch_total=timing_batch_total,
        timing_batch_avg=timing_batch_avg,
        timing_hotspot_stage=timing_hotspot_stage,
    )
    batch_json, batch_txt = write_batch_reports(log_dir, config.platform_id, results, summary)
    logger.info("[timing] batch_total: %s", timing_text(timing_batch_total))
    logger.info("[timing] batch_avg: %s", timing_text(timing_batch_avg))
    logger.info("[timing] batch_hotspot: stage=%s total_sec=%.3fs ratio=%.2f%% wall=%.3fs", timing_hotspot_stage.get("stage"), float(timing_hotspot_stage.get("total_sec", 0.0)), float(timing_hotspot_stage.get("ratio_of_total", 0.0)) * 100.0, float(timing_hotspot_stage.get("batch_wall_sec", 0.0)))
    logger.info("batch_result_code=%s", result_code)
    logger.info("batch_report_json=%s", batch_json)
    logger.info("batch_report_txt=%s", batch_txt)
    _emit_event(
        config,
        "batch_finished",
        {
            "platform_id": config.platform_id,
            "result_code": result_code,
            "success_count": success_count,
            "skipped_count": skipped_count,
            "failed_count": failed_count,
            "candidate_count": len(files),
            "timing_batch_total": dict(timing_batch_total),
            "timing_batch_avg": dict(timing_batch_avg),
            "timing_hotspot_stage": dict(timing_hotspot_stage),
            "batch_report_json": str(batch_json),
            "batch_report_txt": str(batch_txt),
        },
    )
    try:
        shutil.rmtree(work_dir, ignore_errors=True)
    except Exception:
        pass
    return result_code

