# SQLite 迁移到 PostgreSQL

本文说明运行态关系型数据如何从本地 SQLite 切到 PostgreSQL。

## 迁移范围

已支持 PostgreSQL 的存储包括：

- 会话与消息：`.sessions/sessions.db`
- 被上下文裁掉后的 memory archive：默认同 `.sessions/sessions.db`
- Web 登录账号与浏览器 session：`.users/auth.db`
- Telegram 网关状态、会话映射、outbox：`.gateway/telegram.db`
- 飞书网关去重、会话映射、outbox：`.gateway/feishu.db`
- Trace run 索引和执行路径摘要：默认 `.runs/trace_index.db`

没有迁移到 PostgreSQL 的内容：

- `memory/*.md`、`memory/PENDING.json` 等显式记忆文件仍然保留本地文件形态。
- Qdrant 只负责向量历史，不属于本次关系型数据库迁移范围。
- `.runs`、`.evals`、trace jsonl 仍然是文件输出。
- Trace 的原始事件仍然保留 `trace.jsonl`，PostgreSQL 只存轻量索引与执行路径摘要。

## 依赖

先安装依赖：

```bash
pip install -r requirements.txt
```

PostgreSQL 驱动是 `psycopg[binary]`。如果运行时配置了 PostgreSQL，但没有安装该依赖，会报：

```text
psycopg is required for PostgreSQL DATABASE_URL
```

## 启动本地 PostgreSQL

项目的 `docker-compose.yml` 已包含本地 PostgreSQL：

```bash
docker compose up -d postgres
```

默认连接信息：

```env
POSTGRES_HOST_PORT=55432
DATABASE_URL=postgresql://agent:agent_dev_password@127.0.0.1:55432/agent_console
```

数据目录是 `postgres_data/`，已经加入 `.gitignore` 和 `.dockerignore`。

## 配置方式

最简单的配置是在 `.env` 中设置全局数据库：

```env
DATABASE_URL=postgresql://agent:agent_dev_password@127.0.0.1:55432/agent_console
```

也可以按模块覆盖：

```env
SESSION_DATABASE_URL=
WEB_AUTH_DATABASE_URL=
MEMORY_ARCHIVE_DATABASE_URL=
GATEWAY_DATABASE_URL=
TELEGRAM_DATABASE_URL=
FEISHU_DATABASE_URL=
TRACE_DATABASE_URL=
```

优先级规则：

- 显式传入 `*.db` 路径时继续使用 SQLite，评测和测试临时库不会污染主 PostgreSQL。
- 模块专用环境变量优先于 `DATABASE_URL`。
- 没有配置数据库 URL 时，系统保持原来的 SQLite 默认行为。

## 迁移已有 SQLite 数据

确认 `.env` 中已经设置 `DATABASE_URL`，然后执行：

```bash
python scripts/migrate_sqlite_to_postgres.py --root .
```

也可以显式传入 DSN：

```bash
python scripts/migrate_sqlite_to_postgres.py \
  --root . \
  --database-url postgresql://agent:agent_dev_password@127.0.0.1:5432/agent_console
```

脚本会先创建 PostgreSQL 表，再把本地 SQLite 数据 upsert 进去。重复执行是安全的。

## 回滚到 SQLite

删除或注释 `.env` 中的 `DATABASE_URL` 及模块专用数据库变量即可。旧的 SQLite 文件不会被迁移脚本删除。

```env
DATABASE_URL=
SESSION_DATABASE_URL=
WEB_AUTH_DATABASE_URL=
MEMORY_ARCHIVE_DATABASE_URL=
GATEWAY_DATABASE_URL=
TELEGRAM_DATABASE_URL=
FEISHU_DATABASE_URL=
```

## 快速检查

启动服务前可先检查驱动：

```bash
python -c "import psycopg; print(psycopg.__version__)"
```

迁移后可以用 `psql` 看表：

```bash
psql "$DATABASE_URL" -c "\\dt"
```

关键表：

- `sessions`
- `messages`
- `memory_archive`
- `web_users`
- `web_auth_sessions`
- `telegram_state`
- `telegram_conversations`
- `telegram_outbox`
- `feishu_events`
- `feishu_conversations`
- `feishu_outbox`
- `trace_runs`
- `trace_steps`

## Trace 索引设计

Trace 采用“本地原始日志 + 数据库索引”的方式：

- `trace.jsonl`：完整原始事件，继续写在 run 目录中。
- `trace_runs`：每个 run 一行，记录状态、失败分类、workspace、指标、trace 文件路径。
- `trace_steps`：从 trace summary 提取的执行路径，用于展示“计划 -> 调用工具 -> 完成”。

可以单独关闭 trace 索引：

```env
TRACE_INDEX_ENABLED=0
```

也可以让 trace 使用独立 PostgreSQL 库：

```env
TRACE_DATABASE_URL=postgresql://agent:agent_dev_password@127.0.0.1:5432/agent_console
```

已有 trace 可以回填索引：

```bash
python scripts/index_existing_traces.py
```

只索引某个目录：

```bash
python scripts/index_existing_traces.py --root .runs
```

显式指定索引库：

```bash
python scripts/index_existing_traces.py \
  --database-url postgresql://agent:agent_dev_password@127.0.0.1:5432/agent_console
```
