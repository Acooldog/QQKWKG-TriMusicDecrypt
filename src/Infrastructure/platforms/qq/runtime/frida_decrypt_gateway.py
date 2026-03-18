import logging
from typing import Optional

import frida

from src.Infrastructure.platforms.qq.process_locator import find_qqmusic_process

from .qqmusic_decrypt import QQMusicDecryptor


logger = logging.getLogger("qqmusic_decrypt.infrastructure.decrypt")


class FridaDecryptGateway:
    """Gateway adapter for QQMusic decryption via existing core decryptor."""

    def __init__(self, process_match: object = None, preferred_pid: int | None = None):
        self._process_match = process_match
        self._preferred_pid = int(preferred_pid) if preferred_pid else None
        self._session = None
        self._decryptor: Optional[QQMusicDecryptor] = None

    def _get_local_device(self):
        device_manager = frida.get_device_manager()
        device = device_manager.get_local_device()
        logger.info("Frida version: %s", frida.__version__)
        logger.info("Device name: %s", device.name)
        return device

    def _find_qqmusic_process(self):
        process = find_qqmusic_process(self._process_match)
        if process is None:
            raise RuntimeError("请先启动QQ音乐")
        self._preferred_pid = int(process.pid)
        logger.info("找到QQ音乐进程: PID=%s NAME=%s", process.pid, process.name)
        return process

    def _ensure_decryptor(self) -> QQMusicDecryptor:
        if self._decryptor is not None:
            return self._decryptor

        device = self._get_local_device()
        attach_errors: list[str] = []
        if self._preferred_pid is not None:
            try:
                logger.info("尝试附加已验证的QQ音乐进程: PID=%s", self._preferred_pid)
                self._session = device.attach(self._preferred_pid)
            except Exception as exc:
                attach_errors.append(str(exc))
                self._preferred_pid = None

        if self._session is None:
            process = self._find_qqmusic_process()
            try:
                self._session = device.attach(process.pid)
            except Exception as exc:
                attach_errors.append(str(exc))
                detail = "; ".join(item for item in attach_errors if item)
                raise RuntimeError(f"附加QQ音乐进程失败: {detail or exc}") from exc

        self._decryptor = QQMusicDecryptor(self._session)
        return self._decryptor

    def decrypt_file(self, src_file: str, dst_file: str) -> bool:
        decryptor = self._ensure_decryptor()
        return decryptor.decrypt(src_file, dst_file)
