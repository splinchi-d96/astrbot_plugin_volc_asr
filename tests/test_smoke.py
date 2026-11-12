"""冒烟测试：用 stub 的 astrbot 模块验证插件可导入、关键纯函数行为正确。

运行： python -m unittest discover -s tests -v
"""

from __future__ import annotations

import asyncio
import importlib
import importlib.util
import sys
import tempfile
import types
import unittest
from pathlib import Path

PLUGIN_DIR = Path(__file__).resolve().parents[1]
PKG_NAME = PLUGIN_DIR.name


def _install_astrbot_stubs() -> None:
    """构造最小可用的 astrbot 模块，避免测试依赖真实 AstrBot 环境。"""
    if "astrbot" in sys.modules:
        return

    class Plain:
        def __init__(self, text: str = ""):
            self.text = text

    class Record:
        def __init__(self, url: str = "", file: str = "", path: str = ""):
            self.url = url
            self.file = file
            self.path = path

    class At:
        def __init__(self, qq: str = ""):
            self.qq = qq

    class AstrMessageEvent:
        def __init__(self, message=None):
            self.message_obj = types.SimpleNamespace(message=message, message_str="")
            self.message_str = ""
            self.extras: dict = {}

        def get_extra(self, key, default=None):
            return self.extras.get(key, default)

        def set_extra(self, key, value):
            self.extras[key] = value

        def plain_result(self, text):
            return text

    class EventMessageType:
        ALL = "all"

    def event_message_type(_message_type):
        def decorator(func):
            return func

        return decorator

    class Context:
        """占位。"""

    class Star:
        def __init__(self, context=None):
            self.context = context

    message_components = types.ModuleType("astrbot.api.message_components")
    message_components.Plain = Plain
    message_components.Record = Record
    message_components.At = At

    event_mod = types.ModuleType("astrbot.api.event")
    event_mod.AstrMessageEvent = AstrMessageEvent
    event_mod.EventMessageType = EventMessageType
    event_mod.filter = types.SimpleNamespace(
        event_message_type=event_message_type, EventMessageType=EventMessageType
    )

    star_mod = types.ModuleType("astrbot.api.star")
    star_mod.Context = Context
    star_mod.Star = Star

    api = types.ModuleType("astrbot.api")
    api.logger = __import__("logging").getLogger("astrbot")
    api.message_components = message_components
    api.event = event_mod
    api.star = star_mod

    astrbot = types.ModuleType("astrbot")
    astrbot.api = api

    sys.modules.update(
        {
            "astrbot": astrbot,
            "astrbot.api": api,
            "astrbot.api.message_components": message_components,
            "astrbot.api.event": event_mod,
            "astrbot.api.star": star_mod,
        }
    )


def _stub(name: str):
    return sys.modules[f"astrbot.api.{name}"]


_install_astrbot_stubs()
sys.path.insert(0, str(PLUGIN_DIR.parent))


def _mod(name: str):
    return importlib.import_module(f"{PKG_NAME}.{name}")


def _drop_plugin_modules() -> None:
    for name in [n for n in sys.modules if n == PKG_NAME or n.startswith(f"{PKG_NAME}.")]:
        del sys.modules[name]


class ImportTests(unittest.TestCase):
    def test_00_import_as_standalone_file(self):
        """单文件加载（__package__ 为空）时，main.py 的兜底逻辑仍能完成相对导入。"""
        _drop_plugin_modules()
        spec = importlib.util.spec_from_file_location("volc_asr_main_file", PLUGIN_DIR / "main.py")
        assert spec and spec.loader
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)

        self.assertEqual(module.__package__, PKG_NAME)
        self.assertTrue(hasattr(module, "VolcASRPlugin"))

    def test_01_import_as_package_module(self):
        """AstrBot 以 data.plugins.<插件目录>.main 的方式加载插件。"""
        module = importlib.import_module(f"{PKG_NAME}.main")
        self.assertEqual(module.__package__, PKG_NAME)
        self.assertTrue(hasattr(module, "VolcASRPlugin"))


class FormatTests(unittest.TestCase):
    def test_guess_format(self):
        source = _mod("volc_asr.audio.source")
        self.assertEqual(source.guess_format("http://a/b.mp3?x=1"), "mp3")
        self.assertEqual(source.guess_format("C:/tmp/A.SILK"), "silk")
        self.assertEqual(source.guess_format("a.opus"), "ogg")
        self.assertEqual(source.guess_format("noext"), "")
        self.assertEqual(source.guess_format(""), "")

    def test_sniff_format(self):
        sniff = _mod("volc_asr.audio.source").sniff_format
        cases = {
            b"#!SILK_V3\x00\x00": "silk",
            b"\x02#!SILK_V3": "silk",
            b"#!AMR\n\x00\x00": "amr",
            b"RIFF\x00\x00\x00\x00WAVEfmt ": "wav",
            b"OggS\x00\x02\x00\x00": "ogg",
            b"ID3\x04\x00\x00\x00": "mp3",
            b"\xff\xfb\x90\x00": "mp3",
            b"\x00\x00\x00\x18ftypM4A ": "m4a",
            b"fLaC\x00\x00\x00": "flac",
            b"nonsense": "",
            b"": "",
        }
        for raw, expected in cases.items():
            with self.subTest(head=raw[:8]):
                self.assertEqual(sniff(raw), expected)

    def test_resample_pcm16(self):
        transcode = _mod("volc_asr.audio.transcode")
        pcm = b"\x00\x01" * 1200
        self.assertEqual(transcode.resample_pcm16(pcm, 16000, 16000), pcm)
        out = transcode.resample_pcm16(pcm, 24000, 16000)
        self.assertAlmostEqual(len(out) // 2, 800, delta=1)

    def test_pcm_to_wav(self):
        import wave

        wav = _mod("volc_asr.audio.transcode").pcm_to_wav(b"\x00\x00" * 100)
        self.assertTrue(wav.startswith(b"RIFF"))
        self.assertEqual(wav[8:12], b"WAVE")
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "out.wav"
            path.write_bytes(wav)
            with wave.open(str(path), "rb") as wf:
                self.assertEqual(wf.getframerate(), 16000)
                self.assertEqual(wf.getnchannels(), 1)
                self.assertEqual(wf.getsampwidth(), 2)


class ProtocolTests(unittest.TestCase):
    def test_audio_frame_layout(self):
        protocol = _mod("volc_asr.api.protocol")
        framed = protocol.audio_frame(b"abc", 2)
        self.assertEqual(framed[:4], b"\x11\x21\x00\x00")
        self.assertEqual(framed[4:8], b"\x00\x00\x00\x02")
        self.assertEqual(framed[8:12], b"\x00\x00\x00\x03")
        self.assertEqual(framed[12:], b"abc")

        last = protocol.audio_frame(b"abc", None)
        self.assertEqual(last[:2], b"\x11\x22")
        self.assertEqual(last[4:8], b"\x00\x00\x00\x03")
        self.assertEqual(last[8:], b"abc")

    def test_full_client_request_layout(self):
        framed = _mod("volc_asr.api.protocol").full_client_request({"a": 1})
        self.assertEqual(framed[:4], b"\x11\x10\x10\x00")
        self.assertEqual(framed[4:8], b"\x00\x00\x00\x08")

    def test_extract_text(self):
        extract_text = _mod("volc_asr.api.protocol").extract_text
        self.assertEqual(extract_text({"result": {"text": " hi "}}), "hi")
        self.assertEqual(extract_text({"result": [{"text": "a"}, {"text": "b"}, "x"]}), "ab")
        self.assertEqual(extract_text({"resp": {"text": "x"}}), "x")
        self.assertEqual(extract_text({"result": "x"}), "")
        self.assertEqual(extract_text("x"), "")

    def test_biz_code_and_api_error(self):
        protocol = _mod("volc_asr.api.protocol")
        self.assertEqual(
            protocol.biz_code({"header": {"code": 45000030, "message": "no"}}),
            ("45000030", "no"),
        )
        # code 为 0 时按「无业务码」处理（保持原有行为）
        self.assertEqual(protocol.biz_code({"code": 0, "message": "ok"}), ("", "ok"))
        self.assertEqual(protocol.biz_code(None), ("", ""))

        text = protocol.api_error("失败码", "45000030", "denied", "rid")
        self.assertIn("失败码 code=45000030 denied", text)
        self.assertIn("提示：", text)
        self.assertIn("(request_id=rid)", text)


class ConfigTests(unittest.TestCase):
    def test_defaults(self):
        cfg = _mod("volc_asr.config").PluginConfig.from_mapping({})
        self.assertEqual(cfg.api_mode, "flash")
        self.assertEqual(cfg.stream_chunk_bytes, 6400)
        self.assertEqual(cfg.timeout, 30.0)
        self.assertEqual(cfg.max_audio_mb, 10.0)
        self.assertEqual(cfg.text_prefix, "语音消息:")
        self.assertEqual(cfg.error_reply, "语音识别失败，请稍后重试。")
        self.assertEqual(cfg.resource_id, _mod("volc_asr.constants").DEFAULT_RESOURCE_ID)

    def test_invalid_values_fall_back(self):
        cfg = _mod("volc_asr.config").PluginConfig.from_mapping(
            {"api_mode": "nope", "stream_chunk_bytes": 10, "timeout": "abc", "format": "MP3"}
        )
        self.assertEqual(cfg.api_mode, "flash")
        self.assertEqual(cfg.stream_chunk_bytes, 1600)
        self.assertEqual(cfg.timeout, 30.0)
        self.assertEqual(cfg.audio_format, "mp3")

    def test_log_startup_does_not_raise(self):
        cfg = _mod("volc_asr.config").PluginConfig.from_mapping(
            {"api_mode": "stream", "app_id": "legacy"}
        )
        cfg.log_startup()


class MessageChainTests(unittest.TestCase):
    def setUp(self):
        self.chain_mod = _mod("volc_asr.message")
        components = _stub("message_components")
        self.Plain = components.Plain
        self.Record = components.Record
        self.At = components.At

    def test_find_and_replace_nested_record(self):
        record = self.Record(url="http://x/a.silk")
        reply = types.SimpleNamespace(chain=[self.Plain("hi"), record])
        chain = [self.At(qq="1"), reply]

        self.assertIs(self.chain_mod.find_record(chain), record)
        self.assertIn("Record", self.chain_mod.describe(chain))
        self.assertIsNone(
            self.chain_mod.find_record([types.SimpleNamespace(chain=[self.Plain("x")])])
        )

        event = _stub("event").AstrMessageEvent(chain)
        self.chain_mod.apply_text(event, "text", record, "")
        self.assertEqual(reply.chain[1].text, "text")
        self.assertEqual(chain[0].qq, "1")  # 顶层 At 未被替换

    def test_apply_text_writes_message_str(self):
        record = self.Record(url="http://x/a.silk")
        reply = types.SimpleNamespace(chain=[record])
        event = _stub("event").AstrMessageEvent([self.At(qq="1"), reply])

        self.chain_mod.apply_text(event, "你好", record, "语音消息:")
        self.assertEqual(reply.chain[0].text, "语音消息:你好")
        self.assertEqual(event.message_str, "语音消息:你好")
        self.assertEqual(event.message_obj.message_str, "语音消息:你好")
        self.assertEqual(event.get_extra("volc_stt_text"), "你好")

    def test_apply_text_falls_back_to_whole_chain(self):
        record = self.Record(url="http://x/a.silk")
        event = _stub("event").AstrMessageEvent([self.At(qq="1")])
        self.chain_mod.apply_text(event, "hi", record, "")
        self.assertEqual(len(event.message_obj.message), 1)
        self.assertEqual(event.message_obj.message[0].text, "hi")
        self.assertEqual(event.message_str, "hi")

    def test_event_extra(self):
        event = _stub("event").AstrMessageEvent([])
        self.assertIsNone(self.chain_mod.event_extra(event, "k"))
        self.chain_mod.event_extra(event, "k", True)
        self.assertTrue(self.chain_mod.event_extra(event, "k"))


class RecognizerTests(unittest.TestCase):
    def _loader(self, **config):
        cfg = _mod("volc_asr.config").PluginConfig.from_mapping({"api_key": "k", **config})
        http = _mod("volc_asr.http").HttpClient(cfg.timeout)
        return cfg, http, _mod("volc_asr.audio.source").AudioLoader(http, cfg)

    def test_prepare_for_flash_prefers_url(self):
        loader = self._loader()[2]
        Record = _stub("message_components").Record
        url, data, fmt = asyncio.run(loader.prepare_for_flash(Record(url="http://a/b.mp3")))
        self.assertEqual((url, data, fmt), ("http://a/b.mp3", "", "mp3"))

    def test_prepare_for_flash_reads_local_file(self):
        loader = self._loader()[2]
        Record = _stub("message_components").Record
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "voice.mp3"
            path.write_bytes(b"RIFF\x00\x00\x00\x00WAVEfmt " + b"\x00" * 32)
            url, data, fmt = asyncio.run(loader.prepare_for_flash(Record(file=str(path))))
        self.assertEqual(url, "")
        self.assertEqual(fmt, "wav")
        self.assertTrue(data)

    def test_check_size_limits(self):
        errors = _mod("volc_asr.errors").VolcASRError
        loader = self._loader(max_audio_mb=0)[2]
        with self.assertRaises(errors):
            loader._check_size(b"")
        with self.assertRaises(errors):
            loader._check_size(b"x")

    def test_flash_payload(self):
        cfg, http, loader = self._loader(enable_ddc=True, language="zh-CN")
        flash = _mod("volc_asr.api.flash").FlashRecognizer(cfg, http, loader)

        payload = flash._build_payload("http://a/b.mp3", "", "mp3")
        self.assertEqual(payload["audio"]["url"], "http://a/b.mp3")
        self.assertEqual(payload["audio"]["format"], "mp3")
        self.assertEqual(payload["audio"]["language"], "zh-CN")
        self.assertTrue(payload["request"]["enable_ddc"])
        self.assertEqual(payload["user"]["uid"], "astrbot")

        payload = flash._build_payload("", "QUJD", "")
        self.assertEqual(payload["audio"]["data"], "QUJD")
        self.assertNotIn("format", payload["audio"])

    def test_stream_config(self):
        cfg, http, loader = self._loader(api_mode="stream", enable_auto_lang=True)
        body = _mod("volc_asr.api.stream").StreamRecognizer(cfg, http, loader)._build_config("wav")
        self.assertEqual(
            body["audio"], {"format": "wav", "rate": 16000, "bits": 16, "channel": 1}
        )
        self.assertTrue(body["request"]["enable_auto_lang"])

        cfg, http, loader = self._loader(api_mode="stream", language="ja-JP")
        body = _mod("volc_asr.api.stream").StreamRecognizer(cfg, http, loader)._build_config("pcm")
        self.assertEqual(body["audio"]["language"], "ja-JP")
        self.assertNotIn("enable_auto_lang", body["request"])


class PluginTests(unittest.TestCase):
    def test_plugin_lifecycle(self):
        main = importlib.import_module(f"{PKG_NAME}.main")
        plugin = main.VolcASRPlugin(None, {"api_key": "k", "api_mode": "stream"})
        self.assertEqual(plugin.cfg.api_mode, "stream")
        self.assertEqual(plugin.cfg.api_key, "k")
        asyncio.run(plugin.terminate())


if __name__ == "__main__":
    unittest.main()
