from __future__ import annotations

import pathlib
import subprocess
import time

from src.Infrastructure.runtime_paths import RuntimePaths


SUPPORTED_TARGET_FORMATS = {"auto", "flac", "ogg", "m4a", "mp3", "wav"}


def _subprocess_window_kwargs() -> dict[str, object]:
    if hasattr(subprocess, "CREATE_NO_WINDOW"):
        startupinfo = subprocess.STARTUPINFO()
        startupinfo.dwFlags |= subprocess.STARTF_USESHOWWINDOW
        return {
            "creationflags": subprocess.CREATE_NO_WINDOW,
            "startupinfo": startupinfo,
        }
    return {}


def normalize_target_format(value: str) -> str:
    normalized = str(value or "auto").strip().lower()
    if normalized not in SUPPORTED_TARGET_FORMATS:
        raise ValueError(f"unsupported target format: {value}")
    return normalized


def resolve_ffmpeg_path(paths: RuntimePaths | None = None) -> pathlib.Path | None:
    paths = paths or RuntimePaths.discover()
    candidates: list[pathlib.Path] = []
    for pattern in ("ffmpeg*.exe", "ffmpeg.exe"):
        candidates.extend(sorted(paths.assets_dir.glob(pattern)))
        candidates.extend(sorted((paths.bundle_dir / "assets").glob(pattern)))
        candidates.extend(sorted((paths.root_dir / "assets").glob(pattern)))
    seen: set[str] = set()
    for candidate in candidates:
        key = str(candidate).lower()
        if key in seen:
            continue
        seen.add(key)
        if candidate.exists() and candidate.is_file():
            return candidate
    return None


def fast_detect_container(path: pathlib.Path) -> str:
    if not path.exists() or path.stat().st_size < 4:
        return "bin"
    head = path.read_bytes()[:64]
    if head.startswith(b"fLaC"):
        return "flac"
    if head.startswith(b"OggS"):
        return "ogg"
    if head.startswith(b"RIFF") and len(head) >= 12 and head[8:12] == b"WAVE":
        return "wav"
    if head.startswith(b"ID3"):
        return "mp3"
    if len(head) >= 2 and head[0] == 0xFF and head[1] in (0xFB, 0xF3, 0xF2):
        return "mp3"
    if len(head) >= 12 and head[4:8] == b"ftyp":
        return "m4a"
    return "bin"


def probe_audio_container(input_path: pathlib.Path) -> str | None:
    paths = RuntimePaths.discover()
    ffmpeg_path = resolve_ffmpeg_path(paths)
    if ffmpeg_path is None:
        return None
    command = [
        str(ffmpeg_path),
        "-hide_banner",
        "-i",
        str(input_path),
        "-f",
        "null",
        "NUL",
    ]
    completed = subprocess.run(
        command,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        check=False,
        **_subprocess_window_kwargs(),
    )
    stderr = completed.stderr or ""
    marker = "Input #0, "
    start = stderr.find(marker)
    if start < 0:
        return None
    after = stderr[start + len(marker):]
    format_name = after.split(",", 1)[0].strip().lower()
    if format_name == "flac":
        return "flac"
    if format_name == "ogg":
        return "ogg"
    if format_name in {"wav", "wav_pipe"}:
        return "wav"
    if format_name == "mp3":
        return "mp3"
    if format_name in {"mov", "mp4", "m4a", "3gp", "3g2", "mj2"}:
        return "m4a"
    return None


def detect_audio_container(input_path: pathlib.Path) -> tuple[str, str]:
    fast = fast_detect_container(input_path)
    if fast != "bin":
        return fast, "fast"
    probed = probe_audio_container(input_path)
    if probed:
        return probed, "ffmpeg_probe"
    return "bin", "unrecognized"


def _codec_args(target_format: str) -> list[str]:
    if target_format == "mp3":
        return ["-codec:a", "libmp3lame", "-q:a", "2"]
    if target_format == "ogg":
        return ["-codec:a", "libvorbis", "-q:a", "5"]
    if target_format == "m4a":
        return ["-codec:a", "aac", "-b:a", "256k"]
    if target_format == "wav":
        return ["-codec:a", "pcm_s16le"]
    if target_format == "flac":
        return ["-codec:a", "flac"]
    return []


def _stream_selection_args(target_format: str) -> list[str]:
    if target_format in {"mp3", "ogg", "m4a", "wav", "flac"}:
        return ["-map", "0:a:0", "-vn", "-sn", "-dn"]
    return []


def transcode_file(input_path: pathlib.Path, output_path: pathlib.Path, target_format: str) -> dict[str, str | int]:
    paths = RuntimePaths.discover()
    ffmpeg_path = resolve_ffmpeg_path(paths)
    if ffmpeg_path is None:
        raise FileNotFoundError("missing bundled ffmpeg executable in assets")
    output_path.parent.mkdir(parents=True, exist_ok=True)
    temp_output = output_path.with_name(f".{output_path.stem}.transcode.{time.time_ns()}{output_path.suffix}")
    command = [
        str(ffmpeg_path),
        "-y",
        "-hide_banner",
        "-loglevel",
        "error",
        "-i",
        str(input_path),
        *_stream_selection_args(target_format),
        *_codec_args(target_format),
        str(temp_output),
    ]
    try:
        completed = subprocess.run(
            command,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            check=False,
            **_subprocess_window_kwargs(),
        )
        if completed.returncode != 0:
            stderr = completed.stderr.strip() or completed.stdout.strip() or f"ffmpeg rc={completed.returncode}"
            raise RuntimeError(f"ffmpeg transcode failed: {stderr}")
        if output_path.exists():
            output_path.unlink()
        temp_output.replace(output_path)
        return {"ffmpeg_path": str(ffmpeg_path), "output_path": str(output_path), "return_code": completed.returncode}
    finally:
        if temp_output.exists():
            try:
                temp_output.unlink()
            except OSError:
                pass
