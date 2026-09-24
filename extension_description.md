# Fast Batch STL Exporter

> **DISCLAIMER:** This is a "vibecoded" extension created with assistance from AI. Always save and back up your project `.blend` files before running large batch exports. While engineered with an adaptive execution model and crash protection, caution is always recommended when running automated scene-mutating scripts.

**Fast Batch STL Exporter** is a high-performance batch export pipeline and parametric permutation engine for Blender. Built around a custom vectorized NumPy binary STL generator and an adaptive execution engine, it allows you to export entire scene collections, sweep across multi-dimensional geometry parameter spaces, and organize complex manufacturing variants with a single click.

---

## Core Features Breakdown

### 1. Vectorized Binary STL Generation
* **NumPy Memory Buffering:** Instead of relying on slow, per-triangle Python loops or standard I/O writers, the exporter formats vertex coordinates and pre-computed face normals directly into structured NumPy binary buffers.
* **Blazing Fast Disk I/O:** Meshes with hundreds of thousands of triangles are triangulated, evaluated, and streamed to disk in milliseconds.

### 2. Adaptive Dual-Path Execution & Parallel Processing
The exporter intelligently analyzes your preset configuration to select the optimal execution route:
* **Synchronous Bypass:** Standard exports without node overrides are evaluated natively and written instantly in the main thread.
* **Parallel Headless Workers:** If destructive permutations are detected, the exporter seamlessly falls back to an isolated subprocess architecture to protect your active project file. The UI remains fully unlocked, allowing you to launch multiple concurrent exports for different presets simultaneously.

### 3. Predictive ASCII Tree & Overwrite Protection (New)
Stop guessing where your files will end up. A predictive visualizer runs at the bottom of the extension panel:
* **Live ASCII Directory Simulation:** Instantly calculates your permutation matrices and draws a clean ASCII tree (`├──` / `└──`) representing the exact nested folder structure that will be generated on your drive.
* **Leaf Node File Preview:** The visualizer calculates the final dynamic naming rules and lists the specific `.stl` filenames at the end of every directory branch.
* **Collision Detection System:** If multiple permutations or mappings accidentally result in the exact same filename in the same folder, a bold warning automatically appears at the top of the tree, allowing you to fix naming tags before you accidentally overwrite files during export.

### 4. Integrated Live Console (New)
You no longer need to toggle Blender's clunky System Console to monitor headless exports. 
* **Preset-Scoped Output:** Every preset maintains its own isolated console log. The UI console only displays the standard output, execution times, and graph evaluation metrics for the preset you currently have selected.
* **Smart Auto-Switching:** When you configure a preset, the UI displays the ASCII tree. The moment you hit Export, the UI automatically flips to the Console to stream the live progress of the background worker. Selecting a different preset automatically snaps the view back to the tree visualizer.

### 5. Collection Mapping & Exclusion Filters
* **Base Tagging:** Assign custom tags to individual mapped collections.
* **Tag Toggle (`BOOKMARKS`):** When enabled, the collection tag is appended directly to the end of every exported object's filename.
* **Object Exclusion Filter (`FILTER`):** Expand the collapsible filter block on any mapping to reveal a checklist of all valid mesh objects within that collection. You can explicitly exclude specific objects from the batch export without needing to hide them in your viewport.

### 6. Geometry Node & Modifier Overrides
Define temporary parameter states strictly during export and automatically restore original scene settings once finished.
* **Targeting:** Target internal nodes or exposed modifier interface sockets using Float, Int, Boolean, String, or Menu data types.
* **Global vs Local:** Pin overrides globally to affect all mappings in a preset, or keep them local to a specific collection.
* **Shift-Click Reordering:** Hold `Shift` while clicking the UP/DOWN arrows on any item to instantly move it to the absolute top or bottom of the list.
* **Shift-Click Auto-Populate:** Hold `Shift` while clicking the ADD (`+`) button to automatically scan the target node and generate an input row for every available socket, complete with automatic data type detection.

### 7. Parametric Sweeping (The Permutation Engine)
Turn your geometry into an automated variant generator. By clicking the **Sweep (`FILE_REFRESH`)** icon on any input row, you can define a range of values to automatically iterate through.
* **Float/Int Syntax:** Enter your range as `start step count` (e.g., `1.0 0.5 5` generates 1.0, 1.5, 2.0, 2.5, 3.0).
* **String Syntax:** Enter a comma-separated list of strings (e.g., `PartA, PartB, PartC`).
* **Booleans & Menus:** Automatically calculates combinations for `True/False` or iterates through all available enum items.
* **Shift-Click to Expand:** Hold `Shift` and click an active Sweep button to instantly calculate the permutations and unpack them into individual, duplicated input rows.

### 8. Permutation Suffix & Sub-Directory Formatting
When permutation logic is triggered, formatting controls dynamically appear:
* **Directory Button (`FILE_FOLDER`):** Creates an organized sub-folder for that variant.
* **Tag Button (`BOOKMARKS`):** Appends the variant label as a suffix to the STL filename.
* **Smart Underscore (`_`) Formatting Rules:**
  * **No Underscore (`tag`):** Replaces the numerical value entirely (e.g., `tag` -> `_tag`).
  * **Trailing Underscore (`tag_`):** Prepends the tag to the value (e.g., `size_` with value `15` -> `_size_15`).
  * **Leading Underscore (`_tag`):** Appends the tag to the value (e.g., `_mm` with value `15` -> `_15_mm`).
  * **Blank Tag Field:** Defaults to the literal value of the socket.

### 9. Fully Collapsible UI (New)
To manage complex, multi-mapping workflows without scrolling fatigue, every major section of the extension (Presets, Console, Mappings, Exclusions, Global Overrides, Local Overrides, Tree) is now wrapped in a collapsible block. Click the down arrow in the header of any section to fold it away.

### 10. JSON Preset Portability
Save your entire export setup to an external JSON configuration file. Presets, collection bindings, pinned configurations, object exclusions, and permutation rules can be exported or imported with one click, allowing setups to be shared across blend files or team members.

---

## Step-by-Step Workflow Guide

### Standard Batch Export
1. Set the **Root Export Dir** in the 3D Viewport panel (`N-Panel -> Export`).
2. Click `+` on the **Presets** list to create a preset, and name its folder prefix.
3. Click `+` on the **Collections Mapping** list and pick the collection containing your target meshes.
4. Expand the `FILTER` block to explicitly exclude any objects you do not want to export.
5. Set an optional collection subfolder or custom tag suffix. Check the Tree Visualizer at the bottom to verify the output path.
6. Click the **Export** icon next to the preset name.

### Setting Up a Parametric Sweep
To export multiple variations of a model automatically:
1. In the **Overrides** box, click `+` to add an override block.
2. Select your Geometry Node Group.
3. Leave the **Node** field blank to target the Interface.
4. Add an input (e.g., `Wall_Thickness`) and enable the **Sweep (`FILE_REFRESH`)** icon.
5. For a Float input, enter your sweep range (e.g., `2.0 2.0 2` to generate versions at 2.0 and 4.0).
6. Configure the dynamic naming toggles:
   * Set the tag to `_mm` and enable the Tag button (`BOOKMARKS`) to append `_2_mm` and `_4_mm` to the exported files.
   * Enable the Folder button (`FILE_FOLDER`) if you want separate directories for each size.
7. Verify the structure and check for any bold red collision warnings in the Tree Visualizer at the bottom of the panel.
8. Hit **Export**. The UI will automatically swap to the Console view, evaluating the permutations in the background, updating your inline progress bar, and organizing the STL files into their appropriate paths while you continue to work.
