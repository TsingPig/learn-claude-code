"""Lightweight operation log and checkpoint metadata for Unity mutations.

This module does **not** claim to implement a real Unity Undo stack. Instead it
provides honest, traceable bookkeeping:

    * Every Unity tool call can be recorded with a short operation id.
    * Before a destructive operation the runtime is expected to save/back up the
      active scene (via the real MCP scene-save tool) and note it here.
    * Each operation carries a ``recoverable`` flag and a human-readable note so
      the model is told plainly whether the action can be undone.

We deliberately avoid copying the Unity ``Library`` directory or any other
heavyweight snapshot. Real undo, when available from the MCP server, should be
called directly as a tool; this state object only tracks what happened.
"""

from __future__ import annotations

import time
import uuid
from dataclasses import dataclass, field
from typing import Any, Optional


@dataclass
class Operation:
    """A single recorded Unity operation."""

    id: str
    tool: str
    permission_class: str
    arguments: dict[str, Any]
    scene: Optional[str] = None
    target: Optional[str] = None
    recoverable: bool = False
    note: str = ""
    timestamp: float = field(default_factory=time.time)

    def summary(self) -> str:
        state = "recoverable" if self.recoverable else "not auto-recoverable"
        scene = self.scene or "?"
        return f"op={self.id} tool={self.tool} class={self.permission_class} scene={scene} ({state})"


class SessionState:
    """In-memory log of Unity operations performed during a session."""

    def __init__(self) -> None:
        self._operations: list[Operation] = []
        self.active_scene: Optional[str] = None

    @staticmethod
    def _new_id() -> str:
        return uuid.uuid4().hex[:8]

    def set_active_scene(self, scene: Optional[str]) -> None:
        self.active_scene = scene

    def record(
        self,
        tool: str,
        permission_class: str,
        arguments: dict[str, Any],
        *,
        target: Optional[str] = None,
        recoverable: bool = False,
        note: str = "",
    ) -> Operation:
        """Append an operation and return it (with a fresh id)."""
        op = Operation(
            id=self._new_id(),
            tool=tool,
            permission_class=permission_class,
            arguments=dict(arguments or {}),
            scene=self.active_scene,
            target=target,
            recoverable=recoverable,
            note=note,
        )
        self._operations.append(op)
        return op

    @property
    def operations(self) -> list[Operation]:
        return list(self._operations)

    def last(self) -> Optional[Operation]:
        return self._operations[-1] if self._operations else None

    def describe_recent(self, limit: int = 5) -> str:
        recent = self._operations[-limit:]
        if not recent:
            return "(no Unity operations recorded yet)"
        return "\n".join(op.summary() for op in recent)
