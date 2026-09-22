> **DISCLAIMER:** This is a "vibecoded" extension created using Gemini Pro. I highly recommend backing up your files before using it. I am not a programmer, so I cannot guarantee it will run without hiccups. It worked fine for me, but please use it with caution.

This extension batch-exports objects from collections to selected folders as STL files. 

### Presets and Directories
* Each preset contains collections mapped to target subfolders where you want to export the objects. 
* You specify a main root directory, and each preset can also have its own root. 
* The extension will automatically create directories if they do not already exist. 
* If you leave the folder field empty for a preset or a collection, no folder will be created.

### Collection Tags
* You can specify a tag for each collection mapping. 
* If the tag button is enabled, the tag is added to the filename of each exported object from that collection. 
* If the tag button is disabled, you can still use the tag as a custom name for the collection mapping.

### Geometry Node Overrides
You can override geometry node inputs to export geometry with specific parameters without needing to manually change them every time. This is particularly useful for enabling computationally heavy features—like high subdivision levels or voxel resolution—strictly during the export process. You can maintain a low-resolution version in the viewport for performance, and export a high-resolution version for quality with one click, eliminating the need to manually toggle settings back and forth. Overrides can be applied to floats, integers, booleans, menus, and strings.

* **Collection-Specific Settings:** Each collection within a preset has its own set of overrides.
* **Pinning Overrides:** You can pin overrides to apply them to all collections in a preset. 
* **Deleting & Unpinning:** If you delete a pinned override, it is removed from all collections. If you unpin an override, it becomes localized to each individual collection.
* **Targeting Nodes:** If the override target is a "Node," you must specify the parent node group and the child node name. If you only want to override an input exposed to the modifier panel, simply specify the node group name. Finally, define the input name, type, and value.