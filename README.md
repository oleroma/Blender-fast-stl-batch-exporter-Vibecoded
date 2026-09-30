# Fast Batch STL Exporter

> **DISCLAIMER:** This is a "vibecoded" extension created with assistance from AI. Always save and back up your project `.blend` files before running large batch exports. While engineered with an adaptive execution model and crash protection, caution is always recommended when running automated scene-mutating scripts[cite: 2].

**Fast Batch STL Exporter** is a high-performance batch export pipeline and parametric permutation engine for Blender[cite: 2]. Built around a custom vectorized NumPy binary STL generator and an adaptive execution engine, it allows you to export entire scene collections, sweep across multi-dimensional geometry parameter spaces, and organize complex manufacturing variants with a single click[cite: 2].

---

## Core Features Breakdown

### 1. Vectorized Binary STL Generation
* **NumPy Memory Buffering:** Instead of relying on slow, per-triangle Python loops or standard I/O writers, the exporter formats vertex coordinates and pre-computed face normals directly into structured NumPy binary buffers[cite: 2].
* **Blazing Fast Disk I/O:** Meshes with hundreds of thousands of triangles are triangulated, evaluated, and streamed to disk in milliseconds[cite: 2].

### 2. Adaptive Dual-Path Execution & Parallel Processing
The exporter intelligently analyzes your preset configuration to select the optimal execution route[cite: 2]:
* **Synchronous Bypass:** Standard exports without node overrides are evaluated natively and written instantly in the main thread[cite: 2].
* **Parallel Headless Workers:** If destructive permutations are detected, the exporter seamlessly falls back to an isolated subprocess architecture to protect your active project file[cite: 2]. The UI remains fully unlocked, allowing you to launch multiple concurrent exports for different presets simultaneously[cite: 2].

### 3. Predictive ASCII Tree & Overwrite Protection
Stop guessing where your files will end up. A predictive visualizer runs at the bottom of the extension panel[cite: 2]:
* **Live ASCII Directory Simulation:** Instantly calculates your permutation matrices and draws a clean ASCII tree (`├──` / `└──`) representing the exact nested folder structure that will be generated on your drive[cite: 2].
* **Leaf Node File Preview:** The visualizer calculates the final dynamic naming rules and lists the specific `.stl` filenames at the end of every directory branch[cite: 2].
* **Collision Detection System:** If multiple permutations or mappings accidentally result in the exact same filename in the same folder, a bold warning automatically appears at the top of the tree, allowing you to fix naming tags before you accidentally overwrite files during export[cite: 2].
* **4Hz UI Caching Engine:** Tree generation is decoupled from the UI draw loop via a 0.25-second caching engine, ensuring the interface remains completely lag-free even when calculating massive permutation matrices.

### 4. Integrated Live Console
You no longer need to toggle Blender's clunky System Console to monitor headless exports[cite: 2]. 
* **Preset-Scoped Output:** Every preset maintains its own isolated console log[cite: 2]. The UI console only displays the standard output, execution times, and graph evaluation metrics for the preset you currently have selected[cite: 2].
* **Smart Auto-Switching:** When you configure a preset, the UI displays the ASCII tree[cite: 2]. The moment you hit Export, the UI automatically flips to the Console to stream the live progress of the background worker[cite: 2]. Selecting a different preset automatically snaps the view back to the tree visualizer[cite: 2].

### 5. Collection Mapping & Exclusion Filters
* **Base Tagging:** Assign custom tags to individual mapped collections[cite: 2].
* **Tag Toggle (`BOOKMARKS`):** When enabled, the collection tag is appended directly to the end of every exported object's filename[cite: 2].
* **Object Exclusion Filter (`FILTER`):** Expand the collapsible filter block on any mapping to reveal a checklist of all valid mesh objects within that collection[cite: 2]. You can explicitly exclude specific objects from the batch export without needing to hide them in your viewport[cite: 2].

### 6. Geometry Node & Modifier Overrides
Define temporary parameter states strictly during export and automatically restore original scene settings once finished[cite: 2].
* **Zero-Lag Soft Linking:** Uses dynamic string-based soft links and `prop_search` rather than hard property pointers. This prevents Blender from locking up the viewport by constantly re-evaluating the dependency graph when you interact with the UI.
* **Targeting:** Target exposed modifier sockets or deeply nested internal nodes[cite: 2]. The unified Node Search field automatically parses your target groups and displays options formatted as `Instance Name [Base Group Name]` for easy identification[cite: 2].
* **Smart UI Grouping:** When assigning multiple discrete values to the same input socket, the UI seamlessly collapses them into a single clean box, keeping your target controls at the top and your value iterations stacked neatly below[cite: 2].
* **Dynamic Menu Searching:** Targeting a Menu/Enum switch? The value field turns into a dynamic searchable list of all available options specific to that node[cite: 2].
* **Shift-Click Workflows:**
  * **Duplicate Row:** Hold `Shift` and click the `COPY` button on any input row to instantly duplicate it directly below the current line[cite: 2].
  * **Auto-Populate Sockets:** Hold `Shift` and click the global `ADD (+)` button at the bottom of an override block to instantly scan the target node and generate an input row for every available socket[cite: 2].

### 7. Parametric Sweeping (The Permutation Engine)
Turn your geometry into an automated variant generator. By clicking the **Sweep (`FILE_REFRESH`)** icon on any input row, you can define a range of values to automatically iterate through[cite: 2].
* **Float/Int Syntax:** Enter your range as `start step count` (e.g., `1.0 0.5 5` generates 1.0, 1.5, 2.0, 2.5, 3.0)[cite: 2].
* **String Syntax:** Enter a comma-separated list of strings (e.g., `PartA, PartB, PartC`)[cite: 2].
* **Booleans & Menus:** Automatically calculates combinations for `True/False` or iterates through all available enum items[cite: 2].
* **Mutual Exclusivity:** The UI ensures your state remains valid[cite: 2]. Turning on Sweep will automatically purge any manually duplicated rows for that socket[cite: 2]. Conversely, duplicating a row using `Shift+Copy` instantly disables Sweep[cite: 2].

### 8. Permutation Suffix & Sub-Directory Formatting
When permutation logic is triggered, formatting controls dynamically appear[cite: 2]:
* **Directory Button (`FILE_FOLDER`):** Creates an organized sub-folder for that variant[cite: 2].
* **Tag Button (`BOOKMARKS`):** Appends the variant label as a suffix to the STL filename[cite: 2].
* **Smart Underscore (`_`) Formatting Rules:**
  * **No Underscore (`tag`):** Replaces the numerical value entirely (e.g., `tag` -> `_tag`)[cite: 2].
  * **Trailing Underscore (`tag_`):** Prepends the tag to the value (e.g., `size_` with value `15` -> `_size_15`)[cite: 2].
  * **Leading Underscore (`_tag`):** Appends the tag to the value (e.g., `_mm` with value `15` -> `_15_mm`)[cite: 2].
  * **Blank Tag Field:** Defaults to the literal value of the socket[cite: 2].

### 9. Fully Collapsible UI
To manage complex, multi-mapping workflows without scrolling fatigue, every major section of the extension (Presets, Console, Mappings, Exclusions, Global Overrides, Local Overrides, Tree) is wrapped in a collapsible block[cite: 2]. Click the down arrow in the header of any section to fold it away[cite: 2].

### 10. JSON Preset Portability
Save your entire export setup to an external JSON configuration file[cite: 2]. Presets, collection bindings, pinned configurations, object exclusions, and permutation rules can be exported or imported with one click, allowing setups to be shared across blend files or team members[cite: 2].
