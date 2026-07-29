# Unity Agent（UnityCode）

[English](README.en.md)

Unity Agent 是构建在 InplusCode Coding Agent 之上的 Unity Editor 自然语言操作层，工作方式类似 Bezi。它复用完全相同的 Agent Loop，并通过 [MCP for Unity](https://github.com/CoplayDev/unity-mcp) 增加 Unity 的观察、操作、知识和权限工具。

- `python scripts/InplusCode.py`：启动原有的通用 Coding Agent，不加载 Unity 能力。
- `python scripts/UnityCode.py`：启动 Unity Agent，在通用 Agent 基础上增加 Unity 工具。

## 整体结构

```text
用户 → messages → LLM → tool_use → TOOL_HANDLERS → tool_result → 继续同一循环
                                    ├─ 内置工具（bash/read/write/edit/glob/…）
                                    └─ mcp__unity__*（从 Unity MCP 动态发现）
```

| 模块 | 文件 |
| --- | --- |
| 通用同步 MCP 客户端（Streamable HTTP） | `scripts/utils/mcp_client.py` |
| MCP → Anthropic Schema 适配、命名空间和 Handler 绑定 | `scripts/unity_agent/tool_adapter.py` |
| 权限分类（只读、修改、破坏、任意代码执行） | `scripts/unity_agent/permissions.py` |
| 结果序列化、截图路径提取和内容截断 | `scripts/unity_agent/result_formatter.py` |
| 操作日志和检查点元数据 | `scripts/unity_agent/session_state.py` |
| Runtime 装配、静态工具、权限 Hook 和工具发现 | `scripts/unity_agent/bootstrap.py` |
| Unity 领域知识与工作流 | `scripts/skills/unity-agent/` |

## 环境要求

- Windows 11；同时兼容 macOS 和 Linux。
- `PATH` 中可用的 Python 3.11。
- Unity Editor。本仓库沙盒项目使用的版本见 `scripts/unity/AgentSandbox/ProjectSettings/ProjectVersion.txt`。
- Unity 项目已安装 MCP for Unity。本仓库沙盒项目的 `scripts/unity/AgentSandbox/Packages/manifest.json` 已包含 `com.coplaydev.unity-mcp`。
- 已安装 MCP Server/CLI 包 `mcpforunityserver` 和 `unity-mcp` CLI，用于诊断和启动 HTTP Server。

> Python 版本很重要。`python --version` 应输出 3.11.x。本机可以使用仓库的 `.venv` 虚拟环境；如果默认 Python 版本不正确，请显式执行 `.venv\Scripts\python.exe scripts\UnityCode.py`。

## 安装与启动

### 1. 安装 Unity MCP Package

沙盒项目已经完成安装。新项目可以通过 Unity Package Manager 添加下面的 Git URL：

```text
https://github.com/CoplayDev/unity-mcp.git?path=/MCPForUnity#main
```

### 2. 安装 Python MCP SDK

该依赖只由 Unity Agent 使用：

```powershell
pip install "mcp>=1.2,<2"
```

也可以安装仓库的全部依赖：

```powershell
pip install -r requirements.txt
```

### 3. 启动 Unity 和 MCP Server

打开 `scripts/unity/AgentSandbox` Unity 项目，并按照 MCP for Unity 的说明启动 HTTP MCP Server。默认监听地址为：

```text
http://127.0.0.1:8080/mcp
```

### 4. 启动 Unity Agent

```powershell
python scripts/UnityCode.py
```

Unity Agent 会尝试连接 MCP Server、发现当前可用的 Unity 工具、输出连接状态，然后进入交互式命令行。

## 配置

Unity Agent 的配置位于 `scripts/config.yaml` 的 `unity` 节点：

```yaml
unity:
  enabled: true
  project_path: "unity/AgentSandbox"

  mcp:
    transport: "http"
    url: "http://127.0.0.1:8080/mcp"
    auto_connect: true
    timeout_seconds: 30
    reconnect_attempts: 1
    cli_fallback: false

  tools:
    prefix: "mcp__unity__"
    allow_groups:
      - core
      - testing
      - materials
      - prefabs
    deny_tools: []

  permission:
    read_only: "allow"
    mutating: "allow"
    destructive: "ask"
    arbitrary_execution: "ask"

```

主要配置说明：

- `UNITY_MCP_URL` 环境变量可以覆盖 `unity.mcp.url`。
- 缺失的配置项会使用默认值，不影响 `config.yaml` 中已有的其他字段。
- `tools.prefix` 为动态发现的 Unity 工具增加命名空间，避免与内置工具重名。
- `allow_groups` 控制向模型暴露哪些工具组。
- Unity 权限策略独立于全局 Bash 权限，即使全局 `permission.mode` 为 `off`，危险 Unity 操作仍可以要求确认。

## 静态诊断工具

即使 Unity 或 MCP Server 尚未启动，Agent 仍然提供以下工具：

- `unity_status`：显示连接状态、Server URL、动态工具数量、权限策略和最近操作。
- `unity_connect`：连接 MCP Server 并发现 Unity 工具。
- `unity_reload_tools`：Unity Domain Reload 或工具发生变化后重新发现工具。
- `unity_disconnect`：断开 MCP 连接并从工具池移除动态 Unity 工具。

## 工作方式

Unity Agent 遵循下面的默认节奏：

```text
Observe → Act → Verify
观察      操作    验证
```

### Observe：修改前观察

- 确认 Unity MCP 已连接。
- 检查 Editor 是否正在编译。
- 读取当前 Scene、Hierarchy 和目标 GameObject。
- 修改前读取 Console，区分已有错误和新错误。
- 对大型结果使用分页，先读取摘要和组件元数据。

### Act：执行最小修改

- 优先使用结构化 MCP 工具。
- 只执行满足需求所必需的操作。
- 不直接编辑 `.unity`、`.prefab` 或 `.meta` YAML。
- 修改 C# 后触发 Unity 刷新和编译。

### Verify：修改后验证

- 等待 Unity 完成编译。
- 检查 Console 中是否出现新错误。
- 重新读取修改过的对象和组件。
- 层级发生变化时重新读取 Hierarchy。
- 逻辑变化后运行相关 EditMode 或 PlayMode 测试。
- 只有验证通过后才保存 Scene。
- 仅在任务需要视觉确认时截图。

具体工作流见：

- `scripts/skills/unity-agent/references/workflows.md`
- `scripts/skills/unity-agent/references/safety.md`

## 支持的 Unity 操作

当前 MVP 支持：

- 获取 Unity 实例和当前活动 Scene。
- 分页读取 Hierarchy。
- 创建 Cube、Sphere 等 Primitive GameObject。
- 重命名和设置 Position、Rotation、Scale。
- 复制和删除 GameObject。
- 添加、删除 Component 并设置属性。
- 创建和分配 Material。
- 基本 Prefab 操作。
- 保存 Scene。
- 读取和清空 Console。
- Play、Pause 和 Stop。
- 截图。
- 运行 EditMode 和 PlayMode 测试。
- 读取、创建和修改 C# 脚本，并通过 Unity 编译验证。
- 刷新 Assets 并等待编译完成。

## 权限与安全

Unity 工具调用分为四类：

| 权限类别 | 含义 | 默认策略 |
| --- | --- | --- |
| `READ_ONLY` | 查询场景、组件、资源、Console 和截图 | `allow` |
| `MUTATING` | 创建对象、修改属性、添加组件和保存场景 | `allow` |
| `DESTRUCTIVE` | 删除对象、资源或组件，覆盖场景，批量修改 | `ask` |
| `ARBITRARY_EXECUTION` | 执行菜单、C#、自定义工具或批处理 | `ask` |

权限分类优先使用 MCP Tool annotations，再结合工具名称和 `action` 参数判断。

请注意：

- 框架不会伪装成拥有项目级自动撤销能力。
- 普通场景修改在当前 Unity 会话中通常可以使用 Editor Undo。
- 删除 Asset 或覆盖 Scene 等破坏性操作通常无法自动恢复，应事先保存或备份。
- Runtime 会记录修改和破坏性操作，但不会复制 Unity `Library` 或制作完整项目快照。
- 截图的原始 Base64 数据不会送入模型，只返回路径和必要元数据。

## Unity 未运行时的行为

- `python scripts/InplusCode.py` 不受影响，也不会导入 Unity/MCP 代码。
- `python scripts/UnityCode.py` 仍能启动，并保留四个静态诊断工具。
- 如果 MCP Server 无法连接，Agent 会返回可读的诊断信息，而不是直接输出底层异常。
- Unity 和 MCP Server 启动后，可以调用 `unity_connect` 建立连接。

## CLI 诊断命令

```powershell
python --version
unity-mcp --help
unity-mcp status
unity-mcp instance list
```

如果找不到 `unity-mcp`，请确认已安装 `mcpforunityserver`，并且其脚本目录已经加入 `PATH`。

## 常见问题

| 现象 | 可能原因和处理方式 |
| --- | --- |
| 无法连接 MCP Server或连接被拒绝 | Server 未启动或 URL 不正确。启动 Server，并检查 `UNITY_MCP_URL` 和配置文件。 |
| 连接超时 | Unity 可能正在编译，或者 Server 不可访问。等待 Unity 空闲后重试。 |
| 未安装 `mcp` Python SDK | 执行 `pip install "mcp>=1.2,<2"`。 |
| 修改脚本后工具消失 | Domain Reload 改变了工具集合，调用 `unity_reload_tools`。 |
| 破坏性操作被拦截 | 这是默认安全策略。确认操作，或者显式调整 `unity.permission`。 |
| 启动时使用了错误的 Python | 显式使用 Python 3.11，例如 `.venv\Scripts\python.exe scripts\UnityCode.py`。 |

## 端到端 Smoke Test

只有 Unity 和 MCP Server 正常运行时才执行集成测试：

```powershell
$env:RUN_UNITY_MCP_TESTS = "1"
python scripts/unity_agent/e2e_smoke.py
```

测试流程包括：

1. 初始化 MCP Session。
2. 获取工具列表和 Unity 实例。
3. 读取活动 Scene。
4. 创建 `AgentCube` 并设置位置。
5. 创建并分配红色 Material。
6. 添加 `Rigidbody`。
7. 检查 Console。
8. 保存 Scene。
9. 截图。
10. 输出结构化测试摘要。

如果没有设置环境变量，或者 Unity/MCP Server 不可用，测试会明确跳过，不会伪造成功结果。
