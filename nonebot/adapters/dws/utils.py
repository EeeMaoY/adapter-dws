"""适配器工具：统一日志入口。"""

from nonebot.utils import logger_wrapper

log = logger_wrapper("DWS")
"""适配器专用日志函数，输出自动带 ``DWS | `` 前缀。

用法：``log("DEBUG", "message")`` / ``log("ERROR", "message", exception)``
"""
