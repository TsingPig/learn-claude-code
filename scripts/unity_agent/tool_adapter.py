"""Adapt MCP tools into the harness's Anthropic tool format.

Responsibilities:
    * Normalize tool names to the safe charset ``[A-Za-z0-9_-]``.
    * Add a configurable namespace prefix (e.g. ``mcp__unity__``) to avoid
      collisions with the built-in tools, resolving duplicates deterministically.
    * Convert MCP ``inputSchema`` into Anthropic ``input_schema``.
    * Create one handler per tool, **binding the base name safely** so the
      classic Python late-binding-in-a-loop bug cannot occur.
    * Apply group allow-listing and per-tool deny-listing from config.

The adapter is deliberately generic about the MCP server; only the group map
encodes Unity-specific knowledge, and unknown tools default to the ``core``
group so newly added server tools remain available.
"""

from __future__ import annotations

import re
from typing import Any, Callable, Optional

from .permissions import ARBITRARY_TOOLS
from .result_formatter import format_error, format_result

# base tool name -> logical group. Unlisted tools default to "core" (included by
# the default allow-list). Arbitrary-execution tools are grouped separately so
# they can be withheld unless explicitly allowed.
_TOOL_GROUPS: dict[str, str] = {
    # testing
    "run_tests": "testing",
    "get_test_job": "testing",
    # materials / rendering
    "manage_material": "materials",
    "manage_shader": "materials",
    "manage_texture": "materials",
    "manage_graphics": "materials",
    # prefabs
    "manage_prefabs": "prefabs",
    # heavier / optional capabilities
    "manage_ui": "extras",
    "manage_physics": "extras",
    "manage_animation": "extras",
    "manage_vfx": "extras",
    "manage_probuilder": "extras",
    "manage_packages": "extras",
    "manage_build": "extras",
    "manage_profiler": "extras",
    "manage_tools": "extras",
    "generate_image": "extras",
    "generate_audio": "extras",
    "generate_model": "extras",
    "import_model": "extras",
    "import_model_file": "extras",
    "debug_request_context": "extras",
}

_NAME_SAFE_RE = re.compile(r"[^A-Za-z0-9_-]")


def normalize_tool_name(name: str) -> str:
    """Reduce a raw tool name to the safe charset ``[A-Za-z0-9_-]``.

    Any other character (including dots, colons, spaces, slashes) becomes ``_``.
    Empty input yields ``"tool"`` so a usable name is always produced.
    """
    if not name:
        return "tool"
    cleaned = _NAME_SAFE_RE.sub("_", name)
    return cleaned or "tool"


def namespaced_name(base_name: str, prefix: str) -> str:
    """Prefix a normalized base name, keeping the whole string in the safe set."""
    safe_prefix = _NAME_SAFE_RE.sub("_", prefix or "")
    return f"{safe_prefix}{normalize_tool_name(base_name)}"


def group_for(base_name: str) -> str:
    """Return the logical group for a base tool name."""
    name = (base_name or "").lower()
    if name in ARBITRARY_TOOLS:
        return "arbitrary"
    return _TOOL_GROUPS.get(name, "core")


def is_allowed(base_name: str, allow_groups: Optional[list[str]], deny_tools: Optional[list[str]]) -> bool:
    """Decide whether a tool is exposed, per allow-groups and deny-list."""
    if deny_tools and base_name in deny_tools:
        return False
    if not allow_groups:  # empty / None -> allow all groups
        return True
    if "all" in allow_groups:
        return True
    return group_for(base_name) in allow_groups


def to_input_schema(mcp_tool: Any) -> dict[str, Any]:
    """Extract a JSON-schema ``input_schema`` from an MCP tool object/dict."""
    schema = None
    if isinstance(mcp_tool, dict):
        schema = mcp_tool.get("inputSchema") or mcp_tool.get("input_schema")
    else:
        schema = getattr(mcp_tool, "inputSchema", None)
    if not isinstance(schema, dict) or not schema:
        # Anthropic requires an object schema; provide a permissive default.
        return {"type": "object", "properties": {}}
    # Ensure a "type" is present so the Anthropic API accepts it.
    if "type" not in schema:
        schema = {**schema, "type": "object"}
    return schema


def _get_field(mcp_tool: Any, field: str) -> Any:
    if isinstance(mcp_tool, dict):
        return mcp_tool.get(field)
    return getattr(mcp_tool, field, None)


def to_anthropic_tool(mcp_tool: Any, prefix: str, taken_names: set[str]) -> dict[str, Any]:
    """Convert a single MCP tool into an Anthropic tool dict.

    ``taken_names`` is mutated to reserve the chosen (possibly de-duplicated)
    name so subsequent calls avoid collisions.
    """
    base_name = _get_field(mcp_tool, "name") or "tool"
    description = _get_field(mcp_tool, "description") or f"Unity MCP tool '{base_name}'."

    name = namespaced_name(base_name, prefix)
    # Deterministic de-duplication if the namespaced name is already taken.
    if name in taken_names:
        suffix = 2
        while f"{name}_{suffix}" in taken_names:
            suffix += 1
        name = f"{name}_{suffix}"
    taken_names.add(name)

    return {
        "name": name,
        "description": str(description),
        "input_schema": to_input_schema(mcp_tool),
    }


def _annotations_dict(mcp_tool: Any) -> dict[str, Any]:
    ann = _get_field(mcp_tool, "annotations")
    if ann is None:
        return {}
    if isinstance(ann, dict):
        return ann
    if hasattr(ann, "model_dump"):
        try:
            return ann.model_dump(exclude_none=True)
        except Exception:  # noqa: BLE001
            return {}
    return {}


def make_handler(call_tool: Callable[[str, dict], Any], base_name: str) -> Callable[..., str]:
    """Build a handler bound to ``base_name`` (late-binding safe).

    ``base_name`` is passed as a function argument, so the returned closure
    captures it by value — not by reference to a loop variable.
    """

    def handler(**kwargs: Any) -> str:
        try:
            result = call_tool(base_name, kwargs)
        except Exception as exc:  # noqa: BLE001 - convert to stable tool_result
            return format_error(base_name, exc)
        return format_result(base_name, result)

    handler.__name__ = f"unity_{normalize_tool_name(base_name)}"
    handler.__qualname__ = handler.__name__
    return handler


def build_tools_and_handlers(
    mcp_tools: list[Any],
    *,
    prefix: str,
    call_tool: Callable[[str, dict], Any],
    allow_groups: Optional[list[str]] = None,
    deny_tools: Optional[list[str]] = None,
) -> tuple[list[dict[str, Any]], dict[str, Callable[..., str]], dict[str, dict[str, Any]]]:
    """Adapt a list of MCP tools into Anthropic tools + handlers + metadata.

    Returns:
        (tools, handlers, meta) where
          * tools    — list of Anthropic tool dicts,
          * handlers — namespaced_name -> handler callable,
          * meta     — namespaced_name -> {"base": str, "annotations": dict}.
    """
    tools: list[dict[str, Any]] = []
    handlers: dict[str, Callable[..., str]] = {}
    meta: dict[str, dict[str, Any]] = {}
    taken_names: set[str] = set()

    for mcp_tool in mcp_tools:
        base_name = _get_field(mcp_tool, "name") or "tool"
        if not is_allowed(base_name, allow_groups, deny_tools):
            continue
        anthropic_tool = to_anthropic_tool(mcp_tool, prefix, taken_names)
        namespaced = anthropic_tool["name"]
        tools.append(anthropic_tool)
        handlers[namespaced] = make_handler(call_tool, base_name)
        meta[namespaced] = {"base": base_name, "annotations": _annotations_dict(mcp_tool)}

    return tools, handlers, meta
