"""
Fast Batch STL Exporter
Architecture: Modular (Optimized for Agentic Environments)
Data Hierarchy: Preset > Collection > NodeGroup > Node > Input > Value
"""

import bpy
from bpy.app.handlers import persistent
import sys
import os

# ==============================================================================
# === [FIX] CLI HEADLESS BOOTSTRAP ===
# ==============================================================================
if __name__ == "__main__":
    # When executed directly via `blender -P __init__.py` during a headless run,
    # Python lacks the package context, causing relative imports to fail.
    # We dynamically inject the package name and path before reaching the imports.
    addon_dir = os.path.dirname(os.path.realpath(__file__))
    parent_dir = os.path.dirname(addon_dir)
    if parent_dir not in sys.path:
        sys.path.append(parent_dir)

    # Dynamically set the package name to the folder name
    __package__ = os.path.basename(addon_dir)


# Internal Imports (These will now safely execute in headless mode)
from .properties import BatchSTLExportPreset, classes as prop_classes
from .operators import classes as op_classes
from .ui import classes as ui_classes

# Aggregate all classes dynamically for the Blender register sequence
classes = prop_classes + op_classes + ui_classes

@persistent
def reset_batch_stl_state(scene_dummy):
    try:
        for s in bpy.data.scenes:
            if not hasattr(s, "batch_stl_presets"): continue
            for p in s.batch_stl_presets:
                p.is_exporting = False
                p.cancel_export = False
                p.export_progress = 0.0
                p.export_status = ""
    except Exception:
        pass

def update_show_tree(self, context):
    if not self.batch_stl_show_tree:
        self.batch_stl_collapsed_dirs = "[]"

def register():
    # Push all declared classes into the Blender Data RNA structure
    for cls in classes:
        bpy.utils.register_class(cls)

    # Attach Scene-level global configurations
    bpy.types.Scene.batch_stl_root_dir = bpy.props.StringProperty(name="Root Export Dir", default="//", subtype="DIR_PATH")
    bpy.types.Scene.batch_stl_presets = bpy.props.CollectionProperty(type=BatchSTLExportPreset)
    bpy.types.Scene.batch_stl_preset_index = bpy.props.IntProperty(name="Active Preset", default=0)
    bpy.types.Scene.batch_stl_verbose_console = bpy.props.BoolProperty(name="Verbose Console Output", default=False)

    # UI Draw flags (used to save toggled tree menu states globally)
    bpy.types.Scene.batch_stl_ui_presets = bpy.props.BoolProperty(default=True)
    bpy.types.Scene.batch_stl_ui_collections = bpy.props.BoolProperty(default=True)
    bpy.types.Scene.batch_stl_ui_global_ovr = bpy.props.BoolProperty(default=True)
    bpy.types.Scene.batch_stl_ui_local_ovr = bpy.props.BoolProperty(default=True)
    bpy.types.Scene.batch_stl_ui_global_ovr_nested = bpy.props.BoolProperty(default=False)
    bpy.types.Scene.batch_stl_ui_local_ovr_nested = bpy.props.BoolProperty(default=False)
    bpy.types.Scene.batch_stl_ui_exclude = bpy.props.BoolProperty(default=True)
    bpy.types.Scene.batch_stl_ui_tips = bpy.props.BoolProperty(default=False)
    bpy.types.Scene.batch_stl_show_tree = bpy.props.BoolProperty(default=True, update=update_show_tree)
    bpy.types.Scene.batch_stl_show_console = bpy.props.BoolProperty(default=False)
    bpy.types.Scene.batch_stl_collapsed_dirs = bpy.props.StringProperty(default="[]")

    reset_batch_stl_state(None)
    if reset_batch_stl_state not in bpy.app.handlers.load_post:
        bpy.app.handlers.load_post.append(reset_batch_stl_state)

def unregister():
    if reset_batch_stl_state in bpy.app.handlers.load_post:
        bpy.app.handlers.load_post.remove(reset_batch_stl_state)

    for cls in reversed(classes):
        try: bpy.utils.unregister_class(cls)
        except RuntimeError: pass

    # Clean RNA mapping space so variables don't survive addon uninstallation
    properties_to_remove = [
        "batch_stl_root_dir", "batch_stl_presets", "batch_stl_preset_index",
        "batch_stl_verbose_console", "batch_stl_ui_presets", "batch_stl_ui_collections",
        "batch_stl_ui_global_ovr", "batch_stl_ui_local_ovr", "batch_stl_ui_exclude",
        "batch_stl_show_tree", "batch_stl_show_console", "batch_stl_collapsed_dirs",
        "batch_stl_ui_tips", "batch_stl_ui_global_ovr_nested", "batch_stl_ui_local_ovr_nested"
    ]

    for prop in properties_to_remove:
        if hasattr(bpy.types.Scene, prop):
            delattr(bpy.types.Scene, prop)

# ==============================================================================
# === CLI EXECUTION BINDING ===
# ==============================================================================
if __name__ == "__main__":
    if "--batch-stl-headless" in sys.argv:
        # Guarantee Scene RNA properties are mounted before script proceeds
        if not hasattr(bpy.types.Scene, "batch_stl_root_dir"):
            register()

        # Late-bind exporter to avoid startup context issues
        from .exporter import run_headless_export

        idx = sys.argv.index("--batch-stl-headless")
        p_index = int(sys.argv[idx + 1])
        run_headless_export(p_index)
    else:
        # Standard blender text-editor execution initialization
        if not hasattr(bpy.types.Scene, "batch_stl_root_dir"):
            register()
