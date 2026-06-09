# Docs Index

本目录按主题整理 taleclaw 的设计说明、阶段记录和部署文档。建议面试或代码走读时先看 `overview/`，再按主题深入。

## Overview

- [当前代码目录总结](overview/CODEBASE_SUMMARY.md)
- [Project Structure](overview/PROJECT_STRUCTURE.md)
- [课堂代码演示详稿](overview/CLASS_DEMO_SCRIPT.md)

## Runtime

- [Agent Runtime 解耦改造记录](runtime/AGENT_RUNTIME_DECOUPLING_REFACTOR.md)
- [Agent Loop 不可见工具循环保护修复记录](runtime/AGENT_LOOP_UNAVAILABLE_TOOL_GUARD.md)
- [Hybrid 模式：关键词预筛选与 LLM 路由改动记录](runtime/HYBRID_MODE_LLM_ROUTING.md)
- [Model Provider Pool and Route Selection](runtime/MODEL_PROVIDER_POOL_ROUTING.md)

## Sessions And Memory

- [Phase 16：Session SQLite 持久化说明](sessions-memory/PHASE16_SESSION_SQLITE.md)
- [Phase 17：Memory Lifecycle 说明](sessions-memory/PHASE17_MEMORY_LIFECYCLE.md)
- [主会话记忆分层与 SQLite 归档改动记录](sessions-memory/MEMORY_HISTORY_RECENT_ARCHIVE_MVP.md)
- [Task Session Isolation](sessions-memory/TASK_SESSION_ISOLATION.md)
- [TaskSession Memory Scope 修复记录](sessions-memory/TASK_SESSION_MEMORY_SCOPE_FIX.md)
- [Task Memory 混合提取与日志隔离改动记录](sessions-memory/TASK_MEMORY_HYBRID_PROMOTION.md)

## Tools And Plugins

- [Phase 18：Tool Search / Deferred Tools 说明](tools-plugins/PHASE18_TOOL_SEARCH_DEFERRED_TOOLS.md)
- [Phase 19：Plugin MVP 说明](tools-plugins/PHASE19_PLUGIN_MVP.md)
- [Bot 模式受限文件区第一阶段改动记录](tools-plugins/BOT_STORAGE_ARTIFACT_TOOLS.md)
- [Bot 临时沙盒与显式发布改动记录](tools-plugins/BOT_TASK_SANDBOX_AND_PUBLISH.md)
- [Markdown 转 PDF 插件改动记录](tools-plugins/MARKDOWN_PDF_PLUGIN.md)

## Scheduler

- [定时网络搜索插件改动记录](scheduler/SCHEDULED_SEARCH_PLUGIN.md)
- [受审批定时 Agent：数据库基础改动记录](scheduler/SCHEDULED_AGENT_STORAGE_FOUNDATION.md)
- [受审批定时 Agent：规划与工具审计改动记录](scheduler/SCHEDULED_AGENT_PLANNING_AUDIT.md)
- [受审批定时 Agent：自主执行完整改动记录](scheduler/SCHEDULED_AGENT_AUTONOMOUS_EXECUTION.md)

## Web

- [Web Login And Registration](web/WEB_LOGIN_AND_REGISTRATION.md)
- [Web Multi-User Isolation](web/WEB_MULTI_USER_ISOLATION.md)
- [Web 会话删除改动记录](web/WEB_SESSION_DELETION.md)
- [taleclaw Web 流式输出改动记录](web/WEB_STREAMING_OUTPUT.md)
- [taleclaw 文件预览弹窗改动记录](web/WEB_FILE_PREVIEW_MODAL.md)
- [taleclaw Web UI 暗色改版与验证记录](web/WEB_UI_DARK_REFRESH_VERIFICATION.md)

## Gateways

- [Telegram Gateway](gateways/TELEGRAM_GATEWAY.md)
- [Feishu Gateway](gateways/FEISHU_GATEWAY.md)

## Deployment

- [taleclaw 首尔服务器完整部署手册](deployment/SEOUL_SERVER_DEPLOYMENT.md)

## Roadmap And Research

- [mytry 改造成 Nanobot + Coding Assistant 的计划](roadmap-research/REFACTOR_PLAN.md)
- [Function Strength Roadmap](roadmap-research/FUNCTION_STRENGTH_ROADMAP.md)
- [Akashic Agent 学习与后续改进路线](roadmap-research/AKASHIC_AGENT_LEARNING_PLAN.md)
- [Pico Feature Integration Plan](roadmap-research/PICO_FEATURE_INTEGRATION_PLAN.md)
- [Security Review RAG Persona Plan](roadmap-research/SECURITY_REVIEW_RAG_PERSONA_PLAN.md)
- [taleclaw UI 改进与同类 Agent 调研报告](roadmap-research/AGENT_PRODUCT_RESEARCH_AND_UI_REFRESH.md)
- [mytry 项目结构优化建议](roadmap-research/STRUCTURE_SUGGESTION.md)
