from __future__ import annotations

import base64
import json
import os
import pathlib
import subprocess
import tempfile
import time
from typing import Any

from src.Infrastructure.runtime_paths import RuntimePaths

try:
    from mutagen.flac import FLAC
    from mutagen.id3 import ID3
    from mutagen.mp4 import MP4
except Exception:  # pragma: no cover - optional runtime dependency
    FLAC = None  # type: ignore[assignment]
    ID3 = None  # type: ignore[assignment]
    MP4 = None  # type: ignore[assignment]


SUPPORTED_TARGET_FORMATS = {"auto", "flac", "m4a", "mp3", "wav"}


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
    if normalized == "ogg":
        normalized = "m4a"
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


def _probe_media_summary_with_mutagen(input_path: pathlib.Path, container_hint: str) -> dict[str, Any] | None:
    if not input_path.exists():
        return None
    try:
        metadata: dict[str, str] = {}
        cover = False
        cover_codec = ""
        suffix = input_path.suffix.lower()
        if suffix == ".mp3" and ID3 is not None:
            tags = ID3(str(input_path))
            title = tags.get("TIT2")
            artist = tags.get("TPE1")
            album = tags.get("TALB")
            if title:
                metadata["title"] = str(title)
            if artist:
                metadata["artist"] = str(artist)
            if album:
                metadata["album"] = str(album)
            cover = bool(tags.getall("APIC"))
            cover_codec = "apic" if cover else ""
        elif suffix == ".m4a" and MP4 is not None:
            audio = MP4(str(input_path))
            tags = audio.tags or {}
            if "\xa9nam" in tags and tags["\xa9nam"]:
                metadata["title"] = str(tags["\xa9nam"][0])
            if "\xa9ART" in tags and tags["\xa9ART"]:
                metadata["artist"] = str(tags["\xa9ART"][0])
            if "\xa9alb" in tags and tags["\xa9alb"]:
                metadata["album"] = str(tags["\xa9alb"][0])
            cover = bool(tags.get("covr"))
            cover_codec = "covr" if cover else ""
        elif suffix == ".flac" and FLAC is not None:
            audio = FLAC(str(input_path))
            if audio.get("title"):
                metadata["title"] = str(audio.get("title")[0])
            if audio.get("artist"):
                metadata["artist"] = str(audio.get("artist")[0])
            if audio.get("album"):
                metadata["album"] = str(audio.get("album")[0])
            cover = bool(audio.pictures)
            cover_codec = "picture" if cover else ""
        else:
            return None
        return {
            "path": str(input_path),
            "probe_source": "mutagen",
            "container": container_hint if container_hint != "bin" else fast_detect_container(input_path),
            "audio_streams": 1,
            "video_streams": 1 if cover else 0,
            "cover": cover,
            "cover_codec": cover_codec,
            "metadata": metadata,
        }
    except Exception:
        return None


def probe_media_summary(input_path: pathlib.Path) -> dict[str, Any]:
    paths = RuntimePaths.discover()
    ffmpeg_path = resolve_ffmpeg_path(paths)
    fast_container = fast_detect_container(input_path)
    mutagen_summary = _probe_media_summary_with_mutagen(input_path, fast_container)
    if ffmpeg_path is None or not input_path.exists():
        if mutagen_summary is not None:
            return mutagen_summary
        return {
            "path": str(input_path),
            "probe_source": "missing_ffmpeg_or_input",
            "container": fast_container,
            "audio_streams": 0,
            "video_streams": 0,
            "cover": False,
            "cover_codec": "",
            "metadata": {},
        }
    fd, temp_name = tempfile.mkstemp(suffix=".json")
    os.close(fd)
    pathlib.Path(temp_name).unlink(missing_ok=True)
    try:
        command = [
            str(ffmpeg_path),
            "-hide_banner",
            "-loglevel",
            "error",
            "-print_format",
            "json",
            "-show_format",
            "-show_streams",
            "-i",
            str(input_path),
            temp_name,
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
        temp_path = pathlib.Path(temp_name)
        if completed.returncode != 0 or not temp_path.exists():
            if mutagen_summary is not None:
                return mutagen_summary
            return {
                "path": str(input_path),
                "probe_source": "ffprobe_failed",
                "container": fast_container,
                "audio_streams": 0,
                "video_streams": 0,
                "cover": False,
                "cover_codec": "",
                "metadata": {},
                "stderr": (completed.stderr or "").strip(),
            }
        data = json.loads(temp_path.read_text(encoding="utf-8", errors="replace"))
        streams = list(data.get("streams") or [])
        fmt = data.get("format") or {}
        audio_streams = [stream for stream in streams if str(stream.get("codec_type")) == "audio"]
        video_streams = [stream for stream in streams if str(stream.get("codec_type")) == "video"]
        cover_stream = next(
            (
                stream
                for stream in video_streams
                if bool((stream.get("disposition") or {}).get("attached_pic"))
            ),
            None,
        )
        container = str(fmt.get("format_name") or fast_detect_container(input_path)).split(",", 1)[0].strip().lower()
        if container == "ogg":
            container = "m4a"
        return {
            "path": str(input_path),
            "probe_source": "ffprobe_json",
            "container": container,
            "audio_streams": len(audio_streams),
            "video_streams": len(video_streams),
            "cover": bool((mutagen_summary or {}).get("cover")) or cover_stream is not None,
            "cover_codec": str((mutagen_summary or {}).get("cover_codec") or (cover_stream or {}).get("codec_name") or ""),
            "metadata": dict((mutagen_summary or {}).get("metadata") or fmt.get("tags") or {}),
        }
    finally:
        pathlib.Path(temp_name).unlink(missing_ok=True)


def summary_to_log(summary: dict[str, Any]) -> str:
    metadata = summary.get("metadata") or {}
    title = str(metadata.get("title") or metadata.get("TITLE") or "").strip()
    artist = str(metadata.get("artist") or metadata.get("ARTIST") or "").strip()
    album = str(metadata.get("album") or metadata.get("ALBUM") or "").strip()
    return (
        f"container={summary.get('container', '')} "
        f"audio={summary.get('audio_streams', 0)} "
        f"video={summary.get('video_streams', 0)} "
        f"cover={'yes' if summary.get('cover') else 'no'} "
        f"cover_codec={summary.get('cover_codec', '')} "
        f"title={title} artist={artist} album={album} "
        f"probe={summary.get('probe_source', '')}"
    ).strip()


def _codec_args(target_format: str) -> list[str]:
    if target_format == "mp3":
        return ["-codec:a", "libmp3lame", "-q:a", "2"]
    if target_format == "m4a":
        return ["-codec:a", "aac", "-b:a", "256k"]
    if target_format == "wav":
        return ["-codec:a", "pcm_s16le"]
    if target_format == "flac":
        return ["-codec:a", "flac"]
    return []


def _stream_selection_args(target_format: str) -> list[str]:
    if target_format in {"mp3", "m4a", "wav", "flac"}:
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


def attach_cover(input_path: pathlib.Path, output_path: pathlib.Path, cover_path: pathlib.Path) -> dict[str, str | int]:
    paths = RuntimePaths.discover()
    ffmpeg_path = resolve_ffmpeg_path(paths)
    if ffmpeg_path is None:
        raise FileNotFoundError("missing bundled ffmpeg executable in assets")
    if output_path.suffix.lower() not in {".m4a", ".mp3", ".flac"}:
        return {
            "ffmpeg_path": str(ffmpeg_path),
            "output_path": str(output_path),
            "return_code": 0,
            "skipped": "unsupported_cover_container",
        }
    temp_output = output_path.with_name(f".{output_path.stem}.cover.{time.time_ns()}{output_path.suffix}")
    if output_path.suffix.lower() == ".m4a":
        command = [
            str(ffmpeg_path),
            "-y",
            "-hide_banner",
            "-loglevel",
            "error",
            "-i",
            str(input_path),
            "-i",
            str(cover_path),
            "-map",
            "0:a:0",
            "-map",
            "1:v:0",
            "-c:a",
            "copy",
            "-c:v",
            "mjpeg",
            "-disposition:v:0",
            "attached_pic",
            "-metadata:s:v",
            "title=Cover",
            "-metadata:s:v",
            "comment=Cover (front)",
            str(temp_output),
        ]
    elif output_path.suffix.lower() == ".mp3":
        command = [
            str(ffmpeg_path),
            "-y",
            "-hide_banner",
            "-loglevel",
            "error",
            "-i",
            str(input_path),
            "-i",
            str(cover_path),
            "-map",
            "0:a:0",
            "-map",
            "1:v:0",
            "-c:a",
            "copy",
            "-c:v",
            "mjpeg",
            "-id3v2_version",
            "3",
            "-metadata:s:v",
            "title=Cover",
            "-metadata:s:v",
            "comment=Cover (front)",
            str(temp_output),
        ]
    else:
        picture_data = cover_path.read_bytes()
        picture_b64 = base64.b64encode(picture_data).decode("ascii")
        mime = "image/png" if cover_path.suffix.lower() == ".png" else "image/jpeg"
        with tempfile.NamedTemporaryFile("w", encoding="utf-8", suffix=".meta", delete=False) as meta_file:
            meta_file.write(";FFMETADATA1\n")
            meta_file.write(f"metadata_block_picture={picture_b64}\n")
            meta_path = pathlib.Path(meta_file.name)
        try:
            command = [
                str(ffmpeg_path),
                "-y",
                "-hide_banner",
                "-loglevel",
                "error",
                "-i",
                str(input_path),
                "-f",
                "ffmetadata",
                "-i",
                str(meta_path),
                "-map_metadata",
                "1",
                "-map",
                "0:a:0",
                "-c:a",
                "copy",
                "-metadata",
                f"comment=Cover MIME {mime}",
                str(temp_output),
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
        finally:
            meta_path.unlink(missing_ok=True)
        if completed.returncode != 0:
            stderr = completed.stderr.strip() or completed.stdout.strip() or f"ffmpeg rc={completed.returncode}"
            raise RuntimeError(f"ffmpeg attach cover failed: {stderr}")
        if output_path.exists():
            output_path.unlink()
        temp_output.replace(output_path)
        return {"ffmpeg_path": str(ffmpeg_path), "output_path": str(output_path), "return_code": completed.returncode}
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
            raise RuntimeError(f"ffmpeg attach cover failed: {stderr}")
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
