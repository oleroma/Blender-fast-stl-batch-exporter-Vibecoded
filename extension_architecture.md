# Architecture & Structure: Fast Batch STL Exporter

This document outlines the software architecture, data structures, and execution flow of the Fast Batch STL Exporter for Blender. The extension utilizes an adaptive, dual-path execution model to ensure maximum export speed for simple operations, while deploying concurrent headless background processes to protect the master project file during intensive combinatorial geometry generation without locking the user interface.

## 1. High-Level Architecture Overview

The extension evaluates the export parameters and dynamically routes the execution through one of two distinct paths:

1. **The Synchronous Bypass (Main Thread):** If no overrides or permutations are requested, the exporter directly iterates through the evaluated Depsgraph and writes the STLs inline. This skips the overhead of spawning a subprocess for simple 1:1 exports.
2. **Parallel Headless Workers (Background Subprocesses):** If state-mutating overrides are detected, it spawns an isolated background Blender instance. Because the export state is decentralized, the user can spawn multiple concurrent headless workers to export different presets in parallel. These workers receive a throwaway copy of the project file, execute the permutation matrices, and stream progress back to the main thread via standard output (stdout), all while the main UI remains fully unlocked and interactive.

This adaptive separation ensures that heavy permutations cannot corrupt the user's active `.blend` file or freeze their UI, while maintaining instantaneous exports for basic tasks.

---

## 2. Core Modules & Component Structure

### A. The Data Model (PropertyGroups)
The state of the exporter is stored directly in Blender's Scene data (`bpy.types.Scene.batch_stl_presets`), structured hierarchically:
* **`BatchSTLExportPreset`**: The root configuration object. Contains a global preset name, root directory prefix, a list of mapped collections, and global (pinned) overrides. **Crucially, it also acts as the isolated state-holder for parallel execution**, containing its own `is_exporting`, `export_progress`, `cancel_export`, `export_status`, and an isolated `console_logs` collection to store stdout printouts.
* **`BatchSTLExportItem`**: Represents a single mapped collection. Contains the pointer to the target collection, local naming tags, sub-path routes, object exclusion lists (`BatchSTLExcludedObject`), and a list of local overrides.
* **`BatchSTLNodeOverride`**: Represents a targeted parameter injection point (either an internal Geometry Node or a Modifier Interface). Contains a dynamic `node_name` search callback that resolves node instances alongside their base group names (`Instance Name [Base Group]`).
* **`BatchSTLNodeInput`**: Represents a specific input socket and its target value, data type, and permutation rules. Features dynamic context-aware search callbacks for Enum/Menu types (`value_menu`).
* **`BatchSTLLogLine`**: A simple string container used to cache stdout lines for the integrated UI console.

### B. The Permutation Engine
This functional block computes the parameter matrix before export:
* **`parse_sweep_values`**: Dynamically interprets `Sweep` ranges based on type (e.g., parsing float steps `1.0 0.5 5`, splitting comma-separated strings, or dynamically querying inner node enum arrays).
* **`generate_override_combinations`**: Groups identical input targets and computes the Cartesian product (`itertools.product`) of all input states to generate a flat list of discrete permutation configurations.
* **`reconstruct_overrides_for_combo`**: Packages a raw permutation array back into a structured `MockOverride` format that the injection logic can process.

### C. Path Simulation & Tree Visualizer
A predictive engine that visualizes the output without executing the graph:
* **`build_tree_dict`**: Simulates the permutation matrices and directory tagging logic to generate a nested dictionary representing the final folder structure. It calculates the exact resulting `.stl` filenames and attaches them as leaf nodes.
* **Collision Detection:** During simulation, `build_tree_dict` maintains a global set of all absolute file paths. If two combinations yield the exact same file path, they are flagged in a `duplicates` set, triggering a UI warning to prevent unintended overwrites.
* **`get_tree_lines`**: Recursively traverses the generated dictionary to output properly formatted ASCII branch connectors (`├──` and `└──`).

### D. The Orchestrator (`EXPORT_OT_batch_stl_multi`)
Acts as the localized traffic controller for a specific preset's export process. It utilizes instance-level variables (`self.process`, `self._timer`, `self.q`) to allow multiple orchestrators to run simultaneously.
* **If Clean:** Iterates the Depsgraph natively, writes STLs directly via the global binary writer, and completes synchronously.
* **If Dirty (Overrides Present):** 
  * Saves an uncompressed temporary copy of the active `.blend` file.
  * Spawns a headless Blender instance via `subprocess.Popen`.
  * Spawns a dedicated Daemon Thread to non-blockingly read `stdout` from the subprocess using a Thread-safe `queue.Queue`.
  * Transitions into a `modal` timer loop, reading the queue every 0.05s.
  * Appends intercepted `stdout` directly to the active preset's `console_logs` and updates the progress bar.
  * Forces an immediate UI refresh via `area.tag_redraw()` to ensure live 60fps console streaming without requiring mouse movement.
  * Listens for the `BATCH_STL_DONE` token to execute an aggressive `process.kill()` fast-exit.

### E. The Headless Execution Routine
Triggered only when the script is loaded with the `--batch-stl-headless` CLI argument.
* **Phase 0 (Depsgraph Culling):** Permanently mutes all Geometry Node modifiers on objects not actively participating in the current batch.
* **Phase 1 (State Injection):** Temporarily severs targeted Node Group links and injects fixed values directly, avoiding modifier duplication.
* **Phase 2 (Evaluation & Output):** Forces a `depsgraph.update()`, evaluates the mesh, passes it to the binary writer, and prints granular `BATCH_STL_PROGRESS` per object.
* **Phase 3 (Garbage Collection):** Calls `bpy.ops.outliner.orphans_purge()` aggressively after every permutation.

### F. Vectorized Binary STL Writer
Globally scoped to serve both execution paths, strictly internalizing dependencies (`numpy`, `struct`) to preserve lazy loading.
* Maps `mesh.vertices` and `mesh.loop_triangles` directly into flat NumPy arrays.
* Performs dot-product matrix transformations directly in C-space via NumPy.
* Formats the 80-byte header, triangle count, and unstructured triangle arrays into a strict C-struct memory map (`stl_dtype`).
* Flushes the binary blob directly to disk via `data.tobytes()`.

---

## 3. UI State Management & View Routing

* **Collapsible Architecture:** The entire interface (Presets, Displays, Mappings, Global Overrides, Local Overrides, Object Filters) is wrapped in conditional `layout.box()` containers driven by Boolean properties (`batch_stl_ui_*`).
* **Dynamic Input Grouping:** To prevent visual clutter, input variations sharing the exact same socket name are dynamically combined into unified sub-boxes. The socket name and sweep controls are drawn only once in the header row, with subsequent discrete values neatly stacked below.
* **Sweep vs. Discrete Exclusivity:** The UI operations enforce strict state exclusion. Enabling a parametric sweep automatically purges manual duplicate rows and zeroes out static data values. Conversely, duplicating a row (Shift+Copy) or pasting matching state data automatically disables the sweep toggle across the group.
* **Smart Auto-Switching:** 
  * When a user changes the `batch_stl_preset_index`, an `update` callback instantly resets the display block to show the predictive Tree Visualizer.
  * When the `EXPORT_OT_batch_stl_multi` operator is invoked, it programmatically switches the display block to the Global Console mode, ensuring the user immediately sees the live output of their active task.
