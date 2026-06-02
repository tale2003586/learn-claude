from dataclasses import dataclass

from .base import ModeProfile
from .coding import CODING_PROFILE
from .bot import BOT_PROFILE


@dataclass
class RouteResult:
    profile: ModeProfile
    switched: bool = False
    switch_message: str | None = None


class ModeRouter:
    def __init__(self, *, hybrid_classifier=None) -> None:
        self.hybrid_classifier = hybrid_classifier

    def route(self, session, user_text: str) -> RouteResult:
        text = user_text.strip().lower()

        if text in {"/coding", "进入编程模式", "编程模式"}:
            if not self._coding_allowed(session):
                session.set_mode("bot")
                return RouteResult(
                    profile=BOT_PROFILE,
                    switched=True,
                    switch_message="当前账号没有 Coding 模式权限，已保持聊天模式。",
                )
            session.set_mode("coding")
            return RouteResult(
                profile=CODING_PROFILE,
                switched=True,
                switch_message="已进入编程模式。",
            )

        if text in {"/chat", "/bot", "回到聊天模式", "聊天模式"}:
            session.set_mode("bot")
            return RouteResult(
                profile=BOT_PROFILE,
                switched=True,
                switch_message="已回到聊天模式。",
            )

        if text in {"/hybrid", "混合模式", "自动模式"}:
            session.set_mode("hybrid")
            return RouteResult(
                profile=BOT_PROFILE,
                switched=True,
                switch_message="已进入混合模式。",
            )

        if session.current_mode == "coding" and self._coding_allowed(session):
            return RouteResult(profile=CODING_PROFILE)

        if session.current_mode == "bot":
            return RouteResult(profile=BOT_PROFILE)

        # hybrid: 每轮判断
        if self._coding_allowed(session) and self._looks_like_coding_request(text):
            if (
                self.hybrid_classifier is not None
                and self.hybrid_classifier.should_use_coding(user_text)
            ):
                return RouteResult(profile=CODING_PROFILE)

        return RouteResult(profile=BOT_PROFILE)

    def _coding_allowed(self, session) -> bool:
        metadata = getattr(session, "metadata", {}) or {}
        return metadata.get("user_role", "admin") == "admin"

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
