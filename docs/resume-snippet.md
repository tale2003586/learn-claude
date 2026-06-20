# 简历素材：LLM Agent 项目（三层叙事）

> 一个项目拆三份讲。①Runtime 是地基，②Coding Agent 是地基上的垂直应用，③RAG 是给②供血的知识系统。
> 投递前请先修两处硬伤：Web 层全局串行锁（改 per-session 锁）、把 pytest 补进 requirements 并确认测试可跑。

---

## 总览一句话（简历抬头/自我介绍用）

自研多入口 LLM Agent Runtime（非 LangChain 封装）：从消息总线、会话、模型路由、工具治理到 trace 与自动化评测自建一套执行引擎，核心代码约 36K 行 + 49 套 pytest 用例，并在其上构建多层编排的 Coding Agent 与代码安全 RAG 系统。

---

## 技术栈（写全，按层组织）

```
语言/并发: Python 3.11, asyncio, threading
LLM 接入: OpenAI-compatible SDK, 多 Provider 路由池 (chat/coding/summary/reflection 按用途路由)
RAG/检索: Qdrant, fastembed, FlagEmbedding (BGE embedding + reranker), 混合检索, 语义切片
存储: PostgreSQL (psycopg3, trace/run 索引), SQLite (session/candidate store), 本地 JSONL 工件
数据建模: Pydantic v2, dataclass
服务/入口: http.server (Web), CLI, Telegram Bot API, 飞书开放平台
可观测: 结构化 trace (trace.jsonl), metrics/report 聚合, 自研事件体系
评测: SWE-bench 适配, 自研 benchmark harness
工程化: Docker / docker-compose, pytest, 插件化架构
文档/导出: mistune (Markdown), reportlab (PDF)
```

一行压缩版：
`Python · asyncio · OpenAI SDK · Qdrant · PostgreSQL · SQLite · Pydantic v2 · FlagEmbedding(BGE) · Docker · pytest · SWE-bench`

---

# 项目经历（简历正文，可直接粘贴）

## 项目一 · 多入口 LLM Agent Runtime（个人项目 · 核心作者）

`Python · asyncio · OpenAI-compatible SDK · 多 Provider 路由池 · PostgreSQL · SQLite · Pydantic v2 · Docker`

自研多入口 Agent 执行引擎，统一 CLI / Web / Telegram / 飞书四类入口，作为上层 Coding Agent 与 RAG 系统的公共地基。

- **统一消息抽象与依赖装配**：将四类入口归一为 inbound/outbound 消息，通过 `build_runtime` 一处装配消息总线、会话管理、模型池、工具执行器、记忆生命周期、trace store 等 14+ 组件，控制面/能力面/证据面三层解耦。
- **工具治理全链路护栏**：模型只能"请求"工具而非直接执行，经注册 → 按模式可见 → 延迟解锁 → Hook 改写/拒绝 → 循环保护五道关卡；`FileWriteScopeHook` 限制文件写入范围，`ToolLoopGuardHook` 阻断工具死循环，保证 Agent 在真实环境执行安全。
- **可观测性作为一等产物**：每次运行落 `trace.jsonl / metrics.json / report.json`，聚合执行路径、失败分类、token 与耗时指标，可选索引至 PostgreSQL，支持 Agent 行为事后复盘而非只看最终回答。
- **Context 与记忆工程**：实现 token 预算治理（section budget、对话摘要、active-turn 压缩、emergency trim）与三级记忆生命周期（候选 → 晋升 → 归档），结合向量召回做按需记忆注入，降低长会话 token 成本。
- 配套 49 套 pytest 用例覆盖记忆、上下文、工具沙箱、模型路由、网关集成等核心路径。

## 项目二 · 多层编排的 Coding Agent（依托 Runtime 的垂直应用）

`复用 Runtime 执行核 · asyncio 并行 · Git workspace 隔离 · 角色化 Profile · 工具最小权限 · SWE-bench`

在 Runtime 之上构建面向真实代码任务的多 Agent 协作模式，专门解决"理解—修改—验证"的垂直闭环。

- **三级 Agent 编排**：基于公共 `ReasoningLoop` 构建 Lead → 按角色派生 Teammate（`TeammateManager.spawn`）→ 并行 Subagent 的三层结构，每层复用同一执行核但隔离 session、记忆与工具集。
- **任务隔离与 workspace diff**：coding 请求进入独立 task session 并绑定真实代码仓库，记录执行前后 `workspace_diff.json`，避免中间步骤污染主会话，结果可审计。
- **工具最小权限**：按 `agent_type` 给不同子 Agent 下发过滤后的工具子集（只读探索 vs 可写实现），降低越权与误操作风险。
- **经验沉淀**：任务完成后做结论抽取与可晋升记忆筛选，把有价值的解决方案回写记忆系统供后续任务复用。
- **可量化评测**：接入 SWE-bench 并自研 benchmark harness，支持 scripted / 真实模型两种模式，自动产出 summary、rows 与可读报告，对 Coding Agent 做回归评测。

## 项目三 · 代码安全 RAG 系统（为 Coding 过程供血的知识服务）

`Qdrant · fastembed · FlagEmbedding(BGE embedding + reranker) · 混合检索 · 语义切片 · LLM 路由分类`

独立的检索增强系统，回答 Coding Agent 执行中"这段代码安不安全、该怎么改"的疑问。

- **知识库构建**：解析安全公告与 Semgrep SAST 规则，做语义切片后入库 Qdrant，支持增量更新。
- **混合检索 + 重排**：用 BGE embedding 做向量召回并接 reranker 提升精度；引入 LLM 路由分类器判断本轮 query 是否需要安全知识，非安全问题不注入安全上下文，避免污染。
- **双接入方式**：支持自动上下文注入与 `security_rag_search` 工具显式检索，让 Coding Agent 按需查证。
- **检索可观测**：检索路由与命中结果落 trace，便于评估召回质量与调参。

---

# 面试串词（背下来）

> "这个项目我没用 LangChain，而是自己搭了一套 Agent runtime。它分三层：第一层是地基——消息总线、模型路由、工具治理、trace；第二层是依托地基做的 Coding Agent，用 Lead/Teammate/Subagent 三级编排解决真实代码任务，并用 SWE-bench 量化评测；第三层是给 Coding 过程供血的代码安全 RAG。我重点解决三件事：**工具执行怎么安全、Agent 行为怎么可观测、效果怎么量化评测**。"

---

# 不要写 / 防反杀口径

- 不写"高并发 / 大规模多用户" → 改说 **"会话级状态隔离"**（session/memory/workspace 确实按用户隔离，但 Web 层当前是全局串行锁，压测会穿帮）。
- 不写"分布式调度平台 / checkpoint-resume" → 改说 **"单进程本地 runtime + 后台任务"**。
- 不写"全仓代码语义索引" → RAG 只面向 **安全知识库 + 历史会话召回**。
- 被追问规模与不足时，主动承认边界，反而加分（设计文档已诚实标注未实现项）。
