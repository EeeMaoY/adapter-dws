import asyncio
import json
import re
import signal
from contextlib import suppress
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from nonebot.adapters import Adapter as BaseAdapter
from nonebot.drivers import Driver
from nonebot.exception import ApiNotAvailable
from nonebot.log import logger
from nonebot.message import handle_event
from nonebot.typing import overrides
from nonebot.utils import escape_tag

from .bot import Bot
from .config import Config
from .event import (
    EVENT_KEY_GROUP_ALL,
    EVENT_KEY_O2O_ALL,
    GroupMessageEvent,
    MessageEvent,
    PrivateMessageEvent,
    build_event,
)
from .exception import DwsAdapterError, ProcessError, ReadyTimeoutError
from .message import Message
from .process import CREATE_NO_WINDOW, DWSExecutor

READY_RE = re.compile(r"\[event\]\s*ready")
"""dws 订阅就绪标记。"""

NON_RETRYABLE_MARKERS = (
    "unknown flag",
    "unknown_flag",
    "unknown command",
    "unknown_command",
)
"""dws 参数校验类错误为确定性失败，重试无意义。"""

KIND_TO_FLAG: Dict[str, str] = {
    "direct": "all-direct",
    "group": "all-group",
}
KIND_TO_EVENT_KEY: Dict[str, str] = {
    "direct": EVENT_KEY_O2O_ALL,
    "group": EVENT_KEY_GROUP_ALL,
}

QUERY_APIS: Dict[str, List[str]] = {
    "get_chat_messages": ["chat", "+chat-messages"],
    "search_messages": ["chat", "+search-msg"],
    "get_conversation_list": ["chat", "+conversation-list"],
    "recall_message": ["chat", "+messages-recall"],
    "forward_message": ["chat", "+messages-forward"],
    "download_media": ["chat", "message", "download-media"],
}
WRITE_APIS = {"recall_message", "forward_message"}

BODY_STDIN_THRESHOLD = 6000
"""超过该长度的正文改走 ``--body-stdin``（dws 上限 256 KiB）。"""

ACCOUNT_KEYS = (
    "userId",
    "user_id",
    "openDingTalkId",
    "open_dingtalk_id",
    "staffId",
    "id",
)


class Adapter(BaseAdapter):
    """钉钉 DWS 协议适配器。

    事件接收：``dws event +listen-im`` 长连接子进程（NDJSON）。
    消息发送：``dws chat +messages-send`` 等命令（一次调用一个子进程）。
    """

    dws_config: Config
    executor: DWSExecutor

    def __init__(self, driver: Driver, **kwargs: Any):
        super().__init__(driver, **kwargs)
        self.dws_config = Config(**driver.config.dict())
        self.executor = DWSExecutor(self.dws_config)
        self.bot: Optional[Bot] = None
        self._listen_tasks: Dict[str, asyncio.Task] = {}
        self._stopping = False
        self.on_ready(self._startup)
        self.driver.on_shutdown(self._shutdown)

    @classmethod
    @overrides(BaseAdapter)
    def get_name(cls) -> str:
        return "DingTalk"

    # ------------------------------------------------------------------
    # 生命周期

    async def _startup(self) -> None:
        try:
            status = await self.executor.auth_status()
        except DwsAdapterError as e:
            logger.error(
                f"DWS | dws auth status 执行失败: {e!r}；"
                f"请确认已安装 dws CLI（npm install -g dingtalk-workspace-cli）"
            )
            return
        if not (isinstance(status, dict) and status.get("authenticated")):
            logger.error(
                "DWS | dws 未登录，请先执行 `dws auth login --recommend` "
                "完成钉钉登录后重启 NoneBot"
            )
            await self._diagnose_unauthenticated()
            return

        self_id = self.dws_config.dws_self_id or await self._resolve_self_id(
            status
        )
        if self.dws_config.dws_as_bot and not self.dws_config.dws_robot_code:
            logger.warning(
                "DWS | 已启用 dws_as_bot 但未配置 dws_robot_code，"
                "发送将回退为用户身份"
            )

        bot = Bot(self, self_id)
        self.bot = bot
        self.bot_connect(bot)
        logger.opt(colors=True).info(
            f"DWS | Bot <y>{escape_tag(self_id)}</y> 已连接"
        )

        for kind in self.dws_config.dws_listen_kinds:
            task = asyncio.create_task(self._listen_loop(bot, kind))
            self._listen_tasks[kind] = task

    async def _resolve_self_id(self, auth_status: Any) -> str:
        for source in (auth_status, await self._safe_profile_list()):
            account = self._extract_account(source, require_current=True)
            if account:
                return account
        for source in (auth_status, await self._safe_profile_list()):
            account = self._extract_account(source, require_current=False)
            if account:
                return account
        logger.warning(
            "DWS | 无法从登录账号提取 self_id，回退为 dingtalk-dws"
            "（可通过 dws_self_id 配置指定）"
        )
        return "dingtalk-dws"

    async def _safe_profile_list(self) -> Any:
        try:
            return await self.executor.profile_list()
        except DwsAdapterError as e:
            logger.debug(f"DWS | profile list 失败: {e!r}")
            return None

    async def _diagnose_unauthenticated(self) -> None:
        """auth status 报未登录时的辅助诊断。

        dws 需要在 ``~/.dws`` 获取文件锁；锁获取失败时 auth status
        统一返回"未登录"。常见原因：锁被其他 dws 实例占用，或
        当前进程无权写入（低完整性级别目录标签）。此处探测区分。
        """
        try:
            await self.executor.profile_list()
        except DwsAdapterError as e:
            detail = str(e)
            if "Access is denied" in detail or "拒绝访问" in detail or "lock" in detail:
                if self._lock_writable():
                    logger.error(
                        "DWS | 诊断: dws 数据锁获取失败，但锁文件本身可写，"
                        "可能另有 dws 实例正在运行（如之前启动的 bot 或"
                        "其他终端会话），请停止其他 dws 进程后重试"
                    )
                else:
                    logger.error(
                        "DWS | 诊断: 当前进程无法写入凭据目录（.data.lock "
                        "权限被拒）。进程可能运行在低完整性级别（目录带 "
                        "Mandatory Label），请用管理员终端执行: "
                        'icacls <项目目录> /setintegritylevel "(OI)(CI)M" /T /C '
                        "后重试；详见 https://learn.microsoft.com/windows/"
                        "security/identity-protection/"
                        "mandatory-integrity-control"
                    )
            else:
                logger.debug(f"DWS | 诊断: profile list 错误: {detail}")
        except Exception as e:
            logger.debug(f"DWS | 诊断异常: {e!r}")

    @staticmethod
    def _lock_writable() -> bool:
        """探测当前进程能否写入 dws 数据锁文件。"""
        lock_path = Path.home() / ".dws" / ".data.lock"
        try:
            with open(lock_path, "r+b"):
                return True
        except OSError:
            return False

    @staticmethod
    def _extract_account(value: Any, require_current: bool) -> Optional[str]:
        """在返回结构中递归查找账号 ID。"""
        if isinstance(value, dict):
            if not require_current or value.get("isOrgCurrent") in (
                True,
                "true",
            ):
                for key in ACCOUNT_KEYS:
                    account = value.get(key)
                    if isinstance(account, str) and account:
                        return account
            for child in value.values():
                account = Adapter._extract_account(child, require_current)
                if account:
                    return account
        elif isinstance(value, list):
            for item in value:
                account = Adapter._extract_account(item, require_current)
                if account:
                    return account
        return None

    async def _shutdown(self) -> None:
        self._stopping = True
        tasks = list(self._listen_tasks.values())
        for task in tasks:
            task.cancel()
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)
        self._listen_tasks.clear()
        if self.bot is not None:
            self.bot_disconnect(self.bot)
            self.bot = None

    # ------------------------------------------------------------------
    # 事件监听

    async def _listen_loop(self, bot: Bot, kind: str) -> None:
        attempts = 0
        while not self._stopping:
            try:
                await self._listen_once(bot, kind)
            except asyncio.CancelledError:
                raise
            except Exception as e:
                message = str(e)
                if any(marker in message for marker in NON_RETRYABLE_MARKERS):
                    logger.error(
                        f"DWS | listen {kind} 参数被 dws 拒绝，不再重试: "
                        f"{escape_tag(message)}"
                    )
                    return
                logger.opt(exception=e).warning(
                    f"DWS | listen {kind} 退出: {e!r}"
                )
            attempts += 1
            if attempts > self.dws_config.dws_restart_max:
                logger.error(
                    f"DWS | listen {kind} 连续失败 {attempts} 次，停止重启；"
                    f"dws 对订阅失败有本地保护（terminal hold），"
                    f"请排查后重启 NoneBot"
                )
                return
            delay = min(5 * 2 ** (attempts - 1), 600)
            logger.info(
                f"DWS | listen {kind} 将在 {delay}s 后重启"
                f"（第 {attempts}/{self.dws_config.dws_restart_max} 次）"
            )
            await asyncio.sleep(delay)

    async def _listen_once(self, bot: Bot, kind: str) -> None:
        # +listen-im 的 NDJSON 输出本身即扁平事件流，无 --flatten flag
        args = [
            *self.executor._global_args(),
            "event",
            "+listen-im",
            "--kind",
            KIND_TO_FLAG[kind],
            "--events",
            "message",
            "-f",
            "ndjson",
        ]
        try:
            proc = await asyncio.create_subprocess_exec(
                self.executor.exec_path,
                *args,
                stdin=asyncio.subprocess.PIPE,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                creationflags=CREATE_NO_WINDOW,
            )
        except OSError as e:
            raise ProcessError(
                -1,
                f"无法启动 dws listen（{self.executor.exec_path}）: {e}",
            ) from e
        logger.debug(f"DWS | listen {kind} pid={proc.pid}")
        try:
            await self._wait_ready(proc, kind)
            drain_task = asyncio.create_task(self._drain_stderr(proc, kind))
            try:
                await self._consume_stdout(bot, kind, proc)
            finally:
                drain_task.cancel()
                with suppress(BaseException):
                    await drain_task
        finally:
            await self._stop_process(proc, kind)

    async def _wait_ready(self, proc: asyncio.subprocess.Process, kind: str) -> None:
        assert proc.stderr is not None
        recent: List[str] = []

        async def _read_until_ready() -> None:
            while True:
                line = await proc.stderr.readline()
                if not line:
                    await proc.wait()
                    detail = " ".join(recent[-40:]).strip()
                    raise ProcessError(
                        proc.returncode or -1,
                        detail or f"listen {kind} 在 ready 前退出（无诊断输出）",
                    )
                text = line.decode("utf-8", errors="replace").strip()
                if text:
                    recent.append(text)
                    logger.debug(f"DWS | [listen {kind}] {text}")
                if READY_RE.search(text):
                    logger.info(f"DWS | listen {kind} ready: {text}")
                    return

        try:
            await asyncio.wait_for(
                _read_until_ready(), self.dws_config.dws_ready_timeout
            )
        except asyncio.TimeoutError:
            raise ReadyTimeoutError(
                f"等待 listen {kind} ready 超时"
                f"（{self.dws_config.dws_ready_timeout}s）"
            ) from None

    async def _drain_stderr(
        self, proc: asyncio.subprocess.Process, kind: str
    ) -> None:
        assert proc.stderr is not None
        while True:
            line = await proc.stderr.readline()
            if not line:
                return
            text = line.decode("utf-8", errors="replace").strip()
            if text:
                logger.debug(f"DWS | [listen {kind}] {text}")

    async def _consume_stdout(
        self, bot: Bot, kind: str, proc: asyncio.subprocess.Process
    ) -> None:
        assert proc.stdout is not None
        while True:
            line = await proc.stdout.readline()
            if not line:
                return
            text = line.decode("utf-8", errors="replace").strip()
            if not text:
                continue
            try:
                data = json.loads(text)
            except json.JSONDecodeError:
                logger.warning(
                    f"DWS | listen {kind} 忽略非 NDJSON 行: "
                    f"{escape_tag(text[:120])}"
                )
                continue
            if not isinstance(data, dict):
                continue
            event_key = KIND_TO_EVENT_KEY[kind]
            remote_key = data.get("type") or event_key
            if remote_key != event_key:
                logger.debug(
                    f"DWS | listen {kind} 收到非预期事件 {remote_key}，忽略"
                )
                continue
            try:
                event = build_event(event_key, data)
            except Exception as e:
                logger.opt(exception=e).warning(
                    f"DWS | listen {kind} 事件解析失败: "
                    f"{escape_tag(text[:200])}"
                )
                continue
            if isinstance(event, GroupMessageEvent):
                event._group_tome = self.dws_config.dws_group_is_tome
            try:
                await handle_event(bot, event)
            except Exception as e:
                logger.opt(exception=e).error(
                    f"DWS | 事件处理失败: {escape_tag(str(event))}"
                )

    @staticmethod
    async def _stop_process(
        proc: asyncio.subprocess.Process, kind: str
    ) -> None:
        """干净退出 dws 监听进程（会自动取消本次订阅），避免 kill -9。"""
        if proc.returncode is not None:
            return
        # 先关闭 stdin 触发可能的 stdin EOF 退出路径
        if proc.stdin is not None:
            with suppress(Exception):
                proc.stdin.close()
        graceful = False
        try:
            if proc.returncode is None:
                if hasattr(signal, "CTRL_BREAK_EVENT"):
                    import os

                    os.kill(proc.pid, signal.CTRL_BREAK_EVENT)
                else:
                    proc.terminate()
                graceful = True
        except ProcessLookupError:
            return
        except Exception as e:
            logger.debug(f"DWS | listen {kind} 发送退出信号失败: {e!r}")
        if graceful:
            try:
                await asyncio.wait_for(proc.wait(), 5)
            except asyncio.TimeoutError:
                logger.debug(
                    f"DWS | listen {kind} 未在宽限期内退出，terminate"
                )
            else:
                logger.debug(f"DWS | listen {kind} 已干净退出")
                return
        try:
            proc.terminate()
            await asyncio.wait_for(proc.wait(), 5)
        except asyncio.TimeoutError:
            logger.warning(
                f"DWS | listen {kind} terminate 后仍未退出，放弃等待"
            )
        except ProcessLookupError:
            pass

    # ------------------------------------------------------------------
    # API 路由

    @overrides(BaseAdapter)
    async def _call_api(self, bot: Bot, api: str, **data: Any) -> Any:
        if api == "send_message":
            return await self._handle_send(**data)
        if api == "reply_message":
            return await self._handle_reply(**data)
        if api == "dws":
            return await self._handle_raw(**data)
        if api in QUERY_APIS:
            confirm = api in WRITE_APIS
            args = [*QUERY_APIS[api], *self._kwargs_to_args(data)]
            return await self.executor.run(args, confirm=confirm)
        raise ApiNotAvailable(f"unsupported api: {api}")

    async def _handle_raw(self, **data: Any) -> Any:
        args = data.get("args")
        if not isinstance(args, (list, tuple)) or not all(
            isinstance(item, str) for item in args
        ):
            raise ValueError("api 'dws' 需要 args: list[str]")
        return await self.executor.run(
            list(args),
            input_text=data.get("input_text"),
            timeout=data.get("timeout"),
            cwd=data.get("cwd"),
            confirm=bool(data.get("confirm")),
        )

    async def _handle_send(
        self,
        message: Any,
        event: Optional[MessageEvent] = None,
        group_id: Optional[str] = None,
        user_id: Optional[str] = None,
        **kwargs: Any,
    ) -> Any:
        msg = message if isinstance(message, Message) else Message(message)
        reply_seg = next((seg for seg in msg if seg.type == "reply"), None)
        if reply_seg is not None:
            message_id = str(reply_seg.data.get("message_id", ""))
            if not message_id:
                raise ValueError("reply 段缺少 message_id")
            return await self._handle_reply(
                message_id=message_id, message=msg, event=event, **kwargs
            )

        target_group, target_user = self._resolve_target(
            event, group_id, user_id
        )
        invocations = self._build_send_invocations(
            msg, target_group, target_user
        )
        results = [await self.executor.run(**inv) for inv in invocations]
        return results[-1] if len(results) == 1 else results

    async def _handle_reply(
        self,
        message_id: str,
        message: Any,
        event: Optional[MessageEvent] = None,
        group_id: Optional[str] = None,
        **kwargs: Any,
    ) -> Any:
        msg = message if isinstance(message, Message) else Message(message)
        content = msg.extract_content()
        if not content:
            raise ValueError("回复消息需要文本内容")

        identity_args = self._identity_args()
        args = ["chat", "+messages-reply", *identity_args]
        if group_id:
            args += ["--group", group_id]
        elif isinstance(event, GroupMessageEvent):
            args += ["--group", event.conversation_id]
        args += ["--ref-msg-id", message_id]
        stdin_text: Optional[str] = None
        if len(content) > BODY_STDIN_THRESHOLD:
            args += ["--content", "-", "--body-stdin=true"]
            stdin_text = content
        else:
            args += ["--content", content]
        args += self._at_args(msg)
        args += self._misc_args()
        return await self.executor.run(
            args, input_text=stdin_text, confirm=True
        )

    @staticmethod
    def _resolve_target(
        event: Optional[MessageEvent],
        group_id: Optional[str],
        user_id: Optional[str],
    ) -> Tuple[Optional[str], Optional[str]]:
        if group_id:
            return group_id, None
        if user_id:
            return None, user_id
        if isinstance(event, GroupMessageEvent):
            return event.conversation_id, None
        if isinstance(event, PrivateMessageEvent):
            return None, event.sender_open_dingtalk_id
        raise ValueError("send_message 需要 group_id / user_id 或消息事件")

    def _identity_args(self) -> List[str]:
        if self.dws_config.dws_as_bot and self.dws_config.dws_robot_code:
            return [
                "--as",
                "bot",
                "--robot-code",
                self.dws_config.dws_robot_code,
            ]
        return ["--as", "user"]

    @staticmethod
    def _at_args(msg: Message) -> List[str]:
        at_ids = [
            str(seg.data["open_dingtalk_id"])
            for seg in msg
            if seg.type == "at" and seg.data.get("open_dingtalk_id")
        ]
        args: List[str] = []
        if at_ids:
            args += ["--at-open-dingtalk-ids", ",".join(at_ids)]
        if any(seg.type == "at_all" for seg in msg):
            args += ["--at-all=true"]
        return args

    def _misc_args(self) -> List[str]:
        args = [
            f"--ai-tag={'true' if self.dws_config.dws_ai_tag else 'false'}"
        ]
        if self.dws_config.dws_wait_delivery:
            args += ["--wait-delivery=true"]
        return args

    def _build_send_invocations(
        self,
        msg: Message,
        target_group: Optional[str],
        target_user: Optional[str],
    ) -> List[Dict[str, Any]]:
        as_bot = bool(
            self.dws_config.dws_as_bot and self.dws_config.dws_robot_code
        )
        if target_group:
            target_args = ["--group", target_group]
        elif target_user:
            # bot 批量单聊目标使用复数参数
            target_args = (
                ["--open-dingtalk-ids", target_user]
                if as_bot
                else ["--open-dingtalk-id", target_user]
            )
        else:
            raise ValueError("无法确定发送目标")

        base = [
            "chat",
            "+messages-send",
            *self._identity_args(),
            *target_args,
            *self._at_args(msg),
            *self._misc_args(),
        ]

        invocations: List[Dict[str, Any]] = []
        for seg in msg:
            invocation = self._build_content_invocation(
                seg, base, as_bot
            )
            if invocation is not None:
                invocations.append(invocation)
        if not invocations:
            raise ValueError("消息中没有可发送内容")
        return invocations

    def _build_content_invocation(
        self, seg: Any, base: List[str], as_bot: bool
    ) -> Optional[Dict[str, Any]]:
        if seg.type == "text":
            content = str(seg.data.get("text", ""))
            if not content:
                return None
            if len(content) > BODY_STDIN_THRESHOLD:
                return {
                    "args": [*base, "--text", "-", "--body-stdin=true"],
                    "input_text": content,
                    "confirm": True,
                }
            return {"args": [*base, "--text", content], "confirm": True}
        if seg.type == "markdown":
            content = str(seg.data.get("content", ""))
            if not content:
                return None
            if len(content) > BODY_STDIN_THRESHOLD:
                return {
                    "args": [*base, "--markdown", "-", "--body-stdin=true"],
                    "input_text": content,
                    "confirm": True,
                }
            return {"args": [*base, "--markdown", content], "confirm": True}
        if seg.type == "image":
            media_id = seg.data.get("media_id")
            file = seg.data.get("file")
            url = seg.data.get("url")
            if media_id:
                return {
                    "args": [*base, "--media-id", str(media_id)],
                    "confirm": True,
                }
            if file:
                # user 身份通过 markdown 内联本地图（dws 自动上传）；
                # --file 类参数要求工作目录内相对路径，故以父目录为 cwd
                path = Path(str(file))
                return {
                    "args": [*base, "--markdown", f"![image]({path.name})"],
                    "cwd": str(path.parent),
                    "confirm": True,
                }
            if url:
                if as_bot:
                    return {
                        "args": [*base, "--image-url", str(url)],
                        "confirm": True,
                    }
                return {
                    "args": [*base, "--markdown", f"![image]({url})"],
                    "confirm": True,
                }
        if seg.type == "file":
            path = Path(str(seg.data.get("path", "")))
            if not str(seg.data.get("path", "")):
                return None
            return {
                "args": [*base, "--file", path.name],
                "cwd": str(path.parent),
                "confirm": True,
            }
        if seg.type == "contact":
            contact_id = str(seg.data.get("open_dingtalk_id", ""))
            if contact_id:
                return {
                    "args": [*base, "--contact-id", contact_id],
                    "confirm": True,
                }
        if seg.type == "share_chat":
            share_id = str(seg.data.get("open_conversation_id", ""))
            if share_id:
                return {
                    "args": [*base, "--share-chat-id", share_id],
                    "confirm": True,
                }
        # reply / at / at_all / forward 段不在此处发送
        return None

    @staticmethod
    def _kwargs_to_args(data: Dict[str, Any]) -> List[str]:
        """把 kwargs 转为 dws 命令 flag（下划线转连字符）。"""
        args: List[str] = []
        for key, value in data.items():
            if value is None or key in ("message", "event"):
                continue
            flag = "--" + key.replace("_", "-")
            if isinstance(value, bool):
                args.append(f"{flag}={str(value).lower()}")
            elif isinstance(value, (list, tuple)):
                args += [flag, ",".join(str(item) for item in value)]
            else:
                args += [flag, str(value)]
        return args
