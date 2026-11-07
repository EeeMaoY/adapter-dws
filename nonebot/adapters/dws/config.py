from typing import List, Optional

from pydantic import BaseModel
from typing_extensions import Literal

from nonebot.compat import PYDANTIC_V2, ConfigDict


class Config(BaseModel):
    """DWS 适配器配置。

    配置项均以 ``dws_`` 开头，可直接写入 NoneBot 的 ``.env`` 文件。
    """

    if PYDANTIC_V2:  # pragma: pydantic-v2
        model_config = ConfigDict(extra="allow")
    else:  # pragma: pydantic-v1

        class Config:
            extra = "allow"

    dws_exec: str = "dws"
    """dws CLI 可执行文件路径或命令名。"""

    dws_profile: Optional[str] = None
    """dws ``--profile`` 全局参数（如 ``corpId:userId``），缺省使用当前登录账号。"""

    dws_self_id: Optional[str] = None
    """机器人 self_id，缺省时从登录账号信息中提取。"""

    dws_command_timeout: int = 60
    """单次 dws 命令执行超时（秒）。"""

    dws_ready_timeout: int = 60
    """等待事件订阅 ready 标记的超时（秒）。"""

    dws_listen_kinds: List[Literal["direct", "group"]] = ["direct", "group"]
    """监听范围：``direct`` 全部单聊消息，``group`` 全部群聊消息。"""

    dws_restart_max: int = 5
    """监听进程连续异常后的最大重启次数（指数退避）。"""

    dws_auto_confirm: bool = True
    """写命令自动携带 ``--yes`` 通过 dws 运行时确认门。"""

    dws_as_bot: bool = False
    """以企业机器人身份发送消息（需配合 ``dws_robot_code``）。"""

    dws_robot_code: Optional[str] = None
    """企业机器人 Code，``dws_as_bot`` 为真时必填。"""

    dws_ai_tag: bool = False
    """发送的消息是否带 AI 角标（dws 默认为 true，此处默认关闭）。"""

    dws_wait_delivery: bool = False
    """发送后是否等待投递回执。"""

    dws_group_is_tome: bool = False
    """群消息事件是否一律视为与机器人相关（dws 事件负载不含 @ 信息）。"""
