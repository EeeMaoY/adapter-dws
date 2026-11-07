from typing import Any, Dict, List, Optional

from pydantic import BaseModel, PrivateAttr

from nonebot.adapters import Event as BaseEvent
from nonebot.compat import PYDANTIC_V2, ConfigDict, type_validate_python
from nonebot.typing import overrides

from .message import Message, MessageSegment

if PYDANTIC_V2:  # pragma: pydantic-v2
    from pydantic import model_validator as _model_validator
else:  # pragma: pydantic-v1
    from pydantic import root_validator as _root_validator

EVENT_KEY_O2O_ALL = "user_im_message_receive_o2o_all"
"""全部单聊消息 EventKey。"""

EVENT_KEY_GROUP_ALL = "user_im_message_receive_group_all"
"""全部群聊消息 EventKey。"""


class ReferencedMessage(BaseModel):
    """引用回复 / 合并转发中的原消息。"""

    message_id: str = ""
    conversation_id: str = ""
    sender: str = ""
    sender_open_dingtalk_id: str = ""
    content: str = ""
    create_time: str = ""

    if PYDANTIC_V2:  # pragma: pydantic-v2
        model_config = ConfigDict(extra="allow")
    else:  # pragma: pydantic-v1

        class Config:
            extra = "allow"


class DwsEvent(BaseEvent):
    """dws 事件基类，携带订阅元信息。"""

    type: str
    """EventKey，如 ``user_im_message_receive_o2o_all``。"""

    event_id: str
    """事件 ID，可用于去重。"""

    timestamp: int
    """事件发生时间戳（毫秒）。"""

    subscribe_id: str = ""
    """订阅 ID。"""

    @overrides(BaseEvent)
    def get_type(self) -> str:
        return "message"

    @overrides(BaseEvent)
    def get_event_name(self) -> str:
        return self.type

    @overrides(BaseEvent)
    def get_event_description(self) -> str:
        return self.event_id

    @overrides(BaseEvent)
    def get_message(self) -> Message:
        raise NotImplementedError

    @overrides(BaseEvent)
    def get_user_id(self) -> str:
        raise NotImplementedError

    @overrides(BaseEvent)
    def get_session_id(self) -> str:
        raise NotImplementedError

    @overrides(BaseEvent)
    def is_tome(self) -> bool:
        return False


class MessageEvent(DwsEvent):
    """消息事件基类。

    对应 dws 扁平 NDJSON 事件负载；未知字段通过 ``extra="allow"`` 原样保留。
    """

    message_id: str
    """开放消息 ID。"""

    conversation_id: str
    """会话 openConversationId。"""

    sender: str = ""
    """发送人展示名。"""

    sender_open_dingtalk_id: str = ""
    """发送人 openDingTalkId。"""

    content: str = ""
    """消息正文。"""

    create_time: Optional[str] = None
    """消息创建时间。"""

    event_time: Optional[int] = None
    """消息事件时间戳（毫秒）。"""

    quoted_message: Optional[ReferencedMessage] = None
    """引用回复的原消息；非引用回复时为空。"""

    forward_messages: Optional[List[ReferencedMessage]] = None
    """合并转发的原消息列表；非合并转发时为空。"""

    message: Message
    """构造好的消息序列。"""

    @staticmethod
    def _build_message(values: Dict[str, Any]) -> List[MessageSegment]:
        segments: List[MessageSegment] = []
        quoted = values.get("quoted_message")
        if isinstance(quoted, dict) and quoted.get("message_id"):
            segments.append(
                MessageSegment.reply(str(quoted["message_id"])),
            )
        content = values.get("content")
        if content:
            segments.append(MessageSegment.text(str(content)))
        forward = values.get("forward_messages")
        if isinstance(forward, list) and forward:
            segments.append(
                MessageSegment.forward(
                    [
                        item
                        if isinstance(item, dict)
                        else (
                            item.model_dump()
                            if PYDANTIC_V2
                            else item.dict()
                        )
                        for item in forward
                    ]
                ),
            )
        return segments

    if PYDANTIC_V2:  # pragma: pydantic-v2

        @_model_validator(mode="before")
        @classmethod
        def _gen_message(cls, values: Any) -> Any:
            if isinstance(values, dict) and "message" not in values:
                values["message"] = Message(cls._build_message(values))
            return values

    else:  # pragma: pydantic-v1

        @classmethod
        @_root_validator(pre=True)
        def _gen_message(cls, values: Dict[str, Any]) -> Dict[str, Any]:
            values.setdefault("message", Message(cls._build_message(values)))
            return values

    @overrides(DwsEvent)
    def get_event_description(self) -> str:
        return (
            f'Message {self.message_id} from {self.sender} "{self.content}"'
        )

    @overrides(DwsEvent)
    def get_message(self) -> Message:
        return self.message

    @overrides(DwsEvent)
    def get_user_id(self) -> str:
        return self.sender_open_dingtalk_id

    @overrides(DwsEvent)
    def get_session_id(self) -> str:
        return self.conversation_id

    @overrides(DwsEvent)
    def get_plaintext(self) -> str:
        return self.content


class PrivateMessageEvent(MessageEvent):
    """单聊消息事件（``user_im_message_receive_o2o_all``）。"""

    @overrides(MessageEvent)
    def is_tome(self) -> bool:
        return True


class GroupMessageEvent(MessageEvent):
    """群聊消息事件（``user_im_message_receive_group_all``）。

    dws 事件负载不含 @ 信息，``is_tome()`` 由适配器按
    ``dws_group_is_tome`` 配置写入。
    """

    _group_tome: bool = PrivateAttr(default=False)

    @overrides(MessageEvent)
    def is_tome(self) -> bool:
        return self._group_tome


EVENT_CLASSES: Dict[str, type] = {
    EVENT_KEY_O2O_ALL: PrivateMessageEvent,
    EVENT_KEY_GROUP_ALL: GroupMessageEvent,
}


def build_event(event_key: str, data: Dict[str, Any]) -> DwsEvent:
    """按 EventKey 把扁平 NDJSON 负载构造为事件实例。"""
    event_class = EVENT_CLASSES.get(event_key)
    if event_class is None:
        raise ValueError(f"unsupported event key: {event_key}")
    return type_validate_python(event_class, data)


Event = DwsEvent
"""dws 事件基类别名。"""
