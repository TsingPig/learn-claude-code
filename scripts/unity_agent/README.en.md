# Unity Agent (UnityCode)

[中文文档](README.md)

A Bezi-like natural-language layer over the Unity Editor, built **on top of** the
InplusCode coding agent. It reuses the exact same agent loop and adds Unity as a
new set of Observation / Action / Knowledge / Permission tools via
[MCP for Unity](https://github.com/CoplayDev/unity-mcp).

- `python scripts/InplusCode.py` → the plain coding agent (no Unity, unchanged).
- `python scripts/UnityCode.py` → the Unity Agent (adds Unity tools).

## How it fits together

```
User → messages → LLM → tool_use → TOOL_HANDLERS → tool_result → (same loop)
                                     ├─ built-in tools (bash/read/write/edit/glob/…)
                                     └─ mcp__unity__*  (discovered from Unity MCP)
```

| Piece | File |
| --- | --- |
| Generic sync MCP client (Streamable HTTP) | `scripts/utils/mcp_client.py` |
| MCP→Anthropic schema adapter, namespacing, handler binding | `scripts/unity_agent/tool_adapter.py` |
| Permission classification (read/mutate/destroy/arbitrary) | `scripts/unity_agent/permissions.py` |
| Result serialization (screenshots → path, truncation) | `scripts/unity_agent/result_formatter.py` |
| Operation log / checkpoint metadata | `scripts/unity_agent/session_state.py` |
| Runtime wiring (static tools, hook, discovery) | `scripts/unity_agent/bootstrap.py` |
| Domain knowledge | `scripts/skills/unity-agent/` |

## Prerequisites

- Windows 11 (also works on macOS/Linux), **Python 3.11** on `PATH`.
- Unity Editor (this sandbox targets `2022.3.50f1c1`; see
  `scripts/unity/AgentSandbox/ProjectSettings/ProjectVersion.txt`).
- MCP for Unity package installed in the Unity project (already present in
  `scripts/unity/AgentSandbox/Packages/manifest.json` as
  `com.coplaydev.unity-mcp`).
- The MCP server/CLI package `mcpforunityserver` and the `unity-mcp` CLI (for
  diagnostics and to run the HTTP server).

> **Python version matters.** `python --version` must report 3.11.x. On this
> machine the repo uses a virtualenv at `.venv` (Python 3.11). If your default
> `python` is older (e.g. 3.8), call the venv interpreter explicitly, e.g.
> `.venv\Scripts\python.exe scripts/UnityCode.py`.

## Setup

1. **Install the Unity MCP package** (already done for the sandbox). In a fresh
   project, add `com.coplaydev.unity-mcp` via the Unity Package Manager (Git URL
   `https://github.com/CoplayDev/unity-mcp.git?path=/MCPForUnity#main`).

2. **Install the Python MCP SDK** (Unity Agent only):

   ```powershell
   pip install "mcp>=1.2,<2"
   # or: pip install -r requirements.txt
   ```

3. **Install the MCP server/CLI** (`mcpforunityserver` / `unity-mcp`) following
   the CoplayDev/unity-mcp instructions, then **start the HTTP MCP server** so it
   listens at the configured URL (default `http://127.0.0.1:8080/mcp`). Open the
   Unity Editor with the AgentSandbox project so a Unity instance is available.

4. **Start the Unity Agent:**

   ```powershell
   python scripts/UnityCode.py
   ```

   It connects (if `auto_connect: true`), discovers the Unity tools, prints a
   status line, and drops into an interactive prompt.

## Configuration (`scripts/config.yaml` → `unity:`)

```yaml
unity:
  enabled: true
  project_path: "unity/AgentSandbox"   # relative to paths.workspace
  mcp:
    transport: "http"
    url: "http://127.0.0.1:8080/mcp"   # override with env UNITY_MCP_URL
    auto_connect: true
    timeout_seconds: 30
    reconnect_attempts: 1              # single auto-retry on drop
    cli_fallback: false
  tools:
    prefix: "mcp__unity__"             # namespace for discovered tools
    allow_groups: [core, testing, materials, prefabs]
    deny_tools: []
  permission:
    read_only: "allow"
    mutating: "allow"
    destructive: "ask"                 # gated even when global permission.mode = off
    arbitrary_execution: "ask"
```

- **Environment override:** set `UNITY_MCP_URL` to point at a different server;
  it wins over `unity.mcp.url`.
- Missing config falls back to sensible defaults; existing `config.yaml` fields
  are untouched.
- Paths are relative — no machine-specific absolute paths are committed.

## Static diagnostic tools (always available)

Even with Unity closed, the agent has:

- `unity_status` — connection status, URL, tool count, policy, recent ops.
- `unity_connect` — connect + discover tools.
- `unity_reload_tools` — re-discover after a domain reload.
- `unity_disconnect` — disconnect and remove dynamic tools.

## Behavior when Unity is not running

- `python scripts/InplusCode.py` is unaffected — it never imports Unity code.
- `python scripts/UnityCode.py` still starts. Instead of a traceback you get a
  clear diagnostic (connection refused / timed out) and the four static tools.
  Start Unity + the MCP server, then run `unity_connect`.

## CLI diagnostics

```powershell
python --version          # must be 3.11.x
unity-mcp --help
unity-mcp status
unity-mcp instance list
```

If `unity-mcp` is not found, ensure the `mcpforunityserver` package is installed
and its scripts directory is on `PATH`.

## Troubleshooting

| Symptom | Likely cause / fix |
| --- | --- |
| "Cannot reach MCP server … connection refused" | Server not started, or wrong URL. Start the HTTP server; check `UNITY_MCP_URL`. |
| "Timed out connecting" | Unity busy compiling, or server unreachable. Wait, then `unity_connect`. |
| "the 'mcp' Python SDK is not installed" | `pip install "mcp>=1.2,<2"`. |
| Tools missing after a script change | Domain reload changed the tool set → run `unity_reload_tools`. |
| Destructive op blocked | Expected: `destructive`/`arbitrary_execution` default to `ask`. Approve, or adjust `unity.permission`. |
| Wrong Python picked up | Call the 3.11 interpreter directly (e.g. `.venv\Scripts\python.exe`). |


It exercises: initialize → tools/list → instance visibility → read active scene →
create `AgentCube` → set position → red Material → add `Rigidbody` → check
Console → save → screenshot, and prints a structured summary. Without the env
var (or without Unity running) it is skipped — it never fakes success.
