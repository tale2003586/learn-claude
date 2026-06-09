from dataclasses import dataclass

from .base import ModeProfile
from .coding import CODING_PROFILE
from .bot import BOT_PROFILE


@dataclass
class RouteResult:
    profile: ModeProfile
    switched: bool = False
    switch_message: str | None = None
    intent: str = "chat"
    execution: str = "pipeline_bot"
    confidence: float = 1.0
    reason: str = ""


class ModeRouter:
    def __init__(self, *, hybrid_classifier=None) -> None:
        self.hybrid_classifier = hybrid_classifier

    def route(self, session, user_text: str) -> RouteResult:
        text = user_text.strip().lower()

        if text in {"/coding", "进入编程模式", "编程模式"}:
            if not self._coding_allowed(session):
                session.set_mode("bot")
                return self._record(
                    session,
                    RouteResult(
                        profile=BOT_PROFILE,
                        switched=True,
                        switch_message="当前账号没有 Coding 模式权限，已保持聊天模式。",
                        intent="mode_switch",
                        execution="direct_reply",
                        reason="Coding mode requires an admin role.",
                    ),
                )
            session.set_mode("coding")
            return self._record(
                session,
                RouteResult(
                    profile=CODING_PROFILE,
                    switched=True,
                    switch_message="已进入编程模式。",
                    intent="mode_switch",
                    execution="direct_reply",
                    reason="Explicit coding mode command.",
                ),
            )

        if text in {"/chat", "/bot", "回到聊天模式", "聊天模式"}:
            session.set_mode("bot")
            return self._record(
                session,
                RouteResult(
                    profile=BOT_PROFILE,
                    switched=True,
                    switch_message="已回到聊天模式。",
                    intent="mode_switch",
                    execution="direct_reply",
                    reason="Explicit bot mode command.",
                ),
            )

        if text in {"/hybrid", "混合模式", "自动模式"}:
            session.set_mode("hybrid")
            return self._record(
                session,
                RouteResult(
                    profile=BOT_PROFILE,
                    switched=True,
                    switch_message="已进入混合模式。",
                    intent="mode_switch",
                    execution="direct_reply",
                    reason="Explicit hybrid mode command.",
                ),
            )

        if session.current_mode == "coding" and self._coding_allowed(session):
            return self._record(
                session,
                RouteResult(
                    profile=CODING_PROFILE,
                    intent="coding",
                    execution="task_session",
                    reason="Session is pinned to coding mode.",
                ),
            )

        if session.current_mode == "coding" and not self._coding_allowed(session):
            session.set_mode("bot")
            return self._record(
                session,
                RouteResult(
                    profile=BOT_PROFILE,
                    intent="chat",
                    execution="pipeline_bot",
                    confidence=0.55,
                    reason="Coding mode was revoked because the user is not admin.",
                ),
            )

        if session.current_mode == "bot":
            return self._record(
                session,
                RouteResult(
                    profile=BOT_PROFILE,
                    intent="chat",
                    execution="pipeline_bot",
                    reason="Session is pinned to bot mode.",
                ),
            )

        candidate = self._candidate_for_hybrid(text)
        if candidate is not None and candidate["intent"] != "coding":
            return self._record(
                session,
                RouteResult(
                    profile=BOT_PROFILE,
                    intent=candidate["intent"],
                    execution=candidate["execution"],
                    confidence=candidate["confidence"],
                    reason=candidate["reason"],
                ),
            )

        if candidate is not None and self._coding_allowed(session):
            if (
                self.hybrid_classifier is not None
                and self.hybrid_classifier.should_use_coding(user_text)
            ):
                return self._record(
                    session,
                    RouteResult(
                        profile=CODING_PROFILE,
                        intent="coding",
                        execution="task_session",
                        confidence=0.86,
                        reason="Coding candidate accepted by the hybrid classifier.",
                    ),
                )
            return self._record(
                session,
                RouteResult(
                    profile=BOT_PROFILE,
                    intent="chat",
                    execution="pipeline_bot",
                    confidence=0.58,
                    reason="Coding candidate was not accepted by the hybrid classifier.",
                ),
            )

        if candidate is not None and not self._coding_allowed(session):
            return self._record(
                session,
                RouteResult(
                    profile=BOT_PROFILE,
                    intent="chat",
                    execution="pipeline_bot",
                    confidence=0.55,
                    reason="Coding candidate was downgraded because the user is not admin.",
                ),
            )

        return self._record(
            session,
            RouteResult(
                profile=BOT_PROFILE,
                intent="chat",
                execution="pipeline_bot",
                reason="No specialized route matched.",
            ),
        )

    def _coding_allowed(self, session) -> bool:
        metadata = getattr(session, "metadata", {}) or {}
        return metadata.get("user_role", "admin") == "admin"

    def _record(self, session, result: RouteResult) -> RouteResult:
        metadata = getattr(session, "metadata", None)
        if isinstance(metadata, dict):
            metadata["last_route"] = {
                "intent": result.intent,
                "execution": result.execution,
                "profile": result.profile.name,
                "tool_mode": result.profile.tool_mode,
                "confidence": result.confidence,
                "reason": result.reason,
                "switched": result.switched,
            }
        return result

    def _candidate_for_hybrid(self, text: str) -> dict | None:
        if self._looks_like_strong_coding_request(text):
            return {
                "intent": "coding",
                "execution": "task_session",
                "confidence": 0.78,
                "reason": "Request strongly indicates code or project file work.",
            }
        if self._looks_like_scheduler_request(text):
            return {
                "intent": "scheduler",
                "execution": "pipeline_bot",
                "confidence": 0.88,
                "reason": "Request refers to scheduled or immediate task execution.",
            }
        if self._looks_like_storage_request(text):
            return {
                "intent": "storage_file",
                "execution": "pipeline_bot",
                "confidence": 0.82,
                "reason": "Request refers to storage file listing, preview, or download.",
            }
        if self._looks_like_memory_request(text):
            return {
                "intent": "memory_query",
                "execution": "pipeline_bot",
                "confidence": 0.74,
                "reason": "Request asks about remembered or historical context.",
            }
        if self._looks_like_coding_request(text):
            return {
                "intent": "coding",
                "execution": "task_session",
                "confidence": 0.62,
                "reason": "Request contains coding or project-operation indicators.",
            }
        return None

    def _looks_like_scheduler_request(self, text: str) -> bool:
        keywords = [
            "定时",
            "每天",
            "每周",
            "几点",
            "日报",
            "自动任务",
            "当前任务",
            "立即运行",
            "运行一次",
            "完成一次",
            "创建任务",
            "任务 id",
            "schedule",
            "scheduler",
            "schedule_run_now",
            "schedule_create",
        ]
        return any(keyword in text for keyword in keywords)

    def _looks_like_storage_request(self, text: str) -> bool:
        keywords = [
            "storage",
            "文件区",
            "网盘",
            "下载文件",
            "查看文件",
            "预览文件",
            "列出文件",
            "/files",
            "/download",
            "/cat",
            "上传的文件",
            "报告文件",
        ]
        return any(keyword in text for keyword in keywords)

    def _looks_like_memory_request(self, text: str) -> bool:
        keywords = [
            "记忆",
            "记忆系统",
            "历史记忆",
            "长期记忆",
            "历史记录",
            "你记得",
            "之前说过",
            "recall_memory",
        ]
        return any(keyword in text for keyword in keywords)

    def _looks_like_strong_coding_request(self, text: str) -> bool:
        if "运行测试" in text or "run tests" in text or "pytest" in text:
            return True
        action_keywords = [
            "修改",
            "修复",
            "改一下",
            "实现",
            "重构",
            "编辑",
            "调试",
            "debug",
            "fix",
            "edit",
            "refactor",
        ]
        artifact_keywords = [
            "代码",
            "bug",
            "函数",
            "class",
            "import",
            ".py",
            ".js",
            ".ts",
            ".css",
            ".html",
            "git",
            "docker",
            "前端",
            "后端",
        ]
        return (
            any(keyword in text for keyword in action_keywords)
            and any(keyword in text for keyword in artifact_keywords)
        )

    def _looks_like_coding_request(self, text: str) -> bool:
        keywords = [
            "代码",
            "报错",
            "bug",
            "实现",
            "重构",
            "测试",
            "运行",
            "文件",
            "函数",
            "class",
            "import",
            "pytest",
            "python",
            "cli.py",
            ".py",
            "git",
            "shell",
        ]
        return any(keyword in text for keyword in keywords)
