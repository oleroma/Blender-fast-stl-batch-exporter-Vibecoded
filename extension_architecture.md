# Fast Batch STL Exporter: Architecture Documentation

This document outlines the internal architecture, data structures, and execution flow of the **Fast Batch STL Exporter** extension for Blender. 

The extension is designed around a **bifurcated execution model**: a lightweight, responsive UI thread that manages user data and state, and an isolated, headless background process that handles destructive scene mutations and heavy geometry evaluations.

---

## 1. High-Level System Overview

The extension is contained within a single `__init__.py` file, logically segmented into several core sub-systems:
1. **Data Model:** Hierarchical `PropertyGroup` classes storing preset configurations.
2. **UI & Operators:** Panels, UILists, and action operators to mutate the data model.
3. **Headless Orchestrator:** A Modal operator that snapshots the scene, spawns the background process, and reads standard output (stdout) for progress tracking.
4. **Permutation Engine:** Logic to calculate the Cartesian product of parametric sweeps.
5. **Injection System:** Functions to non-destructively apply values to modifier properties or internally sever/reconnect Geometry Node inputs.
6. **Headless Execution Routine:** The bulldozer script run by the background process that evaluates the Depsgraph, purges RAM, and triggers the export.
7. **Binary STL Writer:** A custom NumPy/Struct writer bypassing Blender's native export API for maximum speed.

---

## 2. Data Model Hierarchy (RNA Properties)

The addon relies on a strict, deeply nested hierarchy of Blender `PropertyGroup` classes attached to `bpy.types.Scene`.

* `Scene.batch_stl_presets` (Collection of `BatchSTLExportPreset`)
  * **`BatchSTLExportPreset`**: The root of an export profile.
    * `preset_prefix`: The parent folder name.
    * `pinned_overrides`: Collection of global `BatchSTLNodeOverride` blocks applied to *all* mappings.
    * `mappings`: Collection of `BatchSTLExportItem` objects.
    * **`BatchSTLExportItem`**: Binds a Blender Collection to the export pipeline.
      * `collection_ptr`: Target geometry collection.
      * `tag` / `sub_path`: File and folder formatting rules.
      * `use_filter` / `excluded_objects`: Boolean toggle and list of `BatchSTLExcludedObject` to skip during export.
      * `node_overrides`: Collection of local `BatchSTLNodeOverride` blocks applied *only* to this collection.
      * **`BatchSTLNodeOverride`**: A block targeting a specific Geometry Node group or Modifier.
        * `override_target`: Enum (`NODE` or `MODIFIER`).
        * `parent_group_ptr`: The NodeTree being targeted.
        * `node_name`: The specific internal node (if blank, targets the group interface).
        * `inputs`: Collection of `BatchSTLNodeInput` objects.
        * **`BatchSTLNodeInput`**: A specific socket/parameter to modify.
          * `input_name`, `override_type` (Float, Int, Bool, String, Menu).
          * Standard value fields (`value_float`, `value_bool`, etc.).
          * Formatting rules (`use_tag`, `tag`, `use_dir`).
          * **Sweep Mechanics:** `use_sweep` (bool) and `sweep_range` (string) for automated permutations.

---

## 3. The Permutation Engine (`generate_override_combinations`)

Before exporting, the system calculates every possible geometric variant.
1. **Input Pooling:** It groups all inputs by their explicit target `(override_target, node_name, input_name)`. If a user repeats the same input target 3 times, those 3 values are pooled together.
2. **Sweep Expansion (`parse_sweep_values`):** If an input has `use_sweep` enabled, it intercepts the `sweep_range` string (e.g., `1.0 0.5 3`), splits comma-separated strings, or extracts Enum identifiers from target nodes, dynamically injecting `MockInput` objects into the pool.
3. **Cartesian Product:** It uses `itertools.product(*pools)` to calculate every unique combination of values across all targeted parameters.
4. **Reconstruction:** It flattens the combination tuples back into temporary `MockOverride` blocks ready for injection.

---

## 4. Node & Modifier Injection System

Instead of relying on slow Python loops over individual object modifiers, the extension uses a surgical "Graph Injection" method.

### Modifier Targets
Finds the specific exposed socket on the modifier's interface and uses standard `mod[identifier] = value`.

### Node Targets (Link Severing)
1. Locates the `Group Input` node within the target `NodeTree`.
2. Locates the targeted output socket.
3. **Records state:** Saves the original `default_value` and any incoming `from_socket` connections to a temporary `global_states` list.
4. **Severs links:** Removes the connection wire inside the NodeTree.
5. **Injects:** Sets the `default_value` of the downstream socket to the permutation value.
*(Note: Because the UI relies on a temporary headless copy of the file, the extension no longer executes the complex `revert_overrides` step at the end of the batch, drastically improving final completion speed).*

---

## 5. Execution Flow (The Bifurcated Pipeline)

### Phase A: UI Thread (The Modal Orchestrator)
When the user clicks **Export** (`EXPORT_OT_batch_stl_multi`):
1. **File Dump:** Saves the active `.blend` state to a temporary OS directory using `bpy.ops.wm.save_as_mainfile(copy=True, compress=False)`. Uncompressed dumping prevents UI lock-up.
2. **Subprocess Spawn:** Launches a secondary, headless background Blender process pointing to the temp file, using the `-P __file__` argument to re-execute the addon script, passing `--batch-stl-headless <preset_idx>`.
3. **Queue Listening:** Spawns a Daemon Thread with a `queue.Queue` to non-blockingly read `stdout` from the headless instance.
4. **Modal Timer:** Returns control to the Blender UI. Every 0.05 seconds, it checks the queue for `BATCH_STL_PROGRESS:` tags to update the UI slider, or kills the `subprocess.Popen` object instantly if the user presses `ESC`.

### Phase B: Background Thread (Headless Execution Routine)
The `run_headless_export` function activates in the background:
1. **Scene Culling:** Iterates over the entire scene and aggressively mutes `show_viewport` on *every* Node modifier attached to an object not included in the active preset. This drastically accelerates `depsgraph.update()`.
2. **Batch Iteration:** Loops through each unique `BatchSTLExportItem` signature.
3. **Permutation Loop:**
   * Applies the override injection for the current combo.
   * Forces `bpy.context.view_layer.update()` to evaluate the scene geometry.
   * Generates the dynamic folder `sub_path` and filename `_suffixes`.
   * Filters out any objects listed in `mapping.excluded_objects`.
   * Pulls the final mesh using `obj.evaluated_get(depsgraph).to_mesh()`.
   * Passes the mesh to the STL Writer.
4. **Garbage Collection:** Calls `bpy.ops.outliner.orphans_purge()` recursively after every combination. This prevents memory leaks caused by generating thousands of orphaned mesh blocks.
5. **Cleanup:** Once all batches complete, calls `sys.exit(0)`. The Modal orchestrator detects the clean exit, cleans up the temporary OS directory, and finalizes the UI.

---

## 6. The Vectorized STL Writer (`write_fast_binary_stl`)

A custom, high-performance writer isolated from `bpy.ops.export_mesh.stl`.
* **NumPy Pre-allocation:** Reads loop triangles, vertex coordinates, and normals using `.foreach_get()` into flattened C-contiguous NumPy arrays.
* **Matrix Transforms:** Applies `matrix_world` transformations globally via NumPy dot products `np.dot(verts_vec4, mat.T)` instead of iterating vertex by vertex in Python.
* **Struct Packing:** Constructs a structured `np.dtype` that perfectly matches the binary STL specification (80-byte header, 4-byte face count, and 50-byte triangle blocks).
* **Buffer Stream:** Dumps the entire evaluated NumPy array directly to disk via `.tobytes()`.

---

## 7. UI Interactivity & Polish

* **Shift-Modifier Action Events:** UI operators (e.g., list reordering `UP/DOWN`, `ADD` input) override the `invoke` method to detect `event.shift`. This alters their execution path (e.g., moving items to absolute Top/Bottom, or automatically parsing Node interfaces to populate all available sockets).
* **JSON Serialization:** Two utility operators convert the deep `PropertyGroup` nested hierarchy into dictionary trees for export via `json.dump`, and perfectly reconstruct them upon import, bypassing Blender's complex internal RNA property copying limitations.
* **Graceful Exit:** The `unregister` function utilizes `try...except RuntimeError:` to silently bypass classes already cleared from memory, preventing harmless tracebacks when the headless process executes its hard `sys.exit(0)`.
