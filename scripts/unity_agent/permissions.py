"""Unity tool permission classification.

Every Unity MCP tool call is sorted into one of four sensitivity classes so the
harness can decide whether to allow it outright, ask the user, or deny it. The
functions here are **pure** (no I/O, no prompting) so they are trivially
testable; the interactive prompting lives in ``unity_agent.bootstrap``.

Classification order of precedence:
    1. Arbitrary-execution tools (run menu items / C# / custom tools / batches).
    2. read_console (read-only unless it clears the console).
    3. Screenshots -> read-only observation.
    4. An explicit MCP ``readOnlyHint`` -> read-only.
    5. The specific ``action`` argument, when present, is authoritative:
       read-only keyword -> READ_ONLY; delete/remove/destroy -> DESTRUCTIVE;
       otherwise MUTATING. (This deliberately overrides the coarse tool-level
       ``destructiveHint`` that Unity sets on multi-action tools such as
       ``manage_scene``/``manage_gameobject`` — otherwise reading a hierarchy or
       creating a Cube would be mis-flagged as destructive.)
    6. No action -> fall back to the ``destructiveHint`` annotation.
    7. Everything else defaults to MUTATING.
"""

from __future__ import annotations

from enum import Enum
from typing import Any, Optional


class PermissionClass(str, Enum):
    """Sensitivity tiers for Unity operations."""

    READ_ONLY = "read_only"
    MUTATING = "mutating"
    DESTRUCTIVE = "destructive"
    ARBITRARY_EXECUTION = "arbitrary_execution"


# Tools that can run arbitrary code, menu items, or batched operations.
ARBITRARY_TOOLS: frozenset[str] = frozenset(
    {"execute_menu_item", "execute_code", "execute_custom_tool", "batch_execute"}
)

# Action keywords that imply data loss / irreversible mutation.
DESTRUCTIVE_KEYWORDS: tuple[str, ...] = ("delete", "remove", "destroy")

# Action prefixes that only read state.
READ_ONLY_ACTION_PREFIXES: tuple[str, ...] = (
    "get",
    "read",
    "list",
    "search",
    "find",
    "query",
    "describe",
    "inspect",
    "status",
    "exists",
    "reflect",
    "docs",
    "fetch",
    "ping",
    "telemetry",
    "validate_",  # validate_script is read-only; bare "validate" (scene) may repair
)

# Decisions understood by the permission policy.
VALID_DECISIONS: frozenset[str] = frozenset({"allow", "ask", "deny"})

# Conservative defaults: everyday creation/edits flow freely; anything that can
# destroy work or execute arbitrary code asks first.
DEFAULT_POLICY: dict[PermissionClass, str] = {
    PermissionClass.READ_ONLY: "allow",
    PermissionClass.MUTATING: "allow",
    PermissionClass.DESTRUCTIVE: "ask",
    PermissionClass.ARBITRARY_EXECUTION: "ask",
}

_CONFIG_KEY_TO_CLASS: dict[str, PermissionClass] = {
    "read_only": PermissionClass.READ_ONLY,
    "mutating": PermissionClass.MUTATING,
    "destructive": PermissionClass.DESTRUCTIVE,
    "arbitrary_execution": PermissionClass.ARBITRARY_EXECUTION,
}


def classify_tool(
    base_name: str,
    arguments: Optional[dict[str, Any]] = None,
    annotations: Optional[dict[str, Any]] = None,
) -> PermissionClass:
    """Classify a Unity tool call by its *base* (un-namespaced) name.

    Args:
        base_name: The original MCP tool name, e.g. ``manage_gameobject``.
        arguments: The tool call arguments (may contain an ``action`` field).
        annotations: MCP tool annotations (``readOnlyHint``/``destructiveHint``).
    """
    args = arguments or {}
    ann = annotations or {}
    name = (base_name or "").strip().lower()
    action = str(args.get("action", "")).strip().lower()
    has_action = bool(action)

    # 1) Arbitrary execution — always the most sensitive.
    if name in ARBITRARY_TOOLS:
        return PermissionClass.ARBITRARY_EXECUTION

    # 2) read_console is read-only unless it clears the console buffer.
    if name == "read_console":
        return PermissionClass.MUTATING if action == "clear" else PermissionClass.READ_ONLY

    # 3) Screenshots are treated as observation.
    if "screenshot" in name or "screenshot" in action:
        return PermissionClass.READ_ONLY

    # 4) An explicit read-only annotation is authoritative.
    if ann.get("readOnlyHint") is True:
        return PermissionClass.READ_ONLY

    # 5) The specific action wins over the coarse tool-level destructiveHint.
    #    Unity sets destructiveHint=True on multi-action tools (manage_scene,
    #    manage_gameobject, ...), so we must classify by action first.
    if has_action:
        if action.startswith(READ_ONLY_ACTION_PREFIXES):
            return PermissionClass.READ_ONLY
        if any(keyword in action for keyword in DESTRUCTIVE_KEYWORDS):
            return PermissionClass.DESTRUCTIVE
        return PermissionClass.MUTATING

    # 6) No action to reason about — fall back to the destructive annotation.
    if ann.get("destructiveHint") is True:
        return PermissionClass.DESTRUCTIVE

    # 7) Default: a normal mutation.
    return PermissionClass.MUTATING


def load_policy(unity_cfg: Optional[dict[str, Any]]) -> dict[PermissionClass, str]:
    """Merge the ``unity.permission`` config over the safe defaults."""
    perm = ((unity_cfg or {}).get("permission") or {})
    policy = dict(DEFAULT_POLICY)
    for config_key, cls in _CONFIG_KEY_TO_CLASS.items():
        value = perm.get(config_key)
        if isinstance(value, str) and value in VALID_DECISIONS:
            policy[cls] = value
    return policy


def decide(policy: dict[PermissionClass, str], cls: PermissionClass) -> str:
    """Return the configured decision (``allow``/``ask``/``deny``) for a class."""
    return policy.get(cls, "ask")
