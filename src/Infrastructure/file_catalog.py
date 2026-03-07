from __future__ import annotations

import pathlib


SUPPORTED_SUFFIXES = {".kgm", ".kgma", ".kgg", ".vpr", ".kgm.flac", ".vpr.flac"}
OUTPUT_AUDIO_EXTENSIONS = {".flac", ".ogg", ".wav", ".mp3", ".m4a", ".bin"}
DEDUPE_OUTPUT_EXTENSIONS = OUTPUT_AUDIO_EXTENSIONS - {".bin"}


def iter_supported_files(input_path: pathlib.Path, recursive: bool) -> list[pathlib.Path]:
    if input_path.is_file():
        return [input_path]
    pattern = "**/*" if recursive else "*"
    files: list[pathlib.Path] = []
    for candidate in input_path.glob(pattern):
        if not candidate.is_file():
            continue
        suffixes = "".join(candidate.suffixes).lower()
        if candidate.suffix.lower() in SUPPORTED_SUFFIXES or suffixes in SUPPORTED_SUFFIXES:
            files.append(candidate)
    return sorted(files)


def file_requires_kgg_db(file_path: pathlib.Path) -> bool:
    suffixes = "".join(file_path.suffixes).lower()
    return file_path.suffix.lower() == ".kgg" or suffixes.endswith(".kgg")


def batch_requires_kgg_db(files: list[pathlib.Path]) -> bool:
    return any(file_requires_kgg_db(file_path) for file_path in files)


def find_existing_output(
    input_path: pathlib.Path,
    output_dir: pathlib.Path,
    basename_func,
    desired_extension: str | None = None,
) -> pathlib.Path | None:
    if not output_dir.exists():
        return None
    base = basename_func(input_path)
    if desired_extension:
        candidate = output_dir / f"{base}.{desired_extension}"
        if candidate.exists() and candidate.is_file() and candidate.suffix.lower() in DEDUPE_OUTPUT_EXTENSIONS and candidate.stat().st_size > 1024:
            return candidate
        return None
    for candidate in sorted(output_dir.glob(f"{base}.*")):
        if not candidate.is_file():
            continue
        if candidate.suffix.lower() not in DEDUPE_OUTPUT_EXTENSIONS:
            continue
        if candidate.stat().st_size <= 1024:
            continue
        return candidate
    return None
