"""Serialize MCP tool results into compact, model-friendly strings.

Unity MCP can return very large payloads (full hierarchies, asset lists, and —
critically — base64-encoded screenshots). Dumping those verbatim into the LLM
context is wasteful and can blow the token budget. This module:

    * Extracts text content as-is.
    * Replaces image/binary blobs with a short metadata placeholder (never the
      raw base64).
    * Prefers file paths for screenshots so the model can reason about the
      artifact without ingesting it.
    * Serializes structured content as compact JSON.
    * Truncates the final string to a bounded size, leaving a clear marker.
    * Turns MCP errors into actionable, stably-typed strings (never raises).

The output is always a ``str`` so it composes with the existing large-output
hook and context-compaction pipeline.
"""

from __future__ import annotations

import json
from typing import Any, Optional

# Bound the size handed back to the model. The context-compaction pipeline may
# persist/trim further, but we pre-truncate to avoid pathological payloads.
MAX_RESULT_CHARS = 20000
_IMAGE_PLACEHOLDER = "[image omitted: {mime}, ~{size} base64 chars — not sent to the model]"


def _compact_json(value: Any) -> str:
    try:
        return json.dumps(value, ensure_ascii=False, default=str, separators=(",", ":"))
    except (TypeError, ValueError):
        return str(value)


def _stringify_block(block: Any) -> Optional[str]:
    """Convert one MCP content block into a string, dropping raw base64."""
    block_type = getattr(block, "type", None)

    # Text content.
    if block_type == "text" or hasattr(block, "text"):
        text = getattr(block, "text", None)
        if text is not None:
            return str(text)

    # Image / audio content — never forward the base64 payload.
    if block_type in ("image", "audio") or hasattr(block, "data"):
        mime = getattr(block, "mimeType", None) or getattr(block, "mime_type", "") or block_type or "binary"
        data = getattr(block, "data", "") or ""
        return _IMAGE_PLACEHOLDER.format(mime=mime, size=len(data))

    # Embedded resource (may carry a uri / text).
    resource = getattr(block, "resource", None)
    if resource is not None:
        uri = getattr(resource, "uri", None)
        text = getattr(resource, "text", None)
        if text is not None:
            return str(text)
        if uri is not None:
            return f"[resource: {uri}]"

    # Fallback: dump whatever pydantic model we got, compactly.
    if hasattr(block, "model_dump"):
        try:
            return _compact_json(block.model_dump(mode="json", exclude_none=True))
        except Exception:  # noqa: BLE001
            return str(block)
    return str(block)


def _truncate(text: str, limit: int = MAX_RESULT_CHARS) -> str:
    if len(text) <= limit:
        return text
    omitted = len(text) - limit
    return text[:limit] + f"\n...[truncated {omitted} chars — narrow the query or use paging]"


def format_error(tool_name: str, error: BaseException, *, hint: str = "") -> str:
    """Render an exception as a stable, diagnostic tool_result string."""
    error_type = error.__class__.__name__
    message = str(error) or error_type
    suffix = f" Hint: {hint}" if hint else ""
    return f"[unity-tool-error] tool={tool_name} type={error_type}: {message}.{suffix}"


def format_result(tool_name: str, call_result: Any) -> str:
    """Serialize a ``CallToolResult`` (or similar) into a compact string.

    Args:
        tool_name: The namespaced tool name, for error attribution.
        call_result: An ``mcp.types.CallToolResult`` or any object exposing
            ``content`` / ``structuredContent`` / ``isError``.
    """
    if call_result is None:
        return "(no result)"

    is_error = bool(getattr(call_result, "isError", False))
    content = getattr(call_result, "content", None)
    structured = getattr(call_result, "structuredContent", None)

    parts: list[str] = []
    if isinstance(content, list):
        for block in content:
            rendered = _stringify_block(block)
            if rendered:
                parts.append(rendered)
    elif content is not None:
        parts.append(str(content))

    body = "\n".join(p for p in parts if p).strip()

    # Fall back to structured content when there is no textual body.
    if not body and structured is not None:
        body = _compact_json(structured)

    if not body:
        body = "(empty result)"

    if is_error:
        return _truncate(
            f"[unity-tool-error] tool={tool_name}: {body} "
            f"Diagnose the arguments (action/target names, paths) and retry."
        )

    return _truncate(body)
