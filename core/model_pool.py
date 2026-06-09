import json
import os
import re
from dataclasses import dataclass, field
from typing import Any, Callable, Mapping

from openai import OpenAI

from core.provider import LLMResponse, OpenAICompatibleProvider


DEFAULT_PROVIDER_SETTINGS = {
    "deepseek": {
        "base_url": "https://api.deepseek.com",
        "model": "deepseek-v4-flash",
        "max_tokens_param": "max_tokens",
    },
    "mimo": {
        "base_url": "https://api.xiaomimimo.com/v1",
        "model": "mimo-v2.5-pro",
        "max_tokens_param": "max_completion_tokens",
    },
    "openai": {
        "base_url": "https://api.openai.com/v1",
        "model": "gpt-4.1-mini",
        "max_tokens_param": "max_tokens",
    },
}

PURPOSE_ALIASES = {
    "scheduled_agent": "coding",
    "teammate": "coding",
    "reflection": "summary",
    "task_conclusion": "summary",
    "compact": "summary",
}

ROUTE_ENV_NAMES = {
    "default": "LLM_ROUTE_DEFAULT",
    "chat": "LLM_ROUTE_CHAT",
    "coding": "LLM_ROUTE_CODING",
    "summary": "LLM_ROUTE_SUMMARY",
    "hybrid": "LLM_ROUTE_HYBRID",
    "compact": "LLM_ROUTE_COMPACT",
    "scheduled_agent": "LLM_ROUTE_SCHEDULED_AGENT",
    "teammate": "LLM_ROUTE_TEAMMATE",
    "reflection": "LLM_ROUTE_REFLECTION",
    "scheduler_plan": "LLM_ROUTE_SCHEDULER_PLAN",
    "scheduler_analyze": "LLM_ROUTE_SCHEDULER_ANALYZE",
    "task_conclusion": "LLM_ROUTE_TASK_CONCLUSION",
}


@dataclass(frozen=True)
class ModelProfile:
    name: str
    provider: str
    api_key: str = field(repr=False)
    base_url: str
    model: str
    max_tokens_param: str = "max_tokens"
    wire_api: str = "chat_completions"
    fallbacks: tuple[str, ...] = ()


class ModelPool:
    def __init__(
        self,
        *,
        profiles: Mapping[str, ModelProfile],
        routes: Mapping[str, tuple[str, ...]] | None = None,
        default_profile: str,
        client_factory: Callable[..., Any] = OpenAI,
    ) -> None:
        if not profiles:
            raise RuntimeError("No model providers are configured.")
        if default_profile not in profiles:
            raise RuntimeError(f"Default model provider is not configured: {default_profile}")
        self.profiles = dict(profiles)
        self.routes = dict(routes or {})
        self.default_profile = default_profile
        self.client_factory = client_factory
        self._clients: dict[str, Any] = {}
        self._providers: dict[str, OpenAICompatibleProvider] = {}
        self._routed: dict[str, RoutedModelProvider] = {}

    def primary_profile_name(self, purpose: str = "chat") -> str:
        return self.route_profile_names(purpose)[0]

    def profile_for(self, purpose: str = "chat") -> ModelProfile:
        return self.profiles[self.primary_profile_name(purpose)]

    def model_for(self, purpose: str = "chat") -> str:
        return self.profile_for(purpose).model

    def client_for_profile(self, profile_name: str) -> Any:
        profile = self._require_profile(profile_name)
        if profile.name not in self._clients:
            self._clients[profile.name] = self.client_factory(
                api_key=profile.api_key,
                base_url=profile.base_url,
            )
        return self._clients[profile.name]

    def client_for_purpose(self, purpose: str = "chat") -> Any:
        return self.client_for_profile(self.primary_profile_name(purpose))

    def provider_for_profile(self, profile_name: str) -> OpenAICompatibleProvider:
        profile = self._require_profile(profile_name)
        if profile.name not in self._providers:
            self._providers[profile.name] = OpenAICompatibleProvider(
                self.client_for_profile(profile.name),
                max_tokens_param=profile.max_tokens_param,
                wire_api=profile.wire_api,
            )
        return self._providers[profile.name]

    def routed_provider(self, purpose: str = "chat") -> "RoutedModelProvider":
        key = _normalize_route_name(purpose)
        if key not in self._routed:
            self._routed[key] = RoutedModelProvider(self, key)
        return self._routed[key]

    def route_profiles(self, purpose: str = "chat") -> list[ModelProfile]:
        return [self.profiles[name] for name in self.route_profile_names(purpose)]

    def route_profile_names(self, purpose: str = "chat") -> list[str]:
        names = self._route_chain_for(purpose)
        chain: list[str] = []
        for name in names:
            if name not in self.profiles:
                raise RuntimeError(
                    f"Model route '{purpose}' references unknown provider '{name}'."
                )
            if name not in chain:
                chain.append(name)
            for fallback in self.profiles[name].fallbacks:
                if fallback not in self.profiles:
                    raise RuntimeError(
                        f"Model provider '{name}' references unknown fallback '{fallback}'."
                    )
                if fallback not in chain:
                    chain.append(fallback)
        return chain

    def _route_chain_for(self, purpose: str) -> tuple[str, ...]:
        key = _normalize_route_name(purpose)
        if key in self.routes:
            return self.routes[key]
        alias = PURPOSE_ALIASES.get(key)
        if alias and alias in self.routes:
            return self.routes[alias]
        if "default" in self.routes:
            return self.routes["default"]
        return (self.default_profile,)

    def _require_profile(self, profile_name: str) -> ModelProfile:
        if profile_name not in self.profiles:
            raise RuntimeError(f"Model provider is not configured: {profile_name}")
        return self.profiles[profile_name]


class RoutedModelProvider:
    def __init__(self, pool: ModelPool, purpose: str) -> None:
        self.pool = pool
        self.purpose = purpose

    def chat(
        self,
        *,
        messages: list[dict],
        tools: list[dict],
        model: str,
        max_tokens: int,
        tool_choice: str = "auto",
    ) -> LLMResponse:
        return self._call_with_fallbacks(
            stream=False,
            messages=messages,
            tools=tools,
            model=model,
            max_tokens=max_tokens,
            tool_choice=tool_choice,
        )

    def stream_chat(
        self,
        *,
        messages: list[dict],
        tools: list[dict],
        model: str,
        max_tokens: int,
        on_text: Callable[[str], None],
        tool_choice: str = "auto",
    ) -> LLMResponse:
        return self._call_with_fallbacks(
            stream=True,
            messages=messages,
            tools=tools,
            model=model,
            max_tokens=max_tokens,
            tool_choice=tool_choice,
            on_text=on_text,
        )

    def _call_with_fallbacks(self, *, stream: bool, **kwargs) -> LLMResponse:
        chain = self.pool.route_profiles(self.purpose)
        if not chain:
            raise RuntimeError(f"No model route configured for purpose '{self.purpose}'.")

        errors: list[str] = []
        last_error: Exception | None = None
        primary = chain[0]

        for profile in chain:
            provider = self.pool.provider_for_profile(profile.name)
            call_kwargs = dict(kwargs)
            call_kwargs["model"] = (
                str(kwargs.get("model") or "").strip()
                if profile.name == primary.name and kwargs.get("model")
                else profile.model
            )
            emitted = False
            try:
                if stream:
                    user_on_text = call_kwargs.pop("on_text")

                    def track_text(text: str) -> None:
                        nonlocal emitted
                        if text:
                            emitted = True
                        user_on_text(text)

                    call_kwargs["on_text"] = track_text
                    return provider.stream_chat(**call_kwargs)
                return provider.chat(**call_kwargs)
            except Exception as exc:
                if stream and emitted:
                    raise
                last_error = exc
                errors.append(f"{profile.name}: {type(exc).__name__}: {exc}")

        detail = "; ".join(errors) or "no attempts"
        raise RuntimeError(
            f"All model providers failed for purpose '{self.purpose}': {detail}"
        ) from last_error


def build_model_pool_from_env(
    environ: Mapping[str, str] | None = None,
    *,
    client_factory: Callable[..., Any] = OpenAI,
) -> ModelPool:
    env = environ or os.environ
    selected = _normalize_profile_name(env.get("LLM_PROVIDER", "deepseek"))

    profiles, default_profile = _profiles_from_env(env, selected)
    routes = _routes_from_env(env, default_profile)
    return ModelPool(
        profiles=profiles,
        routes=routes,
        default_profile=default_profile,
        client_factory=client_factory,
    )


def _profiles_from_env(
    env: Mapping[str, str],
    selected: str,
) -> tuple[dict[str, ModelProfile], str]:
    raw_profiles = _json_mapping(env.get("LLM_PROVIDERS_JSON", ""), "LLM_PROVIDERS_JSON")
    if raw_profiles:
        profiles = {}
        for name, raw_profile in raw_profiles.items():
            if not isinstance(raw_profile, dict):
                raise RuntimeError(f"LLM_PROVIDERS_JSON.{name} must be an object.")
            profile = _profile_from_mapping(
                name=name,
                raw=raw_profile,
                env=env,
                selected=selected,
                require_key=True,
            )
            profiles[profile.name] = profile
        default_profile = _normalize_profile_name(
            env.get("LLM_DEFAULT_PROVIDER")
            or env.get("LLM_ROUTE_DEFAULT")
            or selected
        )
        if default_profile not in profiles:
            default_profile = next(iter(profiles))
        return profiles, default_profile

    profiles: dict[str, ModelProfile] = {}
    selected_profile = _profile_from_mapping(
        name=selected,
        raw={},
        env=env,
        selected=selected,
        require_key=True,
    )
    profiles[selected_profile.name] = selected_profile

    for name in DEFAULT_PROVIDER_SETTINGS:
        normalized = _normalize_profile_name(name)
        if normalized in profiles:
            continue
        profile = _profile_from_mapping(
            name=normalized,
            raw={},
            env=env,
            selected=selected,
            require_key=False,
        )
        if profile is not None:
            profiles[profile.name] = profile
    return profiles, selected_profile.name


def _profile_from_mapping(
    *,
    name: str,
    raw: Mapping[str, Any],
    env: Mapping[str, str],
    selected: str,
    require_key: bool,
) -> ModelProfile | None:
    profile_name = _normalize_profile_name(name)
    provider = _normalize_profile_name(str(raw.get("provider") or profile_name))
    default_settings = DEFAULT_PROVIDER_SETTINGS.get(provider, {})
    selected_match = profile_name == selected or provider == selected

    api_key = str(raw.get("api_key") or "").strip()
    if not api_key:
        api_key = _first_env(
            env,
            _api_key_env_names(
                profile_name=profile_name,
                provider=provider,
                selected_match=selected_match,
                explicit=raw.get("api_key_env"),
            ),
        )
    if not api_key:
        if not require_key:
            return None
        joined = " or ".join(
            _api_key_env_names(
                profile_name=profile_name,
                provider=provider,
                selected_match=selected_match,
                explicit=raw.get("api_key_env"),
            )
        )
        raise RuntimeError(f"Missing model API key for provider '{profile_name}'. Set {joined}.")

    base_url = (
        str(raw.get("base_url") or "").strip()
        or _first_env(
            env,
            _provider_env_names(
                profile_name,
                provider,
                selected_match=selected_match,
                suffix="BASE_URL",
            ),
        )
        or str(default_settings.get("base_url") or "").strip()
    )
    if not base_url:
        raise RuntimeError(
            f"Missing base URL for model provider '{profile_name}'. Set LLM_BASE_URL "
            f"or {_env_prefix(profile_name)}_BASE_URL."
        )

    model = (
        str(raw.get("model") or "").strip()
        or _first_env(
            env,
            _provider_env_names(
                profile_name,
                provider,
                selected_match=selected_match,
                suffix="MODEL",
            ),
        )
        or str(default_settings.get("model") or "").strip()
    )
    if not model:
        raise RuntimeError(
            f"Missing model name for model provider '{profile_name}'. Set LLM_MODEL "
            f"or {_env_prefix(profile_name)}_MODEL."
        )

    max_tokens_param = (
        str(raw.get("max_tokens_param") or "").strip()
        or _first_env(
            env,
            _provider_env_names(
                profile_name,
                provider,
                selected_match=selected_match,
                suffix="MAX_TOKENS_PARAM",
            ),
        )
        or str(default_settings.get("max_tokens_param") or "max_tokens").strip()
    )
    wire_api = _normalize_wire_api(
        str(raw.get("wire_api") or "").strip()
        or _first_env(
            env,
            _provider_env_names(
                profile_name,
                provider,
                selected_match=selected_match,
                suffix="WIRE_API",
            ),
        )
        or str(default_settings.get("wire_api") or "chat_completions").strip()
    )
    fallbacks = _parse_profile_list(raw.get("fallbacks", ()))

    return ModelProfile(
        name=profile_name,
        provider=provider,
        api_key=api_key,
        base_url=base_url,
        model=model,
        max_tokens_param=max_tokens_param,
        wire_api=wire_api,
        fallbacks=fallbacks,
    )


def _routes_from_env(
    env: Mapping[str, str],
    default_profile: str,
) -> dict[str, tuple[str, ...]]:
    routes: dict[str, tuple[str, ...]] = {
        "default": (_normalize_profile_name(default_profile),),
    }
    raw_routes = _json_mapping(env.get("LLM_ROUTES_JSON", ""), "LLM_ROUTES_JSON")
    for purpose, value in raw_routes.items():
        routes[_normalize_route_name(purpose)] = _parse_profile_list(value)

    for purpose, env_name in ROUTE_ENV_NAMES.items():
        value = str(env.get(env_name, "")).strip()
        if value:
            routes[purpose] = _parse_profile_list(value)

    fallback_chain = _parse_profile_list(env.get("LLM_ROUTE_FALLBACK", ""))
    if fallback_chain:
        for purpose, chain in list(routes.items()):
            routes[purpose] = _merge_profile_chains(chain, fallback_chain)
    return routes


def _api_key_env_names(
    *,
    profile_name: str,
    provider: str,
    selected_match: bool,
    explicit: Any,
) -> list[str]:
    names = []
    if explicit:
        names.extend(_parse_env_name_list(explicit))
    if selected_match:
        names.append("LLM_API_KEY")
    names.append(f"{_env_prefix(profile_name)}_API_KEY")
    names.append(f"{_env_prefix(provider)}_API_KEY")
    return _dedupe(names)


def _provider_env_names(
    profile_name: str,
    provider: str,
    *,
    selected_match: bool,
    suffix: str,
) -> list[str]:
    names = []
    if selected_match:
        names.append(f"LLM_{suffix}")
    names.append(f"{_env_prefix(profile_name)}_{suffix}")
    names.append(f"{_env_prefix(provider)}_{suffix}")
    return _dedupe(names)


def _json_mapping(value: str, env_name: str) -> dict[str, Any]:
    text = str(value or "").strip()
    if not text:
        return {}
    try:
        payload = json.loads(text)
    except json.JSONDecodeError as exc:
        raise RuntimeError(f"{env_name} must be valid JSON: {exc}") from exc
    if not isinstance(payload, dict):
        raise RuntimeError(f"{env_name} must be a JSON object.")
    return payload


def _parse_profile_list(value: Any) -> tuple[str, ...]:
    if value is None:
        return ()
    if isinstance(value, str):
        items = re.split(r"[,\s]+", value.strip()) if value.strip() else []
    elif isinstance(value, (list, tuple)):
        items = value
    else:
        raise RuntimeError("Model route values must be strings or lists.")
    return tuple(
        _normalize_profile_name(str(item))
        for item in items
        if str(item).strip()
    )


def _parse_env_name_list(value: Any) -> list[str]:
    if isinstance(value, str):
        return [item.strip() for item in re.split(r"[,\s]+", value) if item.strip()]
    if isinstance(value, (list, tuple)):
        return [str(item).strip() for item in value if str(item).strip()]
    raise RuntimeError("api_key_env must be a string or a list.")


def _first_env(env: Mapping[str, str], names: list[str]) -> str:
    for name in names:
        value = str(env.get(name, "")).strip()
        if value:
            return value
    return ""


def _merge_profile_chains(
    primary: tuple[str, ...],
    fallback: tuple[str, ...],
) -> tuple[str, ...]:
    result = list(primary)
    for item in fallback:
        if item not in result:
            result.append(item)
    return tuple(result)


def _normalize_profile_name(value: str) -> str:
    return str(value or "").strip().lower().replace("-", "_")


def _normalize_route_name(value: str) -> str:
    return _normalize_profile_name(value)


def _normalize_wire_api(value: str) -> str:
    normalized = str(value or "chat_completions").strip().lower().replace("-", "_")
    if normalized in {"chat", "chat_completion", "chat_completions"}:
        return "chat_completions"
    if normalized in {"response", "responses"}:
        return "responses"
    raise RuntimeError(
        "Model provider wire_api must be 'chat_completions' or 'responses'."
    )


def _env_prefix(value: str) -> str:
    return re.sub(r"[^A-Z0-9]+", "_", str(value).upper()).strip("_")


def _dedupe(items: list[str]) -> list[str]:
    seen = set()
    result = []
    for item in items:
        if item and item not in seen:
            seen.add(item)
            result.append(item)
    return result
