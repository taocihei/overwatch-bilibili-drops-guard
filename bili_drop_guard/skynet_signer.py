from __future__ import annotations

import json
import struct
import sys
import threading
from pathlib import Path
from typing import Any

import wasmtime


_WASM_FILE_NAME = "live_watch_skynet.wasm"
_SELF_TEST_PAYLOAD = {
    "uid": 93693916,
    "buvid": "AUTO1234567890123456",
    "platform": "web",
    "room_id": 23612045,
    "play_url": "https://example.com/live.flv",
    "qid": 1,
    "sid": "sid-test",
    "cts": 1785680000000,
    "stky": "key-test",
    "screen_status": 50,
    "click_status": 60,
}
_SELF_TEST_SIGNATURE = "a9bc0ec6e57b91192940708eae750d58"


def _wasm_path() -> Path:
    bundle_root = getattr(sys, "_MEIPASS", None)
    if bundle_root:
        return Path(bundle_root) / "assets" / _WASM_FILE_NAME
    return Path(__file__).resolve().parent.parent / "assets" / _WASM_FILE_NAME


class SkynetSigner:
    """Thread-safe bridge for Bilibili's current live-watch WASM signer."""

    def __init__(self, wasm_path: str | Path | None = None) -> None:
        path = Path(wasm_path) if wasm_path is not None else _wasm_path()
        if not path.is_file():
            raise RuntimeError(f"缺少 B 站观看签名组件：{path}")

        self._lock = threading.Lock()
        self._tmp: Any = None
        self._engine = wasmtime.Engine()
        module = wasmtime.Module.from_file(self._engine, str(path))
        self._store = wasmtime.Store(self._engine)
        callbacks = {
            "__cargo_web_snippet_ff5103e6cc179d13b4c7a785bdce2708fd559fc0": self._set_tmp,
            "__cargo_web_snippet_80d6d56760c65e49b7be8b6b01c1ea861b046bf0": self._noop,
            "__cargo_web_snippet_e9638d6405ab65f78daf4a5af9c9de14ecf1e2ec": self._noop,
            "__web_on_grow": self._noop,
        }
        imports = []
        for item in module.imports:
            callback = callbacks.get(item.name)
            if callback is None:
                raise RuntimeError(f"观看签名组件包含未知导入：{item.module}.{item.name}")
            imports.append(wasmtime.Func(self._store, item.type, callback))

        instance = wasmtime.Instance(self._store, module, imports)
        exports = instance.exports(self._store)
        self._memory = exports["memory"]
        self._malloc = exports["__web_malloc"]
        self._skynet = exports["skynet"]

    @staticmethod
    def _noop(*_args: object) -> None:
        return None

    def _read(self, start: int, length: int) -> bytes:
        return bytes(self._memory.read(self._store, start, start + length))

    def _read_u32(self, start: int) -> int:
        return int.from_bytes(self._read(start, 4), "little", signed=False)

    def _decode_stdweb_value(self, pointer: int) -> Any:
        tag = self._read(pointer + 12, 1)[0]
        if tag in {0, 1}:
            return None
        if tag == 2:
            return int.from_bytes(self._read(pointer, 4), "little", signed=True)
        if tag == 3:
            return struct.unpack("<d", self._read(pointer, 8))[0]
        if tag == 4:
            text_pointer = self._read_u32(pointer)
            text_length = self._read_u32(pointer + 4)
            return self._read(text_pointer, text_length).decode("utf-8")
        if tag == 5:
            return False
        if tag == 6:
            return True
        raise RuntimeError(f"观看签名组件返回了不支持的数据类型：{tag}")

    def _set_tmp(self, pointer: int) -> None:
        self._tmp = self._decode_stdweb_value(pointer)

    def sign_json(self, payload: dict[str, Any]) -> str:
        compact = json.dumps(payload, ensure_ascii=False, separators=(",", ":"))
        encoded = compact.encode("utf-8")
        with self._lock:
            text_pointer = int(self._malloc(self._store, len(encoded)))
            if encoded:
                self._memory.write(self._store, encoded, text_pointer)
            arg_pointer = int(self._malloc(self._store, 16))
            tagged_string = struct.pack("<IIxxxxBxxx", text_pointer, len(encoded), 4)
            self._memory.write(self._store, tagged_string, arg_pointer)
            self._tmp = None
            self._skynet(self._store, arg_pointer)
            signature = self._tmp

        if not isinstance(signature, str) or len(signature) != 32:
            raise RuntimeError("B 站观看签名生成失败")
        return signature


_default_signer: SkynetSigner | None = None
_default_signer_lock = threading.Lock()


def sign_live_watch_payload(payload: dict[str, Any]) -> str:
    global _default_signer
    if _default_signer is None:
        with _default_signer_lock:
            if _default_signer is None:
                _default_signer = SkynetSigner()
    return _default_signer.sign_json(payload)


def verify_bundled_signer() -> None:
    """Initialize the packaged WASM/native runtime and verify a deterministic signature."""

    actual = sign_live_watch_payload(_SELF_TEST_PAYLOAD)
    if actual != _SELF_TEST_SIGNATURE:
        raise RuntimeError(f"观看签名组件自检失败：{actual}")
