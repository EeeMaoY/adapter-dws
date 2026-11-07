import asyncio
import json
import shutil
import sys
from pathlib import Path
from typing import Any, List, Optional, Sequence

from .config import Config
from .exception import ActionFailed, ProcessError
from .utils import log

CREATE_NO_WINDOW = 0x08000000 if sys.platform == "win32" else 0


def resolve_dws_exec(exec_name: str) -> str:
    """解析 dws 可执行文件的真实路径。

    Windows 下 npm 全局安装的 ``dws`` 仅为 shim（``dws.cmd`` / sh 脚本），
    ``CreateProcess`` 无法直接执行，需定位包内真实二进制 ``vendor/dws.exe``。
    """
    candidate = Path(exec_name)
    if candidate.is_absolute() and candidate.exists():
        return exec_name

    found = shutil.which(exec_name)
    if found:
        path = Path(found)
        if path.suffix.lower() == ".exe":
            return str(path)
        # npm shim：在全局 node_modules 中查找真实二进制
        package_dir = path.parent / "node_modules" / "dingtalk-workspace-cli"
        real = package_dir / "vendor" / "dws.exe"
        if real.exists():
            return str(real)
        for variant in package_dir.glob("vendor/**/dws*.exe"):
            return str(variant)
    # POSIX 下 shim 可直接执行；Windows 下交给调用方报错处理
    return exec_name


class DWSExecutor:
    """dws CLI 子进程执行器。

    一次 API 调用对应一次 ``dws`` 子进程；长期监听进程由 Adapter 单独管理。
    """

    def __init__(self, config: Config):
        self.config = config
        self.exec_path = resolve_dws_exec(config.dws_exec)
        if self.exec_path != config.dws_exec:
            log("DEBUG", f"dws 可执行文件解析: {config.dws_exec} -> {self.exec_path}")

    def _global_args(self) -> List[str]:
        args: List[str] = []
        if self.config.dws_profile:
            args += ["--profile", self.config.dws_profile]
        return args

    async def run(
        self,
        args: Sequence[str],
        *,
        input_text: Optional[str] = None,
        timeout: Optional[float] = None,
        cwd: Optional[str] = None,
        confirm: bool = False,
    ) -> Any:
        """执行一条 dws 命令并解析 JSON 结果。

        参数:
            args: dws 子命令及参数（不含全局 ``--profile``）
            input_text: 写入 stdin 的文本（配合 ``--body-stdin`` 传递长正文）
            timeout: 超时秒数，缺省取 ``dws_command_timeout``
            cwd: 子进程工作目录（``--file`` 等要求工作目录内相对路径）
            confirm: 是否携带 ``--yes`` 通过 dws 确认门
        """
        cmd = [self.exec_path, *self._global_args(), *args]
        if confirm and self.config.dws_auto_confirm and "--yes" not in args:
            cmd.append("--yes")
        if not any(flag in args for flag in ("--format", "-f")):
            cmd += ["--format", "json"]

        log("DEBUG", f"dws exec: {cmd}")
        try:
            proc = await asyncio.create_subprocess_exec(
                *cmd,
                stdin=(
                    asyncio.subprocess.PIPE
                    if input_text is not None
                    else asyncio.subprocess.DEVNULL
                ),
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                cwd=cwd,
                creationflags=CREATE_NO_WINDOW,
            )
        except OSError as e:
            raise ProcessError(
                -1,
                f"无法启动 dws（{self.exec_path}）: {e}；"
                f"请确认已安装 dws CLI（npm install -g dingtalk-workspace-cli）"
                f"或通过 DWS_EXEC 指定可执行文件路径",
            ) from e
        stdin_data = (
            input_text.encode("utf-8") if input_text is not None else None
        )
        try:
            stdout, stderr = await asyncio.wait_for(
                proc.communicate(stdin_data),
                timeout if timeout is not None else self.config.dws_command_timeout,
            )
        except asyncio.TimeoutError:
            proc.kill()
            await proc.wait()
            raise ProcessError(-1, "dws command timeout") from None

        return self._parse_result(proc.returncode, stdout, stderr, list(args))

    def _parse_result(
        self,
        returncode: Optional[int],
        stdout: bytes,
        stderr: bytes,
        args: List[str],
    ) -> Any:
        stdout_str = stdout.decode("utf-8", errors="replace").strip()
        stderr_str = stderr.decode("utf-8", errors="replace").strip()

        data: Any = None
        if stdout_str:
            try:
                data = json.loads(stdout_str)
            except json.JSONDecodeError:
                data = None

        if returncode != 0:
            detail = stderr_str or stdout_str
            if data is not None:
                raise ActionFailed(data, f"command {' '.join(args)} failed: {data!r}")
            raise ProcessError(returncode or -1, detail)

        if isinstance(data, dict) and data.get("ok") is False:
            raise ActionFailed(data)

        if data is not None:
            return data
        return stdout_str or None

    async def auth_status(self) -> Any:
        """查询当前登录状态。"""
        return await self.run(["auth", "status"])

    async def profile_list(self) -> Any:
        """列出全部已登录账号。"""
        return await self.run(["profile", "list"])
