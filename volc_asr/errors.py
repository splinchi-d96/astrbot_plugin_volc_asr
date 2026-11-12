"""插件内部异常。"""

from __future__ import annotations


class VolcASRError(RuntimeError):
    """识别流程中可预期的错误（配置、本地文件、接口返回异常等）。"""
