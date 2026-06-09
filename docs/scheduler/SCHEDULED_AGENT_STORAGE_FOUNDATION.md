# 受审批定时 Agent：数据库基础改动记录

## 一、当前阶段

本文记录受审批定时 Agent 的第 1 阶段：扩展 SQLite 存储结构和兼容迁移。
后续自主执行链路已经完成，见：

[SCHEDULED_AGENT_AUTONOMOUS_EXECUTION.md](SCHEDULED_AGENT_AUTONOMOUS_EXECUTION.md)

现有定时搜索与受控 Workflow 的运行逻辑保持不变：

```text
APScheduler
  -> ScheduledReportService
  -> web_search
  -> 可选 llm_analyze
  -> write_report
```

第 1 阶段尚未启用自主 Agent 执行。新增字段用于后续实现工具审计、人工审批、
隔离 TaskSession 和运行轨迹。

## 二、设计目标

后续需要支持两类定时任务：

```text
workflow  固定步骤任务，例如搜索、分析和写报告
agent     经过工具审计与用户审批的自主任务
```

为了让旧任务无感升级，历史记录默认视为：

```text
schedule_type = workflow
approval_status = active
```

## 三、schedules 表新增字段

`.scheduler/schedules.db` 中的 `schedules` 表新增：

| 字段 | 类型 | 默认值 | 用途 |
|---|---|---|---|
| `schedule_type` | `TEXT NOT NULL` | `workflow` | 区分固定 Workflow 与未来 Agent 任务 |
| `task_prompt` | `TEXT` | `NULL` | 保存未来定时 Agent 的内部任务说明 |
| `approval_status` | `TEXT NOT NULL` | `active` | 保存审批状态 |
| `requested_tools_json` | `TEXT NOT NULL` | `[]` | 保存 Planner 申请的工具列表 |
| `approved_capabilities_json` | `TEXT NOT NULL` | `[]` | 保存用户批准的能力范围 |
| `limits_json` | `TEXT NOT NULL` | `{}` | 保存 token、步骤数、工具调用次数和超时限制 |
| `plan_json` | `TEXT NOT NULL` | `{}` | 保存 Planner 生成的结构化计划、摘要与理由 |

后续建议使用以下审批状态：

```text
active
awaiting_approval
rejected
paused
```

## 四、schedule_runs 表新增字段

每次运行记录新增：

| 字段 | 类型 | 用途 |
|---|---|---|
| `task_session_id` | `TEXT` | 关联隔离 TaskSession |
| `trace_path` | `TEXT` | 保存工具调用轨迹文件路径 |
| `approval_request_json` | `TEXT` | 保存运行中触发的临时审批请求 |

未来如果 Agent 尝试使用未批准的工具或参数，运行记录可以写入：

```json
{
  "tool": "bash",
  "arguments": {
    "command": "python scripts/report.py"
  }
}
```

并将运行状态标记为：

```text
awaiting_runtime_approval
```

## 五、兼容迁移

迁移在 `ScheduleStore` 初始化时自动执行，不需要手工运行 SQL：

```python
ScheduleStore(...)
```

初始化流程会：

1. 使用 `CREATE TABLE IF NOT EXISTS` 创建新数据库的完整表结构。
2. 使用 `PRAGMA table_info(...)` 检查旧数据库已有字段。
3. 对缺失字段执行 `ALTER TABLE ... ADD COLUMN`。
4. 为旧任务填入 SQLite 默认值。

迁移是幂等的。CLI、Web 服务和 scheduler worker 可以继续使用同一个数据库文件。

## 六、Python 返回结构

数据库内部继续使用 JSON 文本，`ScheduleStore` 返回 Python 对象时会解析为：

```python
{
    "schedule_type": "workflow",
    "task_prompt": None,
    "approval_status": "active",
    "requested_tools": [],
    "approved_capabilities": [],
    "limits": {},
    "plan": {},
}
```

运行记录中的 `approval_request_json` 会解析为：

```python
{
    "approval_request": {
        "tool": "bash",
        "arguments": {
            "command": "python scripts/report.py"
        },
    },
}
```

如果新增 JSON 字段为空、格式错误或类型不符合预期，读取时会使用安全默认值。

## 七、运行记录接口

`ScheduleStore.begin_run(...)` 和 `ScheduleStore.complete_run(...)` 增加了可选参数：

```python
task_session_id
trace_path
approval_request
```

现有调用方不需要调整。未来的 `ScheduledAgentRunner` 可以逐步写入执行元数据：

```python
run_id = store.begin_run(
    schedule,
    task_session_id="task:scheduled-4-run-18",
)

store.complete_run(
    run_id=run_id,
    schedule_id=schedule["id"],
    status="awaiting_runtime_approval",
    trace_path=".task_sessions/scheduled-4-run-18/TRACE.json",
    approval_request={
        "tool": "bash",
        "arguments": {"command": "python scripts/report.py"},
    },
)
```

## 八、测试覆盖

`tests/test_scheduler_plugin.py` 新增覆盖：

```text
旧 schedules 表自动增加 Agent 字段
旧 schedule_runs 表自动增加运行元数据字段
历史任务默认读取为 workflow + active
新增 JSON 字段返回安全默认值
TaskSession、轨迹路径和运行时审批请求可以写入并读回
```

## 九、后续阶段

第 2 阶段已经完成：

```text
ScheduledTaskPlanner
ToolCapabilityAuditor
```

详细记录：

```text
docs/SCHEDULED_AGENT_PLANNING_AUDIT.md
```

第 3 阶段已经完成：

```text
schedule_create_agent_draft
schedule_approve_agent
schedule_reject_agent
schedule_pending_approvals
schedule_approve_runtime
ScheduledAgentRunner
ToolApprovalPolicyHook
```

详细记录：

```text
docs/SCHEDULED_AGENT_AUTONOMOUS_EXECUTION.md
```
