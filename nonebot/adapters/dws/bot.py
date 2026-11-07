from typing import TYPE_CHECKING, Any, Union

from nonebot.adapters import Bot as BaseBot
from nonebot.typing import overrides

from .config import Config
from .event import MessageEvent
from .message import Message, MessageSegment

if TYPE_CHECKING:
    from .adapter import Adapter


class Bot(BaseBot):
    """dws 协议 Bot。

    ``send`` 根据事件类型自动选择发送目标：

    - 私聊事件：``--open-dingtalk-id <sender_open_dingtalk_id>``
    - 群聊事件：``--group <conversation_id>``
    """

    adapter: "Adapter"

    @property
    def dws_config(self) -> Config:
        """适配器配置。"""
        return self.adapter.dws_config

    @overrides(BaseBot)
    async def send(
        self,
        event: MessageEvent,
        message: Union[str, Message, MessageSegment],
        at_sender: bool = False,
        **kwargs: Any,
    ) -> Any:
        msg = message if isinstance(message, Message) else Message(message)
        if (
            at_sender
            and isinstance(event, MessageEvent)
            and event.sender_open_dingtalk_id
        ):
            msg = MessageSegment.at(event.sender_open_dingtalk_id) + msg
        return await self.call_api("send_message", message=msg, event=event, **kwargs)

    async def send_message(
        self,
        message: Union[str, Message, MessageSegment],
        event: Any = None,
        **kwargs: Any,
    ) -> Any:
        """主动发送消息（不依赖事件）。

        通过 ``group_id`` / ``user_id`` 指定目标；携带 ``reply`` 段时自动
        转为引用回复（``dws chat +messages-reply``）。
        """
        msg = message if isinstance(message, Message) else Message(message)
        return await self.call_api("send_message", message=msg, event=event, **kwargs)

    async def reply_message(
        self,
        message_id: str,
        message: Union[str, Message, MessageSegment],
        **kwargs: Any,
    ) -> Any:
        """引用回复指定消息。"""
        msg = message if isinstance(message, Message) else Message(message)
        return await self.call_api(
            "reply_message", message_id=message_id, message=msg, **kwargs
        )

    async def recall_message(
        self, message_id: str, conversation_id: Any = None, **kwargs: Any
    ) -> Any:
        """撤回消息。"""
        return await self.call_api(
            "recall_message",
            msg_id=message_id,
            conversation_id=conversation_id,
            **kwargs,
        )
