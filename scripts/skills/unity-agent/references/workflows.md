# Unity workflow recipes

Concrete, ordered recipes for common tasks. Tool names use the `mcp__unity__`
prefix. Argument shapes vary by server version — inspect each tool's schema and
adjust. Always page large reads and keep `include_properties=false` until you
need properties.

## 0. Connect and orient

1. `unity_status` — verify connection (or `unity_connect`).
2. `mcp__unity__manage_editor` (get state) — confirm not compiling.
3. `mcp__unity__manage_scene` `get_active` — learn the active scene.
4. `mcp__unity__read_console` — note any pre-existing Errors.

## 1. Create a Primitive and place it

1. Observe the active scene and Hierarchy.
2. `mcp__unity__manage_gameobject` create a Cube named `AgentCube`.
3. Set its Transform position to `[0, 1, 0]`.
4. Re-read the object to confirm name + position.

## 2. Make an object a solid color (Material)

1. Search assets for existing Materials/Shaders
   (`mcp__unity__manage_asset` search, `filter_type=Shader` /
   `filter_type=Material`). Do not assume `Standard`; the project may use URP.
2. Create a Material with the correct Shader and set its base color to red.
3. Assign the Material to the target object's Renderer.
4. Re-read the Renderer to confirm the Material is assigned.

## 3. Add a Component and set properties

1. Read the object's current Components (`include_properties=false`).
2. `mcp__unity__manage_components` add `Rigidbody`.
3. Set only the properties you need.
4. Re-read Components (`include_properties=true`, small page) to confirm.

## 4. Edit a C# script safely

1. Read the script (project file tools or `mcp__unity__manage_script`).
2. Make the minimal edit.
3. `mcp__unity__refresh_unity` / trigger a recompile.
4. Wait until the editor state reports it is no longer compiling.
5. `mcp__unity__read_console` — resolve any new compile Errors before using the
   new type/component.

## 5. Save, verify, screenshot

1. Only after the Console is clean and the objects check out:
   `mcp__unity__manage_scene` save.
2. Take a screenshot if visual confirmation is requested. Screenshots come back
   as a file path + metadata, never as inline base64.
3. Produce a structured summary: object name, stable id/reference, scene,
   Transform, Component list, Material, Console error count, screenshot path,
   whether the scene was saved, and per-step success/failure.

## 6. Run tests

1. `mcp__unity__run_tests` for EditMode/PlayMode.
2. Poll the test job if the tool is asynchronous.
3. On failure, read the failing test output and the Console, then fix and
   re-run.

## Paging & payload discipline

- `manage_scene get_hierarchy`: use `page_size` (start ~50) + `cursor`, follow
  `next_cursor`.
- `manage_gameobject get_components`: `include_properties=false` first; when you
  need properties, use a small `page_size` (3–10).
- `manage_asset search`: modest `page_size` (25–50), `generate_preview=false`.
