"""Wire the Unity MCP capability into the InplusCode harness.

The :class:`UnityRuntime` owns the Unity-specific state (MCP client, discovered
tools, permission policy, operation log) and exposes:

    * Four **static** diagnostic tools that exist even when Unity is offline:
      ``unity_status``, ``unity_connect``, ``unity_reload_tools``,
      ``unity_disconnect``.
    * Dynamic discovery that merges the live Unity MCP tools into the shared
      tool pool passed to ``agent_loop`` (mutated in place, so discovery works
      mid-session).
    * A ``PreToolUse`` permission hook that classifies Unity operations and
      applies the configured policy — independent of the global bash policy and
      active even when ``permission.mode`` is ``off``.

Nothing here connects automatically at import time; ``UnityCode.py`` decides when
to connect based on config.
"""

from __future__ import annotations

from typing import Any, Callable, Optional

from rich import print

from utils.hooks import register_hook
from utils.system import load_config
from utils.mcp_client import MCPClient, MCPConnectionError, create_client_from_config

from .permissions import PermissionClass, classify_tool, decide, load_policy
from .session_state import SessionState
from .tool_adapter import build_tools_and_handlers

DEFAULT_PREFIX = "mcp__unity__"

# Static tools are always present so the model can diagnose/connect even when the
# Unity Editor is not running.
_STATIC_TOOL_SPECS: list[dict[str, Any]] = [
    {
        "name": "unity_status",
        "description": "Report Unity MCP connection status, server URL, and the number of discovered Unity tools.",
        "input_schema": {"type": "object", "properties": {}},
    },
    {
        "name": "unity_connect",
        "description": "Connect to the Unity MCP server (Streamable HTTP) and discover the live Unity tools.",
        "input_schema": {"type": "object", "properties": {}},
    },
    {
        "name": "unity_reload_tools",
        "description": "Re-discover Unity MCP tools. Use after a Unity domain reload or when tools change.",
        "input_schema": {"type": "object", "properties": {}},
    },
    {
        "name": "unity_disconnect",
        "description": "Disconnect from the Unity MCP server and remove the dynamic Unity tools.",
        "input_schema": {"type": "object", "properties": {}},
    },
]


def _short_args(arguments: dict[str, Any], limit: int = 120) -> str:
    text = str(arguments)
    return text if len(text) <= limit else text[:limit] + " ..."


class UnityRuntime:
    """Owns Unity MCP state and integrates it with the shared tool pool."""

    def __init__(self, tools: list[dict[str, Any]], handlers: dict[str, Callable[..., str]], *,config: Optional[dict[str, Any]] = None,) -> None: # fmt: skip
        config = config if config is not None else load_config()
        self.unity_cfg: dict[str, Any] = config.get("unity", {}) or {}
        tools_config = self.unity_cfg.get("tools", {}) or {}

        self.tools = tools  # shared, mutated in place
        self.handlers = handlers  # shared, mutated in place

        self.prefix: str = tools_config.get("prefix", DEFAULT_PREFIX)  # 用于动态工具的命名空间前缀

        self.allow_groups: Optional[list[str]] = tools_config.get("allow_groups")
        self.deny_tools: list[str] = tools_config.get("deny_tools", []) or []
        self.policy = load_policy(self.unity_cfg)

        self.state = SessionState()
        self._client: Optional[MCPClient] = None
        self._tool_meta: dict[str, dict[str, Any]] = {}
        self._dynamic_names: set[str] = set()
        self._installed = False

    # ── Setup ─────────────────────────────────────────────────────────────
    def install(self) -> "UnityRuntime":
        """Add static diagnostic tools and register the permission hook."""
        if self._installed:
            return self
        existing = {t["name"] for t in self.tools}
        for spec in _STATIC_TOOL_SPECS:
            if spec["name"] not in existing:
                self.tools.append(dict(spec))
        self.handlers.setdefault("unity_status", self._tool_status)
        self.handlers.setdefault("unity_connect", self._tool_connect)
        self.handlers.setdefault("unity_reload_tools", self._tool_reload)
        self.handlers.setdefault("unity_disconnect", self._tool_disconnect)
        register_hook("PreToolUse", self.permission_hook)
        self._installed = True
        return self

    @property
    def connected(self) -> bool:
        return self._client is not None and self._client.connected

    def enabled(self) -> bool:
        return bool(self.unity_cfg.get("enabled", True))

    def auto_connect_configured(self) -> bool:
        mcp_cfg = self.unity_cfg.get("mcp", {}) or {}
        return bool(mcp_cfg.get("auto_connect", True))

    def maybe_auto_connect(self) -> str:
        """Connect only if enabled and configured; never raise."""
        if not self.enabled():
            return "Unity agent disabled in config (unity.enabled = false)."
        if not self.auto_connect_configured():
            return "Unity auto-connect is off. Use the unity_connect tool when ready."
        return self.connect()

    # ── Connection lifecycle ──────────────────────────────────────────────
    def connect(self) -> str:
        try:
            if self._client is None:
                self._client = create_client_from_config(self.unity_cfg)
            if not self._client.connected:
                self._client.start()
        except ImportError:
            return "Unity MCP unavailable: the 'mcp' Python SDK is not installed. " 'Install it with: pip install "mcp>=1.2,<2"'
        except MCPConnectionError as exc:
            return f"Unity MCP connection failed: {exc}"
        except Exception as exc:  # noqa: BLE001
            return f"Unity MCP connection error: {exc.__class__.__name__}: {exc}"
        return self._discover()

    def _discover(self) -> str:
        try:
            mcp_tools = self._client.list_tools()  # type: ignore[union-attr]
        except MCPConnectionError as exc:
            return f"Connected but failed to list Unity tools: {exc}"
        tools, handlers, meta = build_tools_and_handlers(
            mcp_tools,
            prefix=self.prefix,
            call_tool=self._call_via_client,
            allow_groups=self.allow_groups,
            deny_tools=self.deny_tools,
        )
        self._replace_dynamic(tools, handlers, meta)
        return (
            f"Connected to Unity MCP at {self._client.url}. "  # type: ignore[union-attr]
            f"Discovered {len(mcp_tools)} tools, exposed {len(tools)} "
            f"(prefix '{self.prefix}', groups {self.allow_groups or 'all'})."
        )

    def _replace_dynamic(
        self,
        tools: list[dict[str, Any]],
        handlers: dict[str, Callable[..., str]],
        meta: dict[str, dict[str, Any]],
    ) -> None:
        # Remove any previously discovered dynamic tools first.
        for name in self._dynamic_names:
            self.handlers.pop(name, None)
            self._tool_meta.pop(name, None)
        self.tools[:] = [t for t in self.tools if t["name"] not in self._dynamic_names]
        self._dynamic_names.clear()
        # Add the freshly discovered set.
        for tool in tools:
            self.tools.append(tool)
            self._dynamic_names.add(tool["name"])
        self.handlers.update(handlers)
        self._tool_meta.update(meta)

    def _call_via_client(self, base_name: str, arguments: dict[str, Any]) -> Any:
        if self._client is None or not self._client.connected:
            raise MCPConnectionError("Unity MCP is not connected. Use the unity_connect tool first.")
        return self._client.call_tool(base_name, arguments)

    def close(self) -> None:
        if self._client is not None:
            try:
                self._client.close()
            finally:
                self._client = None

    # ── Static diagnostic tool handlers ──────────────────────────────────
    def _tool_status(self, **_kwargs: Any) -> str:
        mcp_cfg = self.unity_cfg.get("mcp", {}) or {}
        url = mcp_cfg.get("url", "http://127.0.0.1:8080/mcp")
        status = "connected" if self.connected else "disconnected"
        return (
            f"Unity MCP status: {status}\n"
            f"url: {self._client.url if self._client else url}\n"
            f"enabled: {self.enabled()}\n"
            f"dynamic tools: {len(self._dynamic_names)}\n"
            f"policy: {{{', '.join(f'{k.value}={v}' for k, v in self.policy.items())}}}\n"
            f"recent operations:\n{self.state.describe_recent()}"
        )

    def _tool_connect(self, **_kwargs: Any) -> str:
        return self.connect()

    def _tool_reload(self, **_kwargs: Any) -> str:
        if not self.connected:
            return "Not connected. Use unity_connect first."
        return self._discover()

    def _tool_disconnect(self, **_kwargs: Any) -> str:
        self._replace_dynamic([], {}, {})
        self.close()
        return "Disconnected from Unity MCP and removed dynamic Unity tools."

    # ── Permission hook ───────────────────────────────────────────────────
    def permission_hook(self, block: Any) -> Optional[str]:
        """PreToolUse hook: gate Unity tool calls by sensitivity class.

        Returns ``None`` to allow, or a denial string to block. Non-Unity tools
        are ignored (returns ``None``) so this composes with the bash hook.
        """
        name = getattr(block, "name", "")
        if not isinstance(name, str) or not name.startswith(self.prefix):
            return None  # not a namespaced Unity tool — not our concern

        meta = self._tool_meta.get(name, {})
        base = meta.get("base", name[len(self.prefix) :])
        annotations = meta.get("annotations", {})
        arguments = getattr(block, "input", {}) or {}

        cls = classify_tool(base, arguments, annotations)
        decision = decide(self.policy, cls)

        if decision == "allow":
            self._record(base, cls, arguments)
            return None
        if decision == "deny":
            print(f"\n[yellow]Unity {cls.value} operation denied by policy:[/yellow] {base}")
            return f"Permission denied by config: Unity {cls.value} operation '{base}'."
        return self._ask(base, cls, arguments)

    def _ask(self, base: str, cls: PermissionClass, arguments: dict[str, Any]) -> Optional[str]:
        import sys

        print(f"\n[yellow]Unity {cls.value} operation requires approval[/yellow]  " f"{base}({_short_args(arguments)})")
        stdin = getattr(sys, "stdin", None)
        if stdin is None or not stdin.isatty():
            return (
                f"Permission denied: Unity {cls.value} operation '{base}' needs interactive "
                f"approval but no interactive terminal is available."
            )
        choice = input("  Allow? [y/N] ").strip().lower()
        if choice in ("y", "yes"):
            self._record(base, cls, arguments)
            return None
        return f"Permission denied by user: Unity {cls.value} operation '{base}'."

    def _record(self, base: str, cls: PermissionClass, arguments: dict[str, Any]) -> None:
        if cls in (PermissionClass.MUTATING, PermissionClass.DESTRUCTIVE):
            note = (
                "Reversible via Unity Editor Undo if still in the same session."
                if cls is PermissionClass.MUTATING
                else "Destructive: save/back up the scene beforehand; not auto-recoverable."
            )
            self.state.record(base, cls.value, arguments, recoverable=False, note=note)

    # ── System prompt appendix ────────────────────────────────────────────
    def system_appendix(self) -> str:
        status = "connected" if self.connected else "not connected"
        return (
            "\n\n# Unity Agent mode\n"
            f"You can observe and edit the Unity Editor via MCP tools namespaced '{self.prefix}'. "
            f"Unity MCP is currently {status}. Use unity_status/unity_connect/unity_reload_tools/unity_disconnect to manage the connection.\n"
            'When the task involves Unity, FIRST call load_skill("unity-agent") and follow it.\n'
            "Work Observe -> Act -> Verify: read editor/scene/hierarchy/console before changing things, "
            "then re-read the changed objects, wait for compilation, and check the Console after mutations.\n"
            "Prefer the structured Unity MCP tools; never hand-edit .unity/.prefab/.meta YAML. "
            "Verify Unity APIs with the reflection/docs tools instead of guessing.\n"
            "Destructive and arbitrary-execution Unity operations may require approval; keep them minimal and explicit."
        )


def build_unity_runtime(tools: list[dict[str, Any]], handlers: dict[str, Callable[..., str]], *, config: Optional[dict[str, Any]] = None,) -> UnityRuntime: # fmt: skip
    """Create and install a :class:`UnityRuntime` over a shared tool pool."""
    runtime = UnityRuntime(tools, handlers, config=config)
    runtime.install()
    return runtime
