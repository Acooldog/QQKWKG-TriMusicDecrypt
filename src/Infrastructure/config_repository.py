from __future__ import annotations

import json
import pathlib
from typing import Any

from src.Infrastructure.runtime_paths import RuntimePaths, appdata_path
from src.Infrastructure.transcoder import SUPPORTED_TARGET_FORMATS, normalize_target_format


CONFIG_NAMESPACE = "decrypt_cli"
PROJECT_NAME_EN = "QKKDecrypt"
PROJECT_NAME_ZH = "QQ酷狗酷我网易云音乐解密工具"
PROJECT_ADDRESS = "https://github.com/Acooldog/QQKWKG-TriMusicDecrypt"
PROJECT_QQ = "2622138410"
QQMUSIC_ATTRIBUTION = "QQ 音乐解密模型思路参考项目：qqmusic_decrypt（https://github.com/luyikk/qqmusic_decrypt）"
LEGAL_NOTICE = "其他模型为自主逆向学习实现，仅供学习交流使用；禁止商用，禁止倒卖，倒卖者将举报平台并持续追责。\n格式说明：m4a/mp3/flac 支持补封面；m4a/wav 支持补专辑信息，均优先本地后网络。"
FLET_NOTE = "main-ui 分支采用 PySide6。PySide6 基于 Qt for Python，桌面界面由本地 Qt 窗口和 Python 业务逻辑直接驱动。"
DEFAULT_KUGOU_INPUT = pathlib.Path(r"O:\KuGou\KugouMusic")
DEFAULT_KUWO_INPUT = pathlib.Path(r"C:\Users\01080\Documents\Frontier Developments\Planet Coaster\UserMusic\MusicPack")
DEFAULT_QQ_INPUT = pathlib.Path("")
DEFAULT_NETEASE_INPUT = pathlib.Path("")
TRANSCODE_SAMPLE_RATE_OPTIONS = (22050, 32000, 44100, 48000, 88200, 96000)
TRANSCODE_BITRATE_OPTIONS = (96, 128, 160, 192, 256, 320)


def _read_json(path: pathlib.Path) -> dict[str, Any]:
    if not path.exists():
        return {}
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return {}
    return payload if isinstance(payload, dict) else {}


def iter_kugou_key_candidates(paths: RuntimePaths) -> list[pathlib.Path]:
    candidates = [
        paths.root_dir / "assets" / "kugou_key_refreshed.xz",
        paths.assets_dir / "kugou_key.xz",
        paths.root_dir / "assets" / "kugou_key.xz",
        paths.bundle_dir / "assets" / "kugou_key.xz",
        paths.bundle_dir / "assets" / "kugou_key_refreshed.xz",
        pathlib.Path.cwd() / "assets" / "kugou_key.xz",
        pathlib.Path.cwd() / "assets" / "kugou_key_refreshed.xz",
        pathlib.Path.cwd() / "kugou_key.xz",
    ]
    unique: list[pathlib.Path] = []
    seen: set[str] = set()
    for candidate in candidates:
        lowered = str(candidate).lower()
        if lowered in seen:
            continue
        seen.add(lowered)
        unique.append(candidate)
    return unique


def auto_find_kugou_key(paths: RuntimePaths) -> pathlib.Path | None:
    for candidate in iter_kugou_key_candidates(paths):
        if candidate.exists() and candidate.is_file():
            return candidate
    return None


def iter_kgg_db_candidates() -> list[pathlib.Path]:
    candidates: list[pathlib.Path] = []
    appdata = appdata_path()
    if appdata is not None:
        candidates.append(appdata / "KuGou8" / "KGMusicV3.db")
        candidates.extend(sorted(appdata.glob("KuGou*\\KGMusicV3.db")))
    unique: list[pathlib.Path] = []
    seen: set[str] = set()
    for candidate in candidates:
        lowered = str(candidate).lower()
        if lowered in seen:
            continue
        seen.add(lowered)
        unique.append(candidate)
    return unique


def auto_find_kgg_db_path() -> pathlib.Path | None:
    for candidate in iter_kgg_db_candidates():
        if candidate.exists() and candidate.is_file():
            return candidate
    return None


def default_kuwo_signature_path(paths: RuntimePaths) -> pathlib.Path:
    candidates = [
        paths.bundle_dir / "src" / "Infrastructure" / "platforms" / "kuwo" / "runtime_m" / "out" / "recovered_signature.json",
        paths.bundle_dir / "src" / "Infrastructure" / "platforms" / "kuwo" / "runtime_m" / "out" / "out" / "recovered_signature.json",
        paths.root_dir / "src" / "Infrastructure" / "platforms" / "kuwo" / "runtime_m" / "out" / "recovered_signature.json",
        paths.root_dir / "src" / "Infrastructure" / "platforms" / "kuwo" / "runtime_m" / "out" / "out" / "recovered_signature.json",
    ]
    for candidate in candidates:
        if candidate.exists():
            return candidate
    return candidates[0]


def _normalize_optional_config_int(value: Any) -> int | None:
    if value in (None, '', False):
        return None
    try:
        normalized = int(value)
    except Exception:
        return None
    return normalized if normalized > 0 else None


def _normalize_optional_audio_choice(value: Any, allowed: tuple[int, ...]) -> int | None:
    normalized = _normalize_optional_config_int(value)
    if normalized is None:
        return None
    return normalized if normalized in allowed else None


def load_config(paths: RuntimePaths) -> tuple[dict[str, Any], dict[str, Any]]:
    paths.ensure_runtime_dirs()
    root = _read_json(paths.plugins_config)
    payload = root.get(CONFIG_NAMESPACE)
    payload = payload if isinstance(payload, dict) else {}
    config = {
        "shared": {
            "output_dir": str(paths.output_dir),
            "output_mode": "shared",
            "cli_collision_policy": "suffix",
            "recursive": True,
            "transcode_enabled": True,
            "embed_cover_art": True,
            "supplement_album_metadata": False,
            "always_run_as_admin": False,
        },
        "qq": {
            "input_dir": str(DEFAULT_QQ_INPUT),
            "output_dir": str(paths.output_dir / "qq"),
            "process_match": "qqmusic",
            "embed_cover_art": True,
            "transcode_enabled": True,
            "format_rules": {"mflac": "flac", "mgg": "m4a", "mmp4": "m4a"},
            "transcode_sample_rate_hz": None,
            "transcode_bitrate_kbps": None,
            "auto_transcode_after_decode": False,
        },
        "kuwo": {
            "input_dir": str(DEFAULT_KUWO_INPUT),
            "output_dir": str(paths.output_dir / "kuwo"),
            "process_name": "kwmusic.exe",
            "exe_path": "",
            "signature_file": str(default_kuwo_signature_path(paths)),
            "transcode_enabled": True,
            "format_kwm": "auto",
            "transcode_sample_rate_hz": None,
            "transcode_bitrate_kbps": None,
            "auto_transcode_after_decode": False,
        },
        "kugou": {
            "input_dir": str(DEFAULT_KUGOU_INPUT),
            "output_dir": str(paths.output_dir / "kugou"),
            "kgg_db_path": str(auto_find_kgg_db_path() or ""),
            "key_file": str(auto_find_kugou_key(paths) or (paths.assets_dir / "kugou_key.xz")),
            "transcode_enabled": True,
            "target_format_kgma": "auto",
            "target_format_kgg": "auto",
            "transcode_sample_rate_hz": None,
            "transcode_bitrate_kbps": None,
            "auto_transcode_after_decode": False,
        },
        "netease": {
            "input_dir": str(DEFAULT_NETEASE_INPUT),
            "output_dir": str(paths.output_dir / "netease"),
            "transcode_enabled": True,
            "target_format_ncm": "auto",
            "transcode_sample_rate_hz": None,
            "transcode_bitrate_kbps": None,
            "auto_transcode_after_decode": False,
        },
        "transcode_batch": {
            "input_paths": [],
            "output_dir": str(paths.output_dir / "transcode"),
            "recursive": True,
            "max_workers": 2,
            "rules": [
                {
                    "source_format": "\u5168\u90e8",
                    "target_format": "m4a",
                }
            ],
        },
    }
    for section in ("shared", "qq", "kuwo", "kugou", "netease", "transcode_batch"):
        value = payload.get(section)
        if isinstance(value, dict):
            config[section].update(value)
    shared_payload = payload.get("shared") if isinstance(payload.get("shared"), dict) else {}
    if "embed_cover_art" not in shared_payload and "embed_cover_art" in config["qq"]:
        config["shared"]["embed_cover_art"] = config["qq"].get("embed_cover_art", True)
    shared_embed_cover = config["shared"].get("embed_cover_art", True)
    if isinstance(shared_embed_cover, str):
        shared_embed_cover = shared_embed_cover.strip().lower() in {"1", "true", "yes", "y", "on"}
    else:
        shared_embed_cover = bool(shared_embed_cover)
    config["shared"]["embed_cover_art"] = shared_embed_cover

    shared_output_mode = str(config["shared"].get("output_mode", "shared") or "shared").lower()
    if shared_output_mode not in {"shared", "per_platform"}:
        shared_output_mode = "shared"
    config["shared"]["output_mode"] = shared_output_mode

    shared_output_dir = pathlib.Path(str(config["shared"].get("output_dir", paths.output_dir) or paths.output_dir))
    for platform_id in ("qq", "kuwo", "kugou", "netease"):
        platform_output_dir = str(config[platform_id].get("output_dir", "") or "").strip()
        if not platform_output_dir:
            config[platform_id]["output_dir"] = str(shared_output_dir / platform_id)

    shared_album_metadata = config["shared"].get("supplement_album_metadata", False)
    if isinstance(shared_album_metadata, str):
        shared_album_metadata = shared_album_metadata.strip().lower() in {"1", "true", "yes", "y", "on"}
    else:
        shared_album_metadata = bool(shared_album_metadata)
    config["shared"]["supplement_album_metadata"] = shared_album_metadata

    shared_transcode_enabled = config["shared"].get("transcode_enabled", True)
    if isinstance(shared_transcode_enabled, str):
        shared_transcode_enabled = shared_transcode_enabled.strip().lower() in {"1", "true", "yes", "y", "on"}
    else:
        shared_transcode_enabled = bool(shared_transcode_enabled)
    config["shared"]["transcode_enabled"] = shared_transcode_enabled

    shared_always_run_as_admin = config["shared"].get("always_run_as_admin", False)
    if isinstance(shared_always_run_as_admin, str):
        shared_always_run_as_admin = shared_always_run_as_admin.strip().lower() in {"1", "true", "yes", "y", "on"}
    else:
        shared_always_run_as_admin = bool(shared_always_run_as_admin)
    config["shared"]["always_run_as_admin"] = shared_always_run_as_admin

    format_rules = config["qq"].get("format_rules")
    if not isinstance(format_rules, dict):
        format_rules = {"mflac": "flac", "mgg": "m4a", "mmp4": "m4a"}
    for key in ("mflac", "mgg", "mmp4"):
        value = str(format_rules.get(key) or "").strip().lower()
        if value == "ogg":
            value = "m4a"
        if value not in SUPPORTED_TARGET_FORMATS:
            value = "m4a" if key != "mflac" else "flac"
        format_rules[key] = value
    config["qq"]["format_rules"] = format_rules
    config["shared"]["cli_collision_policy"] = str(config["shared"].get("cli_collision_policy", "suffix") or "suffix").lower()
    config["shared"]["recursive"] = bool(config["shared"].get("recursive", True))
    for platform_id in ("qq", "kuwo", "kugou", "netease"):
        transcode_enabled = config[platform_id].get("transcode_enabled", shared_transcode_enabled)
        if isinstance(transcode_enabled, str):
            transcode_enabled = transcode_enabled.strip().lower() in {"1", "true", "yes", "y", "on"}
        else:
            transcode_enabled = bool(transcode_enabled)
        config[platform_id]["transcode_enabled"] = transcode_enabled
        auto_transcode = config[platform_id].get("auto_transcode_after_decode", False)
        if isinstance(auto_transcode, str):
            auto_transcode = auto_transcode.strip().lower() in {"1", "true", "yes", "y", "on"}
        else:
            auto_transcode = bool(auto_transcode)
        config[platform_id]["auto_transcode_after_decode"] = auto_transcode
    config["kuwo"]["format_kwm"] = normalize_target_format(config["kuwo"].get("format_kwm", "auto"))
    for platform_id in ("qq", "kuwo", "kugou", "netease"):
        config[platform_id]["transcode_sample_rate_hz"] = _normalize_optional_audio_choice(config[platform_id].get("transcode_sample_rate_hz"), TRANSCODE_SAMPLE_RATE_OPTIONS)
        config[platform_id]["transcode_bitrate_kbps"] = _normalize_optional_audio_choice(config[platform_id].get("transcode_bitrate_kbps"), TRANSCODE_BITRATE_OPTIONS)
    config["kugou"]["target_format_kgma"] = normalize_target_format(config["kugou"].get("target_format_kgma", "auto"))
    config["kugou"]["target_format_kgg"] = normalize_target_format(config["kugou"].get("target_format_kgg", "auto"))
    config["netease"]["target_format_ncm"] = normalize_target_format(config["netease"].get("target_format_ncm", "auto"))

    transcode_batch = config["transcode_batch"]
    raw_input_paths = transcode_batch.get("input_paths", [])
    if not isinstance(raw_input_paths, list):
        raw_input_paths = []
    transcode_batch["input_paths"] = [str(item).strip() for item in raw_input_paths if str(item).strip()]
    transcode_batch["output_dir"] = str(transcode_batch.get("output_dir") or (paths.output_dir / "transcode"))
    transcode_batch["recursive"] = bool(transcode_batch.get("recursive", True))
    try:
        transcode_batch["max_workers"] = max(1, min(int(transcode_batch.get("max_workers", 2) or 2), 4))
    except Exception:
        transcode_batch["max_workers"] = 2
    raw_rules = transcode_batch.get("rules", [])
    if not isinstance(raw_rules, list) or not raw_rules:
        raw_rules = [{"source_format": "\u5168\u90e8", "target_format": "m4a", "sample_rate_hz": None, "bitrate_kbps": None}]
    normalized_rules: list[dict[str, Any]] = []
    for item in raw_rules:
        if not isinstance(item, dict):
            continue
        source_format = str(item.get("source_format", "\u5168\u90e8") or "\u5168\u90e8").strip() or "\u5168\u90e8"
        target_format = str(item.get("target_format", "m4a") or "m4a").strip().lower() or "m4a"
        if target_format not in SUPPORTED_TARGET_FORMATS:
            target_format = "m4a"
        if source_format in {"??", "?", "\u5168\u90e8"}:
            source_format = "\u5168\u90e8"
        normalized_rules.append(
            {
                "source_format": source_format,
                "target_format": target_format,
                "sample_rate_hz": _normalize_optional_config_int(item.get("sample_rate_hz")),
                "bitrate_kbps": _normalize_optional_config_int(item.get("bitrate_kbps")),
            }
        )
    if not normalized_rules:
        normalized_rules.append(
            {
                "source_format": "\u5168\u90e8",
                "target_format": "m4a",
                "sample_rate_hz": None,
                "bitrate_kbps": None,
            }
        )
    transcode_batch["rules"] = normalized_rules
    return root, config


def save_config(paths: RuntimePaths, root: dict[str, Any], config: dict[str, Any]) -> None:
    paths.ensure_runtime_dirs()
    root[CONFIG_NAMESPACE] = config
    paths.plugins_config.write_text(json.dumps(root, ensure_ascii=False, indent=2), encoding="utf-8")


def save_default_config_if_missing(paths: RuntimePaths) -> dict[str, Any]:
    root, config = load_config(paths)
    save_config(paths, root, config)
    return config


def build_banner(paths: RuntimePaths) -> str:
    return (
        f"{PROJECT_NAME_EN} | {PROJECT_NAME_ZH}\n"
        f"项目地址: {PROJECT_ADDRESS}\n"
        f"QQ: {PROJECT_QQ}\n"
        f"{LEGAL_NOTICE}\n"
        f"{QQMUSIC_ATTRIBUTION}"
    )


def format_help_epilog(paths: RuntimePaths) -> str:
    return (
        f"项目地址: {PROJECT_ADDRESS}\n"
        f"QQ: {PROJECT_QQ}\n"
        f"{QQMUSIC_ATTRIBUTION}\n"
        f"{FLET_NOTE}\n"
        f"{LEGAL_NOTICE}"
    )


def validate_target_format(value: str) -> str:
    return normalize_target_format(value)


def supported_transcode_formats() -> list[str]:
    return sorted(SUPPORTED_TARGET_FORMATS)





