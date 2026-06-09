# 受审批定时 Agent：自主执行完整改动记录

## 一、完成范围

本次完成受审批定时 Agent 的剩余链路，并保留原有固定 Workflow：

```text
用户描述定时任务
  -> ScheduledTaskPlanner 生成最小工具申请
  -> ToolCapabilityAuditor 确定性审查
  -> 低风险能力自动批准
  -> 中高风险能力等待用户批准
  -> worker 仅调度 active 任务
  -> ScheduledAgentRunner 创建隔离 TaskSession
  -> 专用 Pipeline 在白名单内自主调用工具
  -> 保存报告、工具轨迹、任务日志和可提取结论
```

普通聊天中的 `web_search` 也改为首轮直接可见。Bot 模式查询最新信息时，不需要先调用
`tool_search` 解锁搜索工具。

## 二、两类定时任务

现有系统支持：

| 类型 | 执行方式 | 适用场景 |
|---|---|---|
| `workflow` | 固定 `web_search -> llm_analyze -> write_report` | 稳定日报、固定搜索 |
| `agent` | 审批后由隔离子 Agent 自主选择已批准工具 | 多步骤调研、需要记忆或受限文件读取的任务 |

旧任务自动保持为：

```text
schedule_type = workflow
approval_status = active
```

## 三、CLI 新增工具

Scheduler 插件新增：

```text
schedule_create_agent_draft
schedule_approve_agent
schedule_reject_agent
schedule_pending_approvals
schedule_approve_runtime
```

现有工具仍可继续使用：

```text
schedule_create
schedule_create_workflow
schedule_list
schedule_delete
schedule_run_now
schedule_results
```

## 四、创建自主任务

在 CLI 中可以直接说：

```text
创建一个自主定时任务：每天早上 8 点整理最近一天的 AI Agent 新闻，
分析趋势并生成中文报告。
```

主 Agent 应调用：

```text
schedule_create_agent_draft
```

如果 Planner 只申请低风险工具，例如：

```text
web_search
recall_memory
```

任务会直接变为：

```text
active
```

worker 下一次同步时会加入定时队列。

## 五、创建时审批

如果 Planner 申请：

```text
read_file
bash
write_file
edit_file
background_run
```

任务状态会变为：

```text
awaiting_approval
```

worker 不会调度该任务。用户可以先说：

```text
列出等待审批的自动任务。
```

再批准明确范围：

```text
批准任务 4 使用 read_file，只允许读取 docs 目录。
```

对应能力结构：

```json
{
  "tool": "read_file",
  "scope": {
    "paths": ["docs"]
  }
}
```

命令类工具必须逐条明确批准：

```json
{
  "tool": "bash",
  "scope": {
    "commands": ["python scripts/build_report.py"]
  }
}
```

不支持笼统批准全部 shell 命令。

## 六、运行时再次拦截

创建时审批不是唯一防线。执行期间如果模型尝试调用未批准工具，或参数超出批准范围：

```text
ToolApprovalPolicyHook
  -> 阻止执行
  -> 保存 approval_request_json
  -> 当前运行标记为 awaiting_runtime_approval
  -> 写入报告
  -> 停止本次 Pipeline
```

例如已经批准：

```json
{
  "tool": "bash",
  "scope": {
    "commands": ["python scripts/build_report.py"]
  }
}
```

但模型尝试：

```text
rm -rf storage
```

命令不会执行，任务会暂停。

用户审核后可以调用：

```text
schedule_approve_runtime
```

该批准只作用于未来运行。同一次运行不会自动恢复，避免在用户确认后悄悄继续执行。
如需立即执行，用户可以再说：

```text
立即重新运行任务 4。
```

`schedule_pending_approvals` 会同时返回：

```text
schedule_drafts    创建阶段等待审批或被阻断的任务
runtime_requests   执行期间暂停并等待审批的请求
```

## 七、隔离 TaskSession

自主任务不经过 MessageBus，也不模拟真实用户发消息。

`ScheduledAgentRunner` 直接创建内部 TaskSession：

```text
task:scheduled_agent-<random-id>
```

Pipeline 收到的是明确标记的内部任务：

```text
<scheduled-task schedule_id="4" run_id="18">
This is an internal unattended task, not a live user message.
</scheduled-task>
```

每次运行拥有独立 session 和独立任务记忆目录，不污染 CLI 或 Web 日常会话。

## 八、工具白名单

白名单有两层：

1. `ToolRegistry.visible_names_for_turn()` 只向定时 Agent 展示批准能力。
2. `ToolApprovalPolicyHook` 在工具真正执行前再次检查。

即使模型直接构造一个未展示的工具调用，也会在执行前被阻断。

无人值守任务永久禁止：

```text
所有 schedule_* 工具
spawn_teammate
task_create
task_update
claim_task
broadcast
shutdown_request
plan_approval
tool_search
```

因此定时 Agent 不能自行创建更多计划、提升权限或派生持久子 Agent。

## 九、预算限制

每个任务计划保存：

```text
max_reasoning_steps
max_tool_calls
timeout_seconds
```

默认值：

```json
{
  "max_reasoning_steps": 12,
  "max_tool_calls": 16,
  "timeout_seconds": 300
}
```

Pipeline 和 Hook 都会执行限制检查。超限后任务终止并记录错误。

当前超时会在推理步骤和工具调用之间检查。它不会中断已经发出的单次模型网络请求；
模型服务自身的网络超时仍由 API 客户端负责。

## 十、运行产物

每次自主 Agent 运行保存：

```text
storage/reports/<timestamp>-schedule-<id>-run-<id>-<name>.md
.task_sessions/<task-id>/TOOL_TRACE.json
.task_sessions/<task-id>/TASK_LOG.md
.task_sessions/<task-id>/CONCLUSIONS.json
```

报告包括：

```text
任务说明
执行状态
批准能力
最终回复
工具轨迹路径
可选运行时审批请求
可选错误信息
```

任务结论继续复用现有过滤和 promotion 机制，只将耐久信息放入全局 `PENDING.md`。

## 十一、Worker 分流

`scheduler_worker.py` 会根据 `schedule_type` 分流：

```text
workflow -> ScheduledReportService
agent    -> ScheduledAgentRunner
```

只有以下 Agent 会进入 Cron 队列：

```text
enabled = true
approval_status = active
```

等待审批、已拒绝或被阻断的任务不会自动执行。

## 十二、Bot 搜索

`plugins/web_search/plugin.py` 将：

```python
always_on=False
```

改为：

```python
always_on=True
```

因此 Bot 与 Coding 模式首轮都能看到 `web_search`。

## 十三、本地启动

终端一：

```bash
source .venv/bin/activate
python cli.py
```

终端二：

```bash
source .venv/bin/activate
python scheduler_worker.py
```

## 十四、Docker 更新

服务器拉取代码后：

```bash
sudo docker compose up -d --build
sudo docker compose ps
sudo docker compose logs -f scheduler-worker
```

数据库会在启动时自动迁移，不需要手工运行 SQL。

`scheduler-worker` 会挂载：

```text
storage
memory
.sessions
.task_sessions
.scheduler
```

因此容器重建后，自动任务报告、隔离 Session、任务日志和计划数据库仍然保留。

`storage/` 已加入 `.gitignore`。如果仓库历史中已经跟踪过日报文件，需要在提交前执行：

```bash
git rm -r --cached storage
```

该命令只移出 Git 索引，不会删除服务器上的报告文件。

## 十五、主要文件

新增：

```text
plugins/scheduler/agent_runner.py
plugins/scheduler/policy.py
modes/automation.py
docs/SCHEDULED_AGENT_AUTONOMOUS_EXECUTION.md
```

修改：

```text
plugins/scheduler/store.py
plugins/scheduler/plugin.py
plugins/scheduler/planning.py
scheduler_worker.py
core/bootstrap.py
core/pipeline.py
tools/executor.py
tools/tool_registry.py
tools/handlers.py
plugins/web_search/plugin.py
tests/test_scheduler_plugin.py
tests/test_scheduler_planning.py
```

## 十六、验证

```bash
python3 -B -m unittest discover -s tests -v
python3 -B -m py_compile \
  plugins/scheduler/planning.py \
  plugins/scheduler/policy.py \
  plugins/scheduler/agent_runner.py \
  plugins/scheduler/store.py \
  plugins/scheduler/plugin.py \
  scheduler_worker.py \
  core/bootstrap.py \
  core/pipeline.py \
  tools/executor.py \
  tools/tool_registry.py
git diff --check
docker compose config --services
```
