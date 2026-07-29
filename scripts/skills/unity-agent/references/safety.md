# Unity safety and permissions

Unity tool calls are classified by sensitivity. The harness enforces a policy
per class (configured in `scripts/config.yaml` under `unity.permission`). This
protection is independent of the global bash `permission.mode` and stays active
even when that mode is `off`.

## Classes

- **READ_ONLY** — observation only. Hierarchy/Component/Asset queries, Console
  reads, editor state, screenshots. Default: allow.
- **MUTATING** — normal edits. Create objects, set Transform, add Components,
  set properties, create/assign Materials, save the scene. Default: allow.
- **DESTRUCTIVE** — potential data loss. Delete GameObjects/Assets/Components,
  overwrite a scene, bulk edits. Default: ask.
- **ARBITRARY_EXECUTION** — runs arbitrary code/menus. Execute menu items,
  execute C#, execute custom tools, batch execution. Default: ask (and these
  tools are withheld from the default tool groups).

Classification uses the MCP tool annotations first (`readOnlyHint`,
`destructiveHint`), then falls back to the tool name and its `action` argument.

## Rules of engagement

- Before any DESTRUCTIVE action, prefer to **save or back up the active scene**
  first, then explain what will be lost and why the action is necessary.
- Never use a destructive shortcut to reach a goal a mutating sequence can
  achieve.
- Do not attempt to bypass the permission prompts.
- Do not modify the third-party MCP for Unity package. If a custom Editor tool
  is truly required, add it under
  `scripts/unity/AgentSandbox/Assets/Editor/InplusCode/` using the MCP package's
  official custom-tool extension mechanism.

## Checkpoints and undo — be honest

- There is **no** magic project-wide undo. Do not claim an action is reversible
  unless a real mechanism backs it up.
- Recoverability guidance:
  - MUTATING scene edits can often be undone with the Unity Editor's own Undo
    (Ctrl+Z) while the session is live.
  - DESTRUCTIVE edits (deleted assets, overwritten scenes) are generally not
    auto-recoverable — save/back up beforehand.
- The runtime records each mutating/destructive operation with a short
  operation id so you can describe what happened. It does **not** snapshot the
  Unity `Library` directory.

## Screenshots

Screenshots are returned as a file path plus metadata. The raw base64 is never
forwarded to the model — reason about the path, and read the image with a
dedicated image-viewing step only if truly needed.
