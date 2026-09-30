# Architecture & Structure: Fast Batch STL Exporter

This document outlines the software architecture, data structures, and execution flow of the Fast Batch STL Exporter for Blender[cite: 3]. The extension utilizes an adaptive, dual-path execution model to ensure maximum export speed for simple operations, while deploying concurrent headless background processes to protect the master project file during intensive combinatorial geometry generation without locking the user interface[cite: 3].

## 1. High-Level Architecture Overview

The extension evaluates the export parameters and dynamically routes the execution through one of two distinct paths[cite: 3]:

1. **The Synchronous Bypass (Main Thread):** If no overrides or permutations are requested, the exporter directly iterates through the evaluated Depsgraph and writes the STLs inline[cite: 3]. This skips the overhead of spawning a subprocess for simple 1:1 exports[cite: 3].
2. **Parallel Headless Workers (Background Subprocesses):** If state-mutating overrides are detected, it spawns an isolated background Blender instance[cite: 3]. Because the export state is decentralized, the user can spawn multiple concurrent headless workers to export different presets in parallel[cite: 3]. These workers receive a throwaway copy of the project file, execute the permutation matrices, and stream progress back to the main thread via standard output (stdout), all while the main UI remains fully unlocked and interactive[cite: 3].

This adaptive separation ensures that heavy permutations cannot corrupt the user's active `.blend` file or freeze their UI, while maintaining instantaneous exports for basic tasks[cite: 3].

---

## 2. Core Modules & Component Structure

### A. The Data Model (PropertyGroups)
The state of the exporter is stored directly in Blender's Scene data (`bpy.types.Scene.batch_stl_presets`), structured in a strict one-to-many 6-tier hierarchy[cite: 3]:
* **`BatchSTLExportPreset`**: The root configuration object[cite: 3]. Contains a global preset name, root directory prefix, a list of mapped collections, and globally pinned node group overrides[cite: 3]. Crucially, it acts as the isolated state-holder for parallel execution, containing its own `is_exporting`, `export_progress`, `cancel_export`, and `console_logs`[cite: 3].
* **`BatchSTLCollection`**: Represents a single mapped collection target[cite: 3]. Uses a soft-linked string (`collection_name`) to target the collection. Contains local naming tags, sub-path routes, object exclusion lists (`BatchSTLExcludedObject`), and a list of local node groups.
* **`BatchSTLNodeGroup`**: Represents the targeted Node Group tree holding the nodes/modifiers being injected[cite: 3]. Uses a soft-linked string (`group_name`) to target the data block, avoiding dependency graph locking.
* **`BatchSTLNode`**: Represents the specific target node within the tree[cite: 3]. If targeting the modifier directly, it falls back to a `<Modifier Interface>` placeholder[cite: 3].
* **`BatchSTLInput`**: Represents a specific input socket on the target node[cite: 3]. Handles automatic data-type inference (`infer_input_type`) upon assignment[cite: 3].
* **`BatchSTLValue`**: Represents a discrete permutation value or parameter sweep setting (float, int, bool, string, or menu item) assigned to the parent input, along with local tagging and directory instructions[cite: 3].
* **`BatchSTLLogLine`**: A simple string container used to cache stdout lines for the integrated UI console[cite: 3].

### B. The Permutation Engine & Data Adapters
Because the UI data model is strictly 6-tiers deep but the permutation math requires a flattened list, an adapter pattern is employed[cite: 3]:
* **`get_flat_overrides` & `TempMockOverride`:** Traverses the deep `preset > collection > nodegroup > node > input > value` hierarchy and squashes it into flat `MockOverride` instances[cite: 3].
* **`parse_sweep_values`**: Dynamically interprets `Sweep` ranges based on type (e.g., parsing float steps `1.0 0.5 5`, splitting comma-separated strings, or dynamically querying inner node enum arrays)[cite: 3].
* **`generate_override_combinations`**: Groups identical input targets and computes the Cartesian product (`itertools.product`) of all input states to generate a flat list of discrete permutation configurations[cite: 3].
* **`reconstruct_overrides_for_combo`**: Packages a raw permutation array back into a structured format that the injection logic can process[cite: 3].

### C. Path Simulation & Tree Visualizer
A predictive engine that visualizes the output without executing the graph[cite: 3]:
* **UI Caching Engine (`_ui_cache`)**: Calculating matrix paths and file collisions is intensive. The engine utilizes a dictionary cache that stores the resulting state for 0.25 seconds. This completely decouples heavy simulation math from Blender's fast interface draw loop, eliminating viewport lag.
* **`build_tree_dict`**: Simulates the permutation matrices and directory tagging logic to generate a nested dictionary representing the final folder structure[cite: 3]. It calculates the exact resulting `.stl` filenames and attaches them as leaf nodes[cite: 3].
* **Collision Detection:** During simulation, `build_tree_dict` maintains a global set of all absolute file paths[cite: 3]. If two combinations yield the exact same file path, they are flagged in a `duplicates` set, triggering a UI warning to prevent unintended overwrites[cite: 3].
* **`get_tree_lines`**: Recursively traverses the generated dictionary to output properly formatted ASCII branch connectors (`├──` and `└──`)[cite: 3].

### D. The Orchestrator (`EXPORT_OT_batch_stl_multi`)
Acts as the localized traffic controller for a specific preset's export process[cite: 3]. It utilizes instance-level variables (`self.process`, `self._timer`, `self.q`) to allow multiple orchestrators to run simultaneously[cite: 3].
* **If Clean:** Iterates the Depsgraph natively, writes STLs directly via the global binary writer, and completes synchronously[cite: 3].
* **If Dirty (Overrides Present):** 
  * Saves an uncompressed temporary copy of the active `.blend` file[cite: 3].
  * Spawns a headless Blender instance via `subprocess.Popen`[cite: 3].
  * Spawns a dedicated Daemon Thread to non-blockingly read `stdout` from the subprocess using a Thread-safe `queue.Queue`[cite: 3].
  * Transitions into a `modal` timer loop, reading the queue every 0.05s[cite: 3].
  * Appends intercepted `stdout` directly to the active preset's `console_logs` and updates the progress bar[cite: 3].
  * Forces an immediate UI refresh via `area.tag_redraw()` to ensure live 60fps console streaming without requiring mouse movement[cite: 3].
  * Listens for the `BATCH_STL_DONE` token to execute an aggressive `process.kill()` fast-exit[cite: 3].

### E. The Headless Execution Routine
Triggered only when the script is loaded with the `--batch-stl-headless` CLI argument[cite: 3].
* **Phase 0 (Depsgraph Culling):** Permanently mutes all Geometry Node modifiers on objects not actively participating in the current batch[cite: 3].
* **Phase 1 (State Injection):** Temporarily severs targeted Node Group links and injects fixed values directly, avoiding modifier duplication[cite: 3].
* **Phase 2 (Evaluation & Output):** Forces a `depsgraph.update()`, evaluates the mesh, passes it to the binary writer, and prints granular `BATCH_STL_PROGRESS` per object[cite: 3].
* **Phase 3 (Garbage Collection):** Calls `bpy.ops.outliner.orphans_purge()` aggressively after every permutation[cite: 3].

### F. Vectorized Binary STL Writer
Globally scoped to serve both execution paths, strictly internalizing dependencies (`numpy`, `struct`) to preserve lazy loading[cite: 3].
* Maps `mesh.vertices` and `mesh.loop_triangles` directly into flat NumPy arrays[cite: 3].
* Performs dot-product matrix transformations directly in C-space via NumPy[cite: 3].
* Formats the 80-byte header, triangle count, and unstructured triangle arrays into a strict C-struct memory map (`stl_dtype`)[cite: 3].
* Flushes the binary blob directly to disk via `data.tobytes()`[cite: 3].

---

## 3. UI State Management & View Routing

* **Soft-Linking & Depsgraph Protection:** Hard `PointerProperty` connections force Blender to continuously evaluate the dependency graph during UI redraws. This module relies exclusively on `StringProperty` variables bridged by UI `prop_search` elements to eliminate viewport freezing. Additionally, object exclusion relies entirely on the static `hide_viewport` property, avoiding the evaluation traps of the traditional `hide_get()` method.
* **Collapsible Architecture:** The entire interface (Presets, Displays, Collections, Global Overrides, Local Overrides, Object Filters) is wrapped in conditional `layout.box()` containers driven by Boolean properties (`batch_stl_ui_*`)[cite: 3].
* **Single-Table Spanning Layout:** The overrides are drawn as a unified, grid-like table rather than deeply nested graphical boxes[cite: 3]. Using `row.split()`, the UI dynamically aligns `Node Group > Target Node > Input Socket > Value & Options > Actions` into clean, continuous columns[cite: 3]. 
* **Automatic Type Inference:** The extension abstracts away type-selection logic[cite: 3]. When a user targets a node input, `infer_input_type` automatically scans the geometry nodes tree and silently assigns the correct float, int, string, bool, or menu property[cite: 3].
* **Inline Actions & Exclusivity:** Users can append new structural layers via inline `(+)` operator buttons located natively inside the row gaps[cite: 3]. Enabling a parametric sweep automatically purges manual duplicate rows and zeroes out static data values to ensure strict permutation state exclusivity[cite: 3].
* **Smart Auto-Switching:** 
  * When a user changes the `batch_stl_preset_index`, an `update` callback instantly resets the display block to show the predictive Tree Visualizer[cite: 3].
  * When the `EXPORT_OT_batch_stl_multi` operator is invoked, it programmatically switches the display block to the Global Console mode, ensuring the user immediately sees the live output of their active task[cite: 3].
