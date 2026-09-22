# Fast Batch STL Exporter

> **DISCLAIMER:** This is a "vibecoded" extension created with assistance from AI. Always save and back up your project `.blend` files before running large batch exports. While engineered with extensive safety nets and non-destructive state restoration, caution is always recommended when running automated scene-mutating scripts.

**Fast Batch STL Exporter** is a high-performance batch export pipeline and parametric permutation engine for Blender. Built around a custom vectorized NumPy binary STL generator and non-destructive Depsgraph isolation, it allows you to export entire scene collections, sweep across multi-dimensional geometry parameter spaces, and organize complex manufacturing variants with a single click.

---

## Core Features Breakdown

### 1. Vectorized Binary STL Generation
* **NumPy Memory Buffering:** Instead of relying on slow, per-triangle Python loops or standard I/O writers, the exporter formats vertex coordinates and pre-computed face normals directly into structured NumPy binary buffers.
* **Blazing Fast Disk I/O:** Meshes with hundreds of thousands of triangles are triangulated, evaluated, and streamed to disk in milliseconds.

### 2. Presets & Hierarchical Directory Architecture
* **Global Root Directory:** Set a master output folder in the 3D Viewport header (e.g., `//exports/` or an absolute drive path).
* **Preset Folders:** Each preset row in the UI list contains a dedicated folder field (`preset_prefix`) to establish parent directories inside the root path.
* **Collection Sub-Folders:** Each collection mapping can define an optional `sub_path` relative to the preset folder.
* **Automatic Creation:** Folders are created automatically on export if they do not exist. Leaving a folder field blank exports directly to the parent folder without nesting.

### 3. Collection Tagging & Naming Pipeline
* **Base Tagging:** Assign custom tags to individual mapped collections (e.g., `_v1`, `_highres`).
* **Tag Toggle (`BOOKMARKS`):** When enabled, the collection tag is appended directly to the end of every exported object's filename. When disabled, the tag remains visible in the UI as a clean custom label without altering filenames.

### 4. Collection Object Exclusion Filter
* **Granular Object Selection (`FILTER`):** Click the funnel filter icon on any mapped collection row to expose the dedicated **Exclude Objects** panel.
* **Non-Destructive Mesh Toggles:** Displays an interactive checklist of every exportable mesh, curve, surface, font, and metaball inside the collection. Toggle items off to skip them during batch evaluation without hiding or unlinking them in your scene outline.
* **Preset Persistence:** Excluded object lists are automatically serialized and restored when using JSON presets.

### 5. Geometry Node & Modifier Overrides
Overrides let you define temporary parameter states strictly during export—such as adjusting wall thickness, increasing subdivision steps, or toggling structural features—and automatically restore original scene settings once finished.

* **Pinned Overrides (Global):** Applied across all collection mappings within the preset.
* **Local Overrides:** Dedicated specifically to a single mapped collection.
* **Internal Node Target:** Target internal nodes within a Geometry Nodes group by name to inject socket values directly.
* **Modifier Target:** Target exposed sockets on modifier interfaces.
* **Automatic Type Detection:** Selecting a socket automatically resolves its type (`Float`, `Int`, `Boolean`, `String`, or `Menu`), eliminating clutter by removing manual type dropdowns from the UI.
* **Direct Copy/Paste:** Dedicated clipboard actions inside both Global Pinned and Local Override headers allow duplicating configurations across presets.

### 6. Node Group Interface Fallback & Instant Link Severing
When overriding a Node Group, you can **leave the internal Node field blank**:
* **Interface Socket Search:** The input name field searches exposed sockets on the Node Group's interface.
* **Group Input Severing:** Instead of running slow Python loops over dozens of individual object modifiers, the exporter locates the `Group Input` node inside the parent tree and unhooks downstream wires. It directly injects override values and reconnects the original wires immediately after export for an ultra-fast O(1) global update.

### 7. Automated Parameter Sweeping Engine
Instead of manually creating duplicate input rows to define variations, you can enable the **Sweep Engine** (`FILE_REFRESH`) on any input line:
* **Floats & Integers:** Uses a compact 3-value syntax in a single text field: `start step count`. For example, `10.0 2.5 4` automatically sweeps across `10.0`, `12.5`, `15.0`, and `17.5`.
* **Strings:** Accepts comma-separated values (e.g., `matte, gloss, textured`).
* **Booleans:** Automatically toggles and evaluates both `True` and `False` states.
* **Menus / Enums:** Automatically crawls the Geometry Node graph to locate connected `Menu Switch` nodes or node enum items, sweeping across every available menu entry without manual entry.
* **Shift-Click Expansion (Unpack Sweep):** Holding `Shift` while clicking an enabled Sweep button unpacks and converts the evaluated sweep range into individual, fully editable input rows.

### 8. Combinatorial Permutations & Suffix Formatting
When multiple variations exist (either via the Sweep engine or duplicate socket targets), the combinatorial engine computes the Cartesian product across all parameters.
* **Cross-Node Synchronization:** Identical input targets across different nodes pool matching values together to prevent redundant combinations.
* **Permutation Controls (`BOOKMARKS` & `FILE_FOLDER`):**
  * **Tag Button (`BOOKMARKS`):** Appends the variant label as a suffix to the STL filename.
  * **Directory Button (`FILE_FOLDER`):** Creates an organized sub-folder for that variant.
* **Smart Underscore (`_`) Formatting Rules:**
  * **No Underscore (`tag`):** Replaces the numerical value entirely (e.g., `tag` -> `_tag`).
  * **Trailing Underscore (`tag_`):** Prepends the tag to the value (e.g., `size_` with `15` -> `_size_15`).
  * **Leading Underscore (`_tag`):** Appends the tag to the value (e.g., `_mm` with `15` -> `_15_mm`).
  * **Blank Tag Field:** Defaults to the literal value of the socket (e.g., `15` -> `_15`).

### 9. Power-User Shortcuts (Shift Modifiers)
Accelerate workflow setup with integrated keyboard modifiers:
* **Shift + Reorder Arrows (`TRIA_UP` / `TRIA_DOWN`):** Instantly moves the selected item directly to the very top or bottom of the list across Presets, Mappings, Overrides, and Inputs.
* **Shift + Add Input (`PLUS`):** Automatically scans the target node (or the group interface) and populates an input row for every available socket with its detected data type.
* **Shift + Sweep Toggle (`FILE_REFRESH`):** Unpacks an automated sweep range into discrete, individual input rows.

### 10. Live UI Diagnostics & Metrics
* **Mappings Header:** Displays total mapped collections alongside total combined export permutations across the entire preset (e.g., `Mappings (3 items, 18 combos):`).
* **Overrides Header:** Displays a compact, real-time diagnostic line showing active permutation states, targets, and input counts (e.g., `Collection_A [v1] | 6 combos | 2 targets | 5 inputs`).

### 11. Bulletproof Recovery & Console Interrupt Handling
* **Micro-Wrapped Execution:** Every permutation evaluation is wrapped in an isolated `try...finally` block. Geometry generation errors or file write failures immediately revert modified nodes before proceeding.
* **Master Snapshot Engine:** Captures a read-only snapshot of all scene modifier states, sockets, and original wire connections prior to execution.
* **Keyboard Interrupt Catch (`Ctrl+C`):** Cancelling an ongoing export in the terminal triggers graceful recovery, suppressing tracebacks and restoring all scene objects and node trees to their pristine state.

### 12. JSON Preset Portability
Save your entire export configuration to an external JSON file. Presets, collection bindings, exclusions, overrides, and automated sweep definitions can be exported or imported with one click.

---

## Step-by-Step Workflow Guide

### Standard Batch Export
1. Set the **Root Export Dir** in the 3D Viewport panel (`N-Panel -> Export`).
2. Click `+` on the **Presets** list to create a preset, and name its folder prefix.
3. Click `+` on the **Collections Mapping** list and pick the collection containing your target meshes.
4. Set an optional collection subfolder or custom tag suffix.
5. Click the **Export** icon next to the preset name.

### Setting Up Automated Parameter Sweeping
To export multiple parametric variations without creating repetitive input rows:
1. In the **Overrides** box, click `+` to add an override block.
2. Select your Geometry Node Group (leave the **Node** field blank to target the interface).
3. Click `+` to add an input row (or **Shift + Click `+`** to populate all available sockets).
4. Click the **Sweep** button (`FILE_REFRESH`) on the target input:
   * **Float / Int:** Enter `start step count` (e.g., `2.0 0.5 5` sweeps `2.0`, `2.5`, `3.0`, `3.5`, `4.0`).
   * **String:** Enter comma-separated values (e.g., `Round, Chamfer, Sharp`).
   * **Boolean:** Evaluates both states automatically.
   * **Menu:** Automatically cycles all menu options connected to that interface socket.
5. Set optional naming tags (e.g., `_mm`) and toggle the Tag (`BOOKMARKS`) or Directory (`FILE_FOLDER`) buttons.
6. *(Optional)* **Shift + Click** the Sweep button if you want to expand the sweep into separate, manually editable rows.
7. Click **Export**.

### Filtering Objects Within Mapped Collections
1. In the **Mappings** list, select your collection item and click the filter icon (`FILTER`).
2. An **Exclude Objects** box appears below the mapping diagnostics.
3. Click on any object name to toggle it between included (checked) and excluded (unchecked). Excluded objects remain untouched in the viewport but are skipped entirely during STL generation.
