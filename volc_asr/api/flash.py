"""flash 模式：录音文件极速版（HTTP 一次请求 + 网络错误重试）。"""

from __future__ import annotations

import asyncio
import uuid
from typing import Any

import aiohttp

from astrbot.api import logger
from astrbot.api.message_components import Record

from ..audio.source import AudioLoader
from ..config import PluginConfig
from ..constants import SUCCESS_CODES
from ..errors import VolcASRError
from ..http import HttpClient
from .protocol import api_error, biz_code, extract_text, loads_json


class FlashRecognizer:
    def __init__(self, cfg: PluginConfig, http: HttpClient, audio: AudioLoader):
        self.cfg = cfg
        self.http = http
        self.audio = audio

    async def recognize(self, record: Record) -> str:
        """调用录音文件极速版接口，返回识别文本；无有效文本时返回空串。"""
        url, data, fmt = await self.audio.prepare_for_flash(record)
        payload = self._build_payload(url, data, fmt)

        last_error = ""
        for attempt in range(self.cfg.max_retries + 1):
            request_id = str(uuid.uuid4())
            headers = {
                "X-Api-Key": self.cfg.api_key,
                "X-Api-Resource-Id": self.cfg.resource_id,
                "X-Api-Request-Id": request_id,
                "X-Api-Sequence": "-1",
                "Content-Type": "application/json",
            }
            try:
                session = await self.http.session()
                async with session.post(
                    self.cfg.endpoint, headers=headers, json=payload
                ) as resp:
                    raw = await resp.text()
                    parsed = loads_json(raw)
                    self._check_response(resp, parsed, raw, request_id)
                    text = extract_text(parsed)
                    if not text:
                        logger.warning(
                            f"[VolcASR] 未识别到有效文本 (request_id={request_id})，"
                            f"原始响应: {raw[:300]}"
                        )
                        return ""
                    logger.info(
                        f"[VolcASR] 识别成功（{len(text)} 字，request_id={request_id}）: {text}"
                    )
                    return text
            except VolcASRError:
                raise
            except (aiohttp.ClientError, asyncio.TimeoutError) as e:
                last_error = f"{type(e).__name__}: {e}"
                if attempt >= self.cfg.max_retries:
                    break
                logger.warning(f"[VolcASR] 第 {attempt + 1} 次请求失败（{last_error}），准备重试")
                await asyncio.sleep(1 + attempt)

        raise VolcASRError(f"请求火山接口失败: {last_error or '未知网络错误'}")

    def _check_response(
        self, resp: aiohttp.ClientResponse, data: Any, raw: str, request_id: str
    ) -> None:
        """按响应头 + 响应体判定结果，失败时抛 VolcASRError。"""
        status = (resp.headers.get("X-Api-Status-Code") or "").strip()
        api_message = (resp.headers.get("X-Api-Message") or "").strip()
        code, message = biz_code(data)

        if resp.status != 200:  # 403 等情况下业务码在响应体里，如 header.code=45000030
            raise VolcASRError(
                api_error(
                    f"HTTP {resp.status}", code, message or api_message or raw[:300], request_id
                )
            )
        if status and status not in SUCCESS_CODES:
            raise VolcASRError(
                api_error(f"失败码 {status}", code or status, api_message or message, request_id)
            )
        if not status and code and code not in SUCCESS_CODES:
            raise VolcASRError(api_error("失败码", code, message, request_id))

    def _build_payload(self, url: str, data: str, fmt: str) -> dict[str, Any]:
        audio: dict[str, Any] = {"url": url} if url else {"data": data}
        fmt = (fmt or self.cfg.audio_format).lower()
        if fmt:
            audio["format"] = fmt
        if self.cfg.language:
            audio["language"] = self.cfg.language

        request: dict[str, Any] = {
            "model_name": "bigmodel",
            "enable_itn": self.cfg.enable_itn,
            "enable_punc": self.cfg.enable_punc,
            "show_utterances": self.cfg.show_utterances,
        }
        if self.cfg.enable_ddc:
            request["enable_ddc"] = True

        return {"user": {"uid": self.cfg.user_uid}, "audio": audio, "request": request}
