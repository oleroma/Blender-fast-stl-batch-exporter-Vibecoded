# Fast Batch STL Exporter

> **DISCLAIMER:** This is a "vibecoded" extension created with assistance from AI. Always save and back up your project `.blend` files before running large batch exports. While engineered with extensive safety nets—including headless background execution—caution is always recommended when running automated scene-mutating scripts.

**Fast Batch STL Exporter** is a high-performance batch export pipeline and parametric permutation engine for Blender. Built around a custom vectorized NumPy binary STL generator and completely isolated headless background processing, it allows you to export entire scene collections, automatically sweep across multi-dimensional geometry parameter spaces, and filter specific objects with a single click.

---

## Core Features Breakdown

### 1. Vectorized Binary STL Generation
* **NumPy Memory Buffering:** Instead of relying on slow, per-triangle Python loops or standard I/O writers, the exporter formats vertex coordinates and pre-computed face normals directly into structured NumPy binary buffers.
* **Blazing Fast Disk I/O:** Meshes with hundreds of thousands of triangles are triangulated, evaluated, and streamed to disk in milliseconds.

### 2. Presets & Hierarchical Directory Architecture
* **Global Root Directory:** Set a master output folder in the 3D Viewport header (e.g., `//exports/` or an absolute drive path).
* **Preset Folders:** Each preset row in the UI list contains a dedicated folder field (`preset_prefix`). This creates a parent directory inside the root path.
* **Collection Sub-Folders:** Each collection mapping can define an optional `sub_path` relative to the preset folder.
* **Automatic Creation:** Folders are created automatically on export if they do not exist. Leaving a folder field blank exports directly to the parent folder without nesting.

### 3. Collection Tagging & Naming Pipeline
* **Base Tagging:** Assign custom tags to individual mapped collections (e.g., `_v1`, `_highres`).
* **Tag Toggle (`BOOKMARKS`):** When enabled, the collection tag is appended directly to the end of every exported object's filename. When disabled, the tag remains visible in the UI as a clean custom label without altering filenames.

### 4. Geometry Node & Modifier Overrides
Define temporary parameter states strictly during export—such as bumping voxel density, increasing subdivision steps, or toggling structural reinforcements.
* **Smart Data Types:** The UI automatically infers the correct data type (Float, Int, Boolean, String, Menu) by scanning your node tree. No manual type selection is required.
* **Pinned Overrides (Global):** Applied across all collection mappings within the preset.
* **Local Overrides:** Dedicated specifically to a single mapped collection.
* **Internal Node Target:** Specify both the parent Node Group and the exact internal Node Name to inject values directly into an internal socket (severing internal links temporarily).
* **Modifier Target:** Target an exposed socket on a specific modifier interface.

### 5. Node Group Interface Fallback & Instant Link Severing
When overriding a Node Group, you can **leave the internal Node field blank**. The extension will adapt automatically:
* **Interface Socket Search:** The input name field will search the exposed sockets on the Node Group's Interface rather than an internal node.
* **Group Input Severing:** The exporter locates the `Group Input` node inside the parent tree and unhooks the downstream wires. It directly injects your override values into the connected nodes, producing an ultra-fast global update across all objects sharing the node group.

### 6. Automated Parametric Sweeps & Permutations
Turn your geometry into an automated variant generator. Enable the **Sweep Button (`FILE_REFRESH`)** on any input row to automatically iterate through multiple values.
* **Floats & Integers:** Enter a range using the syntax `start step count` (e.g., `1.2 0.5 5` will evaluate 1.2, 1.7, 2.2, 2.7, 3.2).
* **Strings:** Enter a comma-separated list to iterate through text items (e.g., `Left, Right, Center`).
* **Booleans:** Automatically generates two permutations (`True` and `False`).
* **Menus:** Automatically extracts and iterates through every available item identifier in the targeted `Menu Switch` node.
* **Cross-Node Synchronization:** Sweeps calculate the Cartesian product across all defined inputs. If Input A sweeps 3 values and Input B sweeps 2, the exporter automatically evaluates all 6 unique combinations.

### 7. Permutation Suffix & Sub-Directory Formatting
When a sweep (or multiple identical inputs) is active, extra formatting controls appear dynamically on that input row:
* **Tag Button (`BOOKMARKS`):** Appends the variant label as a suffix to the STL filename.
* **Directory Button (`FILE_FOLDER`):** Creates an organized sub-folder for that variant and places the STLs inside it.
* **Smart Underscore (`_`) Formatting Rules:**
  * **No Underscore (`tag`):** Replaces the numerical value entirely.
  * **Trailing Underscore (`tag_`):** Prepends the tag to the value (e.g., `size_` with value `15` -> `_size_15`).
  * **Leading Underscore (`_tag`):** Appends the tag to the value (e.g., `_mm` with value `15` -> `_15_mm`).
  * **Blank Tag Field:** Defaults to the literal value of the socket.

### 8. Object Exclusion Filtering
Don't want to export everything in a collection? 
* Enable the **Filter Button (`FILTER`)** on any mapping row to reveal an "Exclude Objects" checklist.
* The box lists all valid geometry objects inside the collection. Click any object to toggle its exclusion. Excluded objects are skipped entirely during the batch export, and these settings are saved safely to your preset data.

### 9. Headless Background Export & Absolute Safety
The export process has been entirely decoupled from the main Blender UI thread for maximum stability and performance.
* **Instant Uncompressed Temp Dump:** Upon clicking Export, the extension saves an uncompressed throwaway copy of your `.blend` file (taking milliseconds) and hands it off to a completely invisible, headless background Blender process.
* **Aggressive Scene Culling & RAM Purging:** The headless instance ruthlessly deletes all unused objects and permanently mutes unused modifiers before evaluating geometry, resulting in incredibly fast Depsgraph updates. It also aggressively purges orphan mesh data (`orphans_purge`) after every permutation to prevent memory bloat.
* **Live UI Updates:** The main Blender UI locks safely to prevent accidental edits, while a live progress slider tracks the background task in real-time.
* **Emergency Kill Switch:** Press `ESC` or click the **Cancel Export** button at any time to instantly terminate the background process and clean up temporary files. Your active Blender file remains 100% untouched.

### 10. Advanced UI Power Shortcuts (Shift-Modifiers)
* **Expand Sweeps:** Hold `Shift` while clicking an active Sweep toggle to disable the sweep and instantly expand the calculated values into explicit, individual input rows.
* **Auto-Populate Sockets:** Hold `Shift` while clicking the ADD (`+`) button on an input block to automatically scan the target node (or group interface) and generate an input row for *every* available socket.
* **Instant Reordering:** Hold `Shift` while clicking the `UP` or `DOWN` arrows on any preset, mapping, override, or input to instantly move it to the absolute top or bottom of the list.

### 11. JSON Preset Portability
Save your entire export setup to an external JSON configuration file. Presets, collection bindings, exclusions, pinned configurations, and permutation sweep rules can be exported or imported with one click, allowing setups to be shared across blend files or team members.

---

## Step-by-Step Workflow Guide

### Standard Batch Export
1. Set the **Root Export Dir** in the 3D Viewport panel (`N-Panel -> Export`).
2. Click `+` on the **Presets** list to create a preset, and name its folder prefix.
3. Click `+` on the **Collections Mapping** list and pick the collection containing your target meshes.
4. Click the Filter (`FILTER`) icon if you need to exclude specific objects within that collection.
5. Click the **Export** icon next to the preset name.

### Setting Up an Automated Parametric Sweep
To export multiple variations of a model automatically:
1. In the **Overrides** box, click `+` to add an override block.
2. Select your Geometry Node Group. Leave the **Node** field blank to target the Interface.
3. Add an input row by clicking `+`. Pick your target socket (e.g., `Wall_Thickness`). 
   * *Pro-tip: Hold Shift while clicking `+` to populate all available sockets at once.*
4. Enable the **Sweep Button (`FILE_REFRESH`)** on the input row.
5. Enter your sweep parameters. For a Float input, entering `2.0 2.0 3` will generate variants for `2.0`, `4.0`, and `6.0`.
6. Configure the naming options that appear:
   * Enter `_mm` into the tag field and enable the Tag (`BOOKMARKS`) button to append `_2_mm`, `_4_mm`, etc. to the filenames.
   * Enable the Folder (`FILE_FOLDER`) button if you want separate directories generated for each thickness.
7. Hit **Export**. The addon spawns a headless background instance to safely evaluate, format, and save every permutation while updating your UI progress bar.
