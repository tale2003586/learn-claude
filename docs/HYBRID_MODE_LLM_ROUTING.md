# Hybrid 模式：关键词预筛选与 LLM 路由改动记录

## 一、问题

原有 Hybrid 模式使用关键词直接判断是否进入 Coding 路径：

```text
用户消息
  -> 命中 代码、测试、运行、文件、Python、Git 等关键词
  -> 直接进入 Coding TaskSession
```

这种方式速度快，但容易误判。例如：

```text
我想写一篇关于测试焦虑的文章。
```

因为包含“测试”，旧逻辑会错误进入 Coding 路径。

## 二、新链路

Hybrid 模式现在使用两阶段路由：

```text
用户消息
  -> 关键词预筛选
  -> 未命中：直接 Bot
  -> 命中：调用 HybridModeClassifier
  -> LLM 返回 coding：进入隔离 Coding TaskSession
  -> LLM 返回 bot：保留 Bot 路径
```

正则和关键词仍然承担低成本过滤，因此普通聊天不会增加额外模型请求。

## 三、语义说明

Hybrid 模式是逐轮路由，不是永久模式切换：

```text
current_mode = hybrid
```

每条消息都会独立决定使用：

```text
BOT_PROFILE
CODING_PROFILE
```

如果用户希望永久进入 Coding 模式，仍然可以显式发送：

```text
/coding
进入编程模式
编程模式
```

显式切换不会经过 LLM。

## 四、分类器

新增：

```text
modes/hybrid_classifier.py
```

入口：

```python
classifier.should_use_coding(user_text)
```

分类器要求模型返回严格 JSON：

```json
{
  "mode": "coding",
  "reason": "Repository edit requested."
}
```

仅在用户明确要求检查、修改、调试、运行、测试、部署代码或软件项目时选择 `coding`。

以下场景应选择 `bot`：

```text
日常对话
概念解释
写作与润色
头脑风暴
仅仅提到技术词汇，但不要求操作代码或项目
```

## 五、失败策略

以下情况会保守回落到 Bot：

```text
分类模型请求失败
返回内容不是合法 JSON
返回 JSON 缺少 mode
ModeRouter 没有注入分类器
```

这样网络抖动不会错误触发隔离 Coding TaskSession。

## 六、配置

`.env.example` 新增：

```bash
HYBRID_ROUTE_MODEL=
HYBRID_ROUTE_MAX_TOKENS=160
```

`HYBRID_ROUTE_MODEL` 留空时复用主模型。

## 七、生产注入

`core/bootstrap.py` 创建主 Provider 后注入：

```python
router = ModeRouter(
    hybrid_classifier=HybridModeClassifier(
        provider=provider,
        model=MODEL,
    ),
)
```

CLI 与 Web 服务都通过 `build_runtime()` 创建运行时，因此都会使用新路由。

## 八、测试

新增：

```text
tests/test_hybrid_mode_routing.py
```

覆盖：

```text
关键词误命中时，LLM 可以拒绝 Coding 路径
真实编码请求可以进入 Coding 路径
未命中关键词时不调用 LLM
显式 /coding 不调用 LLM
分类服务异常时回落 Bot
缺少分类器时回落 Bot
分类器可以解析 JSON 输出
```
