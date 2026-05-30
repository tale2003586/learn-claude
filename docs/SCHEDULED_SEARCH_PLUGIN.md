# 定时网络搜索插件改动记录

## 一、目标

本次完成定时搜索与受控 Workflow：

```text
CLI 创建每日搜索计划
  -> 独立 worker 常驻
  -> 每天指定时间调用 Tavily
  -> 可选调用 LLM 生成分析
  -> Markdown 报告写入 storage/reports/
  -> CLI 查询最近执行结果
```

该功能不依赖 Web 页面。CLI 退出后，只要 `scheduler_worker.py` 或 Docker 中的
`scheduler-worker` 服务仍在运行，计划就会继续执行。

## 二、CLI 工具

新增插件：

```text
plugins/scheduler/
```

插件提供以下常驻工具：

```text
schedule_create
schedule_create_workflow
schedule_list
schedule_delete
schedule_run_now
schedule_results
```

可以在 CLI 中直接说：

```text
每天早上 8 点搜索最近一天的 AI Agent 新闻，保存为日报。
```

模型会调用：

```text
schedule_create(
  name="daily-ai-agent-news",
  query="latest AI Agent news",
  hour=8,
  minute=0,
  timezone="Asia/Shanghai",
  topic="news",
  time_range="day"
)
```

如果你要求 AI 分析，模型应改用：

```text
schedule_create_workflow(
  name="daily-ai-agent-digest",
  hour=8,
  minute=0,
  timezone="Asia/Shanghai",
  steps=[
    {
      "type": "web_search",
      "query": "latest AI Agent news",
      "topic": "news",
      "time_range": "day",
      "max_results": 8
    },
    {
      "type": "llm_analyze",
      "prompt": "筛选重要资料，分析技术趋势、潜在影响和推荐阅读，保留来源链接。"
    },
    {
      "type": "write_report",
      "title": "每日 AI Agent 日报"
    }
  ]
)
```

## 三、受控 Workflow

AI 可以规划执行链路，但不能在后台执行任意命令。允许的步骤只有：

```text
web_search    调用 Tavily，可出现多个，但必须位于分析步骤前
llm_analyze   调用主模型生成 Markdown 分析，最多出现一次
write_report  保存日报，必须是最后一步
```

限制：

```text
每个 Workflow 包含 2 到 6 个步骤
必须至少有一个 web_search
必须以 write_report 结尾
不允许 bash、文件编辑或任意工具调用
搜索 query 最多 600 字符
分析 prompt 最多 4000 字符
```

当 LLM 分析失败时，原始搜索资料仍然会保存，执行状态记录为：

```text
partial_success
```

## 四、计划与执行记录

新增 SQLite：

```text
.scheduler/schedules.db
```

包含两张表：

```text
schedules      当前有效计划、执行时间、最近状态
schedule_runs  每次执行记录、报告路径或错误信息
```

`schedules` 表新增 `workflow_json`。旧版简单任务不需要手动迁移，读取时会自动映射为：

```text
web_search -> write_report
```

`.scheduler/` 已加入 `.gitignore` 和 `.dockerignore`。服务器计划不会上传到 Git。

## 五、报告文件

成功执行后生成：

```text
storage/reports/<timestamp>-schedule-<id>-run-<run-id>-<name>.md
```

报告包括：

```text
生成时间
计划 ID
搜索 query
topic 与 time_range
搜索结果标题
来源 URL
相关片段
相关性评分
Workflow JSON
可选 AI 分析
```

简单任务只保存 Tavily 返回的检索结果。Workflow 任务可以增加 `llm_analyze`，生成日报分析。
无论是否启用分析，来源 URL 和原始片段都会保留。

## 六、Worker

新增：

```text
scheduler_worker.py
```

Worker 使用 APScheduler `BlockingScheduler` 和 `CronTrigger`。计划真源仍然是
`.scheduler/schedules.db`。Worker 默认每 30 秒同步一次计划，因此通过 CLI 新增或删除
计划后，不需要重启 worker。

配置：

```bash
SCHEDULER_TIMEZONE=Asia/Shanghai
SCHEDULER_RECONCILE_SECONDS=30
SCHEDULER_ANALYSIS_MODEL=
SCHEDULER_ANALYSIS_MAX_TOKENS=1800
```

`SCHEDULER_ANALYSIS_MODEL` 留空时复用主模型。

## 七、本地运行

安装新增依赖：

```bash
source .venv/bin/activate
pip install -r requirements.txt
```

终端一运行 CLI：

```bash
python cli.py
```

终端二运行 worker：

```bash
python scheduler_worker.py
```

CLI 中测试：

```text
创建一个每天早上 8 点执行的任务，搜索最近一天的 AI Agent 新闻。
创建一个每天早上 8 点执行的 AI 日报任务：搜索最近一天的 AI Agent 新闻，分析趋势和影响，保留来源链接。
列出当前定时任务。
立即执行定时任务 1。
读取定时任务 1 最近一次报告。
```

## 八、Docker 部署

`docker-compose.yml` 新增：

```text
scheduler-worker
```

更新服务器后执行：

```bash
sudo docker compose up -d --build
sudo docker compose ps
sudo docker compose logs -f scheduler-worker
```

应看到：

```text
taleclaw scheduler worker started; reconciling every 30s
```

## 九、新增与修改文件

新增：

```text
plugins/web_search/client.py
plugins/scheduler/__init__.py
plugins/scheduler/plugin.py
plugins/scheduler/store.py
plugins/scheduler/reports.py
plugins/scheduler/workflow.py
scheduler_worker.py
tests/test_scheduler_plugin.py
docs/SCHEDULED_SEARCH_PLUGIN.md
```

修改：

```text
plugins/web_search/plugin.py
core/bootstrap.py
requirements.txt
docker-compose.yml
.env.example
.gitignore
.dockerignore
```

## 十、验证

```bash
python3 -B -m unittest discover -s tests -v
python3 -B -m py_compile \
  plugins/web_search/client.py \
  plugins/web_search/plugin.py \
  plugins/scheduler/store.py \
  plugins/scheduler/reports.py \
  plugins/scheduler/workflow.py \
  plugins/scheduler/plugin.py \
  scheduler_worker.py
git diff --check
```

## 十一、受审批定时 Agent 的数据库基础

在现有受控 Workflow 之外，项目已经支持受审批的自主定时 Agent。
自主任务会先规划并审计工具，再由隔离 TaskSession 执行批准范围内的能力。

详细记录：

```text
docs/SCHEDULED_AGENT_STORAGE_FOUNDATION.md
docs/SCHEDULED_AGENT_PLANNING_AUDIT.md
docs/SCHEDULED_AGENT_AUTONOMOUS_EXECUTION.md
```
