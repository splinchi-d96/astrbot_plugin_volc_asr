"""接口端点、流式协议字段、音频格式表与错误码提示。"""

DEFAULT_ENDPOINT = "https://openspeech.bytedance.com/api/v3/auc/bigmodel/recognize/flash"
DEFAULT_RESOURCE_ID = "volc.bigasr.auc_turbo"
DEFAULT_STREAM_ENDPOINT = "wss://openspeech.bytedance.com/api/v3/sauc/bigmodel_nostream"
DEFAULT_STREAM_RESOURCE_ID = "volc.seedasr.sauc.duration"

# 流式二进制协议字段（详见官方 WebSocket 协议文档）
PROTO_VERSION = PROTO_HEADER_SIZE = 0b0001
MSG_FULL_CLIENT, MSG_AUDIO_ONLY, MSG_ERROR = 0b0001, 0b0010, 0b1111
FLAG_NONE, FLAG_POS_SEQ, FLAG_LAST = 0b0000, 0b0001, 0b0010
SER_NONE, SER_JSON = 0b0000, 0b0001
COMP_NONE = 0b0000  # 不压缩

STREAM_FORMATS = {"pcm", "wav", "ogg", "mp3"}  # 流式接口直接支持
NEED_TRANSCODE = {"silk", "amr", "aac", "m4a", "flac"}  # 需要先转码
SUCCESS_CODES = {"20000000", "0", "1000"}  # 后两个兼容旧返回

ERROR_HINTS = {
    "45000030": (
        "当前 API Key 未被授予该资源（resource_id）权限。极速版资源 volc.bigasr.auc_turbo "
        "不一定对所有账号开放；若控制台没有该选项，请改用流式识别：把 api_mode 设为 stream"
    ),
    "45000001": "请求参数无效（字段缺失、取值非法或重复请求）",
    "45000002": "音频为空或无法解码",
    "45000081": "等包超时：音频分包间隔过长或连接空闲太久",
    "45000151": "音频格式不正确，请检查 format 配置或音频本身",
    "20000003": "静音音频，没有检测到人声",
    "55000031": "服务端繁忙，稍后重试",
}

EXT_TO_FORMAT = {
    ".mp3": "mp3",
    ".wav": "wav",
    ".pcm": "pcm",
    ".ogg": "ogg",
    ".opus": "ogg",
    ".silk": "silk",
    ".amr": "amr",
    ".aac": "aac",
    ".m4a": "m4a",
    ".flac": "flac",
}
