# Fast Batch STL Exporter

> **DISCLAIMER:** This is a "vibecoded" extension created with assistance from AI. Always save and back up your project `.blend` files before running large batch exports[cite: 3]. While engineered with an adaptive execution model and crash protection, caution is always recommended when running automated scene-mutating scripts[cite: 3].

**Fast Batch STL Exporter** is a high-performance batch export pipeline and parametric permutation engine for Blender[cite: 3]. Built around a custom vectorized NumPy binary STL generator and an adaptive execution engine, it allows you to export entire scene collections, sweep across multi-dimensional geometry parameter spaces, and organize complex manufacturing variants with a single click[cite: 3].

---

## Core Features Breakdown

### 1. Vectorized Binary STL Generation
* **NumPy Memory Buffering:** Instead of relying on slow, per-triangle Python loops or standard I/O writers, the exporter formats vertex coordinates and pre-computed face normals directly into structured NumPy binary buffers[cite: 3].
* **Blazing Fast Disk I/O:** Meshes with hundreds of thousands of triangles are triangulated, evaluated, and streamed to disk in milliseconds[cite: 3].

### 2. Adaptive Dual-Path Execution & Parallel Processing (New)
The exporter now intelligently analyzes your preset configuration to select the optimal execution route:
* **Synchronous Bypass:** If you are running a standard export without any node or modifier overrides, the exporter evaluates the Depsgraph natively and writes the STLs instantly in the main thread, accompanied by Blender's native cursor progress bar[cite: 3].
* **Parallel Headless Workers:** If destructive permutations are detected, the exporter seamlessly falls back to an isolated subprocess architecture to protect your active project file from memory leaks or infinite loops[cite: 3]. The UI remains fully unlocked, allowing you to continue 3D modeling or even launch multiple concurrent exports for different presets simultaneously.

### 3. Presets & Hierarchical Directory Architecture
* **Global Root Directory:** Set a master output folder in the 3D Viewport header (e.g., `//exports/` or an absolute drive path)[cite: 3].
* **Preset Folders:** Each preset row in the UI list contains a dedicated folder field (`preset_prefix`)[cite: 3]. This creates a parent directory inside the root path[cite: 3].
* **Collection Sub-Folders:** Each collection mapping can define an optional `sub_path` relative to the preset folder[cite: 3].
* **Automatic Creation:** Folders are created automatically on export if they do not exist[cite: 3]. Leaving a folder field blank exports directly to the parent folder without nesting[cite: 3].

### 4. Collection Mapping & Exclusion Filters
* **Base Tagging:** Assign custom tags to individual mapped collections (e.g., `_v1`, `_highres`)[cite: 3].
* **Tag Toggle (`BOOKMARKS`):** When enabled, the collection tag is appended directly to the end of every exported object's filename[cite: 3]. When disabled, the tag remains visible in the UI as a clean custom label without altering filenames[cite: 3].
* **Object Exclusion Filter (`FILTER`):** Clicking the funnel icon on any mapping reveals a checklist of all valid mesh objects within that collection[cite: 3]. You can manually exclude specific objects from the batch export without needing to hide them in your viewport or modify the collection hierarchy[cite: 3].

### 5. Geometry Node & Modifier Overrides
Avoid manually editing node groups or modifier panels just to prepare files for export[cite: 3]. Overrides let you define temporary parameter states strictly during export and automatically restore original scene settings once finished[cite: 3].
* **Targeting:** Target internal nodes or exposed modifier interface sockets using Float, Int, Boolean, String, or Menu data types[cite: 3].
* **Global vs Local:** Pin overrides globally to affect all mappings in a preset, or keep them local to a specific collection[cite: 3].
* **Shift-Click Reordering:** Hold `Shift` while clicking the UP/DOWN arrows on any preset, mapping, or override item to instantly move it to the absolute top or bottom of the list[cite: 3].
* **Shift-Click Auto-Populate:** Hold `Shift` while clicking the ADD (`+`) button on an input block to automatically scan the target node (or group interface) and generate an input row for every available socket, complete with automatic data type detection[cite: 3].

### 6. Node Group Interface Fallback & Instant Link Severing
When overriding a Node Group, you can **leave the internal Node field blank**[cite: 3]. The extension will adapt automatically[cite: 3]:
* **Interface Socket Search:** The input name field will search the exposed sockets on the Node Group's Interface rather than an internal node[cite: 3].
* **Group Input Severing:** The exporter locates the `Group Input` node inside the parent tree and unhooks the downstream wires[cite: 3]. It directly injects your override values into the connected nodes and reconnects the original wires immediately after export[cite: 3]. This produces an ultra-fast O(1) global update across all objects sharing the node group[cite: 3].

### 7. Parametric Sweeping (The Permutation Engine)
Turn your geometry into an automated variant generator[cite: 3]. By clicking the **Sweep (`FILE_REFRESH`)** icon on any input row, you can define a range of values to automatically iterate through[cite: 3].
* **Float/Int Syntax:** Enter your range as `start step count` (e.g., `1.0 0.5 5` generates 1.0, 1.5, 2.0, 2.5, 3.0)[cite: 3].
* **String Syntax:** Enter a comma-separated list of strings (e.g., `PartA, PartB, PartC`)[cite: 3].
* **Booleans & Menus:** Automatically calculates combinations for `True/False` or iterates through all available enum items in a `GeometryNodeMenuSwitch`[cite: 3].
* **Shift-Click to Expand:** If you prefer to manage sweep states manually, hold `Shift` and click an active Sweep button[cite: 3]. The engine will instantly calculate the permutations and unpack them into individual, duplicated input rows[cite: 3].

### 8. Permutation Suffix & Sub-Directory Formatting
When permutation logic is triggered, extra formatting controls appear dynamically on that input row[cite: 3]:
* **Tag Button (`BOOKMARKS`):** Appends the variant label as a suffix to the STL filename[cite: 3].
* **Directory Button (`FILE_FOLDER`):** Creates an organized sub-folder for that variant and places the STLs inside it[cite: 3].
* **Smart Underscore (`_`) Formatting Rules:**[cite: 3]
  * **No Underscore (`tag`):** Replaces the numerical value entirely (e.g., `tag` -> `_tag`)[cite: 3].
  * **Trailing Underscore (`tag_`):** Prepends the tag to the value (e.g., `size_` with value `15` -> `_size_15`)[cite: 3].
  * **Leading Underscore (`_tag`):** Appends the tag to the value (e.g., `_mm` with value `15` -> `_15_mm`)[cite: 3].
  * **Blank Tag Field:** Defaults to the literal value of the socket (e.g., value `15` -> `_15`)[cite: 3].

### 9. Parallel Headless Export & Crash-Proof UI
When utilizing overrides, the exporter operates on a completely non-blocking, isolated architecture to protect your master file[cite: 3].
* **Parallel Concurrent Exports:** Export state is decentralized, allowing you to click "Export" on multiple presets at the same time. The extension will manage multiple background headless instances in parallel without locking the Blender interface.
* **Inline Progress Bars:** When a headless export begins, the export button on that specific preset's row seamlessly transforms into a dedicated, granular progress bar tracking total `Objects × Permutations`.
* **Headless Background Process:** Saves an uncompressed temporary copy of your scene and launches a hidden background instance to perform the actual calculations and STL generation[cite: 3].
* **Instant Boot:** Utilizes `--factory-startup` to skip loading addons, themes, and UI elements, drastically accelerating the background launch time[cite: 3].
* **Aggressive Crash Recovery:** The main thread utilizes aggressive process killing and persistent state-flushing (`load_post` handlers) to guarantee the UI never gets permanently stuck on a progress bar, even if you interrupt the process via the system console or save the file mid-export.
* **Dedicated Kill Switch:** Each actively exporting preset features its own dedicated "Cancel" button (`X` icon) that immediately terminates its specific background process and deletes the temporary file, ensuring no frozen UI states or corrupted project files[cite: 3].

### 10. Refined UX & Undo History
* **Context-Aware Tooltips:** Hover over any micro-button (Add, Remove, Move, Pin, Copy, Paste) for a dynamically generated, precise description of what it will do to the selected property.
* **Beautiful Undo Logging:** Operator actions are cleanly pushed into Blender's Undo History (e.g., "Expand Sweep Permutations", "Auto-Populate Sockets", "Pin Override") rather than generic scripting logs, allowing you to `Ctrl+Z` through your configuration steps safely.

### 11. JSON Preset Portability
Save your entire export setup to an external JSON configuration file[cite: 3]. Presets, collection bindings, pinned configurations, object exclusions, and permutation rules can be exported or imported with one click, allowing setups to be shared across blend files or team members[cite: 3].

---

## Step-by-Step Workflow Guide

### Standard Batch Export
1. Set the **Root Export Dir** in the 3D Viewport panel (`N-Panel -> Export`)[cite: 3].
2. Click `+` on the **Presets** list to create a preset, and name its folder prefix[cite: 3].
3. Click `+` on the **Collections Mapping** list and pick the collection containing your target meshes[cite: 3].
4. Click the `FILTER` icon to explicitly exclude any objects you do not want to export[cite: 3].
5. Set an optional collection subfolder or custom tag suffix[cite: 3].
6. Click the **Export** icon next to the preset name[cite: 3].

### Setting Up a Parametric Sweep
To export multiple variations of a model automatically[cite: 3]:
1. In the **Overrides** box, click `+` to add an override block[cite: 3].
2. Select your Geometry Node Group[cite: 3].
3. Leave the **Node** field blank to target the Interface[cite: 3].
4. Add an input (e.g., `Wall_Thickness`) and enable the **Sweep (`FILE_REFRESH`)** icon[cite: 3].
5. For a Float input, enter your sweep range (e.g., `2.0 2.0 2` to generate versions at 2.0 and 4.0)[cite: 3].
6. Configure the dynamic naming toggles[cite: 3]:
   * Set the tag to `_mm` and enable the Tag button to append `_2_mm` and `_4_mm` to the exported files[cite: 3].
   * Enable the Folder button if you want separate directories for each size[cite: 3].
7. Hit **Export**[cite: 3]. The headless script evaluates the permutations in the background, updates your inline progress bar, and organizes the STL files into their appropriate paths while you continue to work[cite: 3].
