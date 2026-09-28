# Overrides Section Architecture and Functionality

The Overrides section in the Fast Batch STL Exporter allows users to dynamically control and iterate through Geometry Node parameters during the batch export process. It features a hierarchical, nested UI structure designed to clearly represent the relationship between targets and their values.

## Core Hierarchy

The UI is built using nested `layout.box()` containers to visually group children with their parents. A 3% left indentation is applied at each nesting level to enhance readability.

### 1. NodeGroup (Level 1 - Root)
The highest level of the hierarchy. It defines which Geometry Node tree the overrides will apply to.
- **Visuals**: Drawn as the outermost box.
- **Target**: A pointer to a Blender NodeTree (`group_ptr`).
- **Actions** (Right-aligned):
  - **Move Up / Down**: Reorders the NodeGroup (only spawns if multiple groups exist).
  - **Trash**: Deletes the NodeGroup.
  - **Pin / Unpin**: Moves the NodeGroup between the Global (shared across all collections) and Local (specific to the active collection) tables.
  - **Copy / Paste**: Copies the entire group hierarchy to the clipboard to be pasted elsewhere.
- **Add Node Action** (Left-aligned): Spawns a new child Node within this group.

### 2. Node (Level 2)
Targets a specific node inside the parent NodeGroup.
- **Visuals**: Each Node (along with its inputs) is wrapped in its own dedicated box container. This box is indented 3% inward from the NodeGroup wrapper.
- **Target**: A string property (`name`) that can be searched. If left blank or set to `<Modifier Interface>`, the overrides apply directly to the modifier's exposed inputs rather than an internal node.
- **Actions** (Right-aligned):
  - **Move Up / Down**: Reorders the Node (only spawns if multiple nodes exist).
  - **Trash**: Deletes the Node.
- **Add Input Action** (Left-aligned): Spawns a new child Input parameter for this node.

### 3. Input (Level 3)
Targets a specific socket/parameter on the parent Node.
- **Visuals**: All inputs belonging to a Node are now wrapped together in a single shared box container. This box is indented 3% inward from the Node wrapper.
- **Target**: A string property (`name`) mapping to the exact socket name.
- **Actions**:
  - **Add Value / Sweep** (Left-aligned, before the name): Adds a new value iteration to this input. If clicked with `Shift`, it toggles **Sweep Mode**. 
  - **Move Up / Down** (Right-aligned): Reorders the Input (only spawns if multiple inputs exist).
  - **Trash** (Right-aligned): Deletes the Input.

### 4. Value (Level 4 - Leaf)
The specific value(s) to be injected into the target Input during export.
- **Visuals**: Values are drawn horizontally aligned with the Input row. A small gap separates the input column, value column, and metadata column for readability (`align=False` on row splits).
- **Data Types**: Automatically inferred from the target socket. Supports `FLOAT`, `INT`, `STRING`, `BOOLEAN`, and `MENU`.
- **Value Actions** (Right-aligned, on subsequent values if multiple exist):
  - **Move Up / Down**: Reorders the value within the input's list.
  - **Trash**: Deletes the specific value iteration.

## Advanced Functionality

### Sweep Mode
Sweep mode allows automatic iteration through a range or list of values for a single input. It is activated by holding `Shift` and clicking the Add Value button at the beginning of the Input row.
- When enabled, the button turns into a depressed `FILE_REFRESH` icon, and any other discrete values for that input are deleted.
- **Floats/Ints**: Displays a field to specify the Sweep Range (e.g., start, step, count).
- **Booleans**: Automatically iterates through `True` and `False`.
- **Menus**: Automatically iterates through all available enum items for that socket.
- **Populating Sweep Values**: If you `Shift`-click the sweep button while it is active, it will turn off Sweep Mode and automatically populate all the evaluated sweep values into individual, editable value iterations.
- *Note: To maintain visual alignment without rendering artifacts, sweep labels ("True & False", "All values") are drawn as disabled buttons rather than standard text labels.*

### Directory and Tagging
Every value allows appending metadata to help organize the generated STL files.
- **Folder Icon (`use_dir`)**: If enabled, the exported file will be placed in a subdirectory named after this specific value.
- **Bookmark Icon (`use_tag`)**: If enabled, a custom string can be appended/prepended to the exported filename.
- **Tag Formatting**:
  - `[ tag ]` : Replaces the input value entirely in the filename with the tag.
  - `[ _tag ]` : Appends the tag to the value.
  - `[ tag_ ]` : Prepends the tag to the value.

### Evaluation Combinatorics
When multiple values exist across different inputs, nodes, or nodegroups, the batch exporter computes the Cartesian product (all possible combinations) of all overrides. 
- Example: If Input A has 2 values, and Input B has 3 values, 6 total variations will be evaluated and exported for every mesh in the collection.
- The UI Panel dynamically displays the calculated number of combinations and the total projected object output to warn users of exponential scaling.
