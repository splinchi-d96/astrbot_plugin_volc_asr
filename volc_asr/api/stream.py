"""stream 模式：WebSocket 单向流式识别（一次连接发送整段音频）。"""

from __future__ import annotations

import uuid
from typing import Any

import aiohttp

from astrbot.api import logger
from astrbot.api.message_components import Record

from ..audio.source import AudioLoader
from ..config import PluginConfig
from ..errors import VolcASRError
from ..http import HttpClient
from .protocol import api_error, audio_frame, extract_text, full_client_request, recv_response


class StreamRecognizer:
    def __init__(self, cfg: PluginConfig, http: HttpClient, audio: AudioLoader):
        self.cfg = cfg
        self.http = http
        self.audio = audio

    async def recognize(self, record: Record) -> str:
        """通过 WebSocket 单向流式接口识别整段音频。"""
        audio, fmt = await self.audio.prepare_for_stream(record)
        request_id = str(uuid.uuid4())
        headers = {
            "X-Api-Key": self.cfg.api_key,
            "X-Api-Resource-Id": self.cfg.stream_resource_id,
            "X-Api-Request-Id": request_id,
            "X-Api-Connect-Id": str(uuid.uuid4()),
        }

        text = ""
        try:
            session = await self.http.session()
            async with await self._connect(session, headers) as ws:
                await ws.send_bytes(full_client_request(self._build_config(fmt)))

                # 服务端会为 full client request 自动分配序号 1，音频包必须从 2 开始
                seq, step, total = 2, self.cfg.stream_chunk_bytes, len(audio)
                for start in range(0, total, step):
                    piece = audio[start:start + step]
                    if start + step >= total:
                        await ws.send_bytes(audio_frame(piece, None))
                    else:
                        await ws.send_bytes(audio_frame(piece, seq))
                        seq += 1

                while True:
                    flags, data = await recv_response(ws)
                    code = data.get("code")
                    if code not in (None, 0):
                        raise VolcASRError(
                            api_error(
                                "流式识别失败", str(code), str(data.get("message", "")), request_id
                            )
                        )
                    piece_text = extract_text(data)
                    if piece_text:
                        text = piece_text
                    if (flags & 0b0010) or data.get("is_last_package"):
                        break
        except aiohttp.ClientError as e:
            raise VolcASRError(
                f"WebSocket 连接失败: {type(e).__name__}: {e} (request_id={request_id})"
            ) from e

        if not text:
            logger.warning(f"[VolcASR] 流式识别未返回有效文本 (request_id={request_id})")
            return ""
        logger.info(f"[VolcASR] 流式识别成功（{len(text)} 字，request_id={request_id}）: {text}")
        return text

    def _build_config(self, fmt: str) -> dict[str, Any]:
        """构造 full client request 的请求参数。"""
        cfg: dict[str, Any] = {
            "user": {"uid": self.cfg.user_uid},
            "audio": {"format": fmt, "rate": 16000, "bits": 16, "channel": 1},
            "request": {
                "model_name": "bigmodel",
                "enable_itn": self.cfg.enable_itn,
                "enable_punc": self.cfg.enable_punc,
                "enable_ddc": self.cfg.enable_ddc,
                "show_utterances": self.cfg.show_utterances,
            },
        }
        if self.cfg.language:
            cfg["audio"]["language"] = self.cfg.language
        elif self.cfg.enable_auto_lang:
            cfg["request"]["enable_auto_lang"] = True
        return cfg

    async def _connect(
        self, session: aiohttp.ClientSession, headers: dict[str, str]
    ) -> aiohttp.ClientWebSocketResponse:
        """兼容不同 aiohttp 版本的 ws_connect 参数差异。"""
        kwargs: dict[str, Any] = {"headers": headers, "heartbeat": None, "max_msg_size": 0}
        try:
            return await session.ws_connect(
                self.cfg.stream_endpoint, receive_timeout=self.cfg.timeout, **kwargs
            )
        except TypeError:
            return await session.ws_connect(self.cfg.stream_endpoint, **kwargs)
