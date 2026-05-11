# Project Structure

这次目录整理的目标不是大重构，而是让项目更像一个可以展示给面试官的工程：

```text
入口少一点
核心 runtime 聚在一起
状态存储有自己的包
学习文档不要散在根目录
tools/memory/plugins/bus/modes 继续保持领域分层
```

---

## 当前结构

```text
cli.py
config.py

core/
  bootstrap.py
  runtime.py
  agent_loop.py
  pipeline.py
  context.py
  provider.py
  compact.py

bus/
  events.py
  user_bus.py
  team_bus.py

coding_runtime/
  background_task.py
  protocols.py
  task.py
  teammate.py

sessions/
  session.py
  session_store.py

tasksessions/
  session.py
  runner.py
  promotion.py

memory/
  store.py
  lifecycle.py
  dedup.py
  MEMORY.md
  SELF.md
  NOW.md
  PENDING.md
  HISTORY.md
  RECENT_CONTEXT.md

modes/
  base.py
  bot.py
  coding.py
  router.py

tools/
  schema.py
  tool_registry.py
  executor.py
  hooks.py
  handlers.py

skill_runtime/
  loader.py

plugins/
  base.py
  plugin_manager.py
  shell_safety/
  status_commands/

docs/
  REFACTOR_PLAN.md
  AKASHIC_AGENT_LEARNING_PLAN.md
  PHASE16_SESSION_SQLITE.md
  PHASE17_MEMORY_LIFECYCLE.md
  PHASE18_TOOL_SEARCH_DEFERRED_TOOLS.md
  PHASE19_PLUGIN_MVP.md
  STRUCTURE_SUGGESTION.md
```

---

## 为什么这么分

### `core/`

放 agent runtime 的主干：

```text
bootstrap 装配依赖
runtime 管理 bus dispatch 生命周期
agent_loop 消费 inbound 并发布 outbound
pipeline 执行一次模型 turn
context 组装上下文
provider 封装 LLM 调用
compact 管理上下文压缩
```

这些文件合在一起，就是“agent 怎么跑起来”的核心路径。

### `sessions/`

放会话状态和 SQLite 持久化：

```text
Session
SessionManager
SessionStore
```

这样 session history 和 long-term memory 不会混在一起。

### `coding_runtime/`

放只服务于 coding task 的运行时能力：

```text
background task
team inbox / teammate protocol
task planning / claim task
teammate manager
```

这些能力不再属于普通 chat，而是被 coding TaskSession 使用。

### `tasksessions/`

放任务级上下文隔离：

```text
TaskSessionFactory
TaskSessionRunner
TaskMemoryPromoter
```

coding task 会在独立 `task:{task_id}` session 中运行，拥有自己的上下文和 task-local memory，结束后再把有价值的候选记忆提升到全局 `PENDING.md`。

### `memory/`

放长期记忆系统：

```text
Markdown 记忆文件
MemoryStore
MemoryLifecycle
dedup
```

它负责跨会话长期上下文。

### `tools/`

放工具系统：

```text
schema
registry
executor
hooks
actual handlers
```

这对应大模型应用里的 tool calling 层。

### `skill_runtime/`

放 skill 加载器：

```text
SkillLoader
SKILL_LOADER
```

它只负责发现和读取 `skills/**/SKILL.md`。

### `plugins/`

放扩展点：

```text
Plugin base
PluginManager
具体插件
```

后续新增 runtime 能力时，优先考虑放这里，不要继续塞进 `pipeline.py`。

### `docs/`

放学习和阶段文档。

根目录只保留真正入口和项目配置，面试官打开项目会清楚很多。

---

## 还没继续移动的文件

这次没有继续移动这些：

```text
skills.py
todo.py
```

`background_task.py`、`protocols.py`、`task.py`、`teammate.py` 已经移动到 `coding_runtime/`。
`skills.py` 已经移动到 `skill_runtime/loader.py`。
`todo.py` 已经移动到 `legacy/todo.py`，作为旧实现参考保留。

下一轮可以继续整理：

```text
legacy/
  README.md
```

但这一步不急。现在已经把 `core/`、`sessions/`、`tasksessions/`、`coding_runtime/`、`docs/` 分出来，项目职责边界已经清楚很多。

---

## 面试时可以怎么讲

可以这样说：

> 我把项目按 agent runtime 的职责重新分层：`core/` 承载运行主链路，`bus/` 负责消息流，`sessions/` 负责会话持久化，`tasksessions/` 负责任务上下文隔离，`coding_runtime/` 承载后台任务、队友协作和任务认领等工程执行能力，`memory/` 负责长期记忆，`tools/` 负责 tool calling 和执行安全，`plugins/` 提供扩展点。这样新增模式、工具、记忆策略或插件时，不需要继续污染主流程。
