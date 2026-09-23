# Architecture & Structure: Fast Batch STL Exporter

This document outlines the software architecture, data structures, and execution flow of the Fast Batch STL Exporter for Blender. The extension utilizes an adaptive, dual-path execution model to ensure maximum export speed for simple operations, while deploying a headless background process to protect the master project file during intensive combinatorial geometry generation.

## 1. High-Level Architecture Overview

The extension evaluates the export parameters and dynamically routes the execution through one of two distinct paths:

1. **The Synchronous Bypass (Main Thread):** If no overrides or permutations are requested, the exporter directly iterates through the evaluated Depsgraph and writes the STLs inline. This skips the overhead of spawning a subprocess for simple 1:1 exports.
2. **The Headless Worker (Background Subprocess):** If state-mutating overrides are detected, it spawns a background Blender instance. This worker receives a throwaway copy of the project file, executes the permutation matrices, and streams progress back to the main thread via standard output (stdout). 

This adaptive separation ensures that heavy permutations cannot corrupt the user's active `.blend` file or freeze their UI, while maintaining instantaneous exports for basic tasks.

---

## 2. Core Modules & Component Structure

### A. The Data Model (PropertyGroups)
The state of the exporter is stored directly in Blender's Scene data (`bpy.types.Scene.batch_stl_presets`), structured hierarchically:
* **`BatchSTLExportPreset`**: The root configuration object. Contains a global preset name, root directory prefix, a list of mapped collections, and global (pinned) overrides.
* **`BatchSTLExportItem`**: Represents a single mapped collection. Contains the pointer to the target collection, local naming tags, sub-path routes, object exclusion lists (`BatchSTLExcludedObject`), and a list of local overrides.
* **`BatchSTLNodeOverride`**: Represents a targeted parameter injection point (either an internal Geometry Node or a Modifier Interface).
* **`BatchSTLNodeInput`**: Represents a specific input socket and its target value, data type, and permutation rules.

### B. The Permutation Engine
This functional block computes the parameter matrix before export:
* **`parse_sweep_values`**: Dynamically interprets `Sweep` ranges based on type (e.g., parsing float steps `1.0 0.5 5`, splitting comma-separated strings, or querying enum arrays).
* **`generate_override_combinations`**: Groups identical input targets and computes the Cartesian product (`itertools.product`) of all input states to generate a flat list of discrete permutation configurations.
* **`reconstruct_overrides_for_combo`**: Packages a raw permutation array back into a structured `MockOverride` format that the injection logic can process.

### C. The Orchestrator (`EXPORT_OT_batch_stl_multi`)
Acts as the traffic controller for the export process:
* Evaluates the presence of `pinned_overrides` or `node_overrides`.
* **If Clean:** Locks the UI cursor, iterates the Depsgraph, writes STLs directly via the global binary writer, and updates the native cursor progress bar.
* **If Dirty (Overrides Present):** 
  * Saves an uncompressed temporary copy of the active `.blend` file.
  * Uses `subprocess.Popen` with `--factory-startup` to instantly spawn a headless Blender instance.
  * Spawns a Daemon Thread to non-blockingly read `stdout` from the subprocess using a Thread-safe `queue.Queue`.
  * Transitions into a `modal` timer loop, reading the queue every 0.05s to update `scene.export_progress` based on granular per-object counts.
  * Listens for the `BATCH_STL_DONE` token to instantly execute a `process.terminate()` fast-exit, bypassing slow garbage collection.

### D. The Headless Execution Routine
**`run_headless_export`**:
Triggered only when the script is loaded with the `--batch-stl-headless` CLI argument.
* **Phase 0 (Depsgraph Culling):** Scans the scene and permanently mutes all Geometry Node modifiers on objects not actively participating in the current batch.
* **Phase 1 (State Injection):** Temporarily severs targeted Node Group links and injects fixed values directly, avoiding modifier duplication.
* **Phase 2 (Evaluation & Output):** Forces a `depsgraph.update()`, evaluates the mesh, passes it to the binary writer, and prints granular `BATCH_STL_PROGRESS` per object.
* **Phase 3 (Garbage Collection):** Calls `bpy.ops.outliner.orphans_purge()` aggressively after every permutation.

### E. Vectorized Binary STL Writer
**`write_fast_binary_stl`**:
Globally scoped to serve both execution paths, but strictly internalizes its dependencies (`numpy`, `struct`) to preserve lazy loading.
* Maps `mesh.vertices` and `mesh.loop_triangles` directly into flat NumPy arrays.
* Performs dot-product matrix transformations (to align with `matrix_world`) directly in C-space via NumPy.
* Computes face normals algebraically if missing.
* Formats the exact 80-byte header, 4-byte triangle count, and unstructured triangle arrays into a strict C-struct memory map (`stl_dtype`).
* Flushes the binary blob directly to disk via `data.tobytes()`.

---

## 3. Data Flow & Execution Sequence

1. **User Initiation:** User clicks "Export" in the `VIEW3D_PT_batch_export_stl_multi` panel.
2. **Path Evaluation:** `EXPORT_OT_batch_stl_multi` invokes and checks for permutation overrides.
   * **Path A (Synchronous Bypass):** No overrides. Extension evaluates the Depsgraph in the main thread, writes STLs via `numpy`, updates the cursor progress bar, and finishes instantly.
   * **Path B (Headless Orchestration):** Overrides detected. Temp file is saved (`compress=False`). Headless subprocess boots with `--factory-startup`. Modal loop begins listening.
3. **Headless Execution (Path B Only):** 
    * Headless instance culls the Depsgraph and calculates the absolute `total_operations` matrix (Objects × Permutations). It prints `BATCH_STL_TOTAL:X` to stdout.
    * Master file states are overridden, Scene Depsgraph is updated, and evaluated meshes are passed to the NumPy STL writer.
    * Headless instance prints `BATCH_STL_PROGRESS:N` for every object written.
    * Upon completion, it prints `BATCH_STL_DONE`.
4. **UI Synchronization & Fast Teardown:** The main thread catches the progress tags to update the UI progress bar. When it catches `BATCH_STL_DONE`, it forcefully terminates the subprocess, cleans the temp directory, and releases the UI lock immediately.

---

## 4. Performance & Safety Design Patterns

* **Adaptive Execution Path:** Dynamically skipping the headless overhead for simple 1:1 exports ensures the tool feels instantly responsive when permutations are not required.
* **Instant Boot & Fast Teardown:** By appending `--factory-startup` to the subprocess, the headless instance skips loading user addons and UI themes. By manually terminating the subprocess upon catching `BATCH_STL_DONE`, it avoids Blender's slow C-level memory garbage collection sequence during `sys.exit()`, un-greying the UI instantaneously.
* **Zero-Consequence Mutability:** The headless process operates on a throwaway file, performing destructive optimizations (like aggressively muting global modifiers and severing node links) without complex error-handling or state-restoration logic.
* **Lazy Dependency Loading:** Heavy scientific libraries (`numpy`) and low-level memory modules (`struct`) are kept strictly scoped inside `write_fast_binary_stl`. This prevents the main UI thread from allocating unnecessary memory during standard viewport modeling.
* **UX & Undo State Management:** UI operators decouple from Blender's default generic Undo logging by omitting the `'UNDO'` flag from `bl_options` and injecting context-aware, highly readable messages via manual `bpy.ops.ed.undo_push()` (e.g., "Auto-Populate Sockets" or "Expand Sweep Permutations"). Tooltips dynamically adapt to the requested property action using `@classmethod def description`.
