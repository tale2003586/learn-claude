# 受审批定时 Agent：规划与工具审计改动记录

## 一、当前阶段

本次完成受审批定时 Agent 的第 2 阶段：

```text
ScheduledTaskPlanner
ToolCapabilityAuditor
ToolRegistry.catalog()
```

后续阶段已经接入自主 Agent 执行。本文聚焦 Planner 与 Auditor；完整执行链路见：

[SCHEDULED_AGENT_AUTONOMOUS_EXECUTION.md](SCHEDULED_AGENT_AUTONOMOUS_EXECUTION.md)

## 二、目标

用户未来创建自主定时任务时，系统需要先回答：

```text
任务需要哪些工具？
每个工具来自哪里？
工具的风险等级是什么？
哪些能力可以自动批准？
哪些能力必须交给用户审核？
是否存在禁止用于无人值守任务的能力？
```

模型只负责提出候选计划。最终权限判断由本地 Python 代码根据工具注册表完成，
模型不能自行批准能力。

## 三、ToolRegistry 只读目录

`tools/tool_registry.py` 新增：

```python
registry.catalog(mode=None)
```

返回示例：

```python
[
    {
        "name": "web_search",
        "description": "Search the public web for current information.",
        "risk": "low",
        "source": "plugin:web_search",
        "enabled_modes": ["bot", "coding"],
        "always_on": False,
    },
]
```

该目录不会暴露 handler，因此 Planner 只能看到工具元数据，不能绕过执行器直接调用工具。

传入 `mode="bot"` 或 `mode="coding"` 时，只返回对应模式可用的工具。

## 四、ScheduledTaskPlanner

新增：

```text
plugins/scheduler/planning.py
```

入口：

```python
draft = ScheduledTaskPlanner().create_draft(
    task_prompt="每天整理最近一天的 AI Agent 新闻并生成分析。",
    auditor=ToolCapabilityAuditor(registry),
)
```

Planner 要求 LLM 返回一个 JSON 对象：

```json
{
  "summary": "每日 AI Agent 新闻分析",
  "requested_tools": ["web_search", "recall_memory"],
  "limits": {
    "max_reasoning_steps": 12,
    "max_tool_calls": 16,
    "timeout_seconds": 300
  },
  "rationale": "需要检索最新资料，并读取历史上下文避免重复。"
}
```

约束：

```text
task_prompt 最长 4000 字符
最多申请 20 个工具
重复工具自动去重
summary 最长保留 500 字符
rationale 最长保留 2000 字符
```

## 五、预算限制

Planner 可以建议预算，但最终值由本地代码裁剪：

| 字段 | 默认值 | 最小值 | 最大值 |
|---|---:|---:|---:|
| `max_reasoning_steps` | 12 | 1 | 30 |
| `max_tool_calls` | 16 | 1 | 50 |
| `timeout_seconds` | 300 | 30 | 1800 |

例如模型返回：

```json
{
  "max_reasoning_steps": 999,
  "timeout_seconds": 1
}
```

最终会变成：

```json
{
  "max_reasoning_steps": 30,
  "timeout_seconds": 30
}
```

## 六、ToolCapabilityAuditor

Auditor 使用本地 `ToolRegistry` 做确定性分类：

| 风险 | 处理方式 |
|---|---|
| `low` | 自动批准 |
| `normal` | 等待用户批准 |
| `high` | 等待用户批准，后续还需要参数范围约束 |
| 未注册工具 | 阻断任务 |
| 禁止工具 | 阻断任务 |

审计结果示例：

```json
{
  "approval_status": "awaiting_approval",
  "requested_tools": ["web_search", "read_file"],
  "approved_capabilities": [
    {
      "tool": "web_search",
      "risk": "low",
      "scope": {}
    }
  ],
  "requires_approval": [
    {
      "tool": "read_file",
      "risk": "normal",
      "source": "lead",
      "reason": "Normal-risk capability requires user approval."
    }
  ]
}
```

状态规则：

```text
active             所有能力均为 low
awaiting_approval  存在 normal 或 high 风险能力
blocked            存在禁止工具或未注册工具
```

## 七、禁止工具

无人值守任务不能自行修改调度规则，也不能创建新的持久子 Agent。

以下工具不会发送给 Planner，并且即使模型自行写入名称，也会被 Auditor 阻断：

```text
所有 schedule_* 工具
tool_search
task_create
task_update
task_list
task_get
claim_task
spawn_teammate
list_teammates
broadcast
send_message
read_inbox
shutdown_request
shutdown_status
plan_approval
compact
```

其中 `tool_search` 属于 Agent 运行时基础设施。后续可以由执行器按需提供，但它不是用户审批的
业务能力，也不能借此绕过白名单。

## 八、报告写入

Planner 的系统提示明确要求不要申请 `write_report` 工具。

原因是报告持久化应由 scheduler runner 在任务结束后统一完成，而不是向自主 Agent
开放任意文件写入。这样生成日报不需要获得 `write_file` 权限。

## 九、环境变量

`.env.example` 新增：

```bash
SCHEDULER_PLANNER_MODEL=
SCHEDULER_PLANNER_MAX_TOKENS=1000
```

`SCHEDULER_PLANNER_MODEL` 为空时复用主模型。

## 十、测试覆盖

新增：

```text
tests/test_scheduler_planning.py
```

覆盖：

```text
ToolRegistry 目录不会暴露 handler
目录可以按 mode 过滤
low 风险工具自动批准
high 风险工具进入待审批
schedule_* 和 spawn_teammate 被禁止
未注册工具阻断任务
Planner 对重复工具去重
Planner 本地裁剪预算
Planner 拒绝错误类型的 requested_tools
LLM 客户端可以解析 Markdown fenced JSON
```

## 十一、后续阶段

Planner 与 Auditor 已经接入 scheduler 插件：

```text
schedule_create_agent_draft
schedule_approve_agent
schedule_reject_agent
schedule_pending_approvals
schedule_approve_runtime
```

隔离执行器、worker 分流和运行时审批也已经完成。详细记录：

```text
docs/SCHEDULED_AGENT_AUTONOMOUS_EXECUTION.md
```
