"""音频转码：PCM 重采样、PCM 封装 WAV、pilk 解码 silk、ffmpeg 管道转码。"""

from __future__ import annotations

import asyncio
import os
import shutil
import sys
import tempfile
from pathlib import Path

from ..errors import VolcASRError


def resample_pcm16(pcm: bytes, src_rate: int, dst_rate: int) -> bytes:
    """16bit 单声道 PCM 的线性重采样。"""
    if src_rate == dst_rate or not pcm:
        return pcm

    import array

    samples = array.array("h")
    samples.frombytes(pcm[: len(pcm) - (len(pcm) % 2)])
    if sys.byteorder == "big":
        samples.byteswap()

    n_src = len(samples)
    if n_src == 0:
        return b""

    n_dst = int(n_src * dst_rate / src_rate)
    ratio = src_rate / dst_rate
    out = array.array("h", [0]) * n_dst
    for i in range(n_dst):
        pos = i * ratio
        idx = int(pos)
        frac = pos - idx
        s0 = samples[idx]
        s1 = samples[idx + 1] if idx + 1 < n_src else s0
        out[i] = int(s0 + (s1 - s0) * frac)

    if sys.byteorder == "big":
        out.byteswap()
    return out.tobytes()


def pcm_to_wav(pcm: bytes, rate: int = 16000, channels: int = 1) -> bytes:
    """把 16bit PCM 包装成 wav 容器。"""
    import io
    import wave

    buf = io.BytesIO()
    with wave.open(buf, "wb") as wf:
        wf.setnchannels(channels)
        wf.setsampwidth(2)
        wf.setframerate(rate)
        wf.writeframes(pcm)
    return buf.getvalue()


async def transcode_to_wav(
    raw: bytes, src_fmt: str, *, ffmpeg_path: str, timeout: float
) -> bytes:
    """用 ffmpeg 把任意音频转成 16kHz / 单声道 / 16bit 的 wav。"""
    exe = shutil.which(ffmpeg_path) or (ffmpeg_path if os.path.isfile(ffmpeg_path) else None)
    if not exe:
        raise VolcASRError(
            f"音频需要转码但找不到 ffmpeg（ffmpeg_path={ffmpeg_path}）。"
            "请安装 ffmpeg 并加入 PATH，或在配置中填写可执行文件绝对路径。"
        )

    cmd = [
        exe, "-hide_banner", "-loglevel", "error",
        "-i", "pipe:0",
        "-ar", "16000", "-ac", "1", "-acodec", "pcm_s16le",
        "-f", "wav", "pipe:1",
    ]
    try:
        proc = await asyncio.create_subprocess_exec(
            *cmd,
            stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        out, err = await asyncio.wait_for(proc.communicate(raw), timeout=timeout)
    except FileNotFoundError as e:
        raise VolcASRError(f"无法执行 ffmpeg: {e}") from e
    except asyncio.TimeoutError as e:
        raise VolcASRError("ffmpeg 转码超时") from e

    if proc.returncode != 0 or not out:
        detail = err.decode("utf-8", "replace")[:300]
        raise VolcASRError(
            f"ffmpeg 无法解码 {src_fmt or '该音频'}（首字节 {raw[:8].hex()}）: "
            f"{detail or '未知错误'}。若为 amr/silk，请确认 ffmpeg 支持该编码或改用 silk 解码方案"
        )
    return out


async def silk_to_pcm16k(raw: bytes, *, timeout: float) -> bytes:
    """用 pilk 把 silk（QQ/微信语音）解码成 16kHz 单声道 PCM。"""
    try:
        import pilk  # type: ignore[import-not-found]
    except ImportError as e:
        raise VolcASRError(
            "语音是 silk（QQ/微信语音）格式，流式接口不支持，且未安装 silk 解码库。"
            "请执行 pip install pilk 后重试"
        ) from e

    with tempfile.TemporaryDirectory() as tmp:
        src = os.path.join(tmp, "in.silk")
        dst = os.path.join(tmp, "out.pcm")
        await asyncio.to_thread(Path(src).write_bytes, raw)
        try:
            await asyncio.wait_for(
                asyncio.to_thread(pilk.decode, src, dst, pcm_rate=24000), timeout=timeout
            )
        except TypeError:
            # 旧版本 pilk 的 decode 不支持 pcm_rate 参数
            await asyncio.wait_for(asyncio.to_thread(pilk.decode, src, dst), timeout=timeout)
        pcm = await asyncio.to_thread(Path(dst).read_bytes)

    if not pcm:
        raise VolcASRError("silk 解码结果为空")
    return resample_pcm16(pcm, 24000, 16000)


async def to_wav(raw: bytes, src_fmt: str, *, ffmpeg_path: str, timeout: float) -> bytes:
    """把不受支持的格式转成 16kHz 单声道 wav。"""
    if src_fmt == "silk":
        return pcm_to_wav(await silk_to_pcm16k(raw, timeout=timeout))
    return await transcode_to_wav(raw, src_fmt, ffmpeg_path=ffmpeg_path, timeout=timeout)
