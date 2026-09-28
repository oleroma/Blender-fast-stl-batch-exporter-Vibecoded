# Architecture & Structure: Fast Batch STL Exporter

This document outlines the software architecture, data structures, and execution flow of the Fast Batch STL Exporter for Blender.[cite: 2] The extension utilizes an adaptive, dual-path execution model to ensure maximum export speed for simple operations, while deploying concurrent headless background processes to protect the master project file during intensive combinatorial geometry generation without locking the user interface.[cite: 2] It has been optimized from a monolithic structure into a modular, multi-file architecture to support agentic expansion.

## 1. High-Level Architecture Overview

The extension evaluates the export parameters and dynamically routes the execution through one of two distinct paths:[cite: 2]

1. **The Synchronous Bypass (Main Thread):** If no overrides or permutations are requested, the exporter directly iterates through the evaluated Depsgraph and writes the STLs inline.[cite: 2] This skips the overhead of spawning a subprocess for simple 1:1 exports.[cite: 2]
2. **Parallel Headless Workers (Background Subprocesses):** If state-mutating overrides are detected, it spawns an isolated background Blender instance.[cite: 2] Because the export state is decentralized, the user can spawn multiple concurrent headless workers to export different presets in parallel.[cite: 2] These workers receive a throwaway copy of the project file, execute the permutation matrices, and stream progress back to the main thread via standard output (stdout), all while the main UI remains fully unlocked and interactive.[cite: 2]

This adaptive separation ensures that heavy permutations cannot corrupt the user's active `.blend` file or freeze their UI, while maintaining instantaneous exports for basic tasks.[cite: 2]

---

## 2. Core Modules & Component Structure

The system logic is divided into specialized modules to enforce separation of concerns:

### A. The Data Model (`properties.py`)
The state of the exporter is stored directly in Blender's Scene data (`bpy.types.Scene.batch_stl_presets`), structured in a strict one-to-many 6-tier hierarchy:[cite: 2]
* **`BatchSTLExportPreset`**: The root configuration object.[cite: 2] Contains a global preset name, root directory prefix, a list of mapped collections, and globally pinned node group overrides.[cite: 2] Crucially, it acts as the isolated state-holder for parallel execution, containing its own `is_exporting`, `export_progress`, `cancel_export`, and `console_logs`.[cite: 2]
* **`BatchSTLCollection`**: Represents a single mapped collection target.[cite: 2] Contains the pointer to the target collection, local naming tags, sub-path routes, object exclusion lists (`BatchSTLExcludedObject`), and a list of local node groups.[cite: 2]
* **`BatchSTLNodeGroup`**: Represents the targeted Node Group tree holding the nodes/modifiers being injected.[cite: 2]
* **`BatchSTLNode`**: Represents the specific target node within the tree.[cite: 2] If targeting the modifier directly, it falls back to a `<Modifier Interface>` placeholder.[cite: 2]
* **`BatchSTLInput`**: Represents a specific input socket on the target node.[cite: 2] Handles automatic data-type inference (`infer_input_type`) upon assignment.[cite: 2]
* **`BatchSTLValue`**: Represents a discrete permutation value or parameter sweep setting (float, int, bool, string, or menu item) assigned to the parent input, along with local tagging and directory instructions.[cite: 2]
* **`BatchSTLLogLine`**: A simple string container used to cache stdout lines for the integrated UI console.[cite: 2]

### B. Global State & Utilities (`state.py` & `utils.py`)
* **`state.py`**: Acts as isolated central memory for the addon to prevent circular imports, managing the global clipboard dictionary for copy/paste operations across NodeGroups and Collections.
* **`utils.py`**: Contains JSON serialization and deserialization functions to keep the core engine lightweight when managing preset data.

### C. The Permutation Engine & Data Adapters (`core.py`)
Because the UI data model is strictly 6-tiers deep but the permutation math requires a flattened list, an adapter pattern is employed:[cite: 2]
* **`get_flat_overrides` & `TempMockOverride`:** Traverses the deep `preset > collection > nodegroup > node > input > value` hierarchy and squashes it into flat `MockOverride` instances.[cite: 2]
* **`parse_sweep_values`**: Dynamically interprets `Sweep` ranges based on type (e.g., parsing float steps `1.0 0.5 5`, splitting comma-separated strings, or dynamically querying inner node enum arrays).[cite: 2]
* **`generate_override_combinations`**: Groups identical input targets and computes the Cartesian product (`itertools.product`) of all input states to generate a flat list of discrete permutation configurations.[cite: 2]
* **`reconstruct_overrides_for_combo`**: Packages a raw permutation array back into a structured format that the injection logic can process.[cite: 2]

### D. Path Simulation & Tree Visualizer (`core.py` & `ui.py`)
A predictive engine that visualizes the output without executing the graph:[cite: 2]
* **`build_tree_dict`**: Simulates the permutation matrices and directory tagging logic to generate a nested dictionary representing the final folder structure.[cite: 2] It calculates the exact resulting `.stl` filenames and attaches them as leaf nodes.[cite: 2]
* **Collision Detection:** During simulation, `build_tree_dict` maintains a global set of all absolute file paths.[cite: 2] If two combinations yield the exact same file path, they are flagged in a `duplicates` set, triggering a UI warning to prevent unintended overwrites.[cite: 2]
* **`get_tree_lines`**: Recursively traverses the generated dictionary to output properly formatted ASCII branch connectors (`├──` and `└──`).[cite: 2]

### E. The Orchestrator (`operators.py`)
Acts as the localized traffic controller for a specific preset's export process.[cite: 2] It utilizes instance-level variables (`self.process`, `self._timer`, `self.q`) to allow multiple orchestrators to run simultaneously.[cite: 2]
* **If Clean:** Iterates the Depsgraph natively, writes STLs directly via the global binary writer, and completes synchronously.[cite: 2]
* **If Dirty (Overrides Present):** 
  * Saves an uncompressed temporary copy of the active `.blend` file.[cite: 2]
  * Spawns a headless Blender instance via `subprocess.Popen`.[cite: 2]
  * Spawns a dedicated Daemon Thread to non-blockingly read `stdout` from the subprocess using a Thread-safe `queue.Queue`.[cite: 2]
  * Transitions into a `modal` timer loop, reading the queue every 0.05s.[cite: 2]
  * Appends intercepted `stdout` directly to the active preset's `console_logs` and updates the progress bar.[cite: 2]
  * Forces an immediate UI refresh via `area.tag_redraw()` to ensure live 60fps console streaming without requiring mouse movement.[cite: 2]
  * Listens for the `BATCH_STL_DONE` token to execute an aggressive `process.kill()` fast-exit.[cite: 2]

### F. Vectorized Binary STL Writer & Headless Worker (`exporter.py`)
Globally scoped to serve both execution paths, strictly internalizing dependencies (`numpy`, `struct`) to preserve lazy loading.[cite: 2]
* Maps `mesh.vertices` and `mesh.loop_triangles` directly into flat NumPy arrays.[cite: 2]
* Performs dot-product matrix transformations directly in C-space via NumPy.[cite: 2]
* Formats the 80-byte header, triangle count, and unstructured triangle arrays into a strict C-struct memory map (`stl_dtype`).[cite: 2]
* Flushes the binary blob directly to disk via `data.tobytes()`.[cite: 2]
* **Phase 0 (Depsgraph Culling):** Permanently mutes all Geometry Node modifiers on objects not actively participating in the current batch.[cite: 2]
* **Phase 1 (State Injection):** Temporarily severs targeted Node Group links and injects fixed values directly, avoiding modifier duplication.[cite: 2]
* **Phase 2 (Evaluation & Output):** Forces a `depsgraph.update()`, evaluates the mesh, passes it to the binary writer, and prints granular `BATCH_STL_PROGRESS` per object.[cite: 2]
* **Phase 3 (Garbage Collection):** Calls `bpy.ops.outliner.orphans_purge()` aggressively after every permutation.[cite: 2]

### G. Bootstrapping (`__init__.py`)
Wires the extension into Blender's lifecycle and contains dynamic sys-path bootstrapping to allow relative module imports when executed directly as a standalone Python script during headless subprocess boots.

---

## 3. UI State Management & View Routing (`ui.py`)

* **Collapsible Architecture:** The entire interface (Presets, Displays, Collections, Global Overrides, Local Overrides, Object Filters) is wrapped in conditional `layout.box()` containers driven by Boolean properties (`batch_stl_ui_*`).[cite: 2]
* **Single-Table Spanning Layout:** The overrides are drawn as a unified, grid-like table rather than deeply nested graphical boxes.[cite: 2] Using `row.split()`, the UI dynamically aligns `Node Group > Target Node > Input Socket > Value & Options > Actions` into clean, continuous columns.[cite: 2]
* **Automatic Type Inference:** The extension abstracts away type-selection logic.[cite: 2] When a user targets a node input, `infer_input_type` automatically scans the geometry nodes tree and silently assigns the correct float, int, string, bool, or menu property.[cite: 2]
* **Inline Actions & Exclusivity:** Users can append new structural layers via inline `(+)` operator buttons located natively inside the row gaps.[cite: 2] Enabling a parametric sweep automatically purges manual duplicate rows and zeroes out static data values to ensure strict permutation state exclusivity.[cite: 2]
* **Smart Auto-Switching:** 
  * When a user changes the `batch_stl_preset_index`, an `update` callback instantly resets the display block to show the predictive Tree Visualizer.[cite: 2]
  * When the `EXPORT_OT_batch_stl_multi` operator is invoked, it programmatically switches the display block to the Global Console mode, ensuring the user immediately sees the live output of their active task.[cite: 2]
