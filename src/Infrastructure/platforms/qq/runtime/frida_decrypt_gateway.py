import logging
from typing import Optional

import frida

from .qqmusic_decrypt import QQMusicDecryptor


logger = logging.getLogger("qqmusic_decrypt.infrastructure.decrypt")


class FridaDecryptGateway:
    """Gateway adapter for QQMusic decryption via existing core decryptor."""

    def __init__(self):
        self._session = None
        self._decryptor: Optional[QQMusicDecryptor] = None

    def _find_qqmusic_process(self):
        device_manager = frida.get_device_manager()
        device = device_manager.get_local_device()
        logger.info("Frida version: %s", frida.__version__)
        logger.info("Device name: %s", device.name)

        processes = device.enumerate_processes()
        process = next((p for p in processes if "qqmusic" in p.name.lower()), None)
        if not process:
            raise RuntimeError("璇峰厛鍚姩QQ闊充箰")
        logger.info("鎵惧埌QQ闊充箰杩涚▼: PID=%s", process.pid)
        return device, process

    def _ensure_decryptor(self) -> QQMusicDecryptor:
        if self._decryptor is not None:
            return self._decryptor

        device, process = self._find_qqmusic_process()
        self._session = device.attach(process.pid)
        self._decryptor = QQMusicDecryptor(self._session)
        return self._decryptor

    def decrypt_file(self, src_file: str, dst_file: str) -> bool:
        decryptor = self._ensure_decryptor()
        return decryptor.decrypt(src_file, dst_file)


