"""消息链工具：递归查找语音组件、原地替换为文本、写回 message_str。"""

from __future__ import annotations

from contextlib import suppress
from typing import Any, Iterator

from astrbot.api import logger
from astrbot.api.event import AstrMessageEvent
from astrbot.api.message_components import Plain, Record

_SUBTREE_ATTRS = ("chain", "content")  # 引用 / 合并转发的子链挂在这两个属性上
_MAX_DEPTH = 5


def walk(chain: Any, depth: int = 0) -> Iterator[tuple[list, int, Any]]:
    """深度优先遍历消息链，产出 (父列表, 下标, 组件)。"""
    if depth > _MAX_DEPTH or not isinstance(chain, list):
        return
    for idx, comp in enumerate(chain):
        yield chain, idx, comp
        for attr in _SUBTREE_ATTRS:
            sub = getattr(comp, attr, None)
            if isinstance(sub, list):
                yield from walk(sub, depth + 1)


def describe(chain: Any, depth: int = 0) -> str:
    """把消息链渲染为便于排查的字符串，例如 Reply[At, Record]。"""
    if depth > _MAX_DEPTH or not isinstance(chain, list):
        return "?"
    parts: list[str] = []
    for comp in chain:
        sub = next(
            (
                value
                for value in (getattr(comp, attr, None) for attr in _SUBTREE_ATTRS)
                if isinstance(value, list)
            ),
            None,
        )
        name = type(comp).__name__
        parts.append(f"{name}[{describe(sub, depth + 1)}]" if sub is not None else name)
    return ", ".join(parts)


def find_record(chain: Any) -> Record | None:
    """递归找出第一个语音组件。"""
    return next((comp for _, _, comp in walk(chain) if isinstance(comp, Record)), None)


def event_extra(event: AstrMessageEvent, key: str, value: Any = None) -> Any:
    """读取事件级存储；value 不为 None 时写入。"""
    with suppress(Exception):
        if value is None:
            return event.get_extra(key)
        event.set_extra(key, value)
    return None


def apply_text(event: AstrMessageEvent, text: str, record: Record, prefix: str) -> None:
    """原地把语音组件换成文本组件，保留 At / 引用等其它组件。"""
    display = f"{prefix}{text}" if prefix else text
    chain = getattr(event.message_obj, "message", None)
    try:
        replaced = False
        for parent, idx, comp in walk(chain):
            if comp is record:
                parent[idx] = Plain(text=display)
                replaced = True
                break
        if not replaced:
            event.message_obj.message = chain = [Plain(text=display)]
        texts = [
            str(getattr(c, "text", "") or "").strip()
            for c in chain or []
            if isinstance(c, Plain)
        ]
        merged = " ".join(t for t in texts if t) or display
        event.message_str = merged
        event.message_obj.message_str = merged
    except Exception as e:
        logger.warning(f"[VolcASR] 写入识别结果失败: {e}")
    event_extra(event, "volc_stt_text", text)
