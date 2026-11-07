import json
from typing import Any, Optional

from nonebot.utils import escape_tag


class DwsAdapterError(Exception):
    """DWS 适配器异常基类。"""


class NetworkError(DwsAdapterError):
    """dws 进程通信异常。"""


class ActionFailed(DwsAdapterError):
    """dws 命令执行失败（返回了业务错误）。"""

    def __init__(self, result: Any, message: Optional[str] = None):
        self.result = result
        super().__init__(message or f"Action failed: {result!r}")


class ProcessError(DwsAdapterError):
    """dws 子进程异常退出。"""

    def __init__(self, returncode: int, stderr: Optional[str] = None):
        self.returncode = returncode
        self.stderr = stderr
        detail = ""
        if stderr:
            # stderr 可能是多行 JSON 错误，提取其中的 message 字段
            message = _extract_error_message(stderr)
            detail = escape_tag(message or " ".join(stderr.split())[:300])
        super().__init__(f"dws exited with code {returncode}: {detail}")


def _extract_error_message(stderr: str) -> Optional[str]:
    try:
        data = json.loads(stderr.strip())
    except Exception:
        return None
    if isinstance(data, dict):
        error = data.get("error")
        if isinstance(error, dict) and error.get("message"):
            return str(error["message"])
        if data.get("message"):
            return str(data["message"])
    return None


class ReadyTimeoutError(DwsAdapterError):
    """等待事件订阅 ready 标记超时。"""
