from __future__ import annotations

import base64
import os


class ConfigEncryptor:
    def __init__(self, key: str | None = None) -> None:
        self.key = (key or os.getenv("CONFIG_ENCRYPTION_KEY") or "auto-home-demo-key").encode("utf-8")

    def _xor_bytes(self, data: bytes) -> bytes:
        return bytes(byte ^ self.key[index % len(self.key)] for index, byte in enumerate(data))

    def encrypt(self, value: str) -> str:
        payload = self._xor_bytes(value.encode("utf-8"))
        return base64.urlsafe_b64encode(payload).decode("ascii")

    def decrypt(self, value: str) -> str:
        payload = base64.urlsafe_b64decode(value.encode("ascii"))
        return self._xor_bytes(payload).decode("utf-8")

