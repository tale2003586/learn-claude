# Telegram Gateway

## 目标

taleclaw 现在可以通过 Telegram Bot 接收私聊文字消息，并把消息交给现有 AgentLoop。
Telegram 只是一个渠道适配器，不会侵入 Pipeline、工具插件或记忆系统。

```text
Telegram Bot API
      |
telegram-worker
      |
TelegramGateway
      |
InboundMessage -> MessageBus -> AgentLoop -> OutboundMessage
```

## 第一阶段范围

当前已支持：

- Telegram Bot API `getUpdates` 长轮询
- 私聊文字消息
- 隔离用户身份
- 用户白名单
- 显式账号映射和管理员角色
- `/start`
- `/new`
- `/status`
- AI 回复超长文本自动拆分
- 长轮询 offset 持久化
- Docker Compose 按需启动

当前没有开放：

- 群聊
- 图片、语音和文件
- Web 页面中的 Telegram 绑定验证码
- scheduler 日报主动推送到 Telegram
- webhook

这些能力可以在现有 Gateway 层继续增加，不需要重写 AgentLoop。

## 代码结构

```text
gateway/base.py                 通用 ChannelAdapter 接口
gateway/telegram/client.py      Telegram Bot API HTTP 客户端
gateway/telegram/identity.py    Telegram 账号白名单和 taleclaw 用户映射
gateway/telegram/store.py       offset 与当前会话状态 SQLite 存储
gateway/telegram/adapter.py     Telegram Update 与内部消息互转
telegram_worker.py              独立 worker 入口
```

运行状态保存在：

```text
.gateway/telegram.db
```

## 创建 Bot

在 Telegram 中打开 `@BotFather`：

```text
/newbot
```

按照提示创建 Bot，保存 Bot Token。Token 等同于机器人密码，不要提交到 Git。

## 环境变量

在服务器项目目录编辑真实 `.env`：

```bash
nano .env
```

最小自用配置：

```env
TELEGRAM_BOT_TOKEN=替换为BotFather提供的Token
TELEGRAM_ALLOWED_USER_IDS=123456789
```

首次不知道自己的 Telegram user ID 时，可以先启动 worker，向 Bot 发送任意文字。未授权提示
会返回当前 Telegram user ID。把该数字写入 `.env` 后重启 worker。

### 映射已有账号

普通映射：

```env
TELEGRAM_USER_MAP={"123456789":{"user_id":"alice","role":"user"}}
```

自用管理员映射：

```env
TELEGRAM_USER_MAP={"123456789":{"user_id":"admin","role":"admin"}}
```

映射后的 Telegram 消息会使用对应账号自己的：

```text
.users/<user-id>/storage/
.users/<user-id>/memory/
```

只有显式配置 `"role":"admin"` 才会获得 Coding、`bash` 和 scheduler 等高权限。

### 开放普通账号

```env
TELEGRAM_ALLOWED_USER_IDS=*
```

这会允许任意 Telegram 用户使用 Bot，但每个人只会获得独立的普通账号：

```text
telegram_<Telegram user ID>
```

公网 Bot 建议优先使用白名单，不建议一开始就配置 `*`。

### 其他配置

```env
TELEGRAM_POLL_TIMEOUT=30
TELEGRAM_RETRY_DELAY=3
TELEGRAM_PROXY_URL=
```

| 变量 | 含义 |
| --- | --- |
| `TELEGRAM_POLL_TIMEOUT` | `getUpdates` 长轮询等待秒数 |
| `TELEGRAM_RETRY_DELAY` | 网络异常后的重试等待秒数 |
| `TELEGRAM_PROXY_URL` | 可选的 Telegram HTTP 代理，例如 `http://127.0.0.1:7890` |

中国大陆服务器通常无法直接访问 Telegram API。如果日志持续出现网络错误，需要为服务器提供
可用的 HTTP 代理，并设置 `TELEGRAM_PROXY_URL`。Docker 容器中的 `127.0.0.1` 指向容器自身；
代理运行在宿主机时，应使用容器可访问的宿主机地址。

## Docker 启动

`telegram-worker` 使用 Compose profile，默认不会跟随旧部署自动启动。配置 `.env` 后执行：

```bash
sudo docker compose --profile telegram up -d --build telegram-worker
```

查看日志：

```bash
sudo docker compose logs -f --tail=200 telegram-worker
```

停止 Telegram 接入：

```bash
sudo docker compose --profile telegram stop telegram-worker
```

更新代码后重新创建：

```bash
git pull
sudo docker compose --profile telegram up -d --build telegram-worker
```

## 不使用 Docker

```bash
python telegram_worker.py
```

## Bot 命令

```text
/start   显示接入状态和命令
/new     创建一个新的 Telegram 对话
/status  显示当前绑定用户、角色和会话编号
```

直接发送普通文字时，TelegramGateway 会构造：

```text
InboundMessage(
  channel="telegram",
  chat_id="tg_<chat-id>_<conversation-id>_<user-id>",
  metadata={
    "user_id": "...",
    "user_role": "...",
    "gateway": "telegram"
  }
)
```

因此 Web、Telegram 和 CLI 会话互不混淆，但映射到同一个 taleclaw 用户时可以使用同一套隔离记忆
和 storage。

## 安全边界

- 默认拒绝未授权账号。
- 当前只处理 `chat.type=private` 的消息。
- 群聊不会进入 AgentLoop，避免多人共享会话时错误继承权限。
- Bot Token 只放在 `.env`。
- 管理员权限必须在 `TELEGRAM_USER_MAP` 中显式授予。
- Telegram API 调用异常不会在错误信息中打印 Bot Token。

## 后续方向

### Webhook

有 HTTPS 域名后，可以增加：

```text
POST /gateway/telegram/webhook
```

并使用 Telegram 的 `secret_token` 校验请求来源。长轮询和 webhook 应二选一。

### scheduler 主动推送

建议增加持久化 outbox：

```text
scheduler-worker -> outbox -> telegram-worker -> sendMessage
```

不要依赖当前进程内 MessageBus 跨容器直接发送。

### Web 绑定

多用户公开部署时，可以在 Web 端生成一次性验证码：

```text
/bind ABCD-1234
```

由 Gateway 将 Telegram 账号与已经登录的 Web 账号绑定。

## 验证

```bash
python -m unittest discover -s tests -p 'test_telegram_gateway.py' -v
python -m py_compile telegram_worker.py gateway/base.py gateway/telegram/*.py
docker compose config --quiet
git diff --check
```

本次改动完成后，完整单元测试结果为：

```text
Ran 121 tests
OK
```

## 官方参考

- [`getUpdates`](https://core.telegram.org/bots/api#getupdates)：长轮询、`offset` 和 `timeout`
- [`sendMessage`](https://core.telegram.org/bots/api#sendmessage)：发送文本消息与长度限制
- [`setWebhook`](https://core.telegram.org/bots/api#setwebhook)：后续 webhook 与 `secret_token`
