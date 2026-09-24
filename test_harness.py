import sys
import pathlib
import importlib.util
import bpy

addon_path = pathlib.Path("__init__.py").resolve()

if not addon_path.exists():
    print("ERROR: __init__.py not found.")
    sys.exit(1)

# Dynamically load the addon module
spec = importlib.util.spec_from_file_location("fast_batch_stl_exporter", str(addon_path))
mod = importlib.util.module_from_spec(spec)
sys.modules["fast_batch_stl_exporter"] = mod

try:
    # Execute module to catch syntax or import errors
    spec.loader.exec_module(mod)

    # Test Registration
    mod.register()
    print("STATUS: REGISTER_OK")

    # Test Unregistration
    mod.unregister()
    print("STATUS: UNREGISTER_OK")

except Exception as e:
    import traceback
    traceback.print_exc()
    sys.exit(1)
finally:
    # Clean up namespace to avoid conflicts on subsequent headless runs
    sys.modules.pop("fast_batch_stl_exporter", None)
