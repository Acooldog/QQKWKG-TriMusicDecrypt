from __future__ import annotations

import argparse
import json
import pathlib
import sys

from src.Application.decrypt_service import run_batch
from src.Application.models import BatchRunConfig
from src.Infrastructure.config_repository import (
    PROJECT_ADDRESS,
    PROJECT_NAME_EN,
    PROJECT_NAME_ZH,
    PROJECT_QQ,
    auto_find_kgg_db_path,
    auto_find_kugou_key,
    build_banner,
    default_kuwo_signature_path,
    format_help_epilog,
    load_config,
    save_config,
    save_default_config_if_missing,
    supported_transcode_formats,
    validate_target_format,
)
from src.Infrastructure.platforms.registry import build_platform_adapter
from src.Infrastructure.runtime_paths import RuntimePaths


PLATFORM_LABELS = {"qq": "QQ音乐", "kuwo": "酷我音乐", "kugou": "酷狗音乐"}


def pause_exit(code: int = 0, message: str | None = None) -> int:
    if message:
        print(message)
    try:
        input("按任意键退出...")
    except EOFError:
        pass
    return code


def prompt_with_default(prompt: str, default: str) -> str:
    value = input(f"{prompt} [{default}]: ").strip()
    return value or default


def prompt_bool(prompt: str, default: bool) -> bool:
    label = "Y/n" if default else "y/N"
    value = input(f"{prompt} [{label}]: ").strip().lower()
    if not value:
        return default
    return value in {"y", "yes", "1", "true"}


def prompt_choice(prompt: str, default: str, choices: list[str]) -> str:
    allowed = {choice.lower() for choice in choices}
    value = input(f"{prompt} [{default}]: ").strip().lower()
    if not value:
        return default
    if value not in allowed:
        raise ValueError(f"unsupported option: {value}")
    return value


def choose_platform() -> str:
    print("请选择平台:")
    print("1. QQ音乐")
    print("2. 酷我音乐")
    print("3. 酷狗音乐")
    mapping = {"1": "qq", "2": "kuwo", "3": "kugou", "qq": "qq", "kuwo": "kuwo", "kugou": "kugou"}
    value = input("平台 [1]: ").strip().lower() or "1"
    return mapping.get(value, "")


def collision_prompt(base_name: str, extension: str, existing_platform: str | None) -> str:
    print(f"检测到共享输出冲突: {base_name}.{extension}")
    print(f"现有来源平台: {existing_platform or '未知'}")
    print("1. 加平台后缀")
    print("2. 分平台子目录")
    print("3. 覆盖")
    value = input("选择 [1]: ").strip() or "1"
    return {"1": "suffix", "2": "subdir", "3": "overwrite"}.get(value, "suffix")


def _ensure_running_for_interactive(platform_id: str, adapter, settings: dict) -> tuple[bool, str | None]:
    ok, reason = adapter.validate_runtime(settings)
    if ok:
        return True, None
    print(f"未检测到{PLATFORM_LABELS[platform_id]}，请先开启对应软件。")
    value = input("开启完成后输入 y 继续验证，否则按任意键退出: ").strip().lower()
    if value != "y":
        return False, reason or "user_cancelled"
    ok, reason = adapter.validate_runtime(settings)
    if ok:
        return True, None
    return False, reason or "target_process_not_detected"


def _shared_recursive(config: dict) -> bool:
    return bool(config.get("shared", {}).get("recursive", True))


def _validate_kugou_runtime(paths: RuntimePaths, config: dict, input_path: pathlib.Path, recursive: bool, interactive: bool) -> tuple[bool, str | None, dict]:
    adapter = build_platform_adapter("kugou")
    settings = dict(config["kugou"])
    key_file = pathlib.Path(str(settings.get("key_file", "") or "").strip()) if str(settings.get("key_file", "")).strip() else None
    auto_key = auto_find_kugou_key(paths)
    if (key_file is None or not key_file.exists()) and auto_key is not None:
        settings["key_file"] = str(auto_key)
    ok, reason = adapter.validate_runtime(settings)
    if not ok:
        return False, reason, settings
    candidate_files = adapter.collect_files(input_path, recursive)
    has_kgg = any(path.suffix.lower() == ".kgg" for path in candidate_files)
    db_path = pathlib.Path(str(settings.get("kgg_db_path", "") or "").strip()) if str(settings.get("kgg_db_path", "")).strip() else pathlib.Path()
    if has_kgg and (not db_path.exists()):
        found = auto_find_kgg_db_path()
        if found is not None:
            settings["kgg_db_path"] = str(found)
        else:
            return False, "未找到可用的 KGMusicV3.db，无法解密 kgg。", settings
    return True, None, settings


def _run_platform(platform_id: str, config: dict, *, input_override: str | None = None, output_override: str | None = None, recursive_override: bool | None = None, interactive: bool = False) -> int:
    paths = RuntimePaths.discover()
    adapter = build_platform_adapter(platform_id)
    shared = dict(config["shared"])
    settings = dict(config[platform_id])
    settings["embed_cover_art"] = bool(shared.get("embed_cover_art", True))
    settings["supplement_album_metadata"] = bool(shared.get("supplement_album_metadata", False))
    input_path = pathlib.Path(input_override or settings.get("input_dir") or "")
    output_dir = pathlib.Path(output_override or shared.get("output_dir") or paths.output_dir)
    recursive = _shared_recursive(config) if recursive_override is None else recursive_override
    if platform_id == "kugou":
        ok, reason, settings = _validate_kugou_runtime(paths, config, input_path, recursive, interactive)
        if not ok:
            if not interactive and reason:
                print(reason, file=sys.stderr)
            return pause_exit(2, reason) if interactive else 2
        config[platform_id].update(settings)
    elif adapter.requires_running_process():
        if interactive:
            ok, reason = _ensure_running_for_interactive(platform_id, adapter, settings)
            if not ok:
                return pause_exit(2, reason)
        else:
            ok, reason = adapter.validate_runtime(settings)
            if not ok:
                if reason:
                    print(reason, file=sys.stderr)
                return 2
    batch_config = BatchRunConfig(
        platform_id=platform_id,
        input_path=input_path,
        output_dir=output_dir,
        recursive=recursive,
        collision_policy=str(shared.get("cli_collision_policy", "suffix") or "suffix").lower(),
        settings=settings,
        interactive=interactive,
        collision_resolver=collision_prompt if interactive else None,
    )
    config["shared"]["output_dir"] = str(output_dir)
    config["shared"]["recursive"] = recursive
    config[platform_id]["input_dir"] = str(input_path)
    root, _ = load_config(paths)
    save_config(paths, root, config)
    return run_batch(batch_config, adapter)


def run_interactive() -> int:
    paths = RuntimePaths.discover()
    config = save_default_config_if_missing(paths)
    print(build_banner(paths))
    use_config = prompt_bool("是否直接使用配置文件的配置", True)
    platform_id = choose_platform()
    if platform_id not in PLATFORM_LABELS:
        return pause_exit(2, "平台选择无效。")
    if use_config:
        return pause_exit(_run_platform(platform_id, config, interactive=True))

    shared = dict(config["shared"])
    settings = dict(config[platform_id])
    input_dir = pathlib.Path(prompt_with_default("输入文件或目录", str(settings.get("input_dir", ""))))
    output_dir = pathlib.Path(prompt_with_default("共享输出目录", str(shared.get("output_dir", paths.output_dir))))
    recursive = prompt_bool("递归扫描子目录", bool(shared.get("recursive", True)))
    shared["embed_cover_art"] = prompt_bool(
        "是否自动补封面（所有平台共用，可能会导致转换明显变慢）",
        bool(shared.get("embed_cover_art", True)),
    )
    shared["supplement_album_metadata"] = prompt_bool(
        "是否补充专辑信息（仅对 m4a/wav 生效，优先本地后网络）",
        bool(shared.get("supplement_album_metadata", False)),
    )

    if platform_id == "qq":
        rules = dict(settings.get("format_rules", {}))
        rules["mflac"] = prompt_choice("mflac 输出格式 flac/m4a/mp3/wav", str(rules.get("mflac", "flac")), supported_transcode_formats())
        rules["mgg"] = prompt_choice("mgg 输出格式 flac/m4a/mp3/wav", str(rules.get("mgg", "m4a")), supported_transcode_formats())
        rules["mmp4"] = prompt_choice("mmp4 输出格式 flac/m4a/mp3/wav", str(rules.get("mmp4", "m4a")), supported_transcode_formats())
        settings["format_rules"] = rules
    elif platform_id == "kuwo":
        settings["format_kwm"] = prompt_choice("kwm 输出格式 auto/flac/m4a/mp3/wav", str(settings.get("format_kwm", "auto")), supported_transcode_formats())
        settings["signature_file"] = str(default_kuwo_signature_path(paths))
    else:
        settings["target_format_kgma"] = prompt_choice("kgma/kgm/vpr 输出格式 auto/flac/m4a/mp3/wav", str(settings.get("target_format_kgma", "auto")), supported_transcode_formats())
        settings["target_format_kgg"] = prompt_choice("kgg 输出格式 auto/flac/m4a/mp3/wav", str(settings.get("target_format_kgg", "auto")), supported_transcode_formats())
        auto_key = auto_find_kugou_key(paths)
        if auto_key is not None:
            settings["key_file"] = str(auto_key)

    config[platform_id].update(settings)
    config["shared"].update(shared)
    config[platform_id]["input_dir"] = str(input_dir)
    config["shared"]["output_dir"] = str(output_dir)
    config["shared"]["recursive"] = recursive
    root, _ = load_config(paths)
    save_config(paths, root, config)
    if not prompt_bool("立即开始解密", True):
        return pause_exit(0, "配置已保存。")
    return pause_exit(_run_platform(platform_id, config, interactive=True))


def build_parser(paths: RuntimePaths) -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=f"{PROJECT_NAME_EN} / {PROJECT_NAME_ZH}",
        epilog=format_help_epilog(paths),
    )
    sub = parser.add_subparsers(dest="platform")
    for platform_id in ("qq", "kuwo", "kugou"):
        platform_parser = sub.add_parser(platform_id, help=f"{PLATFORM_LABELS[platform_id]} 解密")
        platform_sub = platform_parser.add_subparsers(dest="command")
        dec = platform_sub.add_parser("decrypt", help="执行解密")
        dec.add_argument("--input", help="输入文件或目录")
        dec.add_argument("--output", help="共享输出目录")
        dec.add_argument("--no-recursive", action="store_true", help="禁用递归扫描")
        if platform_id == "qq":
            dec.add_argument("--format-mflac", choices=[item for item in supported_transcode_formats() if item != "auto"], help="mflac 输出格式")
            dec.add_argument("--format-mgg", choices=[item for item in supported_transcode_formats() if item != "auto"], help="mgg 输出格式")
            dec.add_argument("--format-mmp4", choices=[item for item in supported_transcode_formats() if item != "auto"], help="mmp4 输出格式")
        elif platform_id == "kuwo":
            dec.add_argument("--format-kwm", choices=supported_transcode_formats(), help="kwm 输出格式")
            dec.add_argument("--exe-path", help="酷我 exe 路径")
            dec.add_argument("--signature-file", help="酷我签名文件路径")
        else:
            dec.add_argument("--kgg-db", help="KGMusicV3.db 路径")
            dec.add_argument("--key-file", help="kugou_key.xz 路径")
            dec.add_argument("--format-kgma", choices=supported_transcode_formats(), help="kgma/kgm/vpr 输出格式")
            dec.add_argument("--format-kgg", choices=supported_transcode_formats(), help="kgg 输出格式")
        cover_group = dec.add_mutually_exclusive_group()
        cover_group.add_argument("--embed-cover", dest="embed_cover_art", action="store_true", help="自动补封面（所有平台共用），可能会导致转换变慢")
        cover_group.add_argument("--no-embed-cover", dest="embed_cover_art", action="store_false", help="不自动补封面")
        album_group = dec.add_mutually_exclusive_group()
        album_group.add_argument("--supplement-album", dest="supplement_album_metadata", action="store_true", help="补充专辑信息（m4a/wav）")
        album_group.add_argument("--no-supplement-album", dest="supplement_album_metadata", action="store_false", help="不补充专辑信息")
        dec.set_defaults(embed_cover_art=None, supplement_album_metadata=None)
    return parser


def main(argv: list[str] | None = None) -> int:
    if argv is None and len(sys.argv) == 1:
        # Keep no-arg interactive entry explicit for packaged use.
        return run_interactive()
    paths = RuntimePaths.discover()
    parser = build_parser(paths)
    args = parser.parse_args(argv)
    if args.platform is None:
        return run_interactive()
    if args.command != "decrypt":
        parser.print_help()
        return 1
    _, config = load_config(paths)
    platform_id = args.platform
    settings = dict(config[platform_id])
    if args.embed_cover_art is not None:
        config["shared"]["embed_cover_art"] = bool(args.embed_cover_art)
    if args.supplement_album_metadata is not None:
        config["shared"]["supplement_album_metadata"] = bool(args.supplement_album_metadata)
    if platform_id == "qq":
        rules = dict(settings.get("format_rules", {}))
        for source_key, attr_name in (("mflac", "format_mflac"), ("mgg", "format_mgg"), ("mmp4", "format_mmp4")):
            value = getattr(args, attr_name)
            if value:
                rules[source_key] = validate_target_format(value)
        settings["format_rules"] = rules
    elif platform_id == "kuwo":
        if args.format_kwm:
            settings["format_kwm"] = validate_target_format(args.format_kwm)
        if args.exe_path:
            settings["exe_path"] = args.exe_path
        if args.signature_file:
            settings["signature_file"] = args.signature_file
        elif not str(settings.get("signature_file", "")).strip():
            settings["signature_file"] = str(default_kuwo_signature_path(paths))
    else:
        if args.kgg_db:
            settings["kgg_db_path"] = args.kgg_db
        if args.key_file:
            settings["key_file"] = args.key_file
        if args.format_kgma:
            settings["target_format_kgma"] = validate_target_format(args.format_kgma)
        if args.format_kgg:
            settings["target_format_kgg"] = validate_target_format(args.format_kgg)
    config[platform_id].update(settings)
    recursive = not args.no_recursive
    return _run_platform(platform_id, config, input_override=args.input, output_override=args.output, recursive_override=recursive, interactive=False)
