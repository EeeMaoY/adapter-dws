# NoneBot-Adapter-DWS

基于 [钉钉开放平台 DingTalk CLI（dws）](https://open.dingtalk.com/document/development/dingtalk-cli-performing-tasks-within) 的 [NoneBot2](https://github.com/nonebot/nonebot2) 钉钉协议适配器。

与旧版群机器人 Webhook 回调方案（`nonebot-adapter-ding`）完全不同，本适配器通过本机安装的
`dws` CLI，以**登录用户的个人号身份**接入钉钉：

- **事件接收**：`dws event +listen-im` 长连接子进程，订阅全部单聊 / 全部群聊消息（NDJSON）
- **消息发送**：`dws chat +messages-send` / `+messages-reply` 等命令（JSON 输出）
- 无需公网 IP、无需内网穿透，本地运行即可

## 前置要求

1. Python 3.9+
2. NoneBot2 2.0+
3. 安装并登录 [DWS CLI](https://open.dingtalk.com/document/development/dingtalk-cli-performing-tasks-within)：

```bash
# 安装（npm）
npm install -g dingtalk-workspace-cli

# 登录钉钉账号
dws auth login --recommend
```

## 安装

```bash
poetry add nonebot-adapter-dws
# 或
pip install nonebot-adapter-dws
```

在 `bot.py` 中加载适配器：

```python
nonebot.init()

driver = nonebot.get_driver()
driver.register_adapter("nonebot.adapters.dws", Adapter)
```

或使用 nb-cli 的 `pyproject.toml` 适配器声明：

```toml
[tool.nonebot.adapters]
nonebot-adapter-dws = [
  { name = "钉钉", module_name = "nonebot.adapters.dws" }
]
```

## 配置

| 配置项 | 默认值 | 说明 |
|---|---|---|
| `dws_exec` | `"dws"` | dws 可执行文件路径 |
| `dws_profile` | `None` | dws `--profile` 全局参数，缺省使用当前登录账号 |
| `dws_self_id` | `None` | 机器人 self_id，缺省从登录账号信息提取 |
| `dws_listen_kinds` | `["direct", "group"]` | 监听范围：`direct` 全部单聊，`group` 全部群聊 |
| `dws_ready_timeout` | `60` | 等待事件订阅就绪标记的超时（秒） |
| `dws_command_timeout` | `60` | 单次 dws 命令执行超时（秒） |
| `dws_restart_max` | `5` | 监听进程连续异常后的最大重启次数（指数退避） |
| `dws_auto_confirm` | `True` | 写命令自动携带 `--yes` 通过 dws 确认门 |
| `dws_as_bot` | `False` | 以企业机器人身份发送（需配合 `dws_robot_code`） |
| `dws_robot_code` | `None` | 机器人 Code |
| `dws_ai_tag` | `False` | 发送的消息是否带 AI 角标（dws 默认为 true） |
| `dws_wait_delivery` | `False` | 发送后等待投递回执 |
| `dws_group_is_tome` | `False` | 群消息事件是否一律视为与机器人相关 |

示例 `.env`：

```env
DWS_LISTEN_KINDS=["direct","group"]
DWS_GROUP_IS_TOME=true
```

## 使用

```python
from nonebot import on_command
from nonebot.adapters.dws import MessageEvent

ping = on_command("ping")

@ping.handle()
async def handle(event: MessageEvent):
    await ping.finish("pong")
```

发送消息直接使用 `bot.send()`，适配器会按事件类型自动选择目标：

- 私聊事件 → `--open-dingtalk-id <发送人 openDingTalkId>`
- 群聊事件 → `--group <conversation_id>`

`Message` 支持的消息段：`text`、`markdown`、`at`、`at_all`、`image`、`file`、
`reply`、`forward`（仅接收）、`contact`、`share_chat`。

```python
from nonebot.adapters.dws import MessageSegment

# 文本 + @
await bot.send(event, MessageSegment.text("你好 ") + MessageSegment.at(id))

# 引用回复（携带 reply 段时自动走 dws chat +messages-reply）
await bot.send(event, MessageSegment.reply(event.message_id) + MessageSegment.text("收到"))
```

更多底层能力可通过逃生口直接调用 dws 命令：

```python
result = await bot.call_api("dws", args=["chat", "+chat-messages", "--group", cid])
```

语义化 API：`send_message` / `reply_message` / `recall_message` / `get_chat_messages` /
`search_messages` / `get_conversation_list` / `download_media` / `dws`。

## 已知限制

- 消息正文经由命令行参数 / stdin（超长正文自动走 `--body-stdin`）传递，dws 单次正文上限 256 KiB
- 一次 dws 发送命令只接受一种内容类型，混合内容（文本 + 图片等）会拆分为多次顺序发送
- 已读 / 撤回 / reaction 等 notice 类事件需要指定会话订阅，暂未启用
- dws 事件负载不含 @ 信息与群名称，群消息的 `is_tome()` 由 `dws_group_is_tome` 配置决定
- dws CLI 对本地订阅失败有保护机制（terminal hold），适配器重启用指数退避（5s 起，上限 10min）

## 协议

MIT License
