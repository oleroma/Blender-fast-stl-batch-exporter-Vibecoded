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
* **`BatchSTLExportPreset`**: The root configuration object. Contains a global preset name, root directory prefix, a list of mapped collections, and global (pinned) overrides. **Crucially, it also acts as the isolated state-holder for parallel execution**, containing its own `is_exporting`, `export_progress`, `cancel_export`, and `export_status` variables.
* **`BatchSTLExportItem`**: Represents a single mapped collection. Contains the pointer to the target collection, local naming tags, sub-path routes, object exclusion lists (`BatchSTLExcludedObject`), and a list of local overrides.
* **`BatchSTLNodeOverride`**: Represents a targeted parameter injection point (either an internal Geometry Node or a Modifier Interface).
* **`BatchSTLNodeInput`**: Represents a specific input socket and its target value, data type, and permutation rules.

### B. The Permutation Engine
This functional block computes the parameter matrix before export:
* **`parse_sweep_values`**: Dynamically interprets `Sweep` ranges based on type (e.g., parsing float steps `1.0 0.5 5`, splitting comma-separated strings, or querying enum arrays).
* **`generate_override_combinations`**: Groups identical input targets and computes the Cartesian product (`itertools.product`) of all input states to generate a flat list of discrete permutation configurations.
* **`reconstruct_overrides_for_combo`**: Packages a raw permutation array back into a structured `MockOverride` format that the injection logic can process.

### C. The Orchestrator (`EXPORT_OT_batch_stl_multi`)
Acts as the localized traffic controller for a specific preset's export process. It utilizes instance-level variables (`self.process`, `self._timer`, `self.q`) to allow multiple orchestrators to run simultaneously without data collision.
* Evaluates the presence of `pinned_overrides` or `node_overrides`.
* **If Clean:** Iterates the Depsgraph natively, writes STLs directly via the global binary writer, and completes synchronously.
* **If Dirty (Overrides Present):** 
  * Saves an uncompressed temporary copy of the active `.blend` file.
  * Uses `subprocess.Popen` with `--factory-startup` to instantly spawn a headless Blender instance.
  * Spawns a dedicated Daemon Thread to non-blockingly read `stdout` from the subprocess using a Thread-safe `queue.Queue`.
  * Transitions into a localized `modal` timer loop, reading the queue every 0.05s to update the specific preset's `export_progress`.
  * Listens for the `BATCH_STL_DONE` token to execute an aggressive `process.kill()` fast-exit, bypassing slow garbage collection.

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

1. **User Initiation:** User clicks the "Export" icon on a specific preset row.
2. **Path Evaluation:** `EXPORT_OT_batch_stl_multi` invokes and checks for permutation overrides.
   * **Path A (Synchronous Bypass):** No overrides. Extension evaluates the Depsgraph in the main thread and writes STLs via `numpy` instantly.
   * **Path B (Headless Orchestration):** Overrides detected. Temp file is saved (`compress=False`). Headless subprocess boots. The preset's export button is dynamically replaced by an inline progress bar and cancel button. The UI remains fully unlocked.
3. **Headless Execution (Path B Only):** 
    * Headless instance culls the Depsgraph and calculates the absolute `total_operations` matrix.
    * Master file states are overridden, Scene Depsgraph is updated, and evaluated meshes are passed to the NumPy STL writer.
    * Headless instance prints `BATCH_STL_PROGRESS:N` for every object written.
    * Upon completion, it prints `BATCH_STL_DONE`.
4. **UI Synchronization & Fast Teardown:** The main thread catches the progress tags to update the individual preset's progress bar. When it catches `BATCH_STL_DONE`, it forcefully kills the specific subprocess, cleans the temp directory, and restores the preset's export button.

---

## 4. Performance & Safety Design Patterns

* **Parallel Concurrent Exports:** By moving execution state from the global Scene directly to individual Presets and utilizing instance-level operator variables, the extension supports spawning multiple simultaneous headless workers. This allows users to batch export different combinatorial presets in parallel while continuing to 3D model in the unlocked main UI.
* **Adaptive Execution Path:** Dynamically skipping the headless overhead for simple 1:1 exports ensures the tool feels instantly responsive when permutations are not required.
* **Aggressive Failsafes & Crash Recovery:** Replaced graceful subprocess termination with aggressive `kill()` commands to prevent hanging processes. A persistent `@persistent` handler on `load_post` guarantees all UI locks and progress bars are immediately flushed if the user reloads the file after a crash.
* **Instant Boot & Fast Teardown:** By appending `--factory-startup` to the subprocess, the headless instance skips loading user addons and UI themes. 
* **Zero-Consequence Mutability:** The headless process operates on a throwaway file, performing destructive optimizations (like aggressively muting global modifiers and severing node links) without complex error-handling or state-restoration logic.
* **Lazy Dependency Loading:** Heavy scientific libraries (`numpy`) and low-level memory modules (`struct`) are kept strictly scoped inside `write_fast_binary_stl`. This prevents the main UI thread from allocating unnecessary memory during standard viewport modeling.
