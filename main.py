"""astrbot_plugin_volc_asr —— 火山引擎（豆包）语音识别插件。

拦截消息里的语音（Record）组件，调用火山引擎接口转写为文本并原地替换回消息链，
使后续 LLM / 指令流程按纯文本处理。支持 flash（录音文件极速版）与 stream（流式识别）两种模式。
实现按职责放在 volc_asr/ 包内，本文件只做装配与事件入口。
"""

from __future__ import annotations

import importlib.util
import os
import sys
import types

if __package__ in (None, ""):
    # 兼容「按单文件加载 main.py」的场景：登记虚拟包，使下面的相对导入依然成立
    _name = os.path.basename(os.path.dirname(os.path.abspath(__file__)))
    _pkg = types.ModuleType(_name)
    _pkg.__path__ = [os.path.dirname(os.path.abspath(__file__))]
    sys.modules.setdefault(_name, _pkg)
    __package__ = _name
    if __spec__ is not None:
        __spec__ = importlib.util.spec_from_loader(f"{_name}.main", __spec__.loader)

from typing import Any, AsyncGenerator

from astrbot.api import logger
from astrbot.api.event import AstrMessageEvent, filter
from astrbot.api.message_components import Record
from astrbot.api.star import Context, Star

from .volc_asr.api.flash import FlashRecognizer
from .volc_asr.api.stream import StreamRecognizer
from .volc_asr.audio.source import AudioLoader
from .volc_asr.config import PluginConfig
from .volc_asr.errors import VolcASRError
from .volc_asr.http import HttpClient
from .volc_asr.message import apply_text, describe, event_extra, find_record

_HANDLED_KEY = "volc_stt_handled"


class VolcASRPlugin(Star):
    """火山引擎（豆包）语音识别：把语音消息转写成文本，再交给后续 LLM / 指令流程。"""

    def __init__(self, context: Context, config: dict):
        super().__init__(context)
        self.cfg = PluginConfig.from_mapping(config)
        self.http = HttpClient(self.cfg.timeout)

        audio = AudioLoader(self.http, self.cfg)
        self.flash = FlashRecognizer(self.cfg, self.http, audio)
        self.stream = StreamRecognizer(self.cfg, self.http, audio)
        self.cfg.log_startup()

    async def terminate(self):
        """插件卸载 / 重载时释放连接池。"""
        await self.http.close()

    @filter.event_message_type(filter.EventMessageType.ALL)
    async def handle_voice(self, event: AstrMessageEvent) -> AsyncGenerator[Any, None]:
        chain = getattr(getattr(event, "message_obj", None), "message", None)
        record = find_record(chain)
        if record is None:
            if self.cfg.debug:
                logger.info(f"[VolcASR] 未发现语音组件，消息结构: {describe(chain)}")
            return

        # 同一条事件只处理一次，避免多轮分发时重复识别
        if event_extra(event, _HANDLED_KEY):
            return
        event_extra(event, _HANDLED_KEY, True)

        if not self.cfg.api_key:
            logger.error("[VolcASR] 未配置 api_key，已跳过语音识别")
            return

        logger.info(
            f"[VolcASR] 检测到语音消息（url={'有' if getattr(record, 'url', '') else '无'}），开始识别"
        )
        failed = False
        try:
            text = await self._recognize(record)
        except VolcASRError as e:
            logger.error(f"[VolcASR] 识别失败: {e}")
            text, failed = "", True
        except Exception:
            logger.exception("[VolcASR] 识别时发生未预期异常")
            text, failed = "", True

        if text:
            apply_text(event, text, record, self.cfg.text_prefix)
        elif failed and self.cfg.reply_on_error and self.cfg.error_reply:
            yield event.plain_result(self.cfg.error_reply)

    async def _recognize(self, record: Record) -> str:
        """按配置选择接口模式。"""
        if self.cfg.api_mode == "stream":
            return await self.stream.recognize(record)
        return await self.flash.recognize(record)
