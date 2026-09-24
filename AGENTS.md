# Blender Extension Engine Rules & MCP Protocol

## Architecture
- **Single File Strictness:** Keep all Python logic strictly in a single `__init__.py` alongside `blender_manifest.toml`. Do not create separate modules (`operators.py`, `ui.py`, etc.).
- **Platform:** Target the Blender Extension system. Never declare a legacy `bl_info` dictionary.

## API Constraints
- **Context Overrides:** Never pass dictionaries for context overrides; use `with bpy.context.temp_override(...):`.
- **Property Lifecycle:** Register PropertyGroups before classes that depend on them, and unregister in reverse order. Always delete dynamically added properties on `bpy.types.Scene` or `bpy.types.WindowManager` in `unregister()`.
- **Code Output:** Output complete, drop-in class or function blocks rather than partial diffs.

## Verification & MCP Protocol
- **No Blind Edits:** You MUST verify every change to `__init__.py` using the Blender MCP tool before reporting completion.
- **Never Run Raw CLI:** Do not run terminal bash commands for Blender. Use the MCP tool explicitly.
- **Standard Test Harness:** When testing, instruct the MCP server to run `test_harness.py`. 
- If the MCP run returns an error, trace the error, apply the fix in `__init__.py`, and re-run verification before returning the response to the user.
