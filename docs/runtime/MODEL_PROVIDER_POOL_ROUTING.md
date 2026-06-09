# Model Provider Pool and Route Selection

## 1. 背景

项目原来只有一个全局 `client + MODEL`。切换 DeepSeek、MiMo 这类 OpenAI-compatible
模型时，只能整体切换；聊天、代码模式、记忆总结、定时任务规划都会走同一个模型。

这次改动加入 `ModelPool`，让系统可以同时配置多个 provider，并按调用用途选择模型。

## 2. 改动概览

### 2.1 新增模型池

新增 `core/model_pool.py`：

- `ModelProfile`：描述一个模型 provider。
- `ModelPool`：从环境变量构建 provider 池，并解析用途路由。
- `RoutedModelProvider`：对外仍然暴露 `chat()` 和 `stream_chat()`，内部按用途选择模型。

`RoutedModelProvider` 会按路由链 fallback：

- 非流式调用：主 provider 失败后继续尝试后备 provider。
- 流式调用：如果还没有输出任何文字，可以 fallback；如果已经输出文字，则直接抛错，
  避免用户看到重复或混杂内容。

### 2.2 保留旧配置兼容

`config.py` 现在导出：

- `MODEL_POOL`
- `MODEL`
- `MAX_TOKENS_PARAM`
- `client`

旧代码需要 `MODEL/client` 时仍然可用；新代码优先使用 `MODEL_POOL`。

如果不配置多 provider，仍然可以只写：

```env
LLM_PROVIDER=deepseek
DEEPSEEK_API_KEY=...
DEEPSEEK_BASE_URL=https://api.deepseek.com
DEEPSEEK_MODEL=deepseek-v4-flash
```

或：

```env
LLM_PROVIDER=mimo
MIMO_API_KEY=...
MIMO_BASE_URL=https://api.xiaomimimo.com/v1
MIMO_MODEL=mimo-v2.5-pro
```

### 2.3 接入的调用点

已接入路由的用途：

- `chat`：普通 Bot/Hybrid 聊天。
- `coding`：Code 模式和普通 TaskSession。
- `summary`：长期记忆总结。
- `hybrid`：Hybrid 模式是否切换到 Code 的 LLM 判别。
- `compact`：超长上下文自动压缩。
- `scheduled_agent`：定时 agent 执行。
- `scheduler_plan`：定时任务创建时的工具/能力规划。
- `scheduler_analyze`：定时搜索报告的 LLM 分析。
- `task_conclusion`：TaskSession 完成后的结论抽取。

`scheduled_agent` 默认可继承 `coding` 路由；`compact` 和 `task_conclusion` 默认可继承
`summary` 路由。

## 3. 推荐配置

### 3.1 简单单模型

```env
LLM_PROVIDER=deepseek
DEEPSEEK_API_KEY=替换为你的 key
DEEPSEEK_BASE_URL=https://api.deepseek.com
DEEPSEEK_MODEL=deepseek-v4-flash
USE_LOCAL_PROXY=0
```

### 3.2 DeepSeek + MiMo 混合

```env
LLM_PROVIDER=deepseek
DEEPSEEK_API_KEY=替换为你的 DeepSeek Key
DEEPSEEK_BASE_URL=https://api.deepseek.com
DEEPSEEK_MODEL=deepseek-v4-flash

MIMO_API_KEY=替换为你的 MiMo Key
MIMO_BASE_URL=https://api.xiaomimimo.com/v1
MIMO_MODEL=mimo-v2.5-pro

LLM_ROUTE_CHAT=deepseek
LLM_ROUTE_CODING=deepseek
LLM_ROUTE_HYBRID=deepseek
LLM_ROUTE_TEAMMATE=deepseek
LLM_ROUTE_REFLECTION=mimo,deepseek
LLM_ROUTE_SUMMARY=mimo,deepseek
LLM_ROUTE_COMPACT=mimo,deepseek
LLM_ROUTE_SCHEDULER_PLAN=deepseek
LLM_ROUTE_SCHEDULER_ANALYZE=mimo,deepseek
LLM_ROUTE_TASK_CONCLUSION=mimo,deepseek
LLM_ROUTE_FALLBACK=deepseek
```

这套配置的含义：

- 前台聊天和代码执行用 DeepSeek。
- 总结、报告分析、结论抽取优先用 MiMo。
- MiMo 失败时 fallback 到 DeepSeek。

### 3.3 JSON Provider 池

复杂配置可以写成 JSON：

```env
LLM_PROVIDER=deepseek
LLM_PROVIDERS_JSON={"deepseek":{"api_key_env":"DEEPSEEK_API_KEY","base_url":"https://api.deepseek.com","model":"deepseek-v4-flash"},"mimo":{"api_key_env":"MIMO_API_KEY","base_url":"https://api.xiaomimimo.com/v1","model":"mimo-v2.5-pro","max_tokens_param":"max_completion_tokens","fallbacks":["deepseek"]}}
LLM_ROUTES_JSON={"chat":"deepseek","coding":"deepseek","summary":"mimo","hybrid":"deepseek","scheduler_analyze":"mimo"}
DEEPSEEK_API_KEY=...
MIMO_API_KEY=...
```

## 4. 环境变量说明

Provider 相关：

- `LLM_PROVIDER`：默认 provider 名称，未配置路由时使用它。
- `LLM_API_KEY`：当前默认 provider 的通用 key。
- `LLM_BASE_URL`：当前默认 provider 的通用接口地址。
- `LLM_MODEL`：当前默认 provider 的通用模型名。
- `LLM_MAX_TOKENS_PARAM`：当前默认 provider 的 token 参数名。
- `DEEPSEEK_API_KEY` / `DEEPSEEK_BASE_URL` / `DEEPSEEK_MODEL`
- `MIMO_API_KEY` / `MIMO_BASE_URL` / `MIMO_MODEL`
- `LLM_PROVIDERS_JSON`：自定义多个 provider。

路由相关：

- `LLM_ROUTE_DEFAULT`
- `LLM_ROUTE_CHAT`
- `LLM_ROUTE_CODING`
- `LLM_ROUTE_SUMMARY`
- `LLM_ROUTE_HYBRID`
- `LLM_ROUTE_TEAMMATE`
- `LLM_ROUTE_REFLECTION`
- `LLM_ROUTE_COMPACT`
- `LLM_ROUTE_SCHEDULED_AGENT`
- `LLM_ROUTE_SCHEDULER_PLAN`
- `LLM_ROUTE_SCHEDULER_ANALYZE`
- `LLM_ROUTE_TASK_CONCLUSION`
- `LLM_ROUTE_FALLBACK`
- `LLM_ROUTES_JSON`

路由值可以是一个 provider，也可以是逗号分隔的 fallback 链：

```env
LLM_ROUTE_SUMMARY=mimo,deepseek
```

## 5. 涉及文件

- `core/model_pool.py`：新增模型池和路由 provider。
- `config.py`：从模型池导出兼容变量。
- `core/pipeline.py`：按 bot/code/scheduled_agent 选择模型用途。
- `core/bootstrap.py`：接入 `chat/hybrid/summary` 路由。
- `core/compact.py`：自动压缩走 provider 抽象。
- `tasksessions/runner.py`：TaskSession 复制 pipeline 时保留模型池。
- `plugins/scheduler/agent_runner.py`：定时 agent 复制 pipeline 时保留模型池。
- `plugins/scheduler/planning.py`：定时任务规划走 `scheduler_plan`。
- `plugins/scheduler/workflow.py`：定时报告分析走 `scheduler_analyze`。
- `.env.example`、`web/README.md`、`docs/deployment/SEOUL_SERVER_DEPLOYMENT.md`：补充配置说明。
- `tests/test_model_pool_routing.py`：新增模型池和路由测试。

## 6. 验证

已通过：

```bash
python -m py_compile core/model_pool.py config.py core/pipeline.py core/bootstrap.py core/compact.py tasksessions/runner.py plugins/scheduler/agent_runner.py plugins/scheduler/workflow.py plugins/scheduler/planning.py tests/test_model_pool_routing.py
python -m unittest discover -s tests -p 'test_model_pool_routing.py' -v
python -m unittest discover -s tests -p 'test_web_streaming.py' -v
python -m unittest discover -s tests -p 'test_scheduler_planning.py' -v
python -m unittest discover -s tests -p 'test_task_memory_promotion.py' -v
python -m unittest discover -s tests -p 'test_pipeline_tool_loop_guard.py' -v
python -m unittest discover -s tests -p 'test_scheduler_plugin.py' -v
python -m unittest discover -s tests -p 'test_hybrid_mode_routing.py' -v
```

## 7. 部署注意

服务器上修改 `.env` 后需要重建或重启容器：

```bash
sudo docker compose --profile telegram up -d --build --force-recreate \
  agent-console scheduler-worker telegram-worker
```

如果只改 `.env` 且镜像没有变，也可以：

```bash
sudo docker compose --profile telegram up -d --force-recreate \
  agent-console scheduler-worker telegram-worker
```
