import logging
from typing import Dict, Iterable, Set


logger = logging.getLogger("qqmusic_decrypt.application.format_policy")


class FormatPolicyService:
    """Manage source->target format rules and normalization."""

    DEFAULT_RULES = {
        "mflac": "flac",
        "mgg": "ogg",
        "mmp4": "m4a",
    }

    # FORMAT_WHITELIST = {"flac", "ogg", "m4a", "aac", "mp3", "wav"}
    FORMAT_WHITELIST = {"flac", "ogg", "m4a", "mp3", "wav"}
    ALIASES = {"acc": "aac"}

    def normalize_format(self, fmt: str) -> str:
        value = (fmt or "").strip().lower().lstrip(".")
        if not value:
            return ""
        return self.ALIASES.get(value, value)

    def normalize_rules(self, rules: Dict[str, str]) -> Dict[str, str]:
        normalized = dict(self.DEFAULT_RULES)
        source = rules if isinstance(rules, dict) else {}

        for src_ext in self.DEFAULT_RULES:
            requested = self.normalize_format(str(source.get(src_ext, "") or ""))
            if not requested:
                continue
            if requested not in self.FORMAT_WHITELIST:
                logger.warning(
                    "格式配置无效，回退默认: %s -> %s (支持: %s)",
                    src_ext,
                    requested,
                    ",".join(sorted(self.FORMAT_WHITELIST)),
                )
                continue
            normalized[src_ext] = requested

        return normalized

    def default_format(self, src_ext: str) -> str:
        key = self.normalize_source_ext(src_ext)
        return self.DEFAULT_RULES.get(key, "")

    def target_format(self, src_ext: str, rules: Dict[str, str]) -> str:
        key = self.normalize_source_ext(src_ext)
        return rules.get(key, self.default_format(key))

    def normalize_source_ext(self, src_ext: str) -> str:
        return (src_ext or "").strip().lower().lstrip(".")

    def is_supported_source(self, src_ext: str) -> bool:
        return self.normalize_source_ext(src_ext) in self.DEFAULT_RULES

    def needs_transcode(self, src_ext: str, target_fmt: str) -> bool:
        return self.default_format(src_ext) != self.normalize_format(target_fmt)

