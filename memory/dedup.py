import re


_BULLET_RE = re.compile(r"^\s*[-*]\s*")
_TAG_RE = re.compile(r"^\[[^\]]+\]\s*")
_SOURCE_RE = re.compile(r"\s*\(source:\s*`[^`]+`\)\s*$")
_PUNCT_RE = re.compile(r"[`*_#>\-\[\]（）()，。,.!！?？:：；;\s\"']")


def normalize_memory_text(text: str) -> str:
    value = str(text or "").strip().lower()
    value = _BULLET_RE.sub("", value)
    value = _TAG_RE.sub("", value)
    value = _SOURCE_RE.sub("", value)
    value = _PUNCT_RE.sub("", value)
    return value


def parse_memory_items(markdown: str) -> list[str]:
    items: list[str] = []
    for line in markdown.splitlines():
        stripped = line.strip()
        if stripped.startswith(("-", "*")):
            items.append(stripped)
    return items


def is_duplicate_memory(new_text: str, existing_markdown: str) -> bool:
    new_norm = normalize_memory_text(new_text)
    if not new_norm:
        return True
    for item in parse_memory_items(existing_markdown):
        if normalize_memory_text(item) == new_norm:
            return True
    return False
