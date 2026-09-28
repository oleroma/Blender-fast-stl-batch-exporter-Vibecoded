# Fast Batch STL Exporter

> **DISCLAIMER:** This is a "vibecoded" extension created with assistance from AI.[cite: 3] Always save and back up your project `.blend` files before running large batch exports.[cite: 3] While engineered with an adaptive execution model and crash protection, caution is always recommended when running automated scene-mutating scripts.[cite: 3]

**Fast Batch STL Exporter** is a high-performance batch export pipeline and parametric permutation engine for Blender.[cite: 3] Built around a custom vectorized NumPy binary STL generator and an adaptive execution engine, it allows you to export entire scene collections, sweep across multi-dimensional geometry parameter spaces, and organize complex manufacturing variants with a single click.[cite: 3] The extension features an agentic-optimized, modular file architecture for stability and maintainability.

---

## Core Features Breakdown

### 1. Vectorized Binary STL Generation
* **NumPy Memory Buffering:** Instead of relying on slow, per-triangle Python loops or standard I/O writers, the exporter formats vertex coordinates and pre-computed face normals directly into structured NumPy binary buffers.[cite: 3]
* **Blazing Fast Disk I/O:** Meshes with hundreds of thousands of triangles are triangulated, evaluated, and streamed to disk in milliseconds.[cite: 3]

### 2. Adaptive Dual-Path Execution & Parallel Processing
The exporter intelligently analyzes your preset configuration to select the optimal execution route:[cite: 3]
* **Synchronous Bypass:** Standard exports without node overrides are evaluated natively and written instantly in the main thread.[cite: 3]
* **Parallel Headless Workers:** If destructive permutations are detected, the exporter seamlessly falls back to an isolated subprocess architecture to protect your active project file.[cite: 3] The UI remains fully unlocked, allowing you to launch multiple concurrent exports for different presets simultaneously.[cite: 3]

### 3. Predictive ASCII Tree & Overwrite Protection
Stop guessing where your files will end up.[cite: 3] A predictive visualizer runs at the bottom of the extension panel:[cite: 3]
* **Live ASCII Directory Simulation:** Instantly calculates your permutation matrices and draws a clean ASCII tree (`├──` / `└──`) representing the exact nested folder structure that will be generated on your drive.[cite: 3]
* **Leaf Node File Preview:** The visualizer calculates the final dynamic naming rules and lists the specific `.stl` filenames at the end of every directory branch.[cite: 3]
* **Collision Detection System:** If multiple permutations or mappings accidentally result in the exact same filename in the same folder, a bold warning automatically appears at the top of the tree, allowing you to fix naming tags before you accidentally overwrite files during export.[cite: 3]

### 4. Integrated Live Console
You no longer need to toggle Blender's clunky System Console to monitor headless exports.[cite: 3]
* **Preset-Scoped Output:** Every preset maintains its own isolated console log.[cite: 3] The UI console only displays the standard output, execution times, and graph evaluation metrics for the preset you currently have selected.[cite: 3]
* **Smart Auto-Switching:** When you configure a preset, the UI displays the ASCII tree.[cite: 3] The moment you hit Export, the UI automatically flips to the Console to stream the live progress of the background worker.[cite: 3] Selecting a different preset automatically snaps the view back to the tree visualizer.[cite: 3]

### 5. Collection Mapping & Exclusion Filters
* **Base Tagging:** Assign custom tags to individual mapped collections.[cite: 3]
* **Tag Toggle (`BOOKMARKS`):** When enabled, the collection tag is appended directly to the end of every exported object's filename.[cite: 3]
* **Object Exclusion Filter (`FILTER`):** Expand the collapsible filter block on any mapping to reveal a checklist of all valid mesh objects within that collection.[cite: 3] You can explicitly exclude specific objects from the batch export without needing to hide them in your viewport.[cite: 3]

### 6. Geometry Node & Modifier Overrides
Define temporary parameter states strictly during export and automatically restore original scene settings once finished.[cite: 3]
* **Targeting:** Target exposed modifier sockets or deeply nested internal nodes.[cite: 3] The unified Node Search field automatically parses your target groups and displays options formatted as `Instance Name [Base Group Name]` for easy identification.[cite: 3]
* **Smart UI Grouping:** When assigning multiple discrete values to the same input socket, the UI seamlessly collapses them into a single clean box, keeping your target controls at the top and your value iterations stacked neatly below.[cite: 3]
* **Dynamic Menu Searching:** Targeting a Menu/Enum switch?[cite: 3] The value field turns into a dynamic searchable list of all available options specific to that node.[cite: 3]
* **Shift-Click Workflows:**
  * **Duplicate Row:** Hold `Shift` and click the `COPY` button on any input row to instantly duplicate it directly below the current line.[cite: 3]
  * **Auto-Populate Sockets:** Hold `Shift` and click the global `ADD (+)` button at the bottom of an override block to instantly scan the target node and generate an input row for every available socket.[cite: 3]

### 7. Parametric Sweeping (The Permutation Engine)
Turn your geometry into an automated variant generator.[cite: 3] By clicking the **Sweep (`FILE_REFRESH`)** icon on any input row, you can define a range of values to automatically iterate through.[cite: 3]
* **Float/Int Syntax:** Enter your range as `start step count` (e.g., `1.0 0.5 5` generates 1.0, 1.5, 2.0, 2.5, 3.0).[cite: 3]
* **String Syntax:** Enter a comma-separated list of strings (e.g., `PartA, PartB, PartC`).[cite: 3]
* **Booleans & Menus:** Automatically calculates combinations for `True/False` or iterates through all available enum items.[cite: 3]
* **Mutual Exclusivity:** The UI ensures your state remains valid.[cite: 3] Turning on Sweep will automatically purge any manually duplicated rows for that socket.[cite: 3] Conversely, duplicating a row using `Shift+Copy` instantly disables Sweep.[cite: 3]

### 8. Permutation Suffix & Sub-Directory Formatting
When permutation logic is triggered, formatting controls dynamically appear:[cite: 3]
* **Directory Button (`FILE_FOLDER`):** Creates an organized sub-folder for that variant.[cite: 3]
* **Tag Button (`BOOKMARKS`):** Appends the variant label as a suffix to the STL filename.[cite: 3]
* **Smart Underscore (`_`) Formatting Rules:**
  * **No Underscore (`tag`):** Replaces the numerical value entirely (e.g., `tag` -> `_tag`).[cite: 3]
  * **Trailing Underscore (`tag_`):** Prepends the tag to the value (e.g., `size_` with value `15` -> `_size_15`).[cite: 3]
  * **Leading Underscore (`_tag`):** Appends the tag to the value (e.g., `_mm` with value `15` -> `_15_mm`).[cite: 3]
  * **Blank Tag Field:** Defaults to the literal value of the socket.[cite: 3]

### 9. Fully Collapsible UI
To manage complex, multi-mapping workflows without scrolling fatigue, every major section of the extension (Presets, Console, Mappings, Exclusions, Global Overrides, Local Overrides, Tree) is wrapped in a collapsible block.[cite: 3] Click the down arrow in the header of any section to fold it away.[cite: 3]

### 10. JSON Preset Portability
Save your entire export setup to an external JSON configuration file.[cite: 3] Presets, collection bindings, pinned configurations, object exclusions, and permutation rules can be exported or imported with one click, allowing setups to be shared across blend files or team members.[cite: 3]

---

## Step-by-Step Workflow Guide

### Standard Batch Export
1. Set the **Root Export Dir** in the 3D Viewport panel (`N-Panel -> Export`).[cite: 3]
2. Click `+` on the **Presets** list to create a preset, and name its folder prefix.[cite: 3]
3. Click `+` on the **Collections Mapping** list and pick the collection containing your target meshes.[cite: 3]
4. Expand the `FILTER` block to explicitly exclude any objects you do not want to export.[cite: 3]
5. Set an optional collection subfolder or custom tag suffix.[cite: 3] Check the Tree Visualizer at the bottom to verify the output path.[cite: 3]
6. Click the **Export** icon next to the preset name.[cite: 3]

### Setting Up a Parametric Sweep
To export multiple variations of a model automatically:[cite: 3]
1. In the **Overrides** box, click `+` to add an override block.[cite: 3]
2. Select your Geometry Node Group.[cite: 3]
3. Leave the **Node** field blank to target the interface directly, or use the searchable dropdown to select a nested node.[cite: 3]
4. Add an input (e.g., `Wall_Thickness`) and enable the **Sweep (`FILE_REFRESH`)** icon.[cite: 3]
5. For a Float input, enter your sweep range (e.g., `2.0 2.0 2` to generate versions at 2.0 and 4.0).[cite: 3]
6. Configure the dynamic naming toggles:[cite: 3]
   * Set the tag to `_mm` and enable the Tag button (`BOOKMARKS`) to append `_2_mm` and `_4_mm` to the exported files.[cite: 3]
   * Enable the Folder button (`FILE_FOLDER`) if you want separate directories for each size.[cite: 3]
7. Verify the structure and check for any bold red collision warnings in the Tree Visualizer at the bottom of the panel.[cite: 3]
8. Hit **Export**.[cite: 3] The UI will automatically swap to the Console view, evaluating the permutations in the background, updating your inline progress bar, and organizing the STL files into their appropriate paths while you continue to work.[cite: 3]
