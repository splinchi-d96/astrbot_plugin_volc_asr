"""插件配置：把 WebUI 下发的 dict 解析成只读的 PluginConfig。"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable

from astrbot.api import logger

from .constants import (
    DEFAULT_ENDPOINT,
    DEFAULT_RESOURCE_ID,
    DEFAULT_STREAM_ENDPOINT,
    DEFAULT_STREAM_RESOURCE_ID,
)


DEFAULT_TEXT_PREFIX = "语音消息:"
DEFAULT_ERROR_REPLY = "语音识别失败，请稍后重试。"


def _text(cfg: dict[str, Any], key: str, default: str = "") -> str:
    value = cfg.get(key, default)
    return str(value).strip() if value is not None else default


def _num(
    cfg: dict[str, Any], key: str, default: float, cast: Callable[[Any], Any] = int
) -> Any:
    try:
        return cast(cfg.get(key, default))
    except (TypeError, ValueError):
        logger.warning(f"[VolcASR] 配置项 {key} 非法，已使用默认值 {default}")
        return cast(default)


@dataclass(frozen=True)
class PluginConfig:
    """识别流程需要的全部配置项（只读）。"""

    api_key: str = ""
    resource_id: str = DEFAULT_RESOURCE_ID
    endpoint: str = DEFAULT_ENDPOINT
    user_uid: str = "astrbot"

    api_mode: str = "flash"
    stream_endpoint: str = DEFAULT_STREAM_ENDPOINT
    stream_resource_id: str = DEFAULT_STREAM_RESOURCE_ID
    stream_chunk_bytes: int = 6400
    ffmpeg_path: str = "ffmpeg"

    language: str = ""
    audio_format: str = ""
    text_prefix: str = DEFAULT_TEXT_PREFIX
    error_reply: str = DEFAULT_ERROR_REPLY

    enable_itn: bool = True
    enable_punc: bool = True
    enable_ddc: bool = False
    enable_auto_lang: bool = False
    show_utterances: bool = False
    reply_on_error: bool = False
    debug: bool = False

    timeout: float = 30.0
    max_retries: int = 1
    max_audio_mb: float = 10.0

    @classmethod
    def from_mapping(cls, mapping: dict[str, Any] | None) -> PluginConfig:
        raw = mapping or {}
        api_key = _text(raw, "api_key")
        if not api_key and (raw.get("app_id") or raw.get("access_token")):
            logger.warning("[VolcASR] app_id / access_token 已废弃，请改用 api_key")

        api_mode = (_text(raw, "api_mode", "flash") or "flash").lower()
        if api_mode not in ("flash", "stream"):
            logger.warning(f"[VolcASR] api_mode={api_mode} 不合法，已回退为 flash")
            api_mode = "flash"

        return cls(
            api_key=api_key,
            resource_id=_text(raw, "resource_id", DEFAULT_RESOURCE_ID) or DEFAULT_RESOURCE_ID,
            endpoint=_text(raw, "endpoint", DEFAULT_ENDPOINT) or DEFAULT_ENDPOINT,
            user_uid=_text(raw, "user_uid", "astrbot") or "astrbot",
            api_mode=api_mode,
            stream_endpoint=_text(raw, "stream_endpoint", DEFAULT_STREAM_ENDPOINT)
            or DEFAULT_STREAM_ENDPOINT,
            stream_resource_id=_text(raw, "stream_resource_id", DEFAULT_STREAM_RESOURCE_ID)
            or DEFAULT_STREAM_RESOURCE_ID,
            stream_chunk_bytes=max(1600, _num(raw, "stream_chunk_bytes", 6400)),
            ffmpeg_path=_text(raw, "ffmpeg_path", "ffmpeg") or "ffmpeg",
            language=_text(raw, "language"),
            audio_format=_text(raw, "format").lower(),
            text_prefix=_text(raw, "text_prefix", DEFAULT_TEXT_PREFIX),
            error_reply=_text(raw, "error_reply", DEFAULT_ERROR_REPLY),
            enable_itn=bool(raw.get("enable_itn", True)),
            enable_punc=bool(raw.get("enable_punc", True)),
            enable_ddc=bool(raw.get("enable_ddc", False)),
            enable_auto_lang=bool(raw.get("enable_auto_lang", False)),
            show_utterances=bool(raw.get("show_utterances", False)),
            reply_on_error=bool(raw.get("reply_on_error", False)),
            debug=bool(raw.get("debug", False)),
            timeout=_num(raw, "timeout", 30.0, float),
            max_retries=max(0, _num(raw, "max_retries", 1)),
            max_audio_mb=_num(raw, "max_audio_mb", 10, float),
        )

    def log_startup(self) -> None:
        """打印加载摘要与配置告警。"""
        if (
            self.api_mode == "stream"
            and "async" in self.stream_endpoint
            and (self.language or self.enable_auto_lang)
        ):
            logger.warning(
                "[VolcASR] bigmodel_async 不支持 language / enable_auto_lang，"
                "语种参数将被忽略；需要语种自动识别请使用 bigmodel_nostream"
            )
        logger.info(
            f"[VolcASR] 插件已加载：mode={self.api_mode}，"
            f"api_key={'已配置' if self.api_key else '未配置'}，"
            f"language={self.language or '自动检测'}，debug={'开' if self.debug else '关'}"
        )
        if not self.api_key:
            logger.warning("[VolcASR] 未配置 api_key，语音识别将不会生效")
