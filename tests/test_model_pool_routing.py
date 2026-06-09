import json
import unittest
from types import SimpleNamespace

from core.model_pool import ModelPool, ModelProfile, build_model_pool_from_env
from core.pipeline import Pipeline
from core.provider import LLMResponse
from sessions.session import Session
from tools.executor import ToolExecutor


class ModelPoolEnvTests(unittest.TestCase):
    def test_builds_provider_pool_and_routes_from_env(self) -> None:
        env = {
            "LLM_PROVIDER": "deepseek",
            "DEEPSEEK_API_KEY": "deepseek-key",
            "MIMO_API_KEY": "mimo-key",
            "LLM_ROUTE_CHAT": "mimo,deepseek",
            "LLM_ROUTE_CODING": "deepseek",
            "LLM_ROUTE_SUMMARY": "mimo",
        }

        pool = build_model_pool_from_env(env)

        self.assertEqual("mimo", pool.primary_profile_name("chat"))
        self.assertEqual("mimo-v2.5-pro", pool.model_for("chat"))
        self.assertEqual("max_completion_tokens", pool.profile_for("chat").max_tokens_param)
        self.assertEqual(["deepseek"], pool.route_profile_names("coding"))
        self.assertEqual(["mimo"], pool.route_profile_names("summary"))
        self.assertEqual(["mimo"], pool.route_profile_names("reflection"))

    def test_provider_json_supports_profile_fallbacks(self) -> None:
        env = {
            "LLM_PROVIDER": "deepseek",
            "LLM_PROVIDERS_JSON": json.dumps({
                "deepseek": {
                    "api_key": "deepseek-key",
                    "base_url": "https://deepseek.example/v1",
                    "model": "deepseek-test",
                },
                "mimo": {
                    "api_key": "mimo-key",
                    "base_url": "https://mimo.example/v1",
                    "model": "mimo-test",
                    "max_tokens_param": "max_completion_tokens",
                    "fallbacks": ["deepseek"],
                },
            }),
            "LLM_ROUTE_CHAT": "mimo",
        }

        pool = build_model_pool_from_env(env)

        self.assertEqual(["mimo", "deepseek"], pool.route_profile_names("chat"))
        self.assertEqual("mimo-test", pool.model_for("chat"))


class RoutedModelProviderTests(unittest.TestCase):
    def test_chat_falls_back_to_next_provider(self) -> None:
        pool = ModelPool(
            profiles={
                "primary": ModelProfile(
                    name="primary",
                    provider="custom",
                    api_key="p",
                    base_url="https://primary.example",
                    model="primary-model",
                ),
                "backup": ModelProfile(
                    name="backup",
                    provider="custom",
                    api_key="b",
                    base_url="https://backup.example",
                    model="backup-model",
                ),
            },
            routes={"chat": ("primary", "backup")},
            default_profile="primary",
        )
        primary = FailingProvider()
        backup = RecordingProvider("ok")
        pool._providers["primary"] = primary
        pool._providers["backup"] = backup

        response = pool.routed_provider("chat").chat(
            model="override-primary-model",
            messages=[],
            tools=[],
            tool_choice="none",
            max_tokens=100,
        )

        self.assertEqual("ok", response.content)
        self.assertEqual(1, primary.calls)
        self.assertEqual("backup-model", backup.calls[0]["model"])

    def test_stream_fallback_only_before_text_is_emitted(self) -> None:
        pool = ModelPool(
            profiles={
                "primary": ModelProfile(
                    name="primary",
                    provider="custom",
                    api_key="p",
                    base_url="https://primary.example",
                    model="primary-model",
                ),
                "backup": ModelProfile(
                    name="backup",
                    provider="custom",
                    api_key="b",
                    base_url="https://backup.example",
                    model="backup-model",
                ),
            },
            routes={"chat": ("primary", "backup")},
            default_profile="primary",
        )
        pool._providers["primary"] = StreamingFailingProvider(emit_first=False)
        pool._providers["backup"] = StreamingProvider("backup reply")
        emitted = []

        response = pool.routed_provider("chat").stream_chat(
            model="primary-model",
            messages=[],
            tools=[],
            tool_choice="none",
            max_tokens=100,
            on_text=emitted.append,
        )

        self.assertEqual("backup reply", response.content)
        self.assertEqual(["backup reply"], emitted)

    def test_stream_does_not_fallback_after_text_is_emitted(self) -> None:
        pool = ModelPool(
            profiles={
                "primary": ModelProfile(
                    name="primary",
                    provider="custom",
                    api_key="p",
                    base_url="https://primary.example",
                    model="primary-model",
                ),
                "backup": ModelProfile(
                    name="backup",
                    provider="custom",
                    api_key="b",
                    base_url="https://backup.example",
                    model="backup-model",
                ),
            },
            routes={"chat": ("primary", "backup")},
            default_profile="primary",
        )
        pool._providers["primary"] = StreamingFailingProvider(emit_first=True)
        pool._providers["backup"] = StreamingProvider("backup reply")
        emitted = []

        with self.assertRaisesRegex(RuntimeError, "stream down"):
            pool.routed_provider("chat").stream_chat(
                model="primary-model",
                messages=[],
                tools=[],
                tool_choice="none",
                max_tokens=100,
                on_text=emitted.append,
            )

        self.assertEqual(["partial"], emitted)
        self.assertEqual(0, pool._providers["backup"].calls)


class PipelineModelRoutingTests(unittest.TestCase):
    def test_pipeline_uses_coding_route_for_coding_profile(self) -> None:
        model_pool = FakeModelPool()
        pipeline = _pipeline_with_pool(model_pool)
        session = Session(id="task:test", current_mode="coding")
        session.add_message("user", "edit code")

        reply = pipeline.run(session, SimpleNamespace(tool_mode="coding"))

        self.assertEqual("reply from coding", reply)
        self.assertEqual("coding-model", model_pool.providers["coding"].calls[0]["model"])

    def test_pipeline_uses_scheduled_agent_route_for_scheduled_sessions(self) -> None:
        model_pool = FakeModelPool()
        pipeline = _pipeline_with_pool(model_pool)
        session = Session(
            id="task:scheduled",
            current_mode="coding",
            metadata={"kind": "scheduled_agent"},
        )
        session.add_message("user", "scheduled task")

        reply = pipeline.run(session, SimpleNamespace(tool_mode="coding"))

        self.assertEqual("reply from scheduled_agent", reply)
        self.assertEqual(
            "scheduled_agent-model",
            model_pool.providers["scheduled_agent"].calls[0]["model"],
        )


class RecordingProvider:
    def __init__(self, content: str) -> None:
        self.content = content
        self.calls = []

    def chat(self, **kwargs):
        self.calls.append(kwargs)
        return LLMResponse(
            content=self.content,
            raw_message={"role": "assistant", "content": self.content},
        )


class FailingProvider:
    def __init__(self) -> None:
        self.calls = 0

    def chat(self, **kwargs):
        self.calls += 1
        raise RuntimeError("provider down")


class StreamingProvider:
    def __init__(self, content: str) -> None:
        self.content = content
        self.calls = 0

    def stream_chat(self, **kwargs):
        self.calls += 1
        kwargs["on_text"](self.content)
        return LLMResponse(
            content=self.content,
            raw_message={"role": "assistant", "content": self.content},
        )


class StreamingFailingProvider:
    def __init__(self, *, emit_first: bool) -> None:
        self.emit_first = emit_first
        self.calls = 0

    def stream_chat(self, **kwargs):
        self.calls += 1
        if self.emit_first:
            kwargs["on_text"]("partial")
        raise RuntimeError("stream down")


class FakeTools:
    def reset_turn_unlocks(self, session) -> None:
        return None

    def schemas_for_turn(self, session, mode):
        return []


class FakeContextBuilder:
    def build(self, **kwargs):
        return SimpleNamespace(messages=kwargs["session"].messages)


class FakeModelPool:
    def __init__(self) -> None:
        self.providers = {}

    def routed_provider(self, purpose: str):
        self.providers.setdefault(purpose, RecordingProvider(f"reply from {purpose}"))
        return self.providers[purpose]

    def model_for(self, purpose: str) -> str:
        return f"{purpose}-model"


def _pipeline_with_pool(model_pool) -> Pipeline:
    return Pipeline(
        tools=FakeTools(),
        provider=RecordingProvider("default"),
        model="default-model",
        model_pool=model_pool,
        tool_executor=ToolExecutor([]),
        context_builder=FakeContextBuilder(),
    )


if __name__ == "__main__":
    unittest.main()
