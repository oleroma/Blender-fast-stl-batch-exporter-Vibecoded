"""
Fast Batch STL Exporter
Architecture: Single-File Monolithic (Optimized for Agentic Environments)
Data Hierarchy: Preset > Collection > Object > NodeGroup > Node > Input > Value
"""

# ==============================================================================
# === MODULE IMPORTS ===
# In Python, 'import' brings in code from other files (libraries) so we don't
# have to write everything from scratch.
# ==============================================================================
import os          # Helps interact with the Operating System (like making folders, joining file paths)
import json        # Helps read and write data in JSON format (a standard text format for data)
import time        # Used for keeping track of time, measuring how long things take
import itertools   # Provides tools for advanced loops (like creating all possible combinations of lists)
import threading   # Allows the program to run multiple tasks at the same time (in the background)
import queue       # A thread-safe way to pass messages between different background tasks
import subprocess  # Allows this Python script to launch other programs (like another instance of Blender)
import tempfile    # Creates temporary files and folders that get cleaned up later
import sys         # Interacts with the Python interpreter itself (like forcing the program to exit)
import struct      # Converts Python values (like floats/ints) into raw binary data bytes
import shutil      # High-level file operations (like deleting an entire folder with files inside)
import numpy as np # A math library that handles huge lists of numbers extremely fast using C under the hood

import bpy                                 # The main Blender Python API (Application Programming Interface)
from bpy_extras.io_utils import ExportHelper, ImportHelper # Pre-made Blender tools for file browser windows
from bpy.app.handlers import persistent    # A "decorator" that tells Blender to keep a function active even after loading a new file


# ==============================================================================
# === [ 1. GLOBALS & STATE ] ===
# Globals are variables that exist outside of any specific function.
# They hold "state" (memory) while the program runs. Dictionaries ({}) hold key-value pairs.
# ==============================================================================

# Stores copied presets and collections so the user can paste them elsewhere in the UI.
_clipboard = {
    "preset": None,
    "collection": None,
    "nodegroup": None
}

# Persistent cache populated asynchronously by property updates and depsgraph handlers
_ui_cache = {
    "is_dirty": True,
    "visibility": {},
    "metrics": {"total_collections": 0, "total_preset_combos": 0, "total_objects": 0},
    "active_col_metrics": {"c_name": "", "num_targets": 0, "total_inputs": 0, "num_combos": 0, "mapping_total_objects": 0},
    "tree": ({}, set())
}

def mark_dirty(self=None, context=None):
    """Flags the UI cache to be rebuilt by the background timer."""
    _ui_cache["is_dirty"] = True


# ==============================================================================
# === [ 2. CORE LOGIC & ENGINE ] ===
# This section contains standard Python functions (defined with 'def') that do the heavy lifting.
# ==============================================================================

# --- STATE-HASH & OVERRIDE LOGIC ---

# Checks if a Blender "Collection" (like a folder for 3D objects) is hidden or excluded from the scene.
def is_collection_excluded(context, target_collection):
    if not target_collection:
        return True # If it doesn't exist, treat it as excluded.

    # These boolean (True/False) variables track what we find as we search.
    found_any_visible = False
    found_any = False

    # A nested function (a function inside a function). It can access variables from the outer function.
    # This is a recursive function, meaning it calls itself to dig deeper into folders within folders.
    def traverse(layer_collection, parent_excluded=False):
        nonlocal found_any_visible, found_any # 'nonlocal' lets us modify the variables defined above this nested function.
        if found_any_visible: return  # OPTIMIZED: Early exit. If we already found a visible one, stop searching.

        current_excluded = parent_excluded or layer_collection.exclude

        if layer_collection.collection == target_collection:
            found_any = True
            if not current_excluded:
                found_any_visible = True
                return

        # Loop through all child collections (sub-folders) and run this function on them too.
        for child in layer_collection.children:
            traverse(child, current_excluded)
            if found_any_visible: return

    traverse(context.view_layer.layer_collection) # Start the search at the very top of the scene

    if not found_any:
        return True
    return not found_any_visible # Returns True if excluded, False if visible.

# Finds the unique internal ID name of a socket (input dot) on a Geometry Nodes modifier.
def get_modifier_socket_identifier(node_group, socket_name):
    # 'hasattr' checks if an object has a specific property (attribute) without crashing if it doesn't.
    if hasattr(node_group, "interface"):
        for item in node_group.interface.items_tree:
            # We look for a socket that matches the name the user wants to change.
            if getattr(item, "item_type", "") == 'SOCKET' and item.name == socket_name:
                return item.identifier
    else:
        # Fallback for older versions of Blender that used a different property structure.
        for inp in node_group.inputs:
            if inp.name == socket_name:
                return inp.identifier
    return None

# Retrieves the default value of a modifier socket so we can reset it later.
def get_modifier_socket_default(node_group, socket_name):
    if hasattr(node_group, "interface"):
        for item in node_group.interface.items_tree:
            if getattr(item, "item_type", "") == 'SOCKET' and item.name == socket_name:
                return getattr(item, "default_value", None)
    else:
        for inp in node_group.inputs:
            if inp.name == socket_name:
                return getattr(inp, "default_value", None)
    return None

# Tries multiple ways to get the current input value of a modifier.
# Blender's API can be picky, so the 'try...except Exception: pass' block attempts an action,
# and if an error happens, it just ignores the error and tries the next method.
def get_modifier_input(mod, ident):
    try:
        if mod.is_property_set(ident): return mod[ident], True
    except Exception: pass

    if hasattr(mod, "properties") and hasattr(mod.properties, "inputs"):
        prop_input = getattr(mod.properties.inputs, ident, None)
        if prop_input is not None and hasattr(prop_input, "value"):
            return prop_input.value, True
    return None, False

# Overwrites a Geometry Node modifier input with our custom batch value.
def set_modifier_input(mod, ident, value):
    try:
        mod[ident] = value
        return
    except (TypeError, Exception): pass

    if hasattr(mod, "properties") and hasattr(mod.properties, "inputs"):
        prop_input = getattr(mod.properties.inputs, ident, None)
        if prop_input is not None and hasattr(prop_input, "value"):
            prop_input.value = value

# Removes our temporary batch value and restores the default state.
def unset_modifier_input(mod, ident, default_val):
    try:
        mod.property_unset(ident)
        return
    except Exception: pass
    if hasattr(mod, "properties") and hasattr(mod.properties, "inputs"):
        prop_input = getattr(mod.properties.inputs, ident, None)
        if prop_input is not None and hasattr(prop_input, "value") and default_val is not None:
            prop_input.value = default_val

# Looks at the data type (string, int, float, boolean) and grabs the correct value property.
def get_input_value(inp):
    if inp.override_type == 'BOOLEAN': return inp.value_bool
    elif inp.override_type == 'INT': return inp.value_int
    elif inp.override_type == 'FLOAT': return inp.value_float
    elif inp.override_type == 'STRING': return inp.value_string
    elif inp.override_type == 'MENU': return inp.value_menu
    return None

# Generates a unique "fingerprint" (signature) of all overrides so we can group similar tasks together.
def get_override_signature(overrides):
    sig = []
    for ovr in overrides:
        target = ovr.override_target
        pg_name = ovr.parent_group_ptr.name if ovr.parent_group_ptr else ""
        node_name = ovr.node_name
        inputs_sig = []
        for inp in ovr.inputs:
            # We store the data as a 'tuple' (an unchangeable list in parentheses). Tuples are fast to compare.
            inputs_sig.append((inp.input_name, inp.override_type, get_input_value(inp)))
        sig.append((target, pg_name, node_name, tuple(inputs_sig)))
    return tuple(sig)

# --- PERMUTATION ENGINE (Adapters) ---
# Classes are blueprints for creating objects. These "Mock" classes create fake, temporary copies
# of Blender properties so we can do math on them without altering the user's actual saved data.
class TempMockInput:
    # __init__ is the constructor. It runs automatically when you create a new TempMockInput object.
    def __init__(self, name, o_type, val_obj):
        # 'self' refers to the specific object being created right now.
        self.input_name = name
        self.override_type = o_type
        self.use_tag = val_obj.use_tag
        self.tag = val_obj.tag
        self.use_dir = val_obj.use_dir
        self.use_sweep = val_obj.use_sweep
        self.sweep_range = val_obj.sweep_range
        self._val_obj = val_obj

    # @property makes a function act like a regular variable. You can say 'obj.value_bool' instead of 'obj.value_bool()'.
    @property
    def value_bool(self): return self._val_obj.value_bool
    @property
    def value_int(self): return self._val_obj.value_int
    @property
    def value_float(self): return self._val_obj.value_float
    @property
    def value_string(self): return self._val_obj.value_string
    @property
    def value_menu(self): return self._val_obj.value_menu

class TempMockOverride:
    def __init__(self, target, ptr, node_name, temp_inputs):
        self.override_target = target
        self.parent_group_ptr = ptr
        self.node_name = node_name
        self.inputs = temp_inputs

# Grabs every override requested by the user and flattens them into one big list.
def get_flat_overrides(nodegroups):
    overrides = []
    if not nodegroups: return overrides
    for ng in nodegroups:
        ng_ptr = bpy.data.node_groups.get(ng.group_name)
        if not ng_ptr:
            continue
        for node in ng.nodes:
            target = 'MODIFIER' if not node.name or node.name == "<Modifier Interface>" else 'NODE'
            temp_inputs = []
            for inp in node.inputs:
                for val in inp.values:
                    temp_inputs.append(TempMockInput(inp.name, inp.override_type, val))
            if temp_inputs:
                overrides.append(TempMockOverride(target, ng_ptr, node.name, temp_inputs))
    return overrides

class MockInput:
    def __init__(self, base_inp, override_val):
        self.input_name = base_inp.input_name
        self.override_type = base_inp.override_type
        self.use_tag = base_inp.use_tag
        self.tag = base_inp.tag
        self.use_dir = base_inp.use_dir
        self.use_sweep = getattr(base_inp, "use_sweep", False)
        self.sweep_range = getattr(base_inp, "sweep_range", "")
        self._val = override_val
    @property
    def value_bool(self): return bool(self._val)
    @property
    def value_int(self): return int(self._val) if self._val is not None else 0
    @property
    def value_float(self): return float(self._val) if self._val is not None else 0.0
    @property
    def value_string(self): return str(self._val)
    @property
    def value_menu(self): return str(self._val)

class MockOverride:
    def __init__(self, override_target, parent_group_ptr, node_name, inputs):
        self.override_target = override_target
        self.parent_group_ptr = parent_group_ptr
        self.node_name = node_name
        self.inputs = inputs

# If the user sets a "Sweep" (e.g., test sizes from 1 to 10), this parses their text string into actual numbers.
def parse_sweep_values(ovr, inp):
    if inp.override_type == 'BOOLEAN': return [True, False]
    elif inp.override_type == 'STRING':
        if not inp.sweep_range: return [""]
        # .split(',') chops a string into a list wherever there is a comma.
        # [s.strip() for s in ...] is a List Comprehension: a fast way to loop and clean spaces off strings in one line.
        return [s.strip() for s in inp.sweep_range.split(',') if s.strip()]
    elif inp.override_type in ['INT', 'FLOAT']:
        vals = []
        parts = inp.sweep_range.split()
        if len(parts) >= 3:
            try:
                start = float(parts[0])
                step = float(parts[1])
                count = int(parts[2])
                for i in range(count):
                    v = start + i * step
                    vals.append(int(v) if inp.override_type == 'INT' else v)
            except ValueError:
                # 'ValueError' happens if they typed letters instead of numbers. We just default to 0 if that happens.
                try: vals.append(int(parts[0]) if inp.override_type == 'INT' else float(parts[0]))
                except ValueError: vals.append(0 if inp.override_type == 'INT' else 0.0)
        else:
            try: vals.append(int(parts[0]) if parts and inp.override_type == 'INT' else float(parts[0]) if parts else 0.0)
            except: vals.append(0 if inp.override_type == 'INT' else 0.0)
        return vals
    elif inp.override_type == 'MENU':
        items = []
        # If it's a dropdown menu, we dig through Blender's internal node links to find all the valid dropdown choices.
        if ovr.parent_group_ptr:
            if ovr.override_target == 'MODIFIER':
                for node in ovr.parent_group_ptr.nodes:
                    if node.type == 'MENU_SWITCH' and hasattr(node, 'enum_items'):
                        for sock in node.inputs:
                            for link in sock.links:
                                if link.from_node.type == 'GROUP_INPUT' and link.from_socket.name == inp.input_name:
                                    items = [getattr(item, 'identifier', getattr(item, 'name', '')) for item in node.enum_items]
                                    break
                            if items: break
                    if items: break
            elif ovr.override_target == 'NODE' and ovr.node_name:
                n_name = ovr.node_name.split(" [")[0].strip()
                node = ovr.parent_group_ptr.nodes.get(n_name)
                if node:
                    if node.type == 'MENU_SWITCH' and hasattr(node, 'enum_items'):
                        items = [getattr(item, 'identifier', getattr(item, 'name', '')) for item in node.enum_items]
                    elif node.type == 'GROUP' and hasattr(node, 'node_tree') and node.node_tree:
                        for inner_node in node.node_tree.nodes:
                            if inner_node.type == 'MENU_SWITCH' and hasattr(inner_node, 'enum_items'):
                                for sock in inner_node.inputs:
                                    for link in sock.links:
                                        if link.from_node.type == 'GROUP_INPUT' and link.from_socket.name == inp.input_name:
                                            items = [getattr(item, 'identifier', getattr(item, 'name', '')) for item in inner_node.enum_items]
                                            break
                                    if items: break
                            if items: break
        return items if items else [""]
    return []

# Combines different variable sweeps together using `itertools.product`.
# If you have sizes [1, 2] and colors [Red, Blue], it creates [[1, Red], [1, Blue], [2, Red], [2, Blue]].
def generate_override_combinations(overrides):
    grouped_inputs = {}
    for ovr in overrides:
        for inp in ovr.inputs:
            param_key = (ovr.override_target, ovr.node_name, inp.input_name)
            if param_key not in grouped_inputs:
                grouped_inputs[param_key] = []
            grouped_inputs[param_key].append((ovr, inp))

    pools = []
    for param_key, pairs in grouped_inputs.items():
        value_groups = {}
        for ovr, inp in pairs:
            if getattr(inp, "use_sweep", False):
                sweep_vals = parse_sweep_values(ovr, inp)
                for val in sweep_vals:
                    mock_inp = MockInput(inp, val)
                    if val not in value_groups: value_groups[val] = []
                    value_groups[val].append((ovr, mock_inp))
            else:
                val = get_input_value(inp)
                mock_inp = MockInput(inp, val)
                if val not in value_groups: value_groups[val] = []
                value_groups[val].append((ovr, mock_inp))
        if value_groups:
            pools.append(list(value_groups.values()))

    if not pools: return [[]]
    combinations = list(itertools.product(*pools)) # Mathematically crosses the lists to get every combination

    flattened = []
    for combo in combinations:
        flat_combo = []
        for variation in combo: flat_combo.extend(variation)
        flattened.append(flat_combo)
    return flattened

def reconstruct_overrides_for_combo(combo):
    grouped = {}
    for ovr, inp in combo:
        target_key = (ovr.override_target, ovr.parent_group_ptr, ovr.node_name)
        if target_key not in grouped: grouped[target_key] = []
        grouped[target_key].append(inp)
    active_overrides = []
    for (target, group_ptr, node_name), inputs in grouped.items():
        active_overrides.append(MockOverride(target, group_ptr, node_name, inputs))
    return active_overrides

# OPTIMIZED: Captures the initial state of the scene before applying any permutations,
# allowing us to do one single revert at the end of the batch instead of thrashing the depsgraph.
def capture_baseline_states(overrides, target_objects):
    global_states = []
    mod_states = []
    processed_node_sockets = set()
    processed_mod_sockets = set()

    for override in overrides:
        if override.override_target == 'NODE' and override.parent_group_ptr and override.node_name:
            parent_tree = override.parent_group_ptr
            n_name = override.node_name.split(" [")[0].strip()
            target_node = parent_tree.nodes.get(n_name)
            if not target_node: continue
            for inp in override.inputs:
                socket = target_node.inputs.get(inp.input_name)
                if not socket: continue
                key = (parent_tree, target_node.name, inp.input_name)
                if key not in processed_node_sockets:
                    link_from = socket.links[0].from_socket if socket.is_linked else None
                    global_states.append(('SOCKET', socket, socket.default_value, link_from, parent_tree))
                    processed_node_sockets.add(key)

        elif override.override_target == 'MODIFIER' and override.parent_group_ptr:
            for inp in override.inputs:
                ident = get_modifier_socket_identifier(override.parent_group_ptr, inp.input_name)
                if not ident: continue
                default_val = get_modifier_socket_default(override.parent_group_ptr, inp.input_name)
                for obj in target_objects:
                    for mod in obj.modifiers:
                        if mod.type == 'NODES' and mod.node_group == override.parent_group_ptr:
                            key = (mod.name, ident)
                            if key not in processed_mod_sockets:
                                orig_val, is_set = get_modifier_input(mod, ident)
                                mod_states.append((mod, ident, is_set, orig_val, default_val))
                                processed_mod_sockets.add(key)

    return global_states, mod_states

# Modifies Blender's live scene by applying the parameters we generated above.
def apply_overrides(overrides, target_objects, dry_run=False):
    trees_to_update = set() # A 'set' is like a list, but guarantees every item is unique.

    for override in overrides:
        # If we are changing an internal node inside the node graph...
        if override.override_target == 'NODE' and override.parent_group_ptr and override.node_name:
            parent_tree = override.parent_group_ptr
            n_name = override.node_name.split(" [")[0].strip()
            target_node = parent_tree.nodes.get(n_name)
            if not target_node: continue
            for inp in override.inputs:
                socket = target_node.inputs.get(inp.input_name)
                if not socket: continue

                if not dry_run:
                    if socket.is_linked: parent_tree.links.remove(socket.links[0])
                    val = get_input_value(inp)
                    # OPTIMIZED: Check before setting to avoid redundant node tree invalidation
                    if val is not None and socket.default_value != val:
                        socket.default_value = val
                        trees_to_update.add(parent_tree)

        # If we are just changing the modifier sliders on the side menu...
        elif override.override_target == 'MODIFIER' and override.parent_group_ptr:
            for inp in override.inputs:
                ident = get_modifier_socket_identifier(override.parent_group_ptr, inp.input_name)
                if not ident: continue
                val = get_input_value(inp)
                if val is None: continue
                for obj in target_objects:
                    for mod in obj.modifiers:
                        if mod.type == 'NODES' and mod.node_group == override.parent_group_ptr:
                            if not dry_run:
                                orig_val, is_set = get_modifier_input(mod, ident)
                                # OPTIMIZED: Avoid setting modifier input if identical to prevent redundant geometry evaluation
                                if not is_set or orig_val != val:
                                    set_modifier_input(mod, ident, val)

    if not dry_run:
        # 'update_tag()' tells Blender that data changed and it needs to recalculate the 3D models before exporting.
        for tree in trees_to_update: tree.update_tag()
        # OPTIMIZED: Removed explicit obj.update_tag() calls, Blender natively handles updates via modifier properties.

# Takes the original states we saved in `capture_baseline_states` and puts Blender exactly back how we found it.
def revert_overrides(global_states, mod_states, target_objects):
    for mod, ident, is_set, orig_val, default_val in mod_states:
        try:
            curr_val, curr_is_set = get_modifier_input(mod, ident)
            if is_set and orig_val is not None:
                # OPTIMIZED: Only revert if the state actually needs changing
                if not curr_is_set or curr_val != orig_val:
                    set_modifier_input(mod, ident, orig_val)
            else:
                if curr_is_set:
                    unset_modifier_input(mod, ident, default_val)
        except ReferenceError: pass

    trees_to_update = set()
    for state in global_states:
        if state[0] == 'SOCKET':
            _, socket, original_val, link_from, parent_tree = state
            try:
                # OPTIMIZED: Only revert if the state actually needs changing
                if socket.default_value != original_val:
                    socket.default_value = original_val
                    trees_to_update.add(parent_tree)
                if link_from:
                    already_linked = any(l.from_socket == link_from for l in socket.links)
                    if not already_linked:
                        parent_tree.links.new(link_from, socket) # reconnect wires
                        trees_to_update.add(parent_tree)
            except Exception: pass

    for tree in trees_to_update:
        try: tree.update_tag()
        except ReferenceError: pass
    # OPTIMIZED: Removed explicit obj.update_tag() calls, API handles modifier updates natively.

def get_active_preset(scene):
    presets = scene.batch_stl_presets
    index = scene.batch_stl_preset_index
    if presets and 0 <= index < len(presets): return presets[index]
    return None

def get_active_collection(preset):
    if preset and preset.collections and 0 <= preset.collection_index < len(preset.collections):
        return preset.collections[preset.collection_index]
    return None

def get_active_object(collection):
    if collection and collection.objects and 0 <= collection.object_index < len(collection.objects):
        return collection.objects[collection.object_index]
    return None

def log_to_console(preset, text):
    log = preset.console_logs.add()
    log.text = text
    preset.console_index = len(preset.console_logs) - 1
    # Keep the log list small so we don't run out of memory.
    if len(preset.console_logs) > 300:
        preset.console_logs.remove(0) # delete oldest
        preset.console_index = len(preset.console_logs) - 1

# Ensures the Objects UI list stays perfectly synced with Blender's active collection outliner
def sync_collection_objects(c_prop, c_ptr=None):
    if not c_ptr:
        c_ptr = bpy.data.collections.get(c_prop.collection_name)
    if not c_ptr:
        return

    actual_names = {obj.name for obj in c_ptr.all_objects if obj.type in {"MESH", "CURVE", "SURFACE", "META", "FONT"}}
    existing_names = {obj.name: obj for obj in c_prop.objects}

    for i in reversed(range(len(c_prop.objects))):
        if c_prop.objects[i].name not in actual_names:
            c_prop.objects.remove(i)

    for name in actual_names:
        if name not in existing_names:
            new_obj = c_prop.objects.add()
            new_obj.name = name
            new_obj.export = True


# --- BINARY STL WRITER ---
# This is a highly optimized custom exporter. Standard Blender Python looping is slow.
# This method uses 'numpy' to calculate all vertices and triangles at the same time in memory.
def write_fast_binary_stl(filepath, mesh, matrix_world, verbose=False):
    t_start = time.perf_counter() # Marks the starting time
    mesh.calc_loop_triangles() # Asks Blender to convert any squares (quads) to triangles
    num_tris = len(mesh.loop_triangles)
    if num_tris == 0: return

    # Creates an empty block of raw memory the exact size we need for all our 3D points (vertices).
    verts = np.empty((len(mesh.vertices), 3), dtype=np.float32)
    # Blender's 'foreach_get' pushes all its internal C-data instantly into our numpy array. Super fast!
    mesh.vertices.foreach_get("co", verts.ravel())

    # OPTIMIZED: 3x3 Multiplication avoids memory allocation of homogenous N x 4 array
    # Matrix math applies the object's position, rotation, and scale to the raw vertex points.
    mat_3x3 = np.array(matrix_world.to_3x3(), dtype=np.float32)
    trans = np.array(matrix_world.translation, dtype=np.float32)
    verts = np.dot(verts, mat_3x3.T) + trans # dot-product performs multiplication across millions of points at once

    tri_verts = np.empty((num_tris, 3), dtype=np.int32)
    mesh.loop_triangles.foreach_get("vertices", tri_verts.ravel())

    tri_normals = np.empty((num_tris, 3), dtype=np.float32)
    mesh.loop_triangles.foreach_get("normal", tri_normals.ravel())

    mat_norm = np.array(matrix_world.to_3x3().inverted_safe().transposed(), dtype=np.float32)
    tri_normals = np.dot(tri_normals, mat_norm.T)

    # OPTIMIZED: Manual normalization scales faster than np.linalg.norm overhead
    # Math to make sure every normal vector has a length of exactly 1.0 (required by the STL format).
    norms = np.sqrt(np.sum(tri_normals**2, axis=1, keepdims=True))
    norms[norms == 0] = 1.0
    tri_normals /= norms

    # Define the exact byte-structure an STL file expects for every triangle.
    stl_dtype = np.dtype([
        ('normals', np.float32, (3,)), ('v0', np.float32, (3,)),
        ('v1', np.float32, (3,)), ('v2', np.float32, (3,)),
        ('attr', np.uint16)
    ])
    data = np.zeros(num_tris, dtype=stl_dtype)
    data['normals'] = tri_normals
    # Uses 'fancy indexing' in numpy to map the final positions to the exact triangle corners (v0, v1, v2)
    data['v0'] = verts[tri_verts[:, 0]]
    data['v1'] = verts[tri_verts[:, 1]]
    data['v2'] = verts[tri_verts[:, 2]]

    t_format = time.perf_counter()
    if verbose:
        mb_size = (84 + (num_tris * 50)) / (1024 * 1024)
        print(f"  │         │    ├─ STL Memory Map: {num_tris} Tris | {mb_size:.2f} MB | Matrix T-Form: {t_format-t_start:.4f}s")

    # Opens the file in 'wb' (Write Binary) mode.
    with open(filepath, 'wb') as f:
        # Standard STL header is exactly 80 bytes.
        f.write(b'Batch STL Fast Export' + b'\x00' * 59)
        # Writes a 4-byte unsigned integer ('<I') representing how many triangles we have.
        f.write(struct.pack('<I', num_tris))
        # Dumps the entire numpy array into the file instantly as raw bytes.
        f.write(data.tobytes())

    t_write = time.perf_counter()
    if verbose:
        print(f"  │         │    ├─ Disk I/O Write: {t_write-t_format:.4f}s | Path: {os.path.basename(filepath)}")

# --- JSON UTILS ---
# The functions below convert complex Blender property collections into plain Python Dictionaries.
# This makes it easy to save the user's setup to a standard .json file so they can share it or back it up.
def copy_val_to_dict(v):
    return {
        "value_bool": v.value_bool, "value_int": v.value_int, "value_float": v.value_float,
        "value_string": v.value_string, "value_menu": v.value_menu,
        "use_tag": v.use_tag, "tag": v.tag, "use_dir": v.use_dir,
        "use_sweep": getattr(v, "use_sweep", False), "sweep_range": getattr(v, "sweep_range", "")
    }

def copy_input_to_dict(i):
    return {"name": i.name, "override_type": i.override_type, "values": [copy_val_to_dict(v) for v in i.values]}

def copy_node_to_dict(n):
    return {"name": n.name, "inputs": [copy_input_to_dict(i) for i in n.inputs]}

def copy_ng_to_dict(ng):
    return {"group": ng.group_name, "nodes": [copy_node_to_dict(n) for n in ng.nodes]}

def copy_obj_to_dict(o):
    return {
        "name": o.name,
        "export": o.export,
        "tag": getattr(o, "tag", ""),
        "sub_path": getattr(o, "sub_path", ""),
        "nodegroups": [copy_ng_to_dict(ng) for ng in o.nodegroups]
    }

def copy_collection_to_dict(c):
    return {
        "collection_name": c.collection_name,
        "use_tag": c.use_tag, "tag": c.tag, "sub_path": c.sub_path,
        "objects": [copy_obj_to_dict(o) for o in c.objects],
        "nodegroups": [copy_ng_to_dict(ng) for ng in c.nodegroups]
    }

def copy_preset_to_dict(src):
    return {
        "name": src.name, "preset_prefix": src.preset_prefix,
        "collections": [copy_collection_to_dict(c) for c in src.collections]
    }

# The Paste functions do the exact reverse. They read a JSON dictionary and assign the values back to Blender.
def paste_val_from_dict(new_v, data):
    for k, v in data.items(): setattr(new_v, k, v) # 'setattr' assigns the variable dynamically using the text name 'k'.

def paste_input_from_dict(new_i, data):
    new_i.name = data["name"]
    new_i.override_type = data.get("override_type", 'FLOAT')
    for v_data in data.get("values", []): paste_val_from_dict(new_i.values.add(), v_data)

def paste_node_from_dict(new_n, data):
    new_n.name = data["name"]
    for i_data in data.get("inputs", []): paste_input_from_dict(new_n.inputs.add(), i_data)

def paste_ng_from_dict(new_ng, data):
    new_ng.group_name = data.get("group", "")
    for n_data in data.get("nodes", []): paste_node_from_dict(new_ng.nodes.add(), n_data)

def paste_obj_from_dict(new_o, data):
    new_o.name = data.get("name", "")
    new_o.export = data.get("export", True)
    new_o.tag = data.get("tag", "")
    new_o.sub_path = data.get("sub_path", "")
    for ng_data in data.get("nodegroups", []): paste_ng_from_dict(new_o.nodegroups.add(), ng_data)

def paste_collection_from_dict(new_c, data):
    new_c.collection_name = data.get("collection_name", "")
    new_c.use_tag = data.get("use_tag", True)
    new_c.tag = data.get("tag", "")
    new_c.sub_path = data.get("sub_path", "")
    for o_data in data.get("objects", []): paste_obj_from_dict(new_c.objects.add(), o_data)
    for ng_data in data.get("nodegroups", []): paste_ng_from_dict(new_c.nodegroups.add(), ng_data)

def paste_preset_from_dict(new_p, data):
    new_p.name = data.get("name", "Imported Preset")
    new_p.preset_prefix = data.get("preset_prefix", "")
    for c_data in data.get("collections", []): paste_collection_from_dict(new_p.collections.add(), c_data)


# --- UI CACHE ENGINE ---
# Background timer executes heavy calculation outside of `draw()`
def rebuild_ui_cache_if_dirty():
    if not _ui_cache.get("is_dirty", False):
        return 0.25 # Wait 0.25s before checking the dirty flag again

    _ui_cache["is_dirty"] = False
    context = bpy.context
    if not hasattr(context, "scene"):
        _ui_cache["is_dirty"] = True
        return 0.25

    scene = context.scene

    # 1. Evaluate explicit visibility to prevent recursive outliner walks on redraw
    visibility = {}
    if hasattr(context, "view_layer") and context.view_layer:
        def traverse(layer_collection, parent_excluded=False):
            current_excluded = parent_excluded or layer_collection.exclude
            if layer_collection.collection:
                visibility[layer_collection.collection.name] = current_excluded
            for child in layer_collection.children:
                traverse(child, current_excluded)
        traverse(context.view_layer.layer_collection)
    _ui_cache["visibility"] = visibility

    preset = get_active_preset(scene)
    if not preset:
        _ui_cache["metrics"] = {"total_collections": 0, "total_preset_combos": 0, "total_objects": 0}
        _ui_cache["active_col_metrics"] = {"c_name": "", "num_targets": 0, "total_inputs": 0, "num_combos": 0, "mapping_total_objects": 0}
        _ui_cache["tree"] = ({}, set())

        if getattr(context, "window_manager", None):
            for window in context.window_manager.windows:
                for area in window.screen.areas:
                    if area.type == 'VIEW_3D':
                        area.tag_redraw()
        return 0.25

    # 2. Re-calculate metrics fully decoupled from UI
    total_collections = len(preset.collections)
    total_objects = 0
    total_preset_combos = 0

    for c in preset.collections:
        c_ptr = bpy.data.collections.get(c.collection_name)
        sync_collection_objects(c, c_ptr)

        if not c_ptr or visibility.get(c.collection_name, True):
            continue

        c_pinned_ovrs = get_flat_overrides(c.nodegroups)
        for obj_prop in c.objects:
            if not obj_prop.export: continue
            bl_obj = c_ptr.all_objects.get(obj_prop.name)
            if bl_obj and bl_obj.type in {"MESH", "CURVE", "SURFACE", "META", "FONT"} and not bl_obj.hide_viewport:
                obj_ovrs = c_pinned_ovrs + get_flat_overrides(obj_prop.nodegroups)
                combos = len(generate_override_combinations(obj_ovrs))
                total_preset_combos += combos
                total_objects += combos

    _ui_cache["metrics"] = {
        "total_collections": total_collections,
        "total_preset_combos": total_preset_combos,
        "total_objects": total_objects
    }

    # 3. Active collection metrics pre-computation
    active_col = get_active_collection(preset)
    if active_col:
        active_obj = get_active_object(active_col)
        all_ovrs = get_flat_overrides(active_col.nodegroups) + (get_flat_overrides(active_obj.nodegroups) if active_obj else [])
        unique_targets = set()
        total_inputs = 0

        for o in all_ovrs:
            total_inputs += len(o.inputs)
            for i in o.inputs:
                unique_targets.add((o.override_target, o.node_name, i.input_name))

        num_targets = len(unique_targets)
        num_combos = len(generate_override_combinations(all_ovrs))

        active_obj_count = sum(1 for o in active_col.objects if o.export)

        c_name = active_col.collection_name if active_col.collection_name else "Unassigned"
        if active_col.tag: c_name += f" [{active_col.tag}]"

        _ui_cache["active_col_metrics"] = {
            "c_name": c_name, "num_targets": num_targets,
            "total_inputs": total_inputs, "num_combos": num_combos,
            "mapping_total_objects": active_obj_count * num_combos
        }
    else:
        _ui_cache["active_col_metrics"] = {"c_name": "", "num_targets": 0, "total_inputs": 0, "num_combos": 0, "mapping_total_objects": 0}

    # 4. Build visualization tree
    tree_dict, duplicates = build_tree_dict(context, preset, visibility)
    _ui_cache["tree"] = (tree_dict, duplicates)

    if getattr(context, "window_manager", None):
        for window in context.window_manager.windows:
            for area in window.screen.areas:
                if area.type == 'VIEW_3D':
                    area.tag_redraw()

    return 0.25

@persistent
def batch_stl_depsgraph_handler(scene, depsgraph):
    """Triggers the cache timer dynamically upon scene state or object visibility updates"""
    mark_dirty()


# --- TREE VISUALIZER LOGIC ---
# This builds an artificial file-folder structure in memory so the script can
# visually show you what files will be created and where, before you actually click export.
def build_tree_dict(context, preset, visibility_cache=None):
    scene = context.scene
    root_name = bpy.path.abspath(scene.batch_stl_root_dir) if scene.batch_stl_root_dir else "//"
    tree = {}
    all_filepaths = set()
    duplicates = set()

    current_root = tree
    if preset.preset_prefix:
        current_root[preset.preset_prefix] = {}
        current_root = current_root[preset.preset_prefix]

    for c in preset.collections:
        c_ptr = bpy.data.collections.get(c.collection_name)
        if c_ptr:
            if visibility_cache is not None:
                if visibility_cache.get(c.collection_name, True): continue
            else:
                if is_collection_excluded(context, c_ptr): continue
        else:
            continue

        c_root = current_root
        c_root_path = []
        if c.sub_path:
            parts = c.sub_path.replace('\\', '/').split('/')
            for part in parts:
                if part:
                    if part not in c_root: c_root[part] = {}
                    c_root = c_root[part]
                    c_root_path.append(part)

        c_pinned_ovrs = get_flat_overrides(c.nodegroups)

        for obj_prop in c.objects:
            if not obj_prop.export: continue
            bl_obj = c_ptr.all_objects.get(obj_prop.name)
            if not bl_obj or bl_obj.hide_viewport or bl_obj.type not in {"MESH", "CURVE", "SURFACE", "META", "FONT"}:
                continue

            all_overrides = c_pinned_ovrs + get_flat_overrides(obj_prop.nodegroups)
            freq_dict = {}
            for o in all_overrides:
                for i in o.inputs:
                    key = (o.override_target, o.node_name, i.input_name)
                    weight = 2 if getattr(i, "use_sweep", False) else 1
                    freq_dict[key] = freq_dict.get(key, 0) + weight

            combinations = generate_override_combinations(all_overrides)
            if not combinations: combinations = [[]]

            # 1. Resolve Object Tag String Logic
            safe_name = bpy.path.clean_name(bl_obj.name)
            if obj_prop.tag:
                if obj_prop.tag.startswith("_"): safe_name = safe_name + obj_prop.tag
                elif obj_prop.tag.endswith("_"): safe_name = obj_prop.tag + safe_name
                else: safe_name = obj_prop.tag # Completely replaces object name

            for combo in combinations:
                combo_root = c_root
                combo_suffix = ""
                combo_subpath = []

                # 2. Insert Object sub_path into directory tree structure
                obj_path_parts = []
                if obj_prop.sub_path:
                    obj_path_parts = [p for p in obj_prop.sub_path.replace('\\', '/').split('/') if p]
                    for part in obj_path_parts:
                        if part not in combo_root: combo_root[part] = {}
                        combo_root = combo_root[part]

                processed_params = set()
                for ovr, inp in combo:
                    param_key = (ovr.override_target, ovr.node_name, inp.input_name)
                    if param_key not in processed_params:
                        val = get_input_value(inp)
                        # Formats floats nicely. The ":g" string format removes trailing zeros (e.g., 2.0 -> 2).
                        val_str = str(val) if isinstance(val, (int, str)) else f"{val:g}" if isinstance(val, float) else str(val)
                        if inp.tag:
                            if inp.tag.startswith("_"): naming_str = val_str + inp.tag
                            elif inp.tag.endswith("_"): naming_str = inp.tag + val_str
                            else: naming_str = inp.tag
                        else: naming_str = val_str

                        is_permutation = freq_dict.get(param_key, 0) > 1

                        if is_permutation and getattr(inp, "use_tag", False): combo_suffix += f"_{naming_str}"
                        if is_permutation and getattr(inp, "use_dir", False):
                            if naming_str not in combo_root: combo_root[naming_str] = {}
                            combo_root = combo_root[naming_str]
                            combo_subpath.append(naming_str)
                        processed_params.add(param_key)

                if '_files' not in combo_root: combo_root['_files'] = []
                base_tag = c.tag if getattr(c, "use_tag", False) and c.tag else ""
                final_tag = base_tag + combo_suffix

                full_dir_parts = [preset.preset_prefix] if preset.preset_prefix else []
                full_dir_parts.extend(c_root_path)
                full_dir_parts.extend(obj_path_parts) # Include object sub folder
                full_dir_parts.extend(combo_subpath)

                dir_path_str = os.path.normpath(os.path.join(root_name, *full_dir_parts))

                filename = f"{safe_name}{final_tag}.stl"
                combo_root['_files'].append(filename)
                full_path = os.path.join(dir_path_str, filename)

                if full_path in all_filepaths: duplicates.add(full_path)
                else: all_filepaths.add(full_path)

    return {root_name: tree}, duplicates

# This takes the dictionary `build_tree_dict` generated and draws it nicely in the Blender UI panel.
def draw_tree_dict(layout, tree_node, current_path="", toggled_list=None, duplicates=None, actual_path=""):
    if toggled_list is None:
        try:
            import json
            toggled_list = json.loads(bpy.context.scene.batch_stl_collapsed_dirs)
        except Exception:
            toggled_list = []
    if duplicates is None:
        duplicates = set()

    dirs = [k for k in tree_node.keys() if k != '_files']
    files = tree_node.get('_files', [])
    for k in dirs:
        dir_path = current_path + "/" + k
        next_actual = os.path.normpath(os.path.join(actual_path, k)) if actual_path else os.path.normpath(k)

        default_expanded = (k == dirs[-1])
        is_expanded = default_expanded
        if dir_path in toggled_list:
            is_expanded = not default_expanded

        is_collapsed = not is_expanded

        split = layout.split(factor=0.005)
        split.column()
        col = split.column()

        box = col.box()
        row = box.row()

        icon = 'TRIA_RIGHT' if is_collapsed else 'TRIA_DOWN'
        op = row.operator("batch_stl.toggle_dir_tree", text="", icon=icon, emboss=False)
        op.dir_path = dir_path

        row.scale_y = 0.4
        row.label(text=str(k))
        if not is_collapsed and isinstance(tree_node[k], dict):
            # Recursion again! It calls itself to draw sub-folders.
            draw_tree_dict(box, tree_node[k], dir_path, toggled_list, duplicates, next_actual)

    for f in files:
        split = layout.split(factor=0.025)
        split.column()
        col = split.column()

        row = col.row()
        row.scale_y = 0.4

        f_path = os.path.join(actual_path, f) if actual_path else f
        if f_path in duplicates:
            row.alert = True # Turns the text red to alert the user of an issue.

        row.label(text=str(f))

# --- HEADLESS EXPORT EXECUTION ROUTINE ---
# "Headless" means running the software without a screen or graphical window.
# This is used for massive background exports, so the user can keep working on other things.
def run_headless_export(preset_index):
    total_time_start = time.perf_counter()
    scene = bpy.context.scene

    # Error handling to make sure the program exits safely if bad data is given.
    if preset_index < 0 or preset_index >= len(scene.batch_stl_presets):
        print("ERROR: Invalid preset index")
        sys.exit(1) # system exit code 1 means an error occurred

    preset = scene.batch_stl_presets[preset_index]
    root_dir = bpy.path.abspath(scene.batch_stl_root_dir)
    if preset.preset_prefix:
        root_dir = os.path.normpath(os.path.join(root_dir, preset.preset_prefix))

    print(f"\n=== STARTING HEADLESS ISOLATED EXPORT: {preset.name} ===")
    print("\n  [Phase 0] Aggressive Global Depsgraph Culling...")
    t_phase0_start = time.perf_counter()

    # The 'Depsgraph' is Blender's internal system that tracks what depends on what.
    # Here, we figure out what isn't exporting, and mute it so the computer doesn't waste time thinking about it.
    active_export_objects = set()
    for c in preset.collections:
        c_ptr = bpy.data.collections.get(c.collection_name)
        if c_ptr and not is_collection_excluded(bpy.context, c_ptr):
            sync_collection_objects(c, c_ptr)
            for obj_prop in c.objects:
                if obj_prop.export:
                    bl_obj = c_ptr.all_objects.get(obj_prop.name)
                    if bl_obj: active_export_objects.add(bl_obj)

    muted_count = 0
    batch_isolation_map = {} # Tracks modifiers to toggle per-batch to guarantee strict batch isolation

    for obj in bpy.context.view_layer.objects:
        if obj not in active_export_objects:
            # Permanently mute non-export objects
            for mod in getattr(obj, 'modifiers', []):
                if mod.type == 'NODES' and mod.show_viewport:
                    mod.show_viewport = False
                    muted_count += 1
        else:
            # Temporarily mute active export objects, to be unmuted ONLY during their specific batch
            batch_isolation_map[obj] = []
            for mod in getattr(obj, 'modifiers', []):
                if mod.type == 'NODES' and mod.show_viewport:
                    batch_isolation_map[obj].append(mod)
                    mod.show_viewport = False
                    muted_count += 1

    print(f"    ├─ Muted {muted_count} GN modifiers for strict batch isolation in {time.perf_counter() - t_phase0_start:.4f}s")

    execution_batches = {}

    for c in preset.collections:
        c_ptr = bpy.data.collections.get(c.collection_name)
        if not c_ptr or is_collection_excluded(bpy.context, c_ptr): continue

        sig_pinned = get_override_signature(get_flat_overrides(c.nodegroups))

        for obj_prop in c.objects:
            if not obj_prop.export: continue
            bl_obj = c_ptr.all_objects.get(obj_prop.name)
            if not bl_obj or bl_obj.hide_viewport or bl_obj.type not in {"MESH", "CURVE", "SURFACE", "META", "FONT"}:
                continue

            sig_local = get_override_signature(get_flat_overrides(obj_prop.nodegroups))
            full_sig = sig_pinned + sig_local

            # Groups items with identical required steps together in 'execution_batches'
            if full_sig not in execution_batches: execution_batches[full_sig] = []
            execution_batches[full_sig].append((c, obj_prop, bl_obj))

    if not execution_batches:
        print("  └─ No active objects to export.")
        print("BATCH_STL_DONE", flush=True) # flush=True forces Python to print immediately, without waiting
        sys.exit(0) # Exit code 0 means successful completion

    total_operations = 0
    for signature, batch_items in execution_batches.items():
        first_c, first_obj_prop, _ = batch_items[0]
        all_overrides = get_flat_overrides(first_c.nodegroups) + get_flat_overrides(first_obj_prop.nodegroups)
        combinations = generate_override_combinations(all_overrides)
        total_operations += (len(batch_items) * len(combinations))

    print(f"BATCH_STL_TOTAL:{total_operations}", flush=True)

    current_op_step = 0
    batch_counter = 1

    for signature, batch_items in execution_batches.items():
        t_batch_start = time.perf_counter()
        first_c, first_obj_prop, _ = batch_items[0]
        all_overrides = get_flat_overrides(first_c.nodegroups) + get_flat_overrides(first_obj_prop.nodegroups)

        freq_dict = {}
        for o in all_overrides:
            for i in o.inputs:
                key = (o.override_target, o.node_name, i.input_name)
                weight = 2 if getattr(i, "use_sweep", False) else 1
                freq_dict[key] = freq_dict.get(key, 0) + weight

        combinations = generate_override_combinations(all_overrides)

        is_clean_batch = len(get_flat_overrides(first_obj_prop.nodegroups)) == 0
        batch_type = "Clean (Pinned Only)" if is_clean_batch else f"Dirty ({len(get_flat_overrides(first_obj_prop.nodegroups))} Local Overrides)"
        print(f"  ├─ Batch {batch_counter}/{len(execution_batches)} [{batch_type}]: Processing {len(batch_items)} mapped instances with {len(combinations)} permutation(s)")

        batch_objects = {item[2] for item in batch_items}
        batch_export_targets = batch_items

        # --- STRICT BATCH ISOLATION START ---
        # Unmute GN Modifiers for ONLY the objects in this specific batch
        for obj in batch_objects:
            for mod in batch_isolation_map.get(obj, []):
                mod.show_viewport = True
        # ------------------------------------

        # OPTIMIZATION: Capture baseline state once for the entire batch to avoid O(N) depsgraph rebuilds
        baseline_global_states, baseline_mod_states = capture_baseline_states(all_overrides, batch_objects)

        try:
            # This loop walks through every mathematical combination (permutation) we created and runs the export
            for combo_idx, combo in enumerate(combinations):
                print(f"  │    ├─ Permutation {combo_idx + 1}/{len(combinations)}")
                combo_suffix = ""
                combo_subpath = ""
                processed_params = set()
                for ovr, inp in combo:
                    param_key = (ovr.override_target, ovr.node_name, inp.input_name)
                    if param_key not in processed_params:
                        val = get_input_value(inp)
                        val_str = str(val) if isinstance(val, (int, str)) else f"{val:g}" if isinstance(val, float) else str(val)
                        if inp.tag:
                            if inp.tag.startswith("_"): naming_str = val_str + inp.tag
                            elif inp.tag.endswith("_"): naming_str = inp.tag + val_str
                            else: naming_str = inp.tag
                        else: naming_str = val_str

                        is_permutation = freq_dict.get(param_key, 0) > 1
                        if is_permutation and getattr(inp, "use_tag", False): combo_suffix += f"_{naming_str}"
                        if is_permutation and getattr(inp, "use_dir", False): combo_subpath = os.path.join(combo_subpath, naming_str)
                        processed_params.add(param_key)

                t_ovr = time.perf_counter()

                # Apply the current setup iteration and ask Blender to evaluate what the 3D scene looks like now
                active_overrides = reconstruct_overrides_for_combo(combo)
                apply_overrides(active_overrides, batch_objects)
                bpy.context.view_layer.update()
                depsgraph = bpy.context.evaluated_depsgraph_get() # Added back missing depsgraph assignment
                print(f"  │    │    ├─ Applied & Synced Graph: {time.perf_counter() - t_ovr:.4f}s")

                for c, obj_prop, bl_obj in batch_export_targets:
                    # Apply Object sub-directory implicitly
                    obj_subpath = obj_prop.sub_path.replace('\\', '/') if obj_prop.sub_path else ""

                    out_dir = os.path.normpath(os.path.join(root_dir, c.sub_path, obj_subpath, combo_subpath))
                    os.makedirs(out_dir, exist_ok=True)
                    print(f"  │    │    ├─ Exporting: {bl_obj.name}{' ['+c.tag+']' if c.tag else ''}")

                    # Apply Object tagging mechanism
                    safe_name = bpy.path.clean_name(bl_obj.name)
                    if obj_prop.tag:
                        if obj_prop.tag.startswith("_"): safe_name = safe_name + obj_prop.tag
                        elif obj_prop.tag.endswith("_"): safe_name = obj_prop.tag + safe_name
                        else: safe_name = obj_prop.tag

                    t_eval = time.perf_counter()
                    obj_eval = bl_obj.evaluated_get(depsgraph) # Asks Blender to resolve all modifiers to get the final mesh
                    try: mesh = obj_eval.to_mesh()
                    except RuntimeError: mesh = None
                    print(f"  │         ├─ Evaluated Mesh [{bl_obj.name}]: {time.perf_counter() - t_eval:.4f}s")

                    if mesh:
                        base_tag = c.tag if getattr(c, "use_tag", False) and c.tag else ""
                        final_tag = base_tag + combo_suffix
                        filepath = os.path.join(out_dir, f"{safe_name}{final_tag}.stl")

                        # Our custom fast function is called right here!
                        write_fast_binary_stl(filepath, mesh, bl_obj.matrix_world, verbose=True)
                        obj_eval.to_mesh_clear() # Very important to throw away data so we don't leak memory.
                        current_op_step += 1
                        print(f"BATCH_STL_PROGRESS:{current_op_step}", flush=True)

        finally:
            # A 'finally' block *always* executes, even if the 'try' block crashed.
            # This guarantees we don't leave Blender broken after an error.
            # OPTIMIZATION: Revert states ONCE after all permutations complete, breaking the Revert-Update trap
            t_rev = time.perf_counter()
            revert_overrides(baseline_global_states, baseline_mod_states, batch_objects)
            bpy.context.view_layer.update()
            print(f"  │    ├─ Reverted permutation overrides: {time.perf_counter() - t_rev:.4f}s")

        # --- STRICT BATCH ISOLATION END ---
        # Mute GN Modifiers again so they are ignored by the depsgraph in the next batch
        for obj in batch_objects:
            for mod in batch_isolation_map.get(obj, []):
                mod.show_viewport = False
        # ----------------------------------

        # OPTIMIZED: Moved RAM Purge completely out of permutation loop to run per-batch
        # This tells Blender to take out the garbage (free up unused memory).
        t_purge = time.perf_counter()
        bpy.ops.outliner.orphans_purge(do_local_ids=True, do_linked_ids=True, do_recursive=True)
        print(f"  │    ├─ Batch RAM Purge: {time.perf_counter() - t_purge:.4f}s")

        print(f"  │    => Batch Iteration Total Time: {time.perf_counter() - t_batch_start:.4f}s\n")
        batch_counter += 1

    print(f"\n=== HEADLESS EXPORT COMPLETE: {time.perf_counter() - total_time_start:.4f}s Subprocess Execution ===\n")
    print("BATCH_STL_DONE", flush=True)
    sys.exit(0)


# ==============================================================================
# === [ 3. PROPERTY GROUPS ] ===
# PropertyGroups are classes provided by Blender. When you inherit from them
# (e.g., class Name(bpy.types.PropertyGroup):), Blender knows it should save these
# variables inside your .blend file automatically.
# ==============================================================================

# Guesses the data type from the text name or socket type.
def infer_input_type(group_ptr, node_name, input_name):
    if not group_ptr or not input_name: return 'FLOAT'
    if not node_name or node_name == "<Modifier Interface>":
        if hasattr(group_ptr, "interface"):
            item = group_ptr.interface.items_tree.get(input_name)
            if item:
                s_type = getattr(item, "socket_type", "")
                if 'Float' in s_type: return 'FLOAT'
                elif 'Int' in s_type: return 'INT'
                elif 'Bool' in s_type: return 'BOOLEAN'
                elif 'String' in s_type: return 'STRING'
                elif 'Menu' in s_type: return 'MENU'
        else:
            inp = group_ptr.inputs.get(input_name)
            if inp:
                if inp.type in ['VALUE', 'FLOAT']: return 'FLOAT'
                elif inp.type == 'INT': return 'INT'
                elif inp.type == 'BOOLEAN': return 'BOOLEAN'
                elif inp.type == 'STRING': return 'STRING'
                elif inp.type == 'MENU': return 'MENU'
    else:
        n_name = node_name.split(" [")[0].strip()
        node = group_ptr.nodes.get(n_name)
        if node and input_name in node.inputs:
            s_type = node.inputs[input_name].type
            if s_type in ['VALUE', 'FLOAT']: return 'FLOAT'
            elif s_type == 'INT': return 'INT'
            elif s_type == 'BOOLEAN': return 'BOOLEAN'
            elif s_type == 'STRING': return 'STRING'
            elif s_type == 'MENU': return 'MENU'
    return 'FLOAT'

# Callback function (event listener) that fires whenever an input name is updated by the user in the UI.
def on_input_name_update(self, context):
    mark_dirty()
    try:
        found_ng, found_node = None, None
        preset = get_active_preset(context.scene)

        if preset:
            active_col = get_active_collection(preset)
            if active_col:
                for ng in active_col.nodegroups:
                    for n in ng.nodes:
                        if self in n.inputs.values(): found_ng, found_node = ng, n; break
                    if found_ng: break

                if not found_ng:
                    active_obj = get_active_object(active_col)
                    if active_obj:
                        for ng in active_obj.nodegroups:
                            for n in ng.nodes:
                                if self in n.inputs.values(): found_ng, found_node = ng, n; break
                            if found_ng: break

        if found_ng and found_node:
            ng_ptr = bpy.data.node_groups.get(found_ng.group_name)
            self.override_type = infer_input_type(ng_ptr, found_node.name, self.name)
            for v in self.values: v.use_sweep = False
    except Exception: pass

# Creates the dynamic list of search results when you type in a Node field.
def search_target_node_cb(self, context, edit_text):
    if edit_text == self.name: edit_text = ""
    res = ["<Modifier Interface>"]
    found_ng = None
    preset = get_active_preset(context.scene)

    if preset:
        active_col = get_active_collection(preset)
        if active_col:
            for ng in active_col.nodegroups:
                if self in ng.nodes.values(): found_ng = ng; break

            if not found_ng:
                active_obj = get_active_object(active_col)
                if active_obj:
                    for ng in active_obj.nodegroups:
                        if self in ng.nodes.values(): found_ng = ng; break

    ng_ptr = bpy.data.node_groups.get(found_ng.group_name) if found_ng else None
    if ng_ptr:
        for node in ng_ptr.nodes:
            name = node.name
            if node.type == 'GROUP' and getattr(node, "node_tree", None):
                val = f"{name} [{node.node_tree.name}]"
            else:
                val = f"{name} [{node.type}]"
            if not edit_text or edit_text.lower() in val.lower():
                res.append(val)

    return res

def search_menu_items_cb(self, context, edit_text):
    found_inp, found_n, found_ng = None, None, None
    preset = get_active_preset(context.scene)

    if preset:
        active_col = get_active_collection(preset)
        if active_col:
            for ng in active_col.nodegroups:
                for n in ng.nodes:
                    for i in n.inputs:
                        if self in i.values.values(): found_inp, found_n, found_ng = i, n, ng; break
                    if found_inp: break
                if found_inp: break

            if not found_inp:
                active_obj = get_active_object(active_col)
                if active_obj:
                    for ng in active_obj.nodegroups:
                        for n in ng.nodes:
                            for i in n.inputs:
                                if self in i.values.values(): found_inp, found_n, found_ng = i, n, ng; break
                            if found_inp: break
                        if found_inp: break

    items = []
    ng_ptr = bpy.data.node_groups.get(found_ng.group_name) if found_ng else None

    if ng_ptr and found_inp and found_n:
        is_mod = not found_n.name or found_n.name == "<Modifier Interface>"
        if is_mod:
            for node in ng_ptr.nodes:
                if node.type == 'MENU_SWITCH' and hasattr(node, 'enum_items'):
                    for sock in node.inputs:
                        for link in sock.links:
                            if link.from_node.type == 'GROUP_INPUT' and link.from_socket.name == found_inp.name:
                                items = [getattr(item, 'identifier', getattr(item, 'name', '')) for item in node.enum_items]
                                break
                        if items: break
                if items: break
        else:
            n_name = found_n.name.split(" [")[0].strip()
            node = ng_ptr.nodes.get(n_name)
            if node:
                if node.type == 'MENU_SWITCH' and hasattr(node, 'enum_items'):
                    items = [getattr(item, 'identifier', getattr(item, 'name', '')) for item in node.enum_items]
                elif node.type == 'GROUP' and hasattr(node, 'node_tree') and node.node_tree:
                    for inner_node in node.node_tree.nodes:
                        if inner_node.type == 'MENU_SWITCH' and hasattr(inner_node, 'enum_items'):
                            for sock in inner_node.inputs:
                                for link in sock.links:
                                    if link.from_node.type == 'GROUP_INPUT' and link.from_socket.name == found_inp.name:
                                        items = [getattr(item, 'identifier', getattr(item, 'name', '')) for item in inner_node.enum_items]
                                        break
                                if items: break
                        if items: break

    if edit_text == self.value_menu: edit_text = ""
    if not edit_text: return items
    return [item for item in items if edit_text.lower() in item.lower()]

class BatchSTLLogLine(bpy.types.PropertyGroup):
    text: bpy.props.StringProperty()

# These classes define the exact variables Blender will track.
# bpy.props.FloatProperty is Blender's special way of enforcing a decimal number inside its interface.
class BatchSTLValue(bpy.types.PropertyGroup):
    value_bool: bpy.props.BoolProperty(name="Value", default=True, update=mark_dirty)
    value_int: bpy.props.IntProperty(name="Value", default=0, update=mark_dirty)
    value_float: bpy.props.FloatProperty(name="Value", default=0.0, update=mark_dirty)
    value_string: bpy.props.StringProperty(name="Value", default="", update=mark_dirty)
    value_menu: bpy.props.StringProperty(name="Value", default="", search=search_menu_items_cb, update=mark_dirty)

    use_tag: bpy.props.BoolProperty(name="Use Tag", default=False, update=mark_dirty)
    tag: bpy.props.StringProperty(name="Tag", default="", update=mark_dirty)
    use_dir: bpy.props.BoolProperty(name="Use Dir", default=True, update=mark_dirty)

    use_sweep: bpy.props.BoolProperty(name="Sweep", default=False, update=mark_dirty)
    sweep_range: bpy.props.StringProperty(name="Sweep Range", default="", update=mark_dirty)

# A single variable can be part of an Input, which is part of a Node, which is part of a NodeGroup.
# 'CollectionProperty' means "create a list of these items".
class BatchSTLInput(bpy.types.PropertyGroup):
    name: bpy.props.StringProperty(name="Input Socket", default="", update=on_input_name_update)
    override_type: bpy.props.StringProperty(default='FLOAT', update=mark_dirty)
    values: bpy.props.CollectionProperty(type=BatchSTLValue)

class BatchSTLNode(bpy.types.PropertyGroup):
    name: bpy.props.StringProperty(name="Target Node", default="", search=search_target_node_cb, update=mark_dirty, description="Select <Modifier Interface> to target the modifier directly")
    inputs: bpy.props.CollectionProperty(type=BatchSTLInput)

class BatchSTLNodeGroup(bpy.types.PropertyGroup):
    group_name: bpy.props.StringProperty(name="Node Group", default="", update=mark_dirty)
    nodes: bpy.props.CollectionProperty(type=BatchSTLNode)

class BatchSTLObject(bpy.types.PropertyGroup):
    name: bpy.props.StringProperty()
    export: bpy.props.BoolProperty(default=True, update=mark_dirty)
    tag: bpy.props.StringProperty(name="Tag", default="", update=mark_dirty)
    sub_path: bpy.props.StringProperty(name="Sub-folder", default="", update=mark_dirty)
    nodegroups: bpy.props.CollectionProperty(type=BatchSTLNodeGroup)

class BatchSTLCollection(bpy.types.PropertyGroup):
    collection_name: bpy.props.StringProperty(name="Collection", default="", update=mark_dirty)
    use_tag: bpy.props.BoolProperty(name="Use Tag", default=True, update=mark_dirty)
    tag: bpy.props.StringProperty(name="Tag", default="", update=mark_dirty)
    sub_path: bpy.props.StringProperty(name="Sub-folder", default="", update=mark_dirty)
    objects: bpy.props.CollectionProperty(type=BatchSTLObject)
    object_index: bpy.props.IntProperty(default=0, update=mark_dirty)
    nodegroups: bpy.props.CollectionProperty(type=BatchSTLNodeGroup)

class BatchSTLExportPreset(bpy.types.PropertyGroup):
    name: bpy.props.StringProperty(name="Preset Name", default="New Preset", update=mark_dirty)
    preset_prefix: bpy.props.StringProperty(name="Preset Root Directory", default="", update=mark_dirty)
    last_export_time: bpy.props.FloatProperty(name="Last Export Time", default=0.0)

    collections: bpy.props.CollectionProperty(type=BatchSTLCollection)
    collection_index: bpy.props.IntProperty(name="Collection Index", default=0, update=mark_dirty)

    is_exporting: bpy.props.BoolProperty(default=False)
    cancel_export: bpy.props.BoolProperty(default=False)
    export_progress: bpy.props.FloatProperty(name="Progress", default=0.0, min=0.0, max=1.0)
    export_status: bpy.props.StringProperty(default="")
    console_logs: bpy.props.CollectionProperty(type=BatchSTLLogLine)
    console_index: bpy.props.IntProperty(default=0)


# ==============================================================================
# === [ 4. OPERATORS ] ===
# Operators in Blender represent "actions". When you click a button in Blender,
# it usually triggers an Operator class, which runs its 'execute()' function.
# ==============================================================================

class BATCH_STL_OT_export_presets_json(bpy.types.Operator, ExportHelper):
    bl_idname = "batch_stl.export_presets_json" # The internal ID used by Blender to call this
    bl_label = "Export JSON"                    # The readable name on the button
    bl_description = "Export all presets to a JSON file"
    filename_ext = ".json"
    filter_glob: bpy.props.StringProperty(default="*.json", options={'HIDDEN'})

    def execute(self, context):
        # Open file in 'w' (write) mode, then convert Blender properties to a dictionary and save as JSON.
        with open(self.filepath, 'w') as f: json.dump([copy_preset_to_dict(p) for p in context.scene.batch_stl_presets], f, indent=4)
        return {'FINISHED'} # Operators must return a dictionary telling Blender what happened

class BATCH_STL_OT_import_presets_json(bpy.types.Operator, ImportHelper):
    bl_idname = "batch_stl.import_presets_json"
    bl_label = "Import JSON"
    bl_description = "Import presets from a JSON file"
    bl_options = {'REGISTER', 'UNDO'} # Telling Blender the user is allowed to hit Ctrl+Z on this action.
    filename_ext = ".json"
    filter_glob: bpy.props.StringProperty(default="*.json", options={'HIDDEN'})

    def execute(self, context):
        with open(self.filepath, 'r') as f: data = json.load(f)
        for p_data in data: paste_preset_from_dict(context.scene.batch_stl_presets.add(), p_data)
        mark_dirty()
        return {'FINISHED'}

class BATCH_STL_OT_clear_console(bpy.types.Operator):
    bl_idname = "batch_stl.clear_console"
    bl_label = "Clear Console"
    bl_description = "Clear all console logs for the active preset"

    def execute(self, context):
        preset = get_active_preset(context.scene)
        if preset: preset.console_logs.clear()
        return {'FINISHED'}

# A multi-purpose operator that can handle moving lists up, down, deleting, and copying.
class BATCH_STL_OT_preset_actions(bpy.types.Operator):
    bl_idname = "batch_stl.preset_actions"
    bl_label = "Preset Actions"
    bl_options = {'REGISTER', 'INTERNAL'}

    # We pass an 'action' string to this operator when we click the button so it knows which branch to run.
    action: bpy.props.EnumProperty(items=(('ADD', "", ""), ('REMOVE', "", ""), ('UP', "", ""), ('DOWN', "", ""), ('COPY', "", ""), ('PASTE', "", "")))
    shift_pressed: bpy.props.BoolProperty(options={'HIDDEN', 'SKIP_SAVE'}, default=False)

    @classmethod # A classmethod belongs to the class itself, not a specific instance.
    def description(cls, context, properties):
        # Dynamic hover tooltips based on what action is selected!
        if properties.action == 'ADD': return "Create a new preset"
        elif properties.action == 'REMOVE': return "Remove the active preset"
        elif properties.action == 'UP': return "Move preset up (Shift-Click: Move to top)"
        elif properties.action == 'DOWN': return "Move preset down (Shift-Click: Move to bottom)"
        elif properties.action == 'COPY': return "Copy preset to clipboard"
        elif properties.action == 'PASTE': return "Paste preset from clipboard"
        return "Preset Actions"

    # 'invoke' intercepts the button click *before* 'execute', allowing us to check things like whether the user held down Shift.
    def invoke(self, context, event):
        self.shift_pressed = event.shift
        return self.execute(context)

    def execute(self, context):
        lst = context.scene.batch_stl_presets
        idx = context.scene.batch_stl_preset_index

        # Based on the action provided, modify the lists inside Blender.
        if self.action == 'ADD': lst.add(); context.scene.batch_stl_preset_index = len(lst) - 1
        elif self.action == 'REMOVE' and lst:
            if not lst[idx].is_exporting:
                lst.remove(idx); context.scene.batch_stl_preset_index = max(0, idx - 1)
            else: self.report({'WARNING'}, "Cannot remove a preset while it is actively exporting.")
        elif self.action == 'UP' and idx > 0:
            lst.move(idx, 0 if self.shift_pressed else idx - 1)
            context.scene.batch_stl_preset_index = 0 if self.shift_pressed else idx - 1
        elif self.action == 'DOWN' and idx < len(lst) - 1:
            lst.move(idx, len(lst) - 1 if self.shift_pressed else idx + 1)
            context.scene.batch_stl_preset_index = len(lst) - 1 if self.shift_pressed else idx + 1
        elif self.action == 'COPY' and lst: _clipboard["preset"] = copy_preset_to_dict(lst[idx])
        elif self.action == 'PASTE' and _clipboard.get("preset"): paste_preset_from_dict(lst.add(), _clipboard["preset"]); context.scene.batch_stl_preset_index = len(lst) - 1

        if self.action != 'COPY': bpy.ops.ed.undo_push(message="Preset Action")
        mark_dirty()
        return {'FINISHED'}

class BATCH_STL_OT_collection_actions(bpy.types.Operator):
    # (Similar layout to preset_actions, but affects collections instead).
    bl_idname = "batch_stl.collection_actions"
    bl_label = "Collection Actions"
    bl_options = {'REGISTER', 'INTERNAL'}
    action: bpy.props.EnumProperty(items=(('ADD', "", ""), ('REMOVE', "", ""), ('UP', "", ""), ('DOWN', "", ""), ('COPY', "", ""), ('PASTE', "", "")))
    shift_pressed: bpy.props.BoolProperty(options={'HIDDEN', 'SKIP_SAVE'}, default=False)

    @classmethod
    def description(cls, context, properties):
        if properties.action == 'ADD': return "Add a new collection"
        elif properties.action == 'REMOVE': return "Remove the active collection"
        elif properties.action == 'UP': return "Move collection up (Shift-Click: Move to top)"
        elif properties.action == 'DOWN': return "Move collection down (Shift-Click: Move to bottom)"
        elif properties.action == 'COPY': return "Copy collection to clipboard"
        elif properties.action == 'PASTE': return "Paste collection from clipboard"
        return "Collection Actions"

    def invoke(self, context, event):
        self.shift_pressed = event.shift
        return self.execute(context)

    def execute(self, context):
        preset = get_active_preset(context.scene)
        if not preset: return {'CANCELLED'}
        lst, idx = preset.collections, preset.collection_index
        if self.action == 'ADD': lst.add(); preset.collection_index = len(lst) - 1
        elif self.action == 'REMOVE' and lst: lst.remove(idx); preset.collection_index = max(0, idx - 1)
        elif self.action == 'UP' and idx > 0:
            lst.move(idx, 0 if self.shift_pressed else idx - 1)
            preset.collection_index = 0 if self.shift_pressed else idx - 1
        elif self.action == 'DOWN' and idx < len(lst) - 1:
            lst.move(idx, len(lst) - 1 if self.shift_pressed else idx + 1)
            preset.collection_index = len(lst) - 1 if self.shift_pressed else idx + 1
        elif self.action == 'COPY' and lst: _clipboard["collection"] = copy_collection_to_dict(lst[idx])
        elif self.action == 'PASTE' and _clipboard.get("collection"): paste_collection_from_dict(lst.add(), _clipboard["collection"]); preset.collection_index = len(lst) - 1

        if self.action != 'COPY': bpy.ops.ed.undo_push(message="Collection Action")
        mark_dirty()
        return {'FINISHED'}

class BATCH_STL_OT_table_action(bpy.types.Operator):
    bl_idname = "batch_stl.table_action"
    bl_label = "Table Action"
    bl_options = {'REGISTER', 'UNDO'}

    # Operators can have variables passed into them to specify their target!
    # By taking indices for group (ng), node (n), input (i) and value (v), one operator controls the entire table.
    action: bpy.props.StringProperty()
    is_pinned: bpy.props.BoolProperty()
    ng_idx: bpy.props.IntProperty(default=-1)
    n_idx: bpy.props.IntProperty(default=-1)
    i_idx: bpy.props.IntProperty(default=-1)
    v_idx: bpy.props.IntProperty(default=-1)
    shift_pressed: bpy.props.BoolProperty(options={'HIDDEN', 'SKIP_SAVE'}, default=False)

    @classmethod
    def description(cls, context, properties):
        # A very large dynamic tooltip generator
        action = properties.action
        if action == 'ADD_GROUP': return "Add a new Node Group override"
        elif action == 'DEL_GROUP': return "Delete this Node Group override"
        elif action == 'PIN_GROUP': return "Pin Group to Collection Overrides (Applies to all objects)"
        elif action == 'UNPIN_GROUP': return "Unpin Group to Object Overrides (Specific to all valid objects in collection)"
        elif action == 'COPY_GROUP': return "Copy Node Group to clipboard"
        elif action == 'PASTE_GROUP': return "Paste Node Group from clipboard"
        elif action == 'ADD_NODE': return "Add a new Node to override"
        elif action == 'DEL_NODE': return "Delete this Node"
        elif action == 'MOVE_GROUP_UP': return "Move Group Up"
        elif action == 'MOVE_GROUP_DOWN': return "Move Group Down"
        elif action == 'MOVE_NODE_UP': return "Move Node Up"
        elif action == 'MOVE_NODE_DOWN': return "Move Node Down"
        elif action == 'ADD_INPUT': return "Add an Input (Shift-Click: Auto-populate all inputs from Node/Modifier)"
        elif action == 'DEL_INPUT': return "Delete this Input"
        elif action == 'MOVE_INPUT_UP': return "Move Input Up"
        elif action == 'MOVE_INPUT_DOWN': return "Move Input Down"
        elif action == 'DEL_VALUE_OR_INPUT': return "Delete this Value or Input"
        elif action == 'DEL_VALUE': return "Delete this Value iteration"
        elif action == 'MOVE_VALUE_UP': return "Move Value Up"
        elif action == 'MOVE_VALUE_DOWN': return "Move Value Down"
        elif action == 'VALUE_ACTION':
            if properties.v_idx < 0: return "Add a Value iteration (Shift-Click: Toggle Sweep Mode)"
            else: return "Add a Value iteration (Shift-Click: Toggle Sweep / Populate values)"
        return "Table action"

    def invoke(self, context, event):
        self.shift_pressed = event.shift
        return self.execute(context)

    def execute(self, context):
        preset = get_active_preset(context.scene)
        if not preset: return {'CANCELLED'}
        active_col = get_active_collection(preset)
        if not active_col: return {'CANCELLED'}

        if self.is_pinned:
            ng_list = active_col.nodegroups
        else:
            active_obj = get_active_object(active_col)
            if not active_obj: return {'CANCELLED'}
            ng_list = active_obj.nodegroups

        # Big branching tree to modify the data arrays properly based on user clicking '+' or '-' icons
        if self.action == 'ADD_GROUP':
            ng = ng_list.add()
            node = ng.nodes.add()
            node.name = "<Modifier Interface>"
            inp = node.inputs.add()
            inp.values.add()
        elif self.action == 'DEL_GROUP':
            ng_list.remove(self.ng_idx)
        elif self.action == 'PIN_GROUP':
            if not self.is_pinned:
                src_ng = ng_list[self.ng_idx]
                dst_ng = active_col.nodegroups.add()
                paste_ng_from_dict(dst_ng, copy_ng_to_dict(src_ng))
                ng_list.remove(self.ng_idx)
        elif self.action == 'UNPIN_GROUP':
            if self.is_pinned:
                src_ng = ng_list[self.ng_idx]
                ng_dict = copy_ng_to_dict(src_ng)

                # Paste the copied override into every object's local list
                for obj in active_col.objects:
                    dst_ng = obj.nodegroups.add()
                    paste_ng_from_dict(dst_ng, ng_dict)

                # Remove the original pinned override from the collection
                ng_list.remove(self.ng_idx)
        elif self.action == 'COPY_GROUP':
            global _clipboard # Must define global if we intend to change a variable declared outside this scope.
            _clipboard["nodegroup"] = copy_ng_to_dict(ng_list[self.ng_idx])
        elif self.action == 'PASTE_GROUP':
            if _clipboard.get("nodegroup"):
                paste_ng_from_dict(ng_list.add(), _clipboard["nodegroup"])

        elif self.action == 'ADD_NODE':
            node = ng_list[self.ng_idx].nodes.add()
            node.name = "<Modifier Interface>"
            inp = node.inputs.add()
            inp.values.add()
        elif self.action == 'DEL_NODE':
            ng_list[self.ng_idx].nodes.remove(self.n_idx)

        elif self.action == 'ADD_INPUT':
            ng = ng_list[self.ng_idx]
            node = ng.nodes[self.n_idx]
            ng_ptr = bpy.data.node_groups.get(ng.group_name)

            # Auto-populates all available inputs automatically if the user holds SHIFT
            if self.shift_pressed and ng_ptr:
                is_mod = not node.name or node.name == "<Modifier Interface>"
                source_inputs = []
                if is_mod and hasattr(ng_ptr, "interface"):
                    for item in ng_ptr.interface.items_tree:
                        if getattr(item, "item_type", "SOCKET") == 'SOCKET' and getattr(item, "in_out", "INPUT") == 'INPUT':
                            source_inputs.append(item.name)
                elif not is_mod and node.name:
                    target_n = ng_ptr.nodes.get(node.name.split(" [")[0].strip())
                    if target_n:
                        for i in target_n.inputs:
                            if not getattr(i, "is_unavailable", False) and not getattr(i, "hide", False):
                                source_inputs.append(i.name)

                if source_inputs:
                    existing_names = {i.name for i in node.inputs}
                    added = False
                    for s_name in source_inputs:
                        if s_name and s_name not in existing_names:
                            inp = node.inputs.add()
                            inp.name = s_name
                            inp.values.add()
                            added = True
                    if added:
                        mark_dirty()
                        return {'FINISHED'}

            inp = node.inputs.add()
            inp.values.add()
        elif self.action == 'DEL_INPUT':
            ng_list[self.ng_idx].nodes[self.n_idx].inputs.remove(self.i_idx)

        elif self.action == 'DEL_VALUE':
            ng_list[self.ng_idx].nodes[self.n_idx].inputs[self.i_idx].values.remove(self.v_idx)

        elif self.action == 'DEL_VALUE_OR_INPUT':
            inp = ng_list[self.ng_idx].nodes[self.n_idx].inputs[self.i_idx]
            if len(inp.values) > 1:
                inp.values.remove(self.v_idx)
            else:
                ng_list[self.ng_idx].nodes[self.n_idx].inputs.remove(self.i_idx)

        elif self.action in ['ADD_VALUE', 'TOGGLE_SWEEP', 'VALUE_ACTION']:
            vals = ng_list[self.ng_idx].nodes[self.n_idx].inputs[self.i_idx].values

            if self.v_idx < 0:
                vals.add()
            else:
                val = vals[self.v_idx]
                if not val.use_sweep:
                    if self.shift_pressed:
                        val.use_sweep = True
                        for j in reversed(range(len(vals))):
                            if j != self.v_idx: vals.remove(j)
                    else:
                        vals.add()
                else:
                    val.use_sweep = False
                    if self.shift_pressed:
                        inp_obj = ng_list[self.ng_idx].nodes[self.n_idx].inputs[self.i_idx]
                        if inp_obj.override_type in ['FLOAT', 'INT', 'MENU', 'BOOLEAN']:
                            ng_obj = ng_list[self.ng_idx]
                            ng_ptr = bpy.data.node_groups.get(ng_obj.group_name)
                            node_obj = ng_obj.nodes[self.n_idx]
                            target = 'MODIFIER' if not node_obj.name or node_obj.name == "<Modifier Interface>" else 'NODE'

                            temp_inp = TempMockInput(inp_obj.name, inp_obj.override_type, val)
                            temp_ovr = TempMockOverride(target, ng_ptr, node_obj.name, [temp_inp])

                            parsed_vals = parse_sweep_values(temp_ovr, temp_inp)
                            if parsed_vals:
                                first_val = parsed_vals[0]
                                if inp_obj.override_type == 'FLOAT': val.value_float = first_val
                                elif inp_obj.override_type == 'INT': val.value_int = first_val
                                elif inp_obj.override_type == 'MENU': val.value_menu = str(first_val)
                                elif inp_obj.override_type == 'BOOLEAN': val.value_bool = bool(first_val)

                                for p_val in parsed_vals[1:]:
                                    new_val = vals.add()
                                    new_val.use_sweep = False
                                    if inp_obj.override_type == 'FLOAT': new_val.value_float = p_val
                                    elif inp_obj.override_type == 'INT': new_val.value_int = p_val
                                    elif inp_obj.override_type == 'MENU': new_val.value_menu = str(p_val)
                                    elif inp_obj.override_type == 'BOOLEAN': new_val.value_bool = bool(p_val)

        elif self.action == 'MOVE_GROUP_UP':
            if self.ng_idx > 0: ng_list.move(self.ng_idx, self.ng_idx - 1)
        elif self.action == 'MOVE_GROUP_DOWN':
            if self.ng_idx < len(ng_list) - 1: ng_list.move(self.ng_idx, self.ng_idx + 1)

        elif self.action == 'MOVE_NODE_UP':
            nodes = ng_list[self.ng_idx].nodes
            if self.n_idx > 0: nodes.move(self.n_idx, self.n_idx - 1)
        elif self.action == 'MOVE_NODE_DOWN':
            nodes = ng_list[self.ng_idx].nodes
            if self.n_idx < len(nodes) - 1: nodes.move(self.n_idx, self.n_idx + 1)

        elif self.action == 'MOVE_INPUT_UP':
            inputs = ng_list[self.ng_idx].nodes[self.n_idx].inputs
            if self.i_idx > 0: inputs.move(self.i_idx, self.i_idx - 1)
        elif self.action == 'MOVE_INPUT_DOWN':
            inputs = ng_list[self.ng_idx].nodes[self.n_idx].inputs
            if self.i_idx < len(inputs) - 1: inputs.move(self.i_idx, self.i_idx + 1)

        elif self.action == 'MOVE_VALUE_UP':
            vals = ng_list[self.ng_idx].nodes[self.n_idx].inputs[self.i_idx].values
            if self.v_idx > 0: vals.move(self.v_idx, self.v_idx - 1)
        elif self.action == 'MOVE_VALUE_DOWN':
            vals = ng_list[self.ng_idx].nodes[self.n_idx].inputs[self.i_idx].values
            if self.v_idx < len(vals) - 1: vals.move(self.v_idx, self.v_idx + 1)

        mark_dirty()
        return {'FINISHED'}


class BATCH_STL_OT_toggle_dir_tree(bpy.types.Operator):
    bl_idname = "batch_stl.toggle_dir_tree"
    bl_label = "Toggle Directory Tree"
    bl_description = "Expand or collapse this directory in the preview tree"
    bl_options = {'INTERNAL'}

    dir_path: bpy.props.StringProperty()

    def execute(self, context):
        import json
        scene = context.scene
        try:
            collapsed = json.loads(scene.batch_stl_collapsed_dirs)
        except Exception:
            collapsed = []

        if self.dir_path in collapsed:
            collapsed.remove(self.dir_path)
        else:
            collapsed.append(self.dir_path)

        scene.batch_stl_collapsed_dirs = json.dumps(collapsed)
        return {'FINISHED'}

class BATCH_STL_OT_cancel_export(bpy.types.Operator):
    bl_idname = "batch_stl.cancel_export"
    bl_label = "Cancel Export"
    bl_description = "Cancel the ongoing background export"
    preset_index: bpy.props.IntProperty(default=-1)

    def execute(self, context):
        if 0 <= self.preset_index < len(context.scene.batch_stl_presets):
            preset = context.scene.batch_stl_presets[self.preset_index]
            preset.cancel_export = True
            log = preset.console_logs.add()
            log.text = f"[!] Export cancelled manually for '{preset.name}'."
            preset.console_index = len(preset.console_logs) - 1
        return {'FINISHED'}

# -------------------------------------------------------------------------
# Modal Operator: This is the engine for the actual export.
# Normal operators freeze Blender while running. 'Modal' operators keep running
# continuously in the background, listening to a timer or mouse events.
# -------------------------------------------------------------------------
class EXPORT_OT_batch_stl_multi(bpy.types.Operator):
    bl_idname = "export_scene.batch_stl_multi"
    bl_label = "Export"
    bl_description = "Safely evaluate and batch export the mapped collections"
    bl_options = {"REGISTER"}
    preset_index: bpy.props.IntProperty(default=-1)

    @classmethod
    def poll(cls, context):
        # Poll prevents the user from clicking the button if it shouldn't be active (e.g., if there are 0 presets)
        return len(context.scene.batch_stl_presets) > 0

    def invoke(self, context, event):
        self._timer = None
        self.process = None
        self.total_operations = 1
        self.export_start_time = time.perf_counter()
        self.current_op = 0
        scene = context.scene

        self.preset_idx = self.preset_index if self.preset_index >= 0 else scene.batch_stl_preset_index
        if self.preset_idx < 0 or self.preset_idx >= len(scene.batch_stl_presets): return {"CANCELLED"}
        self.preset = scene.batch_stl_presets[self.preset_idx]

        if self.preset.is_exporting: return {'CANCELLED'}
        if not scene.batch_stl_root_dir:
            self.report({'ERROR'}, "Missing Root Directory") # Shows error popups at the bottom of Blender
            return {"CANCELLED"}

        self.preset.console_logs.clear()
        context.scene.batch_stl_show_console = True

        has_overrides = any(bool(c.nodegroups) for c in self.preset.collections) or \
                        any(bool(o.nodegroups) for c in self.preset.collections for o in c.objects)

        verbose = scene.batch_stl_verbose_console

        # If there are no complex modifications needed, just export synchronously (freeze Blender for a moment)
        if not has_overrides:
            root_dir = bpy.path.abspath(scene.batch_stl_root_dir)
            if self.preset.preset_prefix:
                root_dir = os.path.normpath(os.path.join(root_dir, self.preset.preset_prefix))

            total_objs = 0
            for c in self.preset.collections:
                c_ptr = bpy.data.collections.get(c.collection_name)
                if not c_ptr or is_collection_excluded(context, c_ptr): continue
                for obj_prop in c.objects:
                    if not obj_prop.export: continue
                    bl_obj = c_ptr.all_objects.get(obj_prop.name)
                    if bl_obj and bl_obj.type in {"MESH", "CURVE", "SURFACE", "META", "FONT"} and not bl_obj.hide_viewport:
                        total_objs += 1

            context.window_manager.progress_begin(0, max(1, total_objs)) # Shows progress bar on bottom cursor
            depsgraph = context.evaluated_depsgraph_get()
            exported_count = 0

            log_msg = f"\n=== STARTING SYNCHRONOUS EXPORT: {self.preset.name} ==="
            if verbose: print(log_msg)
            log_to_console(self.preset, log_msg)

            for c in self.preset.collections:
                c_ptr = bpy.data.collections.get(c.collection_name)
                if not c_ptr or is_collection_excluded(context, c_ptr): continue

                for obj_prop in c.objects:
                    if not obj_prop.export: continue
                    obj = c_ptr.all_objects.get(obj_prop.name)
                    if not obj or obj.hide_viewport or obj.type not in {"MESH", "CURVE", "SURFACE", "META", "FONT"}:
                        continue

                    # Apply Object Directory Logic implicitly if specified
                    obj_subpath = obj_prop.sub_path.replace('\\', '/') if obj_prop.sub_path else ""
                    out_dir = os.path.normpath(os.path.join(root_dir, c.sub_path, obj_subpath))
                    os.makedirs(out_dir, exist_ok=True)

                    # Apply Object Tagging Logic implicitly if specified
                    safe_name = bpy.path.clean_name(obj.name)
                    if obj_prop.tag:
                        if obj_prop.tag.startswith("_"): safe_name = safe_name + obj_prop.tag
                        elif obj_prop.tag.endswith("_"): safe_name = obj_prop.tag + safe_name
                        else: safe_name = obj_prop.tag

                    t_eval_start = time.perf_counter()
                    obj_eval = obj.evaluated_get(depsgraph)
                    try: mesh = obj_eval.to_mesh()
                    except RuntimeError: mesh = None
                    perf_msg = f"  ├─ Evaluated {obj.name} in {time.perf_counter()-t_eval_start:.4f}s"
                    if verbose: print(perf_msg)
                    log_to_console(self.preset, perf_msg)

                    if mesh:
                        base_tag = c.tag if c.use_tag and c.tag else ""
                        filepath = os.path.join(out_dir, f"{safe_name}{base_tag}.stl")
                        write_fast_binary_stl(filepath, mesh, obj.matrix_world, verbose=verbose)
                        obj_eval.to_mesh_clear()
                        exported_count += 1
                        context.window_manager.progress_update(exported_count)

            context.window_manager.progress_end()
            total_time = time.perf_counter() - self.export_start_time
            self.preset.last_export_time = total_time

            end_msg = f"=== SYNCHRONOUS EXPORT COMPLETE ({total_time:.4f}s) ==="
            if verbose: print(end_msg)
            log_to_console(self.preset, end_msg)
            self.report({'INFO'}, f"Exported {exported_count} objects directly in {total_time:.2f}s.")
            return {'FINISHED'}

        # BUT... If there are overrides, they take too long. We have to spawn a background worker using 'subprocess'.
        self.preset.export_status = f"Spawning Worker... (0.0s)"
        t_spawn_start = time.perf_counter()

        self.temp_dir = tempfile.mkdtemp(prefix="fast_batch_stl_")
        self.temp_blend = os.path.join(self.temp_dir, "batch_stl_export_temp.blend")
        # Save a copy of the scene to a temporary folder
        bpy.ops.wm.save_as_mainfile(filepath=self.temp_blend, copy=True, compress=False)

        # Build the terminal command that launches a hidden ('headless') Blender
        cmd = [
            bpy.app.binary_path, "--factory-startup", "-b", self.temp_blend,
            "-P", __file__, "--", "--batch-stl-headless", str(self.preset_idx)
        ]

        try:
            self.process = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)
        except Exception as e:
            self.report({'ERROR'}, f"Failed to spawn headless Blender: {e}")
            self.cleanup(context)
            return {'CANCELLED'}

        spawn_time = time.perf_counter() - t_spawn_start
        spawn_msg = f"=== INITIATING HEADLESS EXPORT '{self.preset.name}' [{spawn_time:.4f}s Boot] ==="
        if verbose: print(f"\n{spawn_msg}")
        log_to_console(self.preset, spawn_msg)

        # Queues let our background thread safely send text lines back to the main UI without crashing.
        self.q = queue.Queue()
        def enqueue_output(out, q):
            for line in iter(out.readline, ''):
                q.put(line)
            out.close()

        # Creates a background 'thread' to listen to the headless Blender without pausing the active Blender
        self.t = threading.Thread(target=enqueue_output, args=(self.process.stdout, self.q))
        self.t.daemon = True
        self.t.start()

        self.preset.is_exporting = True
        self.preset.cancel_export = False
        self.preset.export_progress = 0.0

        # Create a timer that "wakes up" this operator every 0.05 seconds to check if there is new text
        self._timer = context.window_manager.event_timer_add(0.05, window=context.window)
        context.window_manager.modal_handler_add(self)
        return {'RUNNING_MODAL'} # RUNNING_MODAL keeps the operator alive!

    def modal(self, context, event):
        try:
            if self.preset.cancel_export:
                self.cleanup(context)
                self.report({'WARNING'}, f"Export cancelled for {self.preset.name}.")
                log_to_console(self.preset, f"[!] Export cancelled manually for '{self.preset.name}'.")
                return {'CANCELLED'}

            if event.type == 'TIMER': # Every 0.05s when the timer ticks...
                elapsed = time.perf_counter() - self.export_start_time
                while True:
                    try: line = self.q.get_nowait() # Get text from the background worker
                    except queue.Empty: break
                    else:
                        line = line.rstrip('\r\n')
                        if line.startswith("BATCH_STL_TOTAL:"):
                            try: self.total_operations = int(line.split(":")[1])
                            except Exception: pass
                        elif line.startswith("BATCH_STL_PROGRESS:"):
                            try:
                                self.current_op = int(line.split(":")[1])
                                self.preset.export_progress = self.current_op / max(1, self.total_operations)
                            except Exception: pass
                        elif line.startswith("BATCH_STL_DONE"):
                            self.cleanup(context)
                            total_time = time.perf_counter() - self.export_start_time
                            self.preset.last_export_time = total_time
                            end_msg = f"=== BATCH EXPORT COMPLETE ({total_time:.4f}s) ==="
                            if context.scene.batch_stl_verbose_console: print(end_msg)
                            log_to_console(self.preset, end_msg)
                            self.report({'INFO'}, f"Batch Export {self.preset.name} Complete in {total_time:.2f}s.")
                            for area in context.screen.areas: area.tag_redraw()
                            return {'FINISHED'}
                        elif line:
                            if context.scene.batch_stl_verbose_console: print(f"[{self.preset.name}] {line}")
                            log_to_console(self.preset, f"[{self.preset.name}] {line}")

                if self.preset.is_exporting:
                    if self.total_operations > 1 or self.current_op > 0:
                        self.preset.export_status = f"Obj {self.current_op}/{self.total_operations} | {elapsed:.1f}s"
                    else:
                        self.preset.export_status = f"Spawning Worker... ({elapsed:.1f}s)"

                # Tag redraw forces the Blender UI to refresh and show our updated progress bars
                for area in context.screen.areas: area.tag_redraw()

                # Safety check: if the process crashed, we stop listening
                if self.process and self.process.poll() is not None:
                    self.cleanup(context)
                    log_to_console(self.preset, f"[!] CRASH DETECTED: Worker died unexpectedly.")
                    self.report({'ERROR'}, f"Background worker crashed for preset {self.preset.name}.")
                    return {'CANCELLED'}
        except ReferenceError:
            self.cleanup(context)
            return {'CANCELLED'}
        except Exception as e:
            err_msg = f"[!] Fast Batch STL Error ({self.preset.name if hasattr(self, 'preset') else 'Unknown'}): {e}"
            print(f"\n{err_msg}")
            if hasattr(self, 'preset'): log_to_console(self.preset, err_msg)
            self.cleanup(context)
            self.report({'ERROR'}, "Unexpected error during batch export.")
            return {'CANCELLED'}
        return {'PASS_THROUGH'} # We return PASS_THROUGH so the user can still click around Blender normally!

    # This deletes all temporary files, stops the timer, and cleans up memory
    def cleanup(self, context=None):
        if context:
            if getattr(self, '_timer', None):
                context.window_manager.event_timer_remove(self._timer)
                self._timer = None
            try:
                self.preset.is_exporting = False
                self.preset.cancel_export = False
                self.preset.export_progress = 0.0
                self.preset.export_status = ""
            except ReferenceError: pass
        if getattr(self, 'process', None):
            try:
                if self.process.poll() is None: self.process.kill()
            except Exception: pass
        try:
            if hasattr(self, 'temp_dir') and os.path.exists(self.temp_dir):
                shutil.rmtree(self.temp_dir, ignore_errors=True)
        except Exception: pass


# ==============================================================================
# === [ 5. UI LISTS & PANELS ] ===
# These classes only describe the visual layout (rows, columns, buttons)
# you see in Blender. They do NO actual math or exporting logic.
# ==============================================================================

class BATCH_STL_UL_presets(bpy.types.UIList):
    # 'draw_item' tells Blender how to format a single row in the visual list.
    def draw_item(self, context, layout, data, item, icon, active_data, active_propname, index):
        row = layout.row(align=True) # row(align=True) snaps buttons together without spacing
        row.prop(item, "name", text="", emboss=False) # 'emboss=False' makes it look like plain text, not a button
        row.prop(item, "preset_prefix", text="", emboss=False, icon='FILE_FOLDER')

        if item.is_exporting:
            row.prop(item, "export_progress", text=item.export_status, slider=True)
            cancel_op = row.operator("batch_stl.cancel_export", text="", icon='CANCEL')
            cancel_op.preset_index = index
        else:
            op = row.operator("export_scene.batch_stl_multi", text="", icon='EXPORT')
            op.preset_index = index

class BATCH_STL_UL_collections(bpy.types.UIList):
    def draw_item(self, context, layout, data, item, icon, active_data, active_propname, index):
        row = layout.row(align=True)
        row.prop_search(item, "collection_name", bpy.data, "collections", text="", icon='OUTLINER_COLLECTION')
        row.separator(factor=0.5)
        sub_row = row.row(align=True)
        sub_row.prop(item, "use_tag", text="", icon='BOOKMARKS')
        sub_row.separator(factor=0.5)
        tag_row = sub_row.row(align=True)
        tag_row.prop(item, "tag", text="", emboss=False)
        row.prop(item, "sub_path", text="", emboss=False, icon='FILE_FOLDER')

class BATCH_STL_UL_objects(bpy.types.UIList):
    def draw_item(self, context, layout, data, item, icon, active_data, active_propname, index):
        split = layout.split(factor=0.45)

        # Left side: Export toggle and name
        row = split.row(align=True)
        row.prop(item, "export", text="", icon='CHECKBOX_HLT' if item.export else 'CHECKBOX_DEHLT', emboss=False)
        row.label(text=item.name)

        # Right side: Tag and Directory settings (No boolean toggles, just labels)
        tools = split.row(align=True)

        tools.label(text="", icon='BOOKMARKS')
        tools.prop(item, "tag", text="", emboss=False)

        tools.separator(factor=0.5)

        tools.label(text="", icon='FILE_FOLDER')
        tools.prop(item, "sub_path", text="", emboss=False)

class BATCH_STL_UL_console_logs(bpy.types.UIList):
    def draw_item(self, context, layout, data, item, icon, active_data, active_propname, index):
        layout.label(text=item.text)

# A helper function that stamps down the same 4 or 6 arrow buttons wherever needed.
def draw_inline_controls(layout, operator_id, use_clipboard=False):
    row = layout.row(align=True)
    row.operator(operator_id, icon='ADD', text="").action = 'ADD'
    row.operator(operator_id, icon='REMOVE', text="").action = 'REMOVE'
    row.operator(operator_id, icon='TRIA_UP', text="").action = 'UP'
    row.operator(operator_id, icon='TRIA_DOWN', text="").action = 'DOWN'
    if use_clipboard:
        row.operator(operator_id, icon='COPYDOWN', text="").action = 'COPY'
        row.operator(operator_id, icon='PASTEDOWN', text="").action = 'PASTE'


def draw_overrides_table(layout, scene, nodegroups, is_pinned, is_open_prop, title_text):
    box = layout.box()

    header_row = box.row()
    is_open = getattr(scene, is_open_prop)
    icon_open = 'TRIA_DOWN' if is_open else 'TRIA_RIGHT'
    header_row.prop(scene, is_open_prop, text="", icon=icon_open, emboss=False)
    header_row.label(text=title_text, icon='PINNED' if is_pinned else 'UNPINNED')

    op_row = header_row.row(align=True)
    op = op_row.operator("batch_stl.table_action", text="", icon='ADD')
    op.action = 'ADD_GROUP'; op.is_pinned = is_pinned
    op = op_row.operator("batch_stl.table_action", text="", icon='PASTEDOWN')
    op.action = 'PASTE_GROUP'; op.is_pinned = is_pinned

    if not is_open:
        return

    if len(nodegroups) == 0:
        box.label(text="No overrides defined.")
        return

    for ng_idx, ng in enumerate(nodegroups):
        ng_box = box.box()
        ng_layout = ng_box.column()
        ng_row = ng_layout.row(align=True)
        op = ng_row.operator("batch_stl.table_action", text="", icon='ADD'); op.action = 'ADD_NODE'; op.is_pinned = is_pinned; op.ng_idx = ng_idx

        ng_row.prop_search(ng, "group_name", bpy.data, "node_groups", text="")

        if len(nodegroups) > 1:
            op = ng_row.operator("batch_stl.table_action", text="", icon='TRIA_UP'); op.action = 'MOVE_GROUP_UP'; op.is_pinned = is_pinned; op.ng_idx = ng_idx
            op = ng_row.operator("batch_stl.table_action", text="", icon='TRIA_DOWN'); op.action = 'MOVE_GROUP_DOWN'; op.is_pinned = is_pinned; op.ng_idx = ng_idx
        op = ng_row.operator("batch_stl.table_action", text="", icon='PINNED' if is_pinned else 'UNPINNED'); op.action = 'UNPIN_GROUP' if is_pinned else 'PIN_GROUP'; op.is_pinned = is_pinned; op.ng_idx = ng_idx
        op = ng_row.operator("batch_stl.table_action", text="", icon='COPYDOWN'); op.action = 'COPY_GROUP'; op.is_pinned = is_pinned; op.ng_idx = ng_idx
        op = ng_row.operator("batch_stl.table_action", text="", icon='TRASH'); op.action = 'DEL_GROUP'; op.is_pinned = is_pinned; op.ng_idx = ng_idx

        if not ng.nodes:
            continue

        n_split = ng_layout.split(factor=0.03)
        n_split.column()
        nodes_col = n_split.column()
        nodes_box = nodes_col.box() if len(ng.nodes) > 1 else nodes_col
        nodes_layout = nodes_box.column()

        for n_idx, node in enumerate(ng.nodes):
            node_container = nodes_layout.box()
            node_layout = node_container.column()

            n_row = node_layout.row(align=True)
            op = n_row.operator("batch_stl.table_action", text="", icon='ADD'); op.action = 'ADD_INPUT'; op.is_pinned = is_pinned; op.ng_idx = ng_idx; op.n_idx = n_idx
            n_row.prop(node, "name", text="", icon='NODETREE')

            if len(ng.nodes) > 1:
                op = n_row.operator("batch_stl.table_action", text="", icon='TRIA_UP'); op.action = 'MOVE_NODE_UP'; op.is_pinned = is_pinned; op.ng_idx = ng_idx; op.n_idx = n_idx
                op = n_row.operator("batch_stl.table_action", text="", icon='TRIA_DOWN'); op.action = 'MOVE_NODE_DOWN'; op.is_pinned = is_pinned; op.ng_idx = ng_idx; op.n_idx = n_idx
                op = n_row.operator("batch_stl.table_action", text="", icon='TRASH'); op.action = 'DEL_NODE'; op.is_pinned = is_pinned; op.ng_idx = ng_idx; op.n_idx = n_idx

            if not node.inputs:
                continue

            i_split = node_layout.split(factor=0.03)
            i_split.column()
            inputs_col = i_split.column()
            inputs_box = inputs_col.box()
            inputs_layout = inputs_box.column()

            for i_idx, inp in enumerate(node.inputs):
                input_layout = inputs_layout.column()

                if not inp.values:
                    i_row = input_layout.row(align=True)
                    s_main = i_row.split(factor=0.35, align=False)
                    c_inp = s_main.row(align=True)

                    op = c_inp.operator("batch_stl.table_action", text="", icon='ADD')
                    op.action = 'VALUE_ACTION'; op.is_pinned = is_pinned; op.ng_idx = ng_idx; op.n_idx = n_idx; op.i_idx = i_idx; op.v_idx = -1

                    ng_ptr = bpy.data.node_groups.get(ng.group_name)
                    is_mod = not node.name or node.name == "<Modifier Interface>"
                    if is_mod and ng_ptr and hasattr(ng_ptr, "interface"):
                        c_inp.prop_search(inp, "name", ng_ptr.interface, "items_tree", text="")
                    elif not is_mod and ng_ptr and node.name:
                        target_n = ng_ptr.nodes.get(node.name.split(" [")[0].strip())
                        if target_n: c_inp.prop_search(inp, "name", target_n, "inputs", text="")
                        else: c_inp.prop(inp, "name", text="")
                    else:
                        c_inp.prop(inp, "name", text="")

                    s_val = s_main.split(factor=0.5, align=False)
                    c_val = s_val.row(align=True)
                    c_dir = s_val.row(align=True)

                    if len(node.inputs) > 1:
                        op = c_dir.operator("batch_stl.table_action", text="", icon='TRIA_UP'); op.action = 'MOVE_INPUT_UP'; op.is_pinned = is_pinned; op.ng_idx = ng_idx; op.n_idx = n_idx; op.i_idx = i_idx
                        op = c_dir.operator("batch_stl.table_action", text="", icon='TRIA_DOWN'); op.action = 'MOVE_INPUT_DOWN'; op.is_pinned = is_pinned; op.ng_idx = ng_idx; op.n_idx = n_idx; op.i_idx = i_idx
                    op = c_dir.operator("batch_stl.table_action", text="", icon='TRASH'); op.action = 'DEL_INPUT'; op.is_pinned = is_pinned; op.ng_idx = ng_idx; op.n_idx = n_idx; op.i_idx = i_idx

                    continue

                for v_idx, val in enumerate(inp.values):
                    i_first = (v_idx == 0)
                    i_row = input_layout.row(align=True)

                    s_main = i_row.split(factor=0.35, align=False)
                    c_inp = s_main.row(align=True)

                    if i_first:
                        if getattr(val, "use_sweep", False):
                            op = c_inp.operator("batch_stl.table_action", text="", icon='FILE_REFRESH', depress=True)
                        else:
                            op = c_inp.operator("batch_stl.table_action", text="", icon='ADD')
                        op.action = 'VALUE_ACTION'; op.is_pinned = is_pinned; op.ng_idx = ng_idx; op.n_idx = n_idx; op.i_idx = i_idx; op.v_idx = 0

                        ng_ptr = bpy.data.node_groups.get(ng.group_name)
                        is_mod = not node.name or node.name == "<Modifier Interface>"
                        if is_mod and ng_ptr and hasattr(ng_ptr, "interface"):
                            c_inp.prop_search(inp, "name", ng_ptr.interface, "items_tree", text="")
                        elif not is_mod and ng_ptr and node.name:
                            target_n = ng_ptr.nodes.get(node.name.split(" [")[0].strip())
                            if target_n: c_inp.prop_search(inp, "name", target_n, "inputs", text="")
                            else: c_inp.prop(inp, "name", text="")
                        else:
                            c_inp.prop(inp, "name", text="")
                    else:
                        c_inp.alignment = 'RIGHT'

                    s_val = s_main.split(factor=0.5, align=False)
                    c_val = s_val.row(align=True)

                    if getattr(val, "use_sweep", False):
                        if inp.override_type in ['INT', 'FLOAT', 'STRING']:
                            c_val.prop(val, "sweep_range", text="")
                        elif inp.override_type == 'BOOLEAN':
                            sub = c_val.row(align=True)
                            sub.active = False
                            sub.operator("wm.context_set_string", text="True & False")
                        elif inp.override_type == 'MENU':
                            sub = c_val.row(align=True)
                            sub.active = False
                            sub.operator("wm.context_set_string", text="All values")
                    else:
                        if inp.override_type == 'BOOLEAN':
                            c_val.prop(val, "value_bool", text="True" if val.value_bool else "False", toggle=True)
                        elif inp.override_type == 'INT':
                            c_val.prop(val, "value_int", text="")
                        elif inp.override_type == 'FLOAT':
                            c_val.prop(val, "value_float", text="")
                        elif inp.override_type == 'STRING':
                            c_val.prop(val, "value_string", text="")
                        elif inp.override_type == 'MENU':
                            c_val.prop(val, "value_menu", text="")

                    c_dir = s_val.row(align=True)
                    is_permutation = len(inp.values) > 1 or any(getattr(v, "use_sweep", False) for v in inp.values)
                    if is_permutation:
                        c_dir.prop(val, "use_dir", text="", icon='FILE_FOLDER')
                        c_dir.prop(val, "use_tag", text="", icon='BOOKMARKS')
                        c_dir.prop(val, "tag", text="")

                    if i_first:
                        if len(node.inputs) > 1:
                            op = c_dir.operator("batch_stl.table_action", text="", icon='TRIA_UP'); op.action = 'MOVE_INPUT_UP'; op.is_pinned = is_pinned; op.ng_idx = ng_idx; op.n_idx = n_idx; op.i_idx = i_idx
                            op = c_dir.operator("batch_stl.table_action", text="", icon='TRIA_DOWN'); op.action = 'MOVE_INPUT_DOWN'; op.is_pinned = is_pinned; op.ng_idx = ng_idx; op.n_idx = n_idx; op.i_idx = i_idx

                        op = c_dir.operator("batch_stl.table_action", text="", icon='TRASH'); op.action = 'DEL_VALUE_OR_INPUT'; op.is_pinned = is_pinned; op.ng_idx = ng_idx; op.n_idx = n_idx; op.i_idx = i_idx; op.v_idx = 0
                    else:
                        if len(inp.values) > 1:
                            op = c_dir.operator("batch_stl.table_action", text="", icon='TRIA_UP'); op.action = 'MOVE_VALUE_UP'; op.is_pinned = is_pinned; op.ng_idx = ng_idx; op.n_idx = n_idx; op.i_idx = i_idx; op.v_idx = v_idx
                            op = c_dir.operator("batch_stl.table_action", text="", icon='TRIA_DOWN'); op.action = 'MOVE_VALUE_DOWN'; op.is_pinned = is_pinned; op.ng_idx = ng_idx; op.n_idx = n_idx; op.i_idx = i_idx; op.v_idx = v_idx
                        op = c_dir.operator("batch_stl.table_action", text="", icon='TRASH'); op.action = 'DEL_VALUE'; op.is_pinned = is_pinned; op.ng_idx = ng_idx; op.n_idx = n_idx; op.i_idx = i_idx; op.v_idx = v_idx

# This class defines the massive main panel in the 3D Viewport Toolbar ('N' panel).
class VIEW3D_PT_batch_export_stl_multi(bpy.types.Panel):
    bl_space_type = "VIEW_3D" # Appears in the 3D window
    bl_region_type = "UI"     # Specifically the sidebar UI
    bl_category = "Export"    # Name of the tab
    bl_label = "Fast Batch STL Export"

    def draw(self, context):
        layout = self.layout
        scene = context.scene

        dir_row = layout.row(align=True)
        dir_row.operator("batch_stl.import_presets_json", text="", icon='IMPORT')
        dir_row.operator("batch_stl.export_presets_json", text="", icon='EXPORT')
        dir_row.prop(scene, "batch_stl_show_console", text="", icon='CONSOLE', toggle=True)
        dir_row.prop(scene, "batch_stl_root_dir")

        active_preset = get_active_preset(scene)

        if scene.batch_stl_show_console:
            c_box = layout.box()
            c_box.label(text="Global Export Console Log", icon='CONSOLE')
            if active_preset:
                c_box.template_list("BATCH_STL_UL_console_logs", "", active_preset, "console_logs", active_preset, "console_index", rows=6)
                c_box.operator("batch_stl.clear_console", text="Clear Log", icon='TRASH')
            else:
                c_box.label(text="Select a preset to view logs.")

        layout.separator()

        p_box = layout.box()
        p_header = p_box.row()
        icon = 'TRIA_DOWN' if scene.batch_stl_ui_presets else 'TRIA_RIGHT'
        p_header.prop(scene, "batch_stl_ui_presets", text="", icon=icon, emboss=False)
        p_header.label(text="Presets", icon='PRESET')

        if active_preset: p_header.label(text=f"Last: {active_preset.last_export_time:.2f}s", icon='TIME')
        draw_inline_controls(p_header, "batch_stl.preset_actions", use_clipboard=True)

        if scene.batch_stl_ui_presets:
            p_box.template_list("BATCH_STL_UL_presets", "", scene, "batch_stl_presets", scene, "batch_stl_preset_index", rows=3)

        if not active_preset:
            layout.separator()
            layout.prop(scene, "batch_stl_verbose_console", toggle=True, icon='CONSOLE')
            return

        # OPTIMIZED: UI data explicitly provided by the background UI Timer Cache
        metrics = _ui_cache.get("metrics", {"total_collections": 0, "total_preset_combos": 0, "total_objects": 0})

        layout.separator(factor=0.5)
        m_box = layout.box()
        m_header = m_box.row()
        icon_m = 'TRIA_DOWN' if scene.batch_stl_ui_collections else 'TRIA_RIGHT'
        m_header.prop(scene, "batch_stl_ui_collections", text="", icon=icon_m, emboss=False)

        m_title = f"{active_preset.name} | {metrics['total_collections']} collections | {metrics['total_objects']} files map"
        m_header.label(text=m_title, icon='OUTLINER_COLLECTION')
        draw_inline_controls(m_header, "batch_stl.collection_actions", use_clipboard=True)

        if scene.batch_stl_ui_collections:
            m_box.template_list("BATCH_STL_UL_collections", "", active_preset, "collections", active_preset, "collection_index", rows=5)

        layout.separator()

        active_col = get_active_collection(active_preset)
        if active_col:

            o_box = layout.box()
            o_header = o_box.row()
            icon_o = 'TRIA_DOWN' if scene.batch_stl_ui_objects else 'TRIA_RIGHT'
            o_header.prop(scene, "batch_stl_ui_objects", text="", icon=icon_o, emboss=False)
            o_header.label(text=f"Objects in {active_col.collection_name or 'Collection'}", icon='OBJECT_DATA')

            if scene.batch_stl_ui_objects:
                o_box.template_list("BATCH_STL_UL_objects", "", active_col, "objects", active_col, "object_index", rows=5)

            active_obj = get_active_object(active_col)

            layout.separator()
            col_metrics = _ui_cache.get("active_col_metrics", {"c_name": "", "num_targets": 0, "total_inputs": 0, "num_combos": 0, "mapping_total_objects": 0})
            metric_str = f"{col_metrics['c_name']} | {col_metrics['num_targets']} targets | {col_metrics['total_inputs']} inputs | {col_metrics['mapping_total_objects']} output files"
            header = layout.row()
            header.label(text=metric_str, icon='MODIFIER')
            layout.separator()

            if active_col:
                # Collection Pinned (Shared Overrides)
                draw_overrides_table(layout, scene, active_col.nodegroups, True, "batch_stl_ui_global_ovr", f"Collection Overrides ({active_col.collection_name or 'Shared'})")

            layout.separator(factor=0.5)

            if active_obj:
                # Object Local
                draw_overrides_table(layout, scene, active_obj.nodegroups, False, "batch_stl_ui_local_ovr", f"Object Overrides ({active_obj.name})")

            layout.separator(factor=0.5)
            tip_box = layout.box()
            tip_header = tip_box.row()
            icon_tip = 'TRIA_DOWN' if scene.batch_stl_ui_tips else 'TRIA_RIGHT'
            tip_header.prop(scene, "batch_stl_ui_tips", text="", icon=icon_tip, emboss=False)
            tip_header.label(text="OVERRIDE INFO", icon='INFO')

            if scene.batch_stl_ui_tips:
                col = tip_box.column()
                col.label(text="Hierarchy: Collection > Object > NodeGroup > Node.", icon='BLANK1')
                col.label(text="For modifier targets, leave Node blank or set as <Modifier Interface>", icon='BLANK1')
                col.separator()

                col.label(text="Sweep Mode (Shift-Click '+' button to toggle):", icon='FILE_REFRESH')
                col.label(text="  • Floats/Ints: Define start, step, and count", icon='BLANK1')
                col.label(text="  • Menus/Bools: Auto-iterates all values", icon='BLANK1')
                col.label(text="  • Shift-Click when active to populate all sweep values", icon='BLANK1')
                col.separator()

                col.label(text="Export Tools (Per Value):", icon='BLANK1')
                col.label(text="  • Folder Icon: Save this value's exports into a subfolder", icon='FILE_FOLDER')
                col.label(text="  • Bookmark Icon: Append/Prepend a tag to filename", icon='BOOKMARKS')

                col.label(text="Tag Formatting:", icon='BLANK1')
                col.label(text="  • [ tag ] replaces input value, [ _tag ] appends, [ tag_ ] prepends", icon='BLANK1')

        layout.separator()
        t_box = layout.box()
        t_header = t_box.row(align=True)
        icon_t = 'TRIA_DOWN' if scene.batch_stl_show_tree else 'TRIA_RIGHT'
        t_header.prop(scene, "batch_stl_show_tree", text="", icon=icon_t, emboss=False)
        t_header.label(text="Export Structure & Files", icon='OUTLINER_OB_EMPTY')

        if scene.batch_stl_show_tree and active_preset:
            tree_dict, duplicates = _ui_cache.get("tree", ({}, set()))
            if duplicates:
                warn_box = t_box.box()
                warn_row = warn_box.row()
                warn_row.label(text=f"WARNING: {len(duplicates)} naming collisions detected! Files will be overwritten.", icon='ERROR')

            col = t_box.column(align=True)
            draw_tree_dict(col, tree_dict, duplicates=duplicates)

        layout.separator()
        layout.prop(scene, "batch_stl_verbose_console", toggle=True, icon='CONSOLE')


# ==============================================================================
# === [ 6. REGISTRATION & LIFECYCLE ] ===
# Every Blender Add-on must have a register() and unregister() function to hook
# its classes into Blender's core system when enabled, and remove them when disabled.
# ==============================================================================

# @persistent means this runs every time you open a .blend file, fixing any stuck UI states.
@persistent
def reset_batch_stl_state(scene):
    try:
        for p in bpy.context.scene.batch_stl_presets:
            p.is_exporting = False
            p.cancel_export = False
            p.export_progress = 0.0
            p.export_status = ""
    except Exception: pass
    if "--batch-stl-headless" not in sys.argv:
            if not bpy.app.timers.is_registered(rebuild_ui_cache_if_dirty):
                bpy.app.timers.register(rebuild_ui_cache_if_dirty)

# A list of everything Blender needs to load.
classes = (
    # 1. Properties
    BatchSTLLogLine,
    BatchSTLValue,
    BatchSTLInput,
    BatchSTLNode,
    BatchSTLNodeGroup,
    BatchSTLObject,
    BatchSTLCollection,
    BatchSTLExportPreset,

    # 2. UI Lists
    BATCH_STL_UL_presets,
    BATCH_STL_UL_collections,
    BATCH_STL_UL_objects,
    BATCH_STL_UL_console_logs,

    # 3. Operators
    BATCH_STL_OT_clear_console,
    BATCH_STL_OT_preset_actions,
    BATCH_STL_OT_collection_actions,
    BATCH_STL_OT_table_action,
    BATCH_STL_OT_toggle_dir_tree,
    BATCH_STL_OT_cancel_export,
    BATCH_STL_OT_export_presets_json,
    BATCH_STL_OT_import_presets_json,
    EXPORT_OT_batch_stl_multi,

    # 4. Panels
    VIEW3D_PT_batch_export_stl_multi,
)

def update_show_tree(self, context):
    if not self.batch_stl_show_tree:
        self.batch_stl_collapsed_dirs = "[]"

# Tells Blender this add-on exists and gives it the list of classes to initialize.
def register():
    for cls in classes:
        bpy.utils.register_class(cls)

    # Attach our custom variables directly to Blender's Scene object so they are saved per-file.
    bpy.types.Scene.batch_stl_root_dir = bpy.props.StringProperty(name="Root Export Dir", default="//", subtype="DIR_PATH", update=mark_dirty)
    bpy.types.Scene.batch_stl_presets = bpy.props.CollectionProperty(type=BatchSTLExportPreset)
    bpy.types.Scene.batch_stl_preset_index = bpy.props.IntProperty(name="Active Preset", default=0, update=mark_dirty)
    bpy.types.Scene.batch_stl_verbose_console = bpy.props.BoolProperty(name="Verbose Console Output", default=False)

    bpy.types.Scene.batch_stl_ui_presets = bpy.props.BoolProperty(default=True)
    bpy.types.Scene.batch_stl_ui_collections = bpy.props.BoolProperty(default=True)
    bpy.types.Scene.batch_stl_ui_objects = bpy.props.BoolProperty(default=True)
    bpy.types.Scene.batch_stl_ui_global_ovr = bpy.props.BoolProperty(default=True)
    bpy.types.Scene.batch_stl_ui_local_ovr = bpy.props.BoolProperty(default=True)
    bpy.types.Scene.batch_stl_ui_global_ovr_nested = bpy.props.BoolProperty(default=False)
    bpy.types.Scene.batch_stl_ui_local_ovr_nested = bpy.props.BoolProperty(default=False)
    bpy.types.Scene.batch_stl_ui_tips = bpy.props.BoolProperty(default=False)
    bpy.types.Scene.batch_stl_show_tree = bpy.props.BoolProperty(default=True, update=update_show_tree)
    bpy.types.Scene.batch_stl_show_console = bpy.props.BoolProperty(default=False)
    bpy.types.Scene.batch_stl_collapsed_dirs = bpy.props.StringProperty(default="[]")

    is_headless = "--batch-stl-headless" in sys.argv

    reset_batch_stl_state(None)
    if reset_batch_stl_state not in bpy.app.handlers.load_post:
        bpy.app.handlers.load_post.append(reset_batch_stl_state)

    # OPTIMIZED: Skip adding UI rebuilds and depsgraph handlers if we are running in headless export mode
    if not is_headless:
        if batch_stl_depsgraph_handler not in bpy.app.handlers.depsgraph_update_post:
            bpy.app.handlers.depsgraph_update_post.append(batch_stl_depsgraph_handler)

        if not bpy.app.timers.is_registered(rebuild_ui_cache_if_dirty):
            bpy.app.timers.register(rebuild_ui_cache_if_dirty)

# Triggers when the user un-checks the add-on box in preferences. It deletes all the data.
def unregister():
    if reset_batch_stl_state in bpy.app.handlers.load_post:
        bpy.app.handlers.load_post.remove(reset_batch_stl_state)

    if batch_stl_depsgraph_handler in bpy.app.handlers.depsgraph_update_post:
        bpy.app.handlers.depsgraph_update_post.remove(batch_stl_depsgraph_handler)

    if bpy.app.timers.is_registered(rebuild_ui_cache_if_dirty):
        bpy.app.timers.unregister(rebuild_ui_cache_if_dirty)

    for cls in reversed(classes):
        try: bpy.utils.unregister_class(cls)
        except RuntimeError: pass

    properties_to_remove = [
        "batch_stl_root_dir", "batch_stl_presets", "batch_stl_preset_index",
        "batch_stl_verbose_console", "batch_stl_ui_presets", "batch_stl_ui_collections", "batch_stl_ui_objects",
        "batch_stl_ui_global_ovr", "batch_stl_ui_local_ovr",
        "batch_stl_show_tree", "batch_stl_show_console", "batch_stl_collapsed_dirs",
        "batch_stl_ui_tips", "batch_stl_ui_global_ovr_nested", "batch_stl_ui_local_ovr_nested"
    ]

    for prop in properties_to_remove:
        if hasattr(bpy.types.Scene, prop):
            delattr(bpy.types.Scene, prop)


# ==============================================================================
# === [ 7. CLI EXECUTION BINDING ] ===
# This checks if the file is being run directly from the command line/terminal
# instead of being imported into Blender's UI normally.
# ==============================================================================

if __name__ == "__main__":
    # If the user typed "--batch-stl-headless" in the terminal window:
    if "--batch-stl-headless" in sys.argv:
        if not hasattr(bpy.types.Scene, "batch_stl_root_dir"):
            register()

        # Find the preset number from the command line arguments and launch the headless exporter.
        idx = sys.argv.index("--batch-stl-headless")
        p_index = int(sys.argv[idx + 1])
        run_headless_export(p_index)
    else:
        # Standard fallback just in case the file is executed without parameters
        if not hasattr(bpy.types.Scene, "batch_stl_root_dir"):
            register()
