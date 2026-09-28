# Blender Extension Engine Rules & MCP Protocol

## Architecture
- **Modular Architecture:** The extension is structured as a multi-file package (`state.py`, `utils.py`, `core.py`, `exporter.py`, `properties.py`, `operators.py`, `ui.py`, `__init__.py`) optimized for agentic workflows. Do not revert to a monolithic single-file structure.
- **Platform:** Target the modern Blender Extension system (Blender 4.2+ and 5.0+). Use `blender_manifest.toml` for metadata definition. Do NOT use the legacy `bl_info` dictionary in `__init__.py`.
- **Headless CLI Execution:** When running background worker subprocesses via `blender -b -P`, ensure `__init__.py` or the target script contains dynamic `sys.path` and `__package__` bootstrapping logic so relative module imports (`from . import ...`) do not crash.

## API Constraints
- **Context Overrides:** Never pass dictionaries for context overrides; use `with bpy.context.temp_override(...):`.
- **Property Lifecycle:** Register PropertyGroups before classes that depend on them, and unregister in reverse order. Always delete dynamically added properties on `bpy.types.Scene` or `bpy.types.WindowManager` in `unregister()`.
- **Code Output:** Prefer using precise file edits (e.g. line-by-line replacement) rather than replacing entire large blocks or files, to avoid overwriting unrelated code or breaking existing logic.

## Edit rules
Dont edit anything outside the scope of current request. Do changes only in parts that neccessary for completing the task. Be careful not to alter any functionality.

## Extension documentation
Use 'extension_architecture.md' and 'README.md' for all necessary information. After implementing anything, update those files to reflect changes.
