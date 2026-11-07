from typing import TYPE_CHECKING

from nonebot.log import logger
from nonebot.utils import escape_tag

if TYPE_CHECKING:
    from .event import ReferencedMessage


def log(level: str, message: str) -> None:
    """输出带 ``DWS`` 前缀的适配器日志。"""
    getattr(logger, level.lower())(f"<d>DWS</d> | {escape_tag(message)}")


def referenced_to_segments_data(
    referenced: "ReferencedMessage",
) -> dict:
    """把引用 / 转发子消息转为可 JSON 序列化的 dict。"""
    return {
        "message_id": referenced.message_id,
        "conversation_id": referenced.conversation_id,
        "sender": referenced.sender,
        "sender_open_dingtalk_id": referenced.sender_open_dingtalk_id,
        "content": referenced.content,
        "create_time": referenced.create_time,
    }
