# Blender Extension Engine Rules & MCP Protocol

## Architecture
- **Single File Strictness:** Keep all Python logic strictly in a single `__init__.py` alongside `blender_manifest.toml`. Do not create separate modules (`operators.py`, `ui.py`, etc.).
- **Platform:** Target the Blender Extension system. Never declare a legacy `bl_info` dictionary.

## API Constraints
- **Context Overrides:** Never pass dictionaries for context overrides; use `with bpy.context.temp_override(...):`.
- **Property Lifecycle:** Register PropertyGroups before classes that depend on them, and unregister in reverse order. Always delete dynamically added properties on `bpy.types.Scene` or `bpy.types.WindowManager` in `unregister()`.
- **Code Output:** Output complete, drop-in class or function blocks rather than partial diffs.

## Edit rules ##
Dont edit anything outside the scope of current request. Do changes only in parts that neccessary for completing the task. Be careful not to alter any functionality.

## Extension documentation
Use 'extension_architecture.md' and 'README.md' for all necessary information. After implementing anything, update those files to reflect changes.
