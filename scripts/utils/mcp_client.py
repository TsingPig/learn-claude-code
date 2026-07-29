"""Generic synchronous MCP client.

This module is intentionally **domain-agnostic**: it knows how to talk to any
Model Context Protocol (MCP) server over Streamable HTTP, but it knows nothing
about Unity. Unity-specific behaviour lives in ``scripts/unity_agent``.

Why a sync wrapper?
    The MCP Python SDK is asyncio-based, while the InplusCode agent loop is a
    plain synchronous ``while`` loop. Rather than sprinkle ``asyncio.run()`` at
    every call site (which re-does the TCP/HTTP handshake every time), we run a
    single background thread that owns one asyncio event loop and one persistent
    ``ClientSession``. Calls from the main thread are marshalled onto that loop
    with :func:`asyncio.run_coroutine_threadsafe`.

Lifecycle:
    client = MCPClient("http://127.0.0.1:8080/mcp")
    client.start()            # connect + initialize (blocking)
    tools = client.list_tools()
    result = client.call_tool("manage_scene", {"action": "get_active"})
    client.close()            # graceful shutdown, joins the bg thread

The transport is created in a single seam (:meth:`_open_transport`) so a future
CLI-based transport can be dropped in behind the same interface without touching
call sites.
"""

from __future__ import annotations

import asyncio
import threading
from concurrent.futures import TimeoutError as FutureTimeoutError
from datetime import timedelta
from typing import Any, Callable, Optional

# NOTE: ``mcp`` is an optional dependency. Import lazily inside methods so that
# importing this module never crashes when the SDK is absent (e.g. the plain
# InplusCode agent running without Unity extras installed).


class MCPConnectionError(RuntimeError):
    """Raised when the MCP server cannot be reached or the session drops.

    The message is deliberately human-friendly so it can be surfaced straight to
    the model as a tool_result instead of a raw traceback.
    """


class MCPNotConnectedError(MCPConnectionError):
    """Raised when a call is attempted before :meth:`start` succeeds."""


def _friendly_error(exc: BaseException, url: str) -> str:
    """Turn a low-level transport exception into actionable guidance."""
    text = str(exc) or exc.__class__.__name__
    lowered = text.lower()
    hint = ""
    if any(k in lowered for k in ("refused", "connect", "connection", "reset", "10061")):
        hint = (
            " Is the Unity Editor open with MCP for Unity running, and is the "
            "MCP HTTP server listening at this URL? Start the server, then retry."
        )
    elif "timeout" in lowered or "timed out" in lowered:
        hint = (
            " The server did not respond in time. Confirm the Unity Editor is " "not busy compiling and that the MCP server is reachable."
        )
    return f"Cannot reach MCP server at {url}: {text}.{hint}"


class MCPClient:
    """Thread-safe synchronous facade over an async MCP ``ClientSession``.

    Only Streamable HTTP is implemented natively. The single transport seam
    (:meth:`_open_transport`) keeps the door open for alternative transports
    (e.g. wrapping a CLI) without changing any call site.
    """

    def __init__(
        self,
        url: str,
        *,
        headers: Optional[dict[str, str]] = None,
        timeout_seconds: float = 30.0,
        reconnect_attempts: int = 1,
        sse_read_timeout_seconds: float = 300.0,
    ) -> None:
        self.url = url
        self.headers = headers or {}
        self.timeout_seconds = float(timeout_seconds)
        self.reconnect_attempts = max(0, int(reconnect_attempts))
        self.sse_read_timeout_seconds = float(sse_read_timeout_seconds)

        self._loop: Optional[asyncio.AbstractEventLoop] = None
        self._thread: Optional[threading.Thread] = None
        self._session: Any = None  # mcp.ClientSession when connected
        self._session_task: Optional[asyncio.Future] = None
        self._ready: Optional[asyncio.Event] = None
        self._stop: Optional[asyncio.Event] = None
        self._connect_error: Optional[BaseException] = None
        self._started = False
        self._lock = threading.Lock()

    # ── Public API ────────────────────────────────────────────────────────
    @property
    def connected(self) -> bool:
        return self._started and self._session is not None

    def start(self) -> None:
        """Spin up the background loop and establish the MCP session.

        Blocks until the session is initialised or raises
        :class:`MCPConnectionError` on failure. Idempotent.
        """
        with self._lock:
            if self._started:
                return
            self._loop = asyncio.new_event_loop()
            self._thread = threading.Thread(target=self._run_loop, name="mcp-client-loop", daemon=True)
            self._thread.start()
            self._connect_blocking()
            self._started = True

    def list_tools(self) -> list[Any]:
        """Return the server's advertised tools (``list[mcp.types.Tool]``)."""
        self._require_connected()
        result = self._run_coro(self._list_tools(), self.timeout_seconds + 5)
        return list(result.tools)

    def call_tool(self, name: str, arguments: Optional[dict[str, Any]] = None, *, timeout: Optional[float] = None) -> Any:
        """Invoke a tool and return the raw ``CallToolResult``.

        On a transport-level failure this performs up to ``reconnect_attempts``
        automatic reconnect-and-retry cycles before raising
        :class:`MCPConnectionError`.
        """
        self._require_connected()
        call_timeout = timeout if timeout is not None else (self.timeout_seconds + 10)
        attempts = self.reconnect_attempts + 1
        last_exc: Optional[BaseException] = None
        for attempt in range(attempts):
            try:
                return self._run_coro(self._call_tool(name, arguments or {}), call_timeout)
            except (MCPConnectionError, FutureTimeoutError, ConnectionError) as exc:
                last_exc = exc
                if attempt < attempts - 1:
                    try:
                        self._reconnect()
                    except MCPConnectionError as reconnect_exc:
                        last_exc = reconnect_exc
                        break
                    continue
                break
        raise MCPConnectionError(_friendly_error(last_exc or RuntimeError("unknown"), self.url))

    def reload_tools(self) -> list[Any]:
        """Re-fetch the tool list (tools can change after a domain reload)."""
        return self.list_tools()

    def list_resources(self) -> list[Any]:
        """Return the server's advertised resources (``list[mcp.types.Resource]``)."""
        self._require_connected()
        result = self._run_coro(self._list_resources(), self.timeout_seconds + 5)
        return list(getattr(result, "resources", []) or [])

    def read_resource(self, uri: str) -> Any:
        """Read a resource by URI, returning the raw ``ReadResourceResult``."""
        self._require_connected()
        return self._run_coro(self._read_resource(uri), self.timeout_seconds + 5)

    def close(self) -> None:
        """Tear down the session and background loop. Safe to call twice."""
        with self._lock:
            if not self._started:
                return
            try:
                self._stop_session()
            finally:
                loop = self._loop
                if loop is not None and loop.is_running():
                    loop.call_soon_threadsafe(loop.stop)
                if self._thread is not None:
                    self._thread.join(timeout=5)
                self._loop = None
                self._thread = None
                self._session = None
                self._started = False

    # ── Context manager sugar ─────────────────────────────────────────────
    def __enter__(self) -> "MCPClient":
        self.start()
        return self

    def __exit__(self, *_exc: object) -> None:
        self.close()

    # ── Internal: event loop thread ───────────────────────────────────────
    def _run_loop(self) -> None:
        assert self._loop is not None
        asyncio.set_event_loop(self._loop)
        try:
            self._loop.run_forever()
        finally:
            try:
                self._loop.run_until_complete(self._loop.shutdown_asyncgens())
            except Exception:
                pass
            self._loop.close()

    def _connect_blocking(self) -> None:
        future = asyncio.run_coroutine_threadsafe(self._connect(), self._loop)  # type: ignore[arg-type]
        try:
            future.result(timeout=self.timeout_seconds + 10)
        except FutureTimeoutError as exc:
            raise MCPConnectionError(
                f"Timed out connecting to MCP server at {self.url} "
                f"after {self.timeout_seconds}s. Is the Unity Editor open with "
                f"MCP running at this URL?"
            ) from exc

    # ── Internal: transport seam ──────────────────────────────────────────

    def _open_transport(self):
        """Create and manage the Streamable HTTP transport."""

        from contextlib import asynccontextmanager

        import httpx
        from mcp.client.streamable_http import streamable_http_client
        from mcp.shared._httpx_utils import create_mcp_http_client

        @asynccontextmanager
        async def transport():
            timeout = httpx.Timeout(self.timeout_seconds, read=self.sse_read_timeout_seconds)

            async with create_mcp_http_client(headers=self.headers or None, timeout=timeout) as http_client:
                async with streamable_http_client(url=self.url, http_client=http_client) as streams:
                    yield streams

        return transport()

    # ── Internal: coroutines running on the bg loop ───────────────────────
    async def _session_main(self) -> None:
        """Own the transport + session for their whole lifetime.

        MCP context managers are task-bound, so a single coroutine must both
        enter and exit them. We keep them open by awaiting ``self._stop`` and
        signal readiness (or failure) via ``self._ready``.
        """
        from mcp import ClientSession

        try:
            async with self._open_transport() as (read_stream, write_stream, _get_sid):
                async with ClientSession(read_stream, write_stream) as session:
                    await session.initialize()
                    self._session = session
                    self._connect_error = None
                    assert self._ready is not None
                    self._ready.set()
                    assert self._stop is not None
                    await self._stop.wait()
        except Exception as exc:  # noqa: BLE001 - surfaced as friendly error
            self._connect_error = exc
            self._session = None
            if self._ready is not None:
                self._ready.set()

    async def _connect(self) -> None:
        self._ready = asyncio.Event()
        self._stop = asyncio.Event()
        self._connect_error = None
        self._session = None
        self._session_task = asyncio.ensure_future(self._session_main())
        try:
            await asyncio.wait_for(self._ready.wait(), timeout=self.timeout_seconds)
        except asyncio.TimeoutError as exc:
            raise MCPConnectionError(f"Timed out connecting to MCP server at {self.url} " f"after {self.timeout_seconds}s.") from exc
        if self._connect_error is not None:
            raise MCPConnectionError(_friendly_error(self._connect_error, self.url))

    async def _list_tools(self):
        return await self._session.list_tools()

    async def _call_tool(self, name: str, arguments: dict[str, Any]):
        return await self._session.call_tool(name, arguments)

    async def _list_resources(self):
        return await self._session.list_resources()

    async def _read_resource(self, uri: str):
        try:
            from pydantic import AnyUrl

            parsed = AnyUrl(uri) if isinstance(uri, str) else uri
        except Exception:  # noqa: BLE001
            parsed = uri
        return await self._session.read_resource(parsed)

    @staticmethod
    async def _await_future(fut: asyncio.Future) -> None:
        try:
            await fut
        except Exception:
            pass

    # ── Internal: helpers ─────────────────────────────────────────────────
    def _require_connected(self) -> None:
        if not self._started:
            raise MCPNotConnectedError(f"MCP client for {self.url} has not been started. Call start() first.")

    def _run_coro(self, coro, timeout: float):
        loop = self._loop
        if loop is None:
            raise MCPNotConnectedError("MCP event loop is not running.")
        future = asyncio.run_coroutine_threadsafe(coro, loop)
        return future.result(timeout=timeout)

    def _stop_session(self) -> None:
        loop = self._loop
        if loop is None:
            return
        if self._stop is not None:
            loop.call_soon_threadsafe(self._stop.set)
        if self._session_task is not None:
            try:
                waiter = asyncio.run_coroutine_threadsafe(self._await_future(self._session_task), loop)
                waiter.result(timeout=10)
            except Exception:
                pass
        self._session = None
        self._session_task = None

    def _reconnect(self) -> None:
        """Tear down the current session and open a fresh one (single retry)."""
        self._stop_session()
        self._connect_blocking()


def create_client_from_config(unity_cfg: dict[str, Any]) -> MCPClient:
    """Build an :class:`MCPClient` from the ``unity.mcp`` config sub-tree.

    Environment overrides:
        UNITY_MCP_URL overrides ``unity.mcp.url``.
    """
    import os

    mcp_cfg = (unity_cfg or {}).get("mcp", {}) or {}
    url = os.getenv("UNITY_MCP_URL") or mcp_cfg.get("url", "http://127.0.0.1:8080/mcp")
    timeout = float(mcp_cfg.get("timeout_seconds", 30))
    reconnect = int(mcp_cfg.get("reconnect_attempts", 1))
    return MCPClient(url, timeout_seconds=timeout, reconnect_attempts=reconnect)
