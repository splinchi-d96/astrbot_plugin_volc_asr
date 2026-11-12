"""火山 v3 协议层：流式二进制帧的构造 / 解析，以及响应体的通用解析。"""

from __future__ import annotations

import json
import struct
from typing import Any

import aiohttp

from astrbot.api import logger

from ..constants import (
    COMP_NONE,
    ERROR_HINTS,
    FLAG_LAST,
    FLAG_NONE,
    FLAG_POS_SEQ,
    MSG_AUDIO_ONLY,
    MSG_ERROR,
    MSG_FULL_CLIENT,
    PROTO_HEADER_SIZE,
    PROTO_VERSION,
    SER_JSON,
    SER_NONE,
)
from ..errors import VolcASRError


def encode_header(msg_type: int, flags: int, serialization: int) -> bytes:
    """构造 4 字节二进制协议头（大端，无压缩）。"""
    return bytes(
        [
            (PROTO_VERSION << 4) | PROTO_HEADER_SIZE,
            (msg_type << 4) | flags,
            (serialization << 4) | COMP_NONE,
            0x00,
        ]
    )


def full_client_request(payload: dict[str, Any]) -> bytes:
    """首个包：携带请求参数的 full client request。"""
    body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    return encode_header(MSG_FULL_CLIENT, FLAG_NONE, SER_JSON) + struct.pack(">I", len(body)) + body


def audio_frame(data: bytes, seq: int | None) -> bytes:
    """音频包；seq 为 None 时按最后一包（负包）发送。"""
    if seq is None:
        head = encode_header(MSG_AUDIO_ONLY, FLAG_LAST, SER_NONE)
        return head + struct.pack(">I", len(data)) + data
    head = encode_header(MSG_AUDIO_ONLY, FLAG_POS_SEQ, SER_NONE)
    return head + struct.pack(">I", seq) + struct.pack(">I", len(data)) + data


async def recv_response(ws: aiohttp.ClientWebSocketResponse) -> tuple[int, dict[str, Any]]:
    """读取一个服务端响应帧，返回 (flags, 解析后的 JSON)。"""
    while True:
        msg = await ws.receive()
        if msg.type == aiohttp.WSMsgType.BINARY:
            break
        if msg.type == aiohttp.WSMsgType.ERROR:
            raise VolcASRError(f"WebSocket 错误: {ws.exception()}")
        if msg.type in (
            aiohttp.WSMsgType.CLOSE,
            aiohttp.WSMsgType.CLOSING,
            aiohttp.WSMsgType.CLOSED,
        ):
            raise VolcASRError("WebSocket 连接被服务端关闭")

    raw: bytes = msg.data
    if len(raw) < 4:
        return 0, {}

    flags = raw[1] & 0x0F
    if (raw[1] >> 4) == MSG_ERROR and len(raw) >= 12:
        code = struct.unpack(">I", raw[4:8])[0]
        size = struct.unpack(">I", raw[8:12])[0]
        detail = raw[12:12 + size].decode("utf-8", "replace")
        raise VolcASRError(f"流式识别返回错误帧 code={code}: {detail}")

    pos = 4 + (4 if flags & 0x01 else 0)  # 带 sequence 时多 4 字节
    if len(raw) < pos + 4:
        return flags, {}
    size = struct.unpack(">I", raw[pos:pos + 4])[0]
    payload = raw[pos + 4:pos + 4 + size]

    data: dict[str, Any] = {}
    if payload:
        try:
            parsed = json.loads(payload.decode("utf-8"))
            if isinstance(parsed, dict):
                data = parsed
        except (ValueError, UnicodeDecodeError):
            logger.warning(f"[VolcASR] 无法解析流式响应帧: {payload[:200]!r}")
    return flags, data


def loads_json(raw: str) -> Any:
    if not raw:
        return {}
    try:
        return json.loads(raw)
    except ValueError:
        logger.warning(f"[VolcASR] 响应不是合法 JSON: {raw[:300]}")
        return {}


def biz_code(data: Any) -> tuple[str, str]:
    """从响应体取业务错误码与描述，兼容 header 嵌套与顶层两种结构。"""
    if not isinstance(data, dict):
        return "", ""
    header = data.get("header")
    header = header if isinstance(header, dict) else {}
    code = str(header.get("code") or data.get("code") or "").strip()
    message = str(header.get("message") or data.get("message") or "").strip()
    return code, message


def api_error(prefix: str, code: str, message: str, request_id: str) -> str:
    text = " ".join(p for p in (prefix, f"code={code}" if code else "", message) if p)
    hint = ERROR_HINTS.get(code)
    return f"{text}{f'｜提示：{hint}' if hint else ''} (request_id={request_id})"


def extract_text(data: Any) -> str:
    """取整段识别文本，兼容 result 为对象 / 数组以及旧版 resp 结构。"""
    if not isinstance(data, dict):
        return ""
    result = data.get("result") or data.get("resp")
    if isinstance(result, dict):
        return str(result.get("text") or "").strip()
    if isinstance(result, list):
        return "".join(
            str(item.get("text") or "") for item in result if isinstance(item, dict)
        ).strip()
    return ""
