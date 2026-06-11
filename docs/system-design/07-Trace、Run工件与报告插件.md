# Trace、Run 工件与报告插件

这篇文档讲每次 run 的运行证据怎么记录、怎么聚合指标，以及报告为什么放到插件里。

## 这层解决什么问题

agent 失败时，最终回复通常不够用。

你需要知道：

- 哪个 run 失败了。
- 失败发生在第几步。
- 调了哪个 provider/model。
- 模型请求耗时多久。
- 工具调用参数和输出摘要是什么。
- 哪个 hook 拒绝了工具。
- workspace 改了什么。
- 最终 run 状态是什么。

这些信息由 `runtime/trace/` 负责。

## TraceStore

`TraceStore` 默认把 run 写到：

```text
<WORKDIR>/.runs/<run_id>/
```

它提供这些核心方法：

- `start_run(run_state)`
- `append_event(run_state, event_name, payload, ...)`
- `write_run_state(run_state)`
- `write_report(run_state, report)`
- `write_metrics(run_state)`
- `run_dir(run_state_or_id)`

## run 目录里的文件

一个 run 目录通常包含：

- `run_state.json`
- `trace.jsonl`
- `report.json`
- `metrics.json`
- `report.md`
- coding task 下可能还有 `workspace_diff.json`

其中 `report.md` 不是 trace core 直接写的，而是由 `plugins/run_report` 在 `after_run` 阶段生成。

## trace event 结构

`TraceStore.append_event()` 会写 JSONL，每行一个事件。

事件里包含：

- `timestamp`
- `run_id`
- `session_id`
- `request_id`
- `event`
- `span_id`
- `parent_span_id`
- `step`
- `payload`

`request_id` 默认来自 run metadata，没有的话用 `run_id`。

这让后续导出 OpenTelemetry 或类似格式有基础字段。

## 当前记录的关键事件

运行级：

- `run.started`
- `inbound_received`
- `route_selected`
- `run_finished`
- `run.completed`
- `run_failed`
- `run.failed`

reasoning 级：

- `reasoning_step_started`
- `reasoning.step.started`
- `reasoning.step.completed`
- `reasoning_loop_completed`
- `reasoning_budget_exceeded`

context 级：

- `context.build.started`
- `context.build.completed`
- `context.sanitized`

model 级：

- `model_requested`
- `model.call.started`
- `model.call.completed`
- `model.call.failed`
- `model.route.attempts`

tool 级：

- `tool.call.started`
- `tool.call.completed`
- `tool.call.failed`
- `tool_executed`

workspace 级：

- `workspace.resolved`
- `workspace.snapshot.captured`
- `workspace.diff.written`

## metrics.json 怎么来

`TraceStore.write_report()` 会调用 `write_metrics()`。

metrics 是从 `trace.jsonl` 聚合出来的。

当前会统计：

- run id / session id / status
- reasoning steps
- model calls / failures
- tool calls / failures / denials
- model 总耗时
- tool 总耗时
- input/output/total tokens
- model retry count
- model route attempts
- sanitized messages
- run duration
- models
- tools

这使得 trace 不只是调试日志，也能形成 run 级指标。

## report.json 和 report.md 的分工

`report.json` 是核心机器可读产物，由 trace store 写：

```json
{
  "run_state": {...},
  "report": {...},
  "generated_at": "..."
}
```

`report.md` 是人类阅读产物，由 `plugins/run_report` 写。

这样做的边界是：

- runtime core 负责事实和结构化数据。
- 插件负责展示形态。

这也符合你现在定下的方向：系统不必内置所有人类可读报告。

## eval report 插件

benchmark summary 的 markdown 也放到了插件：

- `plugins/run_report`：单个 run 的 `report.md`
- `plugins/eval_report`：benchmark 的 `summary.md`

`Plugin` 基类现在有：

```python
def after_run(self, context: RunContext) -> None:
    return None

def after_eval(self, context: EvalContext) -> None:
    return None
```

`PluginManager` 对应提供：

- `after_run(...)`
- `after_eval(...)`

这样 run 级报告和 eval 级报告都走插件生命周期。

## secret 过滤

TraceStore 写 JSON 前会走 `_json_safe()`。

其中 `_looks_secret()` 会过滤这些 key：

- `token`
- `password`
- `secret`
- `api_key`
- `apikey`
- 以及包含 authorization/access_token/refresh_token 等标记的 key

这不是完整 DLP，但能避免常见密钥字段直接落盘。

## Trace Viewer

项目已经有 trace viewer 的基础：run 目录里有结构化 trace、metrics、run state、report。

Web 侧可以读取这些文件，把它们展示成：

- run 列表
- metrics
- run state
- trace 表格

目前 trace viewer 的核心价值是不用只看原始文件。

## 当前边界

当前 trace 还没有：

- OpenTelemetry 原生导出。
- 跨进程 trace collector。
- 大规模 trace 存储后端。
- span duration 的统一闭合模型。

但字段上已经有 run/session/request/span/parent_span/step/tool_call_id，后续导出 OTEL 不需要重做整条链路。

## 总结

当前 trace 系统的设计重点是把每次 run 变成可复盘证据。

runtime core 写结构化事实，metrics 从 trace 聚合，人类报告由插件生成。这个边界让系统可以服务调试、评测和 UI 展示，同时不把展示逻辑塞进主执行链。
