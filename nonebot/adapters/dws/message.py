from typing import Any, Dict, Iterable, List, Optional, Type

from nonebot.adapters import Message as BaseMessage
from nonebot.adapters import MessageSegment as BaseMessageSegment


class MessageSegment(BaseMessageSegment["Message"]):
    """dws 消息段。

    - ``text``: ``{"text": str}`` 纯文本
    - ``markdown``: ``{"content": str}`` Markdown 正文
    - ``at``: ``{"open_dingtalk_id": str}`` @ 指定成员
    - ``at_all``: ``{}`` @ 所有人
    - ``image``: ``{"file": str}`` / ``{"url": str}`` / ``{"media_id": str}`` 图片
    - ``file``: ``{"path": str}`` 本地文件
    - ``reply``: ``{"message_id": str}`` 引用回复
    - ``forward``: ``{"messages": list}`` 合并转发（仅接收）
    - ``contact``: ``{"open_dingtalk_id": str}`` 名片
    - ``share_chat``: ``{"open_conversation_id": str}`` 群邀请
    """

    @classmethod
    def get_message_class(cls) -> Type["Message"]:
        return Message

    def __str__(self) -> str:
        if self.type == "text":
            return self.data.get("text", "")
        if self.type == "markdown":
            return self.data.get("content", "")
        if self.type == "at":
            return f"@{self.data.get('open_dingtalk_id', '')}"
        if self.type == "at_all":
            return "@everyone"
        if self.type == "reply":
            return f"[reply:{self.data.get('message_id', '')}]"
        if self.type == "image":
            source = (
                self.data.get("media_id")
                or self.data.get("url")
                or self.data.get("file")
            )
            return f"[image:{source}]"
        if self.type == "file":
            return f"[file:{self.data.get('path', '')}]"
        if self.type == "forward":
            return f"[forward:{len(self.data.get('messages', []))} messages]"
        if self.type == "contact":
            return f"[contact:{self.data.get('open_dingtalk_id', '')}]"
        if self.type == "share_chat":
            return f"[share_chat:{self.data.get('open_conversation_id', '')}]"
        return f"[unhandled:{self.type}]"

    def is_text(self) -> bool:
        return self.type == "text"

    @classmethod
    def text(cls, content: str) -> "MessageSegment":
        return cls("text", {"text": content})

    @classmethod
    def markdown(cls, content: str) -> "MessageSegment":
        return cls("markdown", {"content": content})

    @classmethod
    def at(cls, open_dingtalk_id: str) -> "MessageSegment":
        return cls("at", {"open_dingtalk_id": open_dingtalk_id})

    @classmethod
    def at_all(cls) -> "MessageSegment":
        return cls("at_all", {})

    @classmethod
    def image(
        cls,
        *,
        file: Optional[str] = None,
        url: Optional[str] = None,
        media_id: Optional[str] = None,
    ) -> "MessageSegment":
        data: Dict[str, str] = {}
        if file is not None:
            data["file"] = file
        if url is not None:
            data["url"] = url
        if media_id is not None:
            data["media_id"] = media_id
        if not data:
            raise ValueError("image segment requires one of file/url/media_id")
        return cls("image", data)

    @classmethod
    def file(cls, path: str) -> "MessageSegment":
        return cls("file", {"path": path})

    @classmethod
    def reply(cls, message_id: str) -> "MessageSegment":
        return cls("reply", {"message_id": message_id})

    @classmethod
    def forward(cls, messages: List[Dict[str, Any]]) -> "MessageSegment":
        return cls("forward", {"messages": messages})

    @classmethod
    def contact(cls, open_dingtalk_id: str) -> "MessageSegment":
        return cls("contact", {"open_dingtalk_id": open_dingtalk_id})

    @classmethod
    def share_chat(cls, open_conversation_id: str) -> "MessageSegment":
        return cls("share_chat", {"open_conversation_id": open_conversation_id})


class Message(BaseMessage[MessageSegment]):
    """dws 消息序列。

    纯文本消息自动构造为单个 ``text`` 段；接收事件中引用回复会前置 ``reply`` 段、
    合并转发会附加 ``forward`` 段。
    """

    @classmethod
    def get_segment_class(cls) -> Type[MessageSegment]:
        return MessageSegment

    @staticmethod
    def _construct(msg: str) -> Iterable[MessageSegment]:
        yield MessageSegment.text(msg)

    def extract_content(self) -> str:
        """提取全部 text 与 markdown 段的正文（不含 @ / 附件等）。"""
        return "".join(
            str(seg) for seg in self if seg.type in ("text", "markdown")
        )

    def has_reply(self) -> bool:
        return any(seg.type == "reply" for seg in self)
