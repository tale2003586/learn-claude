from core.agent_spec import AgentSpec


class ModelTaskRunner:
    """Run one-shot model tasks that do not need tools or session lifecycle."""

    def __init__(
        self,
        *,
        provider=None,
        model: str = "",
        model_pool=None,
        default_max_tokens: int = 800,
    ) -> None:
        self.provider = provider
        self.model = model
        self.model_pool = model_pool
        self.default_max_tokens = max(1, int(default_max_tokens))

    def run(
        self,
        *,
        spec: AgentSpec,
        messages: list[dict],
        max_tokens: int | None = None,
    ) -> str:
        provider, model = self._provider_and_model(spec)
        response = provider.chat(
            model=model,
            messages=messages,
            tools=[],
            tool_choice="none",
            max_tokens=max(1, int(max_tokens or spec.max_tokens or self.default_max_tokens)),
        )
        return str(response.content or "")

    def _provider_and_model(self, spec: AgentSpec):
        if self.model_pool is not None:
            purpose = spec.model_purpose or "summary"
            return (
                self.model_pool.routed_provider(purpose),
                self.model_pool.model_for(purpose),
            )
        if self.provider is None:
            raise RuntimeError("ModelTaskRunner has no provider or model_pool.")
        return self.provider, self.model
