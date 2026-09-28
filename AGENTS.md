# Blender Extension Engine Rules & MCP Protocol

## Architecture
- **Modular Architecture:** The extension is structured as a multi-file package (`state.py`, `utils.py`, `core.py`, `exporter.py`, `properties.py`, `operators.py`, `ui.py`, `__init__.py`) optimized for agentic workflows. Do not revert to a monolithic single-file structure.
- **Platform:** Target the Blender Extension system. Use the standard `bl_info` dictionary for metadata definition in `__init__.py`.
- **Headless CLI Execution:** When running background worker subprocesses via `blender -P`, ensure `__init__.py` contains dynamic `sys.path` and `__package__` bootstrapping logic so relative module imports (`from . import ...`) do not crash.

## API Constraints
- **Context Overrides:** Never pass dictionaries for context overrides; use `with bpy.context.temp_override(...):`.
- **Property Lifecycle:** Register PropertyGroups before classes that depend on them, and unregister in reverse order. Always delete dynamically added properties on `bpy.types.Scene` or `bpy.types.WindowManager` in `unregister()`.
- **Code Output:** Output complete, drop-in class or function blocks rather than partial diffs.

## Edit rules
Dont edit anything outside the scope of current request. Do changes only in parts that neccessary for completing the task. Be careful not to alter any functionality.

## Extension documentation
Use 'extension_architecture.md' and 'README.md' for all necessary information. After implementing anything, update those files to reflect changes.
