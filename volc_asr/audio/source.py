"""音频来源：格式推断与嗅探、本地路径解析、读取校验、按接口模式准备音频。"""

from __future__ import annotations

import asyncio
import base64
import inspect
import os
from pathlib import Path
from urllib.parse import unquote, urlparse

from astrbot.api import logger
from astrbot.api.message_components import Record

from ..config import PluginConfig
from ..constants import EXT_TO_FORMAT, NEED_TRANSCODE, STREAM_FORMATS
from ..errors import VolcASRError
from ..http import HttpClient
from .transcode import silk_to_pcm16k, to_wav, transcode_to_wav


def guess_format(name: str) -> str:
    """从 URL 或文件名推断音频格式，推断不出时返回空串。"""
    path = (name or "").split("?")[0].split("#")[0]
    return EXT_TO_FORMAT.get(os.path.splitext(path)[1].lower(), "")


def sniff_format(raw: bytes) -> str:
    """按文件头判断真实音频格式，识别不出时返回空串。"""
    if not raw:
        return ""
    head = raw[:16]
    if head.startswith(b"#!SILK") or head.startswith(b"\x02#!SILK"):
        return "silk"
    if head.startswith(b"#!AMR"):
        return "amr"
    if head.startswith(b"RIFF") and raw[8:12] == b"WAVE":
        return "wav"
    if head.startswith(b"OggS"):
        return "ogg"
    if head.startswith(b"ID3"):
        return "mp3"
    if len(head) >= 2 and head[0] == 0xFF and (head[1] & 0xE0) == 0xE0:
        return "mp3"
    if head[4:8] == b"ftyp":
        return "m4a"
    if head.startswith(b"fLaC"):
        return "flac"
    return ""


def _is_http(value: str) -> bool:
    return value.lower().startswith(("http://", "https://"))


async def resolve_local_path(record: Record) -> str | None:
    """尽力从 Record 组件拿到本地文件路径。"""
    converter = getattr(record, "convert_to_file_path", None)
    if callable(converter):
        try:
            result = converter()
            if inspect.isawaitable(result):
                result = await result
            if result and os.path.isfile(str(result)):
                return str(result)
        except Exception as e:  # 转换失败则退回到属性解析
            logger.debug(f"[VolcASR] convert_to_file_path 调用失败: {e}")

    for attr in ("file", "path", "url"):
        raw = getattr(record, attr, "") or ""
        if not isinstance(raw, str):
            continue
        raw = raw.strip()
        if not raw or _is_http(raw):
            continue
        if raw.startswith("file://"):
            raw = unquote(urlparse(raw).path)
            # Windows 下 file:///C:/xxx 解析后是 /C:/xxx
            if os.name == "nt" and len(raw) > 2 and raw[0] == "/" and raw[2] == ":":
                raw = raw[1:]
        if os.path.isfile(raw):
            return raw
    return None


class AudioLoader:
    """把 Record 组件变成可上传的音频数据。"""

    def __init__(self, http: HttpClient, cfg: PluginConfig):
        self.http = http
        self.cfg = cfg

    async def _locate(self, record: Record) -> tuple[str, str]:
        """返回 (url, 本地路径)，二者必有其一；都没有时抛错。"""
        url = str(getattr(record, "url", "") or "").strip()
        if _is_http(url):
            return url, ""
        path = await resolve_local_path(record)
        if not path:
            raise VolcASRError("无法获取语音文件（既没有可访问的 URL，也不是本地文件）")
        return "", path

    async def load_bytes(self, record: Record) -> tuple[bytes, str]:
        """从 URL 下载或本地文件读取音频原始字节，返回 (字节, 格式)。"""
        url, path = await self._locate(record)
        if url:
            session = await self.http.session()
            async with session.get(url) as resp:
                if resp.status != 200:
                    raise VolcASRError(f"下载语音失败: HTTP {resp.status}")
                raw = await resp.read()
            return self._check_size(raw), self.cfg.audio_format or guess_format(url)
        return await self._read_file(path), self.cfg.audio_format or guess_format(path)

    async def prepare_for_flash(self, record: Record) -> tuple[str, str, str]:
        """返回 (url, base64 数据, 格式)：能拿到公网 URL 时用 url，否则上传本地文件。"""
        url, path = await self._locate(record)
        if url:
            return url, "", guess_format(url)

        raw = await self._read_file(path)
        fmt = sniff_format(raw) or self.cfg.audio_format or guess_format(path)
        # 录音文件接口不接受 silk/amr 等格式，先转成 wav 再上传
        if fmt in NEED_TRANSCODE:
            logger.info(f"[VolcASR] 音频格式 {fmt} 不被录音文件接口支持，先转码为 wav")
            raw, fmt = await to_wav(
                raw, fmt, ffmpeg_path=self.cfg.ffmpeg_path, timeout=self.cfg.timeout
            ), "wav"
        return "", base64.b64encode(raw).decode("ascii"), fmt

    async def prepare_for_stream(self, record: Record) -> tuple[bytes, str]:
        """读取音频并确保格式可被流式接口接受，返回 (音频数据, 格式)。"""
        raw, fmt = await self.load_bytes(record)
        fmt = sniff_format(raw) or fmt
        if fmt in STREAM_FORMATS:
            return raw, fmt

        logger.info(
            f"[VolcASR] 音频实际格式为 {fmt or '未知'}（首字节 {raw[:8].hex()}），"
            "流式接口不支持，开始转码"
        )
        if fmt == "silk":
            return await silk_to_pcm16k(raw, timeout=self.cfg.timeout), "pcm"
        return await transcode_to_wav(
            raw, fmt, ffmpeg_path=self.cfg.ffmpeg_path, timeout=self.cfg.timeout
        ), "wav"

    async def _read_file(self, path: str) -> bytes:
        try:
            raw = await asyncio.to_thread(Path(path).read_bytes)
        except OSError as e:
            raise VolcASRError(f"读取本地语音文件失败: {e}") from e
        return self._check_size(raw)

    def _check_size(self, raw: bytes) -> bytes:
        if not raw:
            raise VolcASRError("语音文件为空")
        limit = int(self.cfg.max_audio_mb * 1024 * 1024)
        if len(raw) > limit:
            raise VolcASRError(
                f"语音文件过大（{len(raw) / 1024 / 1024:.1f}MB，上限 {self.cfg.max_audio_mb:g}MB）"
            )
        return raw
