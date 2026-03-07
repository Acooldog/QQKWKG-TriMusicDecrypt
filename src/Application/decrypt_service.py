from __future__ import annotations

import logging
import pathlib
import shutil
import time
from collections import defaultdict
from typing import Any

from src.Application.models import BatchRunConfig, BatchSummary, FileResult, PlatformAdapter, TIMING_STAGE_KEYS
from src.Infrastructure.output_manifest_repository import OutputManifestRepository
from src.Infrastructure.runtime_logging import setup_logger, timing_text, write_batch_reports
from src.Infrastructure.runtime_paths import RuntimePaths
from src.Infrastructure.transcoder import normalize_target_format, transcode_file


AUDIO_OUTPUT_EXTS = {".flac", ".ogg", ".wav", ".mp3", ".m4a"}


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


def _resolve_publish_target(
    *,
    base_name: str,
    extension: str,
    platform_id: str,
    output_dir: pathlib.Path,
    manifest_repo: OutputManifestRepository,
    config: BatchRunConfig,
) -> tuple[pathlib.Path, str, str | None]:
    target = output_dir / f"{base_name}.{extension}"
    if not target.exists():
        return target, "direct", None
    existing_platform = manifest_repo.get_platform(target)
    if existing_platform in {None, platform_id}:
        return target, "existing_same_platform", existing_platform
    choice = _default_collision_choice(base_name, extension, existing_platform, config)
    if choice == "overwrite":
        return target, "overwrite", existing_platform
    if choice == "subdir":
        sub_target = output_dir / platform_id / f"{base_name}.{extension}"
        return sub_target, "subdir", existing_platform
    suffix_target = output_dir / f"{base_name}.{platform_id}.{extension}"
    return suffix_target, "suffix", existing_platform


def _publish_file(source_path: pathlib.Path, target_path: pathlib.Path) -> pathlib.Path:
    target_path.parent.mkdir(parents=True, exist_ok=True)
    if target_path.exists():
        target_path.unlink()
    source_path.replace(target_path)
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


def _emit_event(config: BatchRunConfig, event_name: str, payload: dict[str, Any]) -> None:
    if config.event_sink is None:
        return
    try:
        config.event_sink(event_name, payload)
    except Exception:
        # UI / observer failures must not break the decrypt pipeline.
        pass


def _stop_requested(config: BatchRunConfig) -> bool:
    if config.stop_requested is None:
        return False
    try:
        return bool(config.stop_requested())
    except Exception:
        return False


def run_batch(config: BatchRunConfig, adapter: PlatformAdapter) -> int:
    paths = RuntimePaths.discover()
    paths.ensure_runtime_dirs()
    logger, log_path, log_dir = setup_logger(paths)
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
    success_count = 0
    skipped_count = 0
    failed_count = 0
    stopped = False

    for index, file_path in enumerate(files, start=1):
        if _stop_requested(config):
            stopped = True
            logger.info("batch_stop_requested: platform=%s before_file=%d", config.platform_id, index)
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

        publish_hint: tuple[pathlib.Path, str, str | None] | None = None
        dedupe_started = time.perf_counter()
        if predicted_ext:
            publish_hint = _resolve_publish_target(
                base_name=basename,
                extension=predicted_ext,
                platform_id=config.platform_id,
                output_dir=config.output_dir,
                manifest_repo=manifest_repo,
                config=config,
            )
            hinted_target, hinted_mode, existing_platform = publish_hint
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

        try:
            decrypt_started = time.perf_counter()
            detail = adapter.decrypt_one(file_path, work_dir, config.settings, log_dir=log_dir)
            file_timing["decrypt_sec"] = round(time.perf_counter() - decrypt_started, 6)
            decrypt_detail_timing = _artifact_timing(detail)
            _log_decrypt_detail(logger, config.platform_id, index, len(files), file_path.name, detail, decrypt_detail_timing)

            working_path = pathlib.Path(str(detail["output_path"]))
            detected_container = str(detail.get("detected_container") or detail.get("final_extension") or "bin").lower()
            if detected_container == "bin":
                raise RuntimeError(str(detail.get("reason") or "unrecognized_audio_container"))

            working_path, final_extension, transcode_meta = _maybe_transcode(logger, file_path, desired_target, working_path, detected_container, file_timing)
            publish_started = time.perf_counter()
            if publish_hint is None or publish_hint[0].suffix.lower() != f".{final_extension}":
                publish_hint = _resolve_publish_target(
                    base_name=basename,
                    extension=final_extension,
                    platform_id=config.platform_id,
                    output_dir=config.output_dir,
                    manifest_repo=manifest_repo,
                    config=config,
                )
            final_target, publish_mode, existing_platform = publish_hint
            if final_target.exists() and publish_mode == "existing_same_platform":
                if working_path.exists():
                    working_path.unlink()
                skipped_count += 1
                file_timing["publish_sec"] = round(time.perf_counter() - publish_started, 6)
                file_timing["total_sec"] = round(time.perf_counter() - file_started, 6)
                _accumulate(timing_batch_total, file_timing)
                logger.info("skip_duplicate_after_decode: %s -> %s", file_path.name, final_target)
                logger.info("[timing] file_done [%d/%d] %s reason=already_decrypted %s", index, len(files), file_path.name, timing_text(file_timing))
                result = FileResult(ok=True, skipped=True, platform_id=config.platform_id, input_path=str(file_path), output_path=str(final_target), reason="already_decrypted", timing=_copy_timing(file_timing), decrypt_detail_timing=decrypt_detail_timing, payload=detail)
                results.append(result)
                _emit_event(
                    config,
                    "file_finished",
                    {
                        "platform_id": config.platform_id,
                        "index": index,
                        "total": len(files),
                        "result": "already_decrypted",
                        "output_path": str(final_target),
                        "timing": dict(result.timing),
                        "decrypt_detail_timing": dict(result.decrypt_detail_timing),
                    },
                )
                continue
            published = _publish_file(working_path, final_target)
            file_timing["publish_sec"] = round(time.perf_counter() - publish_started, 6)
            file_timing["total_sec"] = round(time.perf_counter() - file_started, 6)
            _accumulate(timing_batch_total, file_timing)
            manifest_repo.set_platform(published, config.platform_id)
            payload = dict(detail)
            payload.update({
                "detected_container": detected_container,
                "final_extension": final_extension,
                "publish_mode": publish_mode,
                "existing_platform": existing_platform,
            })
            if transcode_meta is not None:
                payload["transcode"] = transcode_meta
            logger.info("success: %s -> %s", file_path.name, published)
            logger.info("[timing] decrypt [%d/%d] %s elapsed=%.3fs", index, len(files), file_path.name, file_timing["decrypt_sec"])
            logger.info("[timing] file_done [%d/%d] %s reason=success %s", index, len(files), file_path.name, timing_text(file_timing))
            result = FileResult(ok=True, skipped=False, platform_id=config.platform_id, input_path=str(file_path), output_path=str(published), timing=_copy_timing(file_timing), decrypt_detail_timing=decrypt_detail_timing, payload=payload)
            results.append(result)
            _emit_event(
                config,
                "file_finished",
                {
                    "platform_id": config.platform_id,
                    "index": index,
                    "total": len(files),
                    "result": "success",
                    "output_path": str(published),
                    "timing": dict(result.timing),
                    "decrypt_detail_timing": dict(result.decrypt_detail_timing),
                    "payload": dict(payload),
                },
            )
            success_count += 1
        except Exception as exc:
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
                    "reason": str(exc),
                    "timing": dict(result.timing),
                },
            )
            failed_count += 1
        if _stop_requested(config):
            stopped = True
            logger.info("batch_stop_requested: platform=%s after_file=%d", config.platform_id, index)
            break

    timed_file_count = len(files) if files else 1
    timing_batch_avg = {key: round(float(timing_batch_total.get(key, 0.0)) / float(timed_file_count), 6) for key in TIMING_STAGE_KEYS}
    hotspot_candidates = {key: value for key, value in timing_batch_total.items() if key != "total_sec"}
    hotspot_stage = max(hotspot_candidates, key=hotspot_candidates.get) if hotspot_candidates else None
    timing_hotspot_stage = {
        "stage": hotspot_stage,
        "total_sec": round(float(hotspot_candidates.get(hotspot_stage, 0.0)), 6) if hotspot_stage else 0.0,
        "ratio_of_total": round(float(hotspot_candidates.get(hotspot_stage, 0.0)) / float(timing_batch_total.get("total_sec", 0.0)), 6) if hotspot_stage and float(timing_batch_total.get("total_sec", 0.0)) > 0.0 else 0.0,
        "batch_wall_sec": round(time.perf_counter() - batch_started, 6),
    }
    result_code = 3 if stopped else (0 if failed_count == 0 else 2)
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
