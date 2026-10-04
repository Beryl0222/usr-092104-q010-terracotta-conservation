"""事件信封基础校验的兼容入口。

领域事件目录已迁至 :mod:`src.contract`，本模块保留原有导入路径，
仓库现有事件版本可继续调用 ``validate_event``。
"""
from src.contract import validate_event

__all__ = ["validate_event"]
