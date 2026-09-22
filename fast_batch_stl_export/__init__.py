import bpy

from . import properties, core_engine, json_io, operators, ui, modal_orchestrator, headless_runner

classes = (
    *properties.classes,
    *json_io.classes,
    *operators.classes,
    *modal_orchestrator.classes,
    *ui.classes,
    *headless_runner.classes, # <-- Added the headless operator
)

def register():
    for cls in classes:
        bpy.utils.register_class(cls)
    properties.register_properties()

def unregister():
    for cls in reversed(classes):
        try:
            bpy.utils.unregister_class(cls)
        except RuntimeError:
            pass
    properties.unregister_properties()
