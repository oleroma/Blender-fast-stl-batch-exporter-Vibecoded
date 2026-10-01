# Architecture Documentation: Fast Batch STL Exporter

## 1. System Overview
The Fast Batch STL Exporter is a highly optimized, monolithic Blender add-on contained entirely within a single `__init__.py` file. It is designed to evaluate, permute, and export complex Geometry Nodes and modifier setups across multiple objects without blocking the main Blender UI. 

Key architectural highlights include:
*   **Asynchronous Subprocess Exporting:** Utilizes a modal operator to spawn a headless background instance of Blender, communicating via stdout pipes.
*   **Vectorized STL Generation:** Bypasses Blender's native Python loops in favor of `numpy` matrix math for near-instant binary STL generation.
*   **Decoupled UI Caching:** A background timer maintains UI state, preventing heavy graph evaluations during UI redraw ticks.

---

## 2. Data Hierarchy (Property Groups)
The state of the add-on is saved directly into the `.blend` file using Blender's `PropertyGroup` classes. The data model follows a strict hierarchy attached to `bpy.types.Scene`:

1.  **Preset (`BatchSTLExportPreset`)**: The root container. Holds a list of Collections and Global (Pinned) NodeGroups.
2.  **Collection (`BatchSTLCollection`)**: Maps to a specific Blender Collection. Holds a list of excluded objects and Local NodeGroups.
3.  **NodeGroup (`BatchSTLNodeGroup`)**: Targets a specific Geometry Nodes group or modifier interface.
4.  **Node (`BatchSTLNode`)**: Targets a specific node within the group, or the modifier itself.
5.  **Input (`BatchSTLInput`)**: Targets a specific socket on the node (e.g., "Size", "Radius").
6.  **Value (`BatchSTLValue`)**: Holds the actual data (Float, Int, String, Boolean, Menu). Supports "Sweep" definitions (start, step, count) to generate multiple values dynamically.

---

## 3. Core Subsystems & Logic

### 3.1. The Permutation & Override Engine
Located in the core logic section of `__init__.py`, this engine is responsible for generating every possible variation of a model before export.
*   **Mock Classes:** `TempMockInput`, `MockInput`, and `MockOverride` are used to build lightweight representations of the data hierarchy without mutating actual Blender data.
*   **Sweep Parsing (`parse_sweep_values`):** Translates user-defined string ranges (e.g., "1 0.5 3") into discrete lists of values (e.g., `[1.0, 1.5, 2.0]`).
*   **Combinatorics (`generate_override_combinations`):** Uses Python's `itertools.product` to cross-multiply all input variations, generating a flattened list of "states" to evaluate.
*   **Application & Reversion:** `apply_overrides` manipulates the live Blender Dependency Graph (Depsgraph), temporarily changing modifier properties or rewiring node sockets. `revert_overrides` safely restores the user's original setup using cached original states.

### 3.2. Fast Binary STL Writer
Standard Blender Python looping is notoriously slow for large meshes. The `write_fast_binary_stl` function overcomes this:
*   Extracts raw vertices and loop triangles directly into `numpy` arrays via Blender's fast `foreach_get` method.
*   Applies the object's `matrix_world` transformations using vectorized dot-product multiplication.
*   Calculates and normalizes triangle normals across the entire array simultaneously.
*   Constructs a highly specific structured `numpy` array matching the exact byte layout of the binary STL format, dumping it to disk instantly using `f.write(data.tobytes())`.

### 3.3. Asynchronous UI & Caching Engine
Blender's UI redraws constantly. If complex depsgraph calculations are tied to the UI `draw()` loop, Blender freezes.
*   **`_ui_cache`:** A global dictionary storing pre-computed metrics, visibility states, and file-tree structures.
*   **Timer & Handlers:** `rebuild_ui_cache_if_dirty` runs on a recurring `bpy.app.timers` loop (0.25s). When data is changed (caught by `mark_dirty()` or the `batch_stl_depsgraph_handler`), the cache is marked dirty and recalculated *outside* the UI thread.
*   **Tree Visualizer (`build_tree_dict`):** Pre-calculates the exact folder structure and filenames of the upcoming export, immediately flagging naming collisions (duplicates).

### 3.4. Headless Export Orchestration
To prevent UI lockups during massive batch exports, the main export operator (`EXPORT_OT_batch_stl_multi`) relies on a modal loop.
1.  **Preparation:** Saves a temporary copy of the current `.blend` file to a temp directory.
2.  **Subprocess:** Uses Python's `subprocess.Popen` to launch a secondary, invisible instance of Blender via the CLI.
3.  **CLI Hook:** The secondary instance detects `--batch-stl-headless` in `sys.argv` at the bottom of `__init__.py` and routes execution directly to `run_headless_export()`.
4.  **Communication:** The headless worker prints structured tags (e.g., `BATCH_STL_PROGRESS:5`). The primary Blender instance runs a background `threading.Thread` to read these stdout lines into a thread-safe `queue.Queue`.
5.  **Modal Polling:** The primary instance's modal timer checks the queue every 0.05 seconds, updating UI progress bars seamlessly until it receives `BATCH_STL_DONE`.

---

## 4. UI Layout (View Layer)
Defined using `bpy.types.Panel` and `bpy.types.UIList`.
*   **VIEW3D_PT_batch_export_stl_multi:** The monolithic 'N-Panel' in the 3D Viewport.
*   Dynamically draws the property tables. Heavily relies on `_ui_cache` to display object metrics and the preview directory tree without performing real-time mesh evaluations.

## 5. Lifecycle Management
*   **`register()` / `unregister()`:** Properly initializes all PropertyGroups, Operators, UI Lists, and attaches global variables/handlers to the Blender Application environment. Includes automated cleanup of timers and temporary variables to prevent memory leaks on script reload.
