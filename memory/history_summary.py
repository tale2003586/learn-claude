class HistorySummarizer:
    """Compact assistant replies before they enter derived memory files."""

    def __init__(
        self,
        provider=None,
        model: str = "",
        direct_limit: int = 240,
        summary_limit: int = 480,
    ):
        self.provider = provider
        self.model = model
        self.direct_limit = direct_limit
        self.summary_limit = summary_limit

    def summarize(self, assistant_text: str) -> str:
        text = assistant_text.strip()
        if not text:
            return ""
        if len(text) <= self.direct_limit or not self.provider or not self.model:
            return self._fallback(text)

        try:
            result = self.provider.chat(
                model=self.model,
                messages=[
                    {
                        "role": "system",
                        "content": (
                            "Summarize the assistant reply for long-term memory. "
                            "Keep decisions, conclusions, file paths, commands, and "
                            "unresolved issues. Remove conversational filler. "
                            "Return only a compact plain-text summary."
                        ),
                    },
                    {"role": "user", "content": text},
                ],
                tools=[],
                tool_choice="none",
                max_tokens=220,
            )
            summary = (result.content or "").strip()
            if summary:
                return self._trim(summary)
        except Exception:
            pass

        return self._fallback(text)

    def _fallback(self, text: str) -> str:
        return self._trim(text)

    def _trim(self, text: str) -> str:
        if len(text) <= self.summary_limit:
            return text
        return text[: self.summary_limit].rstrip() + "..."
