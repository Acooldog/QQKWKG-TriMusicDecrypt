from __future__ import annotations

import json
import pathlib
from typing import Any

from src.Infrastructure.runtime_paths import RuntimePaths, appdata_path
from src.Infrastructure.transcoder import SUPPORTED_TARGET_FORMATS, normalize_target_format


CONFIG_NAMESPACE = "decrypt_cli"
PROJECT_NAME_EN = "QKKDecrypt"
PROJECT_NAME_ZH = "QQ酷狗酷我音乐解密工具"
PROJECT_ADDRESS = "https://github.com/Acooldog/QQKWKG-TriMusicDecrypt"
PROJECT_QQ = "2622138410"
QQMUSIC_ATTRIBUTION = "QQ 音乐解密模型思路参考项目：qqmusic_decrypt（https://github.com/luyikk/qqmusic_decrypt）"
LEGAL_NOTICE = "其他模型为自主逆向学习实现，仅供学习交流使用；禁止商用，禁止倒卖，倒卖者将举报平台并持续追责。\n格式说明：m4a/mp3/flac 支持补封面；m4a/wav 支持补专辑信息，均优先本地后网络。"
FLET_NOTE = "main-ui 分支采用 PySide6。PySide6 基于 Qt for Python，桌面界面由本地 Qt 窗口和 Python 业务逻辑直接驱动。"
DEFAULT_KUGOU_INPUT = pathlib.Path(r"O:\KuGou\KugouMusic")
DEFAULT_KUWO_INPUT = pathlib.Path(r"C:\Users\01080\Documents\Frontier Developments\Planet Coaster\UserMusic\MusicPack")
DEFAULT_QQ_INPUT = pathlib.Path("")


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
        paths.assets_dir / "kugou_key.xz",
        paths.bundle_dir / "assets" / "kugou_key.xz",
        paths.root_dir / "assets" / "kugou_key.xz",
        pathlib.Path.cwd() / "assets" / "kugou_key.xz",
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


def load_config(paths: RuntimePaths) -> tuple[dict[str, Any], dict[str, Any]]:
    paths.ensure_runtime_dirs()
    root = _read_json(paths.plugins_config)
    payload = root.get(CONFIG_NAMESPACE)
    payload = payload if isinstance(payload, dict) else {}
    config = {
        "shared": {
            "output_dir": str(paths.output_dir),
            "cli_collision_policy": "suffix",
            "recursive": True,
            "embed_cover_art": True,
            "supplement_album_metadata": False,
        },
        "qq": {
            "input_dir": str(DEFAULT_QQ_INPUT),
            "process_match": "qqmusic",
            "embed_cover_art": True,
            "format_rules": {"mflac": "flac", "mgg": "m4a", "mmp4": "m4a"},
        },
        "kuwo": {
            "input_dir": str(DEFAULT_KUWO_INPUT),
            "process_name": "kwmusic.exe",
            "exe_path": "",
            "signature_file": str(default_kuwo_signature_path(paths)),
            "format_kwm": "auto",
        },
        "kugou": {
            "input_dir": str(DEFAULT_KUGOU_INPUT),
            "kgg_db_path": str(auto_find_kgg_db_path() or ""),
            "key_file": str(auto_find_kugou_key(paths) or (paths.assets_dir / "kugou_key.xz")),
            "target_format_kgma": "auto",
            "target_format_kgg": "auto",
        },
    }
    for section in ("shared", "qq", "kuwo", "kugou"):
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

    shared_album_metadata = config["shared"].get("supplement_album_metadata", False)
    if isinstance(shared_album_metadata, str):
        shared_album_metadata = shared_album_metadata.strip().lower() in {"1", "true", "yes", "y", "on"}
    else:
        shared_album_metadata = bool(shared_album_metadata)
    config["shared"]["supplement_album_metadata"] = shared_album_metadata

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
    config["kuwo"]["format_kwm"] = normalize_target_format(config["kuwo"].get("format_kwm", "auto"))
    config["kugou"]["target_format_kgma"] = normalize_target_format(config["kugou"].get("target_format_kgma", "auto"))
    config["kugou"]["target_format_kgg"] = normalize_target_format(config["kugou"].get("target_format_kgg", "auto"))
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

