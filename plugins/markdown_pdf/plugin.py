import json
import os
import tempfile
from pathlib import Path

from plugins.base import Plugin, ToolRegistration
from plugins.markdown_pdf.renderer import render_markdown_pdf
from tools.schema import function_tool


DEFAULT_MAX_BYTES = 2 * 1024 * 1024
MARKDOWN_SUFFIXES = {".md", ".markdown"}


class MarkdownPdfPlugin(Plugin):
    name = "markdown_pdf"

    def setup(self, context) -> None:
        super().setup(context)
        self.workspace = context.workspace.resolve()
        self.max_bytes = _positive_int(
            os.getenv("MARKDOWN_PDF_MAX_BYTES"),
            default=DEFAULT_MAX_BYTES,
        )

    def tools(self) -> list[ToolRegistration]:
        return [
            ToolRegistration(
                schema=function_tool(
                    "markdown_to_pdf",
                    (
                        "Convert a workspace Markdown file into a PDF. Supports Chinese text, "
                        "headings, lists, quotes, code blocks, links, and safe local images. "
                        "The default output directory is storage/generated."
                    ),
                    {
                        "input_path": {
                            "type": "string",
                            "description": "Workspace-relative .md or .markdown source path.",
                        },
                        "output_path": {
                            "type": "string",
                            "description": (
                                "Optional workspace-relative .pdf destination. Defaults to "
                                "storage/generated/<source-name>.pdf."
                            ),
                        },
                        "title": {
                            "type": "string",
                            "description": "Optional document title.",
                        },
                        "overwrite": {
                            "type": "boolean",
                            "description": "Replace an existing PDF. Defaults to false.",
                        },
                    },
                    ["input_path"],
                ),
                handler=self.markdown_to_pdf,
                risk="normal",
                enabled_modes={"bot", "coding"},
                always_on=True,
                source="plugin:markdown_pdf",
            )
        ]

    def markdown_to_pdf(
        self,
        input_path: str,
        output_path: str | None = None,
        title: str | None = None,
        overwrite: bool = False,
    ) -> str:
        source = self._resolve_workspace_path(
            input_path,
            label="input_path",
            suffixes=MARKDOWN_SUFFIXES,
        )
        if not source.is_file():
            raise ValueError(f"Markdown source does not exist: {input_path}")

        size = source.stat().st_size
        if size > self.max_bytes:
            raise ValueError(
                f"Markdown source is too large: {size} bytes; "
                f"limit is {self.max_bytes} bytes"
            )

        destination_value = output_path or f"storage/generated/{source.stem}.pdf"
        destination = self._resolve_workspace_path(
            destination_value,
            label="output_path",
            suffixes={".pdf"},
        )
        if destination.exists() and not overwrite:
            raise ValueError(
                f"PDF already exists: {destination_value}; "
                "set overwrite=true to replace it"
            )
        if destination.is_dir():
            raise ValueError(f"PDF destination is a directory: {destination_value}")

        destination.parent.mkdir(parents=True, exist_ok=True)
        markdown_text = source.read_text(encoding="utf-8")
        temporary_path = None
        try:
            with tempfile.NamedTemporaryFile(
                prefix=f".{destination.stem}-",
                suffix=".pdf.tmp",
                dir=destination.parent,
                delete=False,
            ) as temporary:
                temporary_path = Path(temporary.name)
            result = render_markdown_pdf(
                markdown_text=markdown_text,
                source_path=source,
                output_path=temporary_path,
                workspace=self.workspace,
                title=(title or "").strip() or None,
            )
            os.replace(temporary_path, destination)
        finally:
            if temporary_path is not None and temporary_path.exists():
                temporary_path.unlink()
        return json.dumps(
            {
                "status": "created",
                "input_path": str(source.relative_to(self.workspace)),
                "output_path": str(destination.relative_to(self.workspace)),
                "bytes": destination.stat().st_size,
                "local_images": result.local_images,
                "skipped_images": result.skipped_images,
            },
            ensure_ascii=False,
            indent=2,
        )

    def _resolve_workspace_path(
        self,
        value: str,
        *,
        label: str,
        suffixes: set[str],
    ) -> Path:
        if not isinstance(value, str) or not value.strip():
            raise ValueError(f"{label} is required")

        relative = Path(value.strip())
        if relative.is_absolute():
            raise ValueError(f"{label} must be workspace-relative")

        resolved = (self.workspace / relative).resolve()
        try:
            resolved.relative_to(self.workspace)
        except ValueError as exc:
            raise ValueError(f"{label} must stay inside the workspace") from exc

        if resolved.suffix.lower() not in suffixes:
            allowed = ", ".join(sorted(suffixes))
            raise ValueError(f"{label} must use one of: {allowed}")
        return resolved


def _positive_int(value: str | None, *, default: int) -> int:
    if not value:
        return default
    try:
        parsed = int(value)
    except ValueError:
        return default
    return parsed if parsed > 0 else default
