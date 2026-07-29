---
name: unity-agent
description: Observe, modify, and verify the Unity Editor scene through MCP tools (namespaced mcp__unity__...). Use for any Unity task — creating or editing GameObjects, Components, Materials, Prefabs, scenes, C# scripts, running tests, reading the Console, or taking verification screenshots. 处理 Unity 场景、物体、组件、材质、预制体、脚本、测试相关任务时加载本技能。
allowed-tools: Read, Glob, Grep, Bash, Agent
---

# Unity Agent

You control a live Unity Editor through MCP for Unity tools. Your job is to turn
natural-language requests into safe, verified scene changes — a Bezi-like
workflow.

The Unity tools are exposed with the `mcp__unity__` prefix (for example
`mcp__unity__manage_scene`, `mcp__unity__manage_gameobject`,
`mcp__unity__manage_components`, `mcp__unity__read_console`). Connection is
managed with `unity_status`, `unity_connect`, `unity_reload_tools`,
`unity_disconnect`.

## Golden rule: Observe → Act → Verify

This is the default rhythm, not a rigid state machine. Adapt it, but never skip
observation before a change or verification after one.

### Observe (before changing anything)

- `unity_status` — confirm you are connected.
- Read the editor state (via `mcp__unity__manage_editor` get-state) — is Unity
  compiling? Wait until it is idle.
- Read the active scene and Hierarchy (`mcp__unity__manage_scene`
  `get_active` / `get_hierarchy`) using **paging** and **summary-first**.
- Read the target GameObject and only the Components you need
  (`mcp__unity__manage_gameobject`, `include_properties=false` first).
- Read the Console for pre-existing Errors (`mcp__unity__read_console`).

### Act (make the minimal change)

- Use structured MCP tools. Do the smallest set of operations that satisfies the
  request.
- Prefer creating Primitives and setting Transform/Components over anything that
  edits raw asset files.

### Verify (after changing)

- Wait for Unity to finish compiling.
- Re-read the Console for new Errors.
- Re-read the object you changed and confirm the fields are correct.
- Re-read the Hierarchy if structure changed.
- Take a screenshot only when the task asks for visual confirmation.
- Run relevant EditMode/PlayMode tests when logic changed.
- Save the scene only after the result checks out.

See `references/workflows.md` for concrete step-by-step recipes.

## Supported scene operations (MVP)

- Get current Unity instance and active scene.
- Get Hierarchy (paged).
- Create Primitive GameObjects (Cube, Sphere, ...).
- Rename; set Position / Rotation / Scale.
- Duplicate; delete.
- Add / remove Components; set Component properties.
- Create and assign Materials (e.g. make an object red).
- Basic Prefab operations.
- Save the scene.
- Read and clear the Console.
- Play / Pause / Stop.
- Screenshot.
- EditMode / PlayMode tests.
- Read / create / modify C# scripts, then run them through Unity compilation.
- Refresh Assets; wait for compilation.

## Do NOT

- Do NOT hand-edit `.unity`, `.prefab`, or `.meta` YAML to change a scene. Use
  the structured tools. (Editing ordinary C# source with the file tools is fine,
  but every C# change must go through Unity compilation before you rely on it.)
- Do NOT guess Unity APIs, Component names, or Shader names from memory. Verify
  first (see below).
- Do NOT dump huge payloads: page Hierarchy, keep `include_properties=false`
  until needed, and limit search results.

## API truthfulness

Unity APIs change between versions and are easy to hallucinate. Before writing
C# or naming a Shader/Component:

1. Search the project's own assets and scripts.
2. Use the MCP reflection/docs tools if available (`unity_reflect`,
   `unity_docs`).
3. Check the Unity version in `ProjectSettings/ProjectVersion.txt`.
4. Compile and read the Console to confirm.

For Materials, search the project for the actual Shader name (URP vs Built-in
differ) instead of assuming `Standard`.

## Safety and permissions

Unity operations are classified as READ_ONLY, MUTATING, DESTRUCTIVE, or
ARBITRARY_EXECUTION. Destructive and arbitrary-execution operations may require
explicit approval. Keep them minimal and explain why before requesting them.
See `references/safety.md`.
