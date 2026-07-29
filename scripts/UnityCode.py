"""UnityCode — the Unity Agent entry point.

This is a thin launcher that REUSES the InplusCode agent loop. It does not fork
or duplicate the loop; it only:

    1. Builds a Unity-augmented copy of the tool pool (so the base pool used by
       `python scripts/InplusCode.py` is never mutated).
    2. Installs the Unity runtime (static diagnostic tools + permission hook).
    3. Optionally auto-connects to the Unity MCP server.
    4. Appends a short Unity domain hint to the system prompt.
    5. Runs the same interactive CLI, delegating each turn to
       `InplusCode.agent_loop(...)` with the Unity tools/handlers/system.

Run with:  python scripts/UnityCode.py
"""

from __future__ import annotations

try:
    import readline

    # macOS 的 libedit 在处理中文输入时有退格问题，这四行修复它
    readline.parse_and_bind("set bind-tty-special-chars off")
    readline.parse_and_bind("set input-meta on")
    readline.parse_and_bind("set output-meta on")
    readline.parse_and_bind("set convert-meta off")
except ImportError:
    pass

from rich import print

from utils.tools import TOOLS, TOOL_HANDLERS
from utils.system import SYSTEM

from InplusCode import agent_loop

from unity_agent.bootstrap import build_unity_runtime

# Work on copies so InplusCode's global tool pool stays pristine.
UNITY_TOOLS: list = list(TOOLS)
UNITY_HANDLERS: dict = dict(TOOL_HANDLERS)

runtime = build_unity_runtime(UNITY_TOOLS, UNITY_HANDLERS)

# Try to connect up front, but never crash if Unity is closed.
print(f"[cyan]{runtime.maybe_auto_connect()}[/cyan]")

system_prompt = SYSTEM + runtime.system_appendix()

print("\n[bold]Unity Agent[/bold] ready. Describe a Unity task, or type 'q' to quit.\n")

history: list = []
try:
    while True:
        try:
            query = input("\033[36m Unity >> \033[0m")
        except (EOFError, KeyboardInterrupt):
            break
        if query.strip().lower() in ("q", "exit", ""):
            break
        history.append({"role": "user", "content": query})
        agent_loop(history, prompt=system_prompt, tools=UNITY_TOOLS, handlers=UNITY_HANDLERS)
        print()
finally:
    runtime.close()
