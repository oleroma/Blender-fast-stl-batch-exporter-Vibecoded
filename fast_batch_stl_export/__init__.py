"""
Fast Batch STL Exporter
Architecture: Single-File Monolithic (Optimized for Agentic Environments)
"""

import os
import json
import time
import itertools
import threading
import queue
import subprocess
import tempfile
import sys
import struct
import numpy as np

import bpy
from bpy_extras.io_utils import ExportHelper, ImportHelper
from bpy.app.handlers import persistent


# ==============================================================================
# === [ 1. GLOBALS & STATE ] ===
# ==============================================================================

_clipboard = {
    "preset": None,
    "mapping": None,
    "override": None,
    "input": None
}


# ==============================================================================
# === [ 2. CORE LOGIC & ENGINE ] ===
# ==============================================================================

# --- STATE-HASH & OVERRIDE LOGIC ---
def is_collection_excluded(context, target_collection):
    def traverse(layer_collection):
        if layer_collection.collection == target_collection:
            return layer_collection.exclude
        for child in layer_collection.children:
            result = traverse(child)
            if result is not None:
                return result
        return None
    result = traverse(context.view_layer.layer_collection)
    return result if result is not None else True

def get_modifier_socket_identifier(node_group, socket_name):
    if hasattr(node_group, "interface"):
        for item in node_group.interface.items_tree:
            if getattr(item, "item_type", "") == 'SOCKET' and item.name == socket_name:
                return item.identifier
    else:
        for inp in node_group.inputs:
            if inp.name == socket_name:
                return inp.identifier
    return None

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

def get_modifier_input(mod, ident):
    try:
        if mod.is_property_set(ident): return mod[ident], True
    except Exception: pass
    if hasattr(mod, "properties") and hasattr(mod.properties, "inputs"):
        prop_input = getattr(mod.properties.inputs, ident, None)
        if prop_input is not None and hasattr(prop_input, "value"):
            return prop_input.value, True
    return None, False

def set_modifier_input(mod, ident, value):
    try:
        mod[ident] = value
        return
    except (TypeError, Exception): pass
    if hasattr(mod, "properties") and hasattr(mod.properties, "inputs"):
        prop_input = getattr(mod.properties.inputs, ident, None)
        if prop_input is not None and hasattr(prop_input, "value"):
            prop_input.value = value

def unset_modifier_input(mod, ident, default_val):
    try:
        mod.property_unset(ident)
        return
    except Exception: pass
    if hasattr(mod, "properties") and hasattr(mod.properties, "inputs"):
        prop_input = getattr(mod.properties.inputs, ident, None)
        if prop_input is not None and hasattr(prop_input, "value") and default_val is not None:
            prop_input.value = default_val

def get_input_value(inp):
    if inp.override_type == 'BOOLEAN': return inp.value_bool
    elif inp.override_type == 'INT': return inp.value_int
    elif inp.override_type == 'FLOAT': return inp.value_float
    elif inp.override_type == 'STRING': return inp.value_string
    elif inp.override_type == 'MENU': return inp.value_menu
    return None

def get_override_signature(overrides):
    sig = []
    for ovr in overrides:
        target = ovr.override_target
        pg_name = ovr.parent_group_ptr.name if ovr.parent_group_ptr else ""
        node_name = ovr.node_name
        inputs_sig = []
        for inp in ovr.inputs:
            inputs_sig.append((inp.input_name, inp.override_type, get_input_value(inp)))
        sig.append((target, pg_name, node_name, tuple(inputs_sig)))
    return tuple(sig)

# --- PERMUTATION ENGINE ---
class MockInput:
    def __init__(self, base_inp, override_val):
        self.base_inp = base_inp
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

def parse_sweep_values(ovr, inp):
    if inp.override_type == 'BOOLEAN': return [True, False]
    elif inp.override_type == 'STRING':
        if not inp.sweep_range: return [""]
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
            except ValueError: vals.append(0 if inp.override_type == 'INT' else 0.0)
        else: vals.append(0 if inp.override_type == 'INT' else 0.0)
        return vals
    elif inp.override_type == 'MENU':
        items = []
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
                for val in parse_sweep_values(ovr, inp):
                    mock_inp = MockInput(inp, val)
                    if val not in value_groups: value_groups[val] = []
                    value_groups[val].append((ovr, mock_inp))
            else:
                val = get_input_value(inp)
                if val not in value_groups: value_groups[val] = []
                value_groups[val].append((ovr, inp))
        pools.append(list(value_groups.values()))

    if not pools: return [[]]
    combinations = list(itertools.product(*pools))

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

def apply_overrides(overrides, target_objects, dry_run=False):
    global_states = []
    mod_states = []
    trees_to_update = set()

    for override in overrides:
        if override.override_target == 'NODE' and override.parent_group_ptr and override.node_name:
            parent_tree = override.parent_group_ptr
            n_name = override.node_name.split(" [")[0].strip()
            target_node = parent_tree.nodes.get(n_name)
            if not target_node: continue
            for inp in override.inputs:
                socket = target_node.inputs.get(inp.input_name)
                if not socket: continue
                link_from = socket.links[0].from_socket if socket.is_linked else None
                global_states.append(('SOCKET', socket, socket.default_value, link_from, parent_tree))
                if not dry_run:
                    if socket.is_linked: parent_tree.links.remove(socket.links[0])
                    val = get_input_value(inp)
                    if val is not None: socket.default_value = val
            if not dry_run: trees_to_update.add(parent_tree)

        elif override.override_target == 'MODIFIER' and override.parent_group_ptr:
            for inp in override.inputs:
                ident = get_modifier_socket_identifier(override.parent_group_ptr, inp.input_name)
                if not ident: continue
                default_val = get_modifier_socket_default(override.parent_group_ptr, inp.input_name)
                val = get_input_value(inp)
                if val is None: continue
                for obj in target_objects:
                    for mod in obj.modifiers:
                        if mod.type == 'NODES' and mod.node_group == override.parent_group_ptr:
                            orig_val, is_set = get_modifier_input(mod, ident)
                            mod_states.append((mod, ident, is_set, orig_val, default_val))
                            if not dry_run: set_modifier_input(mod, ident, val)

    if not dry_run:
        for tree in trees_to_update: tree.update_tag()
        for obj in target_objects: obj.update_tag()
    return global_states, mod_states

def revert_overrides(global_states, mod_states, target_objects):
    for mod, ident, is_set, orig_val, default_val in mod_states:
        try:
            if is_set and orig_val is not None: set_modifier_input(mod, ident, orig_val)
            else: unset_modifier_input(mod, ident, default_val)
        except ReferenceError: pass
    trees_to_update = set()
    for state in global_states:
        if state[0] == 'SOCKET':
            _, socket, original_val, link_from, parent_tree = state
            try:
                socket.default_value = original_val
                if link_from:
                    already_linked = any(l.from_socket == link_from for l in socket.links)
                    if not already_linked: parent_tree.links.new(link_from, socket)
                trees_to_update.add(parent_tree)
            except Exception: pass
    for tree in trees_to_update:
        try: tree.update_tag()
        except ReferenceError: pass
    for obj in target_objects:
        try: obj.update_tag()
        except ReferenceError: pass

def get_active_preset(scene):
    presets = scene.batch_stl_presets
    index = scene.batch_stl_preset_index
    if presets and 0 <= index < len(presets): return presets[index]
    return None

def get_active_mapping(preset):
    if preset and preset.mappings and 0 <= preset.mapping_index < len(preset.mappings):
        return preset.mappings[preset.mapping_index]
    return None

def log_to_console(preset, text):
    log = preset.console_logs.add()
    log.text = text
    preset.console_index = len(preset.console_logs) - 1
    if len(preset.console_logs) > 300:
        preset.console_logs.remove(0)
        preset.console_index = len(preset.console_logs) - 1

# --- BINARY STL WRITER ---
def write_fast_binary_stl(filepath, mesh, matrix_world, verbose=False):
    t_start = time.perf_counter()
    mesh.calc_loop_triangles()
    num_tris = len(mesh.loop_triangles)
    if num_tris == 0: return

    verts = np.empty((len(mesh.vertices), 3), dtype=np.float32)
    mesh.vertices.foreach_get("co", verts.ravel())
    mat = np.array(matrix_world, dtype=np.float32)
    verts_vec4 = np.c_[verts, np.ones(len(verts), dtype=np.float32)]
    verts = np.dot(verts_vec4, mat.T)[:, :3]

    tri_verts = np.empty((num_tris, 3), dtype=np.int32)
    mesh.loop_triangles.foreach_get("vertices", tri_verts.ravel())

    tri_normals = np.empty((num_tris, 3), dtype=np.float32)
    mesh.loop_triangles.foreach_get("normal", tri_normals.ravel())

    mat_norm = np.array(matrix_world.to_3x3().inverted_safe().transposed(), dtype=np.float32)
    tri_normals = np.dot(tri_normals, mat_norm.T)
    norms = np.linalg.norm(tri_normals, axis=1, keepdims=True)
    norms[norms == 0] = 1.0
    tri_normals /= norms

    stl_dtype = np.dtype([
        ('normals', np.float32, (3,)), ('v0', np.float32, (3,)),
        ('v1', np.float32, (3,)), ('v2', np.float32, (3,)),
        ('attr', np.uint16)
    ])
    data = np.zeros(num_tris, dtype=stl_dtype)
    data['normals'] = tri_normals
    data['v0'] = verts[tri_verts[:, 0]]
    data['v1'] = verts[tri_verts[:, 1]]
    data['v2'] = verts[tri_verts[:, 2]]

    t_format = time.perf_counter()
    if verbose:
        mb_size = (84 + (num_tris * 50)) / (1024 * 1024)
        print(f"  │         │    ├─ STL Memory Map: {num_tris} Tris | {mb_size:.2f} MB | Matrix T-Form: {t_format-t_start:.4f}s")

    with open(filepath, 'wb') as f:
        f.write(b'Batch STL Fast Export' + b'\x00' * 59)
        f.write(struct.pack('<I', num_tris))
        f.write(data.tobytes())

    t_write = time.perf_counter()
    if verbose:
        print(f"  │         │    ├─ Disk I/O Write: {t_write-t_format:.4f}s | Path: {os.path.basename(filepath)}")

# --- JSON UTILS ---
def copy_override_to_dict(o):
    return {
        "override_target": o.override_target,
        "parent_group": o.parent_group_ptr.name if o.parent_group_ptr else "",
        "node_name": o.node_name,
        "inputs": [{
            "input_name": i.input_name, "override_type": i.override_type,
            "value_bool": i.value_bool, "value_int": i.value_int, "value_float": i.value_float,
            "value_string": i.value_string, "value_menu": i.value_menu,
            "use_tag": i.use_tag, "tag": i.tag, "use_dir": i.use_dir,
            "use_sweep": getattr(i, "use_sweep", False), "sweep_range": getattr(i, "sweep_range", "")
        } for i in o.inputs]
    }

def paste_override_from_dict(new_o, data):
    new_o.override_target = data["override_target"]
    pg_name = data["parent_group"]
    new_o.parent_group_ptr = bpy.data.node_groups.get(pg_name) if pg_name else None
    new_o.node_name = data["node_name"]
    for i_data in data["inputs"]:
        new_i = new_o.inputs.add()
        for k, v in i_data.items(): setattr(new_i, k, v)

def copy_mapping_to_dict(m):
    return {
        "collection_name": m.collection_ptr.name if m.collection_ptr else "",
        "use_tag": m.use_tag, "tag": m.tag, "sub_path": m.sub_path,
        "use_filter": getattr(m, "use_filter", False),
        "excluded_objects": [e.name for e in m.excluded_objects],
        "overrides": [copy_override_to_dict(o) for o in m.node_overrides]
    }

def paste_mapping_from_dict(new_m, data):
    c_name = data.get("collection_name", "")
    new_m.collection_ptr = bpy.data.collections.get(c_name) if c_name else None
    new_m.use_tag = data.get("use_tag", True)
    new_m.tag = data.get("tag", "")
    new_m.sub_path = data.get("sub_path", "")
    new_m.use_filter = data.get("use_filter", False)
    for obj_name in data.get("excluded_objects", []):
        new_m.excluded_objects.add().name = obj_name
    for o_data in data.get("overrides", []):
        paste_override_from_dict(new_m.node_overrides.add(), o_data)

def copy_preset_to_dict(src):
    return {
        "name": src.name, "preset_prefix": src.preset_prefix,
        "pinned_overrides": [copy_override_to_dict(o) for o in src.pinned_overrides],
        "mappings": [copy_mapping_to_dict(m) for m in src.mappings]
    }

def paste_preset_from_dict(new_p, data):
    new_p.name = data.get("name", "Imported Preset")
    new_p.preset_prefix = data.get("preset_prefix", "")
    for o_data in data.get("pinned_overrides", []): paste_override_from_dict(new_p.pinned_overrides.add(), o_data)
    for m_data in data.get("mappings", []): paste_mapping_from_dict(new_p.mappings.add(), m_data)

# --- TREE VISUALIZER LOGIC ---
def build_tree_dict(scene, preset):
    root_name = bpy.path.abspath(scene.batch_stl_root_dir) if scene.batch_stl_root_dir else "//"
    tree = {"children": {}, "files": [], "text": root_name, "ptr": scene, "prop": "batch_stl_root_dir", "suffix": ""}
    all_filepaths = set()
    duplicates = set()

    current_root = tree
    if preset.preset_prefix:
        key = preset.preset_prefix
        if key not in current_root["children"]:
            current_root["children"][key] = {"children": {}, "files": [], "text": key, "ptr": preset, "prop": "preset_prefix", "suffix": ""}
        current_root = current_root["children"][key]

    for mapping in preset.mappings:
        mapping_root = current_root
        mapping_root_path = []
        if mapping.sub_path:
            parts = mapping.sub_path.replace('\\', '/').split('/')
            for i, part in enumerate(parts):
                if part:
                    if part not in mapping_root["children"]:
                        ptr = mapping if i == len(parts) - 1 else None
                        prop = "sub_path" if i == len(parts) - 1 else ""
                        mapping_root["children"][part] = {"children": {}, "files": [], "text": part, "ptr": ptr, "prop": prop, "suffix": ""}
                    mapping_root = mapping_root["children"][part]
                    mapping_root_path.append(part)

        all_overrides = list(preset.pinned_overrides) + list(mapping.node_overrides)
        combinations = generate_override_combinations(all_overrides)
        valid_objs = []
        if mapping.collection_ptr:
            excluded_names = {e.name for e in mapping.excluded_objects} if getattr(mapping, "use_filter", False) else set()
            for obj in mapping.collection_ptr.all_objects:
                if obj.type in {"MESH", "CURVE", "SURFACE", "META", "FONT"} and not obj.hide_get() and not obj.hide_viewport:
                    if obj.name not in excluded_names: valid_objs.append(obj)

        if not valid_objs: continue
        if not combinations: combinations = [[]]

        for combo in combinations:
            combo_root = mapping_root
            combo_suffix = ""
            combo_subpath = []
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

                    if getattr(inp, "use_tag", False): combo_suffix += f"_{naming_str}"
                    if getattr(inp, "use_dir", False):
                        if naming_str not in combo_root["children"]:
                            base_inp = getattr(inp, "base_inp", inp)
                            combo_root["children"][naming_str] = {
                                "children": {}, "files": [], 
                                "text": val_str, "ptr": base_inp, "prop": "tag", "suffix": ""
                            }
                        combo_root = combo_root["children"][naming_str]
                        combo_subpath.append(naming_str)
                    processed_params.add(param_key)

            base_tag = mapping.tag if getattr(mapping, "use_tag", False) and mapping.tag else ""
            final_tag = base_tag + combo_suffix

            full_dir_parts = [preset.preset_prefix] if preset.preset_prefix else []
            full_dir_parts.extend(mapping_root_path)
            full_dir_parts.extend(combo_subpath)
            dir_path_str = os.path.normpath(os.path.join(root_name, *full_dir_parts))

            for obj in valid_objs:
                filename = f"{bpy.path.clean_name(obj.name)}{final_tag}.stl"
                full_path = os.path.join(dir_path_str, filename)
                if full_path in all_filepaths: duplicates.add(full_path)
                else: all_filepaths.add(full_path)

                if getattr(mapping, "use_tag", False):
                    file_node = {
                        "text": bpy.path.clean_name(obj.name), 
                        "ptr": mapping, 
                        "prop": "tag", 
                        "suffix": combo_suffix + ".stl"
                    }
                else:
                    file_node = {
                        "text": filename,
                        "ptr": None,
                        "prop": "",
                        "suffix": ""
                    }
                combo_root["files"].append(file_node)

    return {"root": tree}, duplicates

def get_tree_lines(tree_dict):
    def traverse(d_node, prefix=""):
        lines = []
        dirs = list(d_node.get("children", {}).values())
        files = d_node.get("files", [])
        total_items = len(dirs) + len(files)
        current_item = 0
        
        for child in dirs:
            current_item += 1
            is_last = (current_item == total_items)
            connector = "└── " if is_last else "├── "
            
            line_data = child.copy()
            line_data["prefix"] = prefix + connector
            line_data["icon"] = 'FILE_FOLDER'
            lines.append(line_data)
            
            extension = "    " if is_last else "│   "
            lines.extend(traverse(child, prefix + extension))
            
        for f in files:
            current_item += 1
            is_last = (current_item == total_items)
            connector = "└── " if is_last else "├── "
            
            line_data = f.copy()
            line_data["prefix"] = prefix + connector
            line_data["icon"] = 'MESH_DATA'
            lines.append(line_data)
            
        return lines

    result_lines = []
    root_node = tree_dict.get("root", {})
    if root_node:
        root_data = root_node.copy()
        root_data["prefix"] = ""
        root_data["icon"] = 'FILE_FOLDER'
        result_lines.append(root_data)
        result_lines.extend(traverse(root_node, ""))
    return result_lines

# --- HEADLESS EXPORT EXECUTION ROUTINE ---
def run_headless_export(preset_index):
    total_time_start = time.perf_counter()
    scene = bpy.context.scene

    if preset_index < 0 or preset_index >= len(scene.batch_stl_presets):
        print("ERROR: Invalid preset index")
        sys.exit(1)

    preset = scene.batch_stl_presets[preset_index]
    root_dir = bpy.path.abspath(scene.batch_stl_root_dir)
    if preset.preset_prefix:
        root_dir = os.path.normpath(os.path.join(root_dir, preset.preset_prefix))

    print(f"\n=== STARTING HEADLESS ISOLATED EXPORT: {preset.name} ===")
    print("\n  [Phase 0] Aggressive Global Depsgraph Culling...")
    t_phase0_start = time.perf_counter()

    active_export_objects = set()
    for mapping in preset.mappings:
        if mapping.collection_ptr and not is_collection_excluded(bpy.context, mapping.collection_ptr):
            active_export_objects.update(mapping.collection_ptr.all_objects)

    muted_count = 0
    for obj in bpy.context.view_layer.objects:
        if obj not in active_export_objects:
            for mod in getattr(obj, 'modifiers', []):
                if mod.type == 'NODES' and mod.show_viewport:
                    mod.show_viewport = False
                    muted_count += 1

    print(f"    ├─ Permanently Muted {muted_count} unused GN modifiers to accelerate Graph evaluation in {time.perf_counter() - t_phase0_start:.4f}s")

    execution_batches = {}
    sig_pinned = get_override_signature(preset.pinned_overrides)

    for mapping in preset.mappings:
        if not mapping.collection_ptr: continue
        if is_collection_excluded(bpy.context, mapping.collection_ptr):
            print(f"  ├─ Skipping '{mapping.collection_ptr.name}' (Excluded from View Layer)")
            continue
        sig_local = get_override_signature(mapping.node_overrides)
        full_sig = sig_pinned + sig_local
        if full_sig not in execution_batches: execution_batches[full_sig] = []
        execution_batches[full_sig].append(mapping)

    if not execution_batches:
        print("  └─ No active collections to export.")
        print("BATCH_STL_DONE", flush=True)
        sys.exit(0)

    total_operations = 0
    for signature, mappings_in_batch in execution_batches.items():
        first_mapping = mappings_in_batch[0]
        all_overrides = list(preset.pinned_overrides) + list(first_mapping.node_overrides)
        combinations = generate_override_combinations(all_overrides)
        batch_obj_count = 0
        for m in mappings_in_batch:
            excluded_names = {e.name for e in m.excluded_objects} if getattr(m, "use_filter", False) else set()
            for obj in m.collection_ptr.all_objects:
                if obj.type in {"MESH", "CURVE", "SURFACE", "META", "FONT"} and not obj.hide_get() and not obj.hide_viewport:
                    if obj.name not in excluded_names:
                        batch_obj_count += 1
        total_operations += (batch_obj_count * len(combinations))

    print(f"BATCH_STL_TOTAL:{total_operations}", flush=True)

    current_op_step = 0
    batch_counter = 1

    for signature, mappings_in_batch in execution_batches.items():
        t_batch_start = time.perf_counter()
        first_mapping = mappings_in_batch[0]
        all_overrides = list(preset.pinned_overrides) + list(first_mapping.node_overrides)
        combinations = generate_override_combinations(all_overrides)

        is_clean_batch = len(first_mapping.node_overrides) == 0
        batch_type = "Clean (Pinned Only)" if is_clean_batch else f"Dirty ({len(first_mapping.node_overrides)} Local Overrides)"
        collection_names = [f"{m.collection_ptr.name} [{m.tag}]" for m in mappings_in_batch]
        print(f"  ├─ Batch {batch_counter}/{len(execution_batches)} [{batch_type}]: Processing {len(collection_names)} mapped instances with {len(combinations)} permutation(s)")

        batch_objects = set()
        for m in mappings_in_batch: batch_objects.update(m.collection_ptr.all_objects)

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
                    if getattr(inp, "use_tag", False): combo_suffix += f"_{naming_str}"
                    if getattr(inp, "use_dir", False): combo_subpath = os.path.join(combo_subpath, naming_str)
                    processed_params.add(param_key)

            t_ovr = time.perf_counter()
            global_states, mod_states = [], []

            try:
                active_overrides = reconstruct_overrides_for_combo(combo)
                global_states, mod_states = apply_overrides(active_overrides, batch_objects)
                bpy.context.view_layer.update()
                depsgraph = bpy.context.evaluated_depsgraph_get()
                print(f"  │    │    ├─ Applied & Synced Graph: {time.perf_counter() - t_ovr:.4f}s")

                for mapping in mappings_in_batch:
                    out_dir = os.path.normpath(os.path.join(root_dir, mapping.sub_path, combo_subpath))
                    os.makedirs(out_dir, exist_ok=True)
                    print(f"  │    │    ├─ Exporting: {mapping.collection_ptr.name}{' ['+mapping.tag+']' if mapping.tag else ''}")

                    excluded_names = {e.name for e in mapping.excluded_objects} if getattr(mapping, "use_filter", False) else set()
                    for obj in mapping.collection_ptr.all_objects:
                        if obj.type in {"MESH", "CURVE", "SURFACE", "META", "FONT"} and not obj.hide_get() and not obj.hide_viewport:
                            if obj.name in excluded_names: continue
                            t_eval = time.perf_counter()
                            obj_eval = obj.evaluated_get(depsgraph)
                            try: mesh = obj_eval.to_mesh()
                            except RuntimeError: mesh = None
                            print(f"  │         ├─ Evaluated Mesh [{obj.name}]: {time.perf_counter() - t_eval:.4f}s")

                            if mesh:
                                base_tag = mapping.tag if getattr(mapping, "use_tag", False) and mapping.tag else ""
                                final_tag = base_tag + combo_suffix
                                filepath = os.path.join(out_dir, f"{bpy.path.clean_name(obj.name)}{final_tag}.stl")
                                write_fast_binary_stl(filepath, mesh, obj.matrix_world, verbose=True)
                                obj_eval.to_mesh_clear()
                                current_op_step += 1
                                print(f"BATCH_STL_PROGRESS:{current_op_step}", flush=True)

            finally:
                t_rev = time.perf_counter()
                revert_overrides(global_states, mod_states, batch_objects)
                bpy.context.view_layer.update()
                print(f"  │    │    ├─ Reverted permutation overrides: {time.perf_counter() - t_rev:.4f}s")

            t_purge = time.perf_counter()
            bpy.ops.outliner.orphans_purge(do_local_ids=True, do_linked_ids=True, do_recursive=True)
            print(f"  │    │    ├─ RAM Purge: {time.perf_counter() - t_purge:.4f}s")

        print(f"  │    => Batch Iteration Total Time: {time.perf_counter() - t_batch_start:.4f}s\n")
        batch_counter += 1

    print(f"\n=== HEADLESS EXPORT COMPLETE: {time.perf_counter() - total_time_start:.4f}s Subprocess Execution ===\n")
    print("BATCH_STL_DONE", flush=True)
    sys.exit(0)


# ==============================================================================
# === [ 3. PROPERTY GROUPS ] ===
# ==============================================================================

def on_input_name_update(self, context):
    try:
        ovr = None
        for p in context.scene.batch_stl_presets:
            for o in p.pinned_overrides:
                if self in o.inputs.values():
                    ovr = o; break
            if ovr: break
            for m in p.mappings:
                for o in m.node_overrides:
                    if self in o.inputs.values():
                        ovr = o; break
                if ovr: break
            if ovr: break

        if ovr:
            count = sum(1 for i in ovr.inputs if i.input_name == self.input_name)
            if count > 1:
                for i in ovr.inputs:
                    if i.input_name == self.input_name:
                        i.use_sweep = False

        if ovr and ovr.parent_group_ptr:
            if ovr.override_target == 'NODE' and ovr.node_name:
                n_name = ovr.node_name.split(" [")[0].strip()
                node = ovr.parent_group_ptr.nodes.get(n_name)
                if node and self.input_name in node.inputs:
                    s_type = node.inputs[self.input_name].type
                    if s_type in ['VALUE', 'FLOAT']: self.override_type = 'FLOAT'
                    elif s_type == 'INT': self.override_type = 'INT'
                    elif s_type == 'BOOLEAN': self.override_type = 'BOOLEAN'
                    elif s_type == 'STRING': self.override_type = 'STRING'
                    elif s_type == 'MENU': self.override_type = 'MENU'
            elif ovr.override_target == 'MODIFIER':
                if hasattr(ovr.parent_group_ptr, "interface"):
                    item = ovr.parent_group_ptr.interface.items_tree.get(self.input_name)
                    if item:
                        s_type = getattr(item, "socket_type", "")
                        if 'Float' in s_type: self.override_type = 'FLOAT'
                        elif 'Int' in s_type: self.override_type = 'INT'
                        elif 'Bool' in s_type: self.override_type = 'BOOLEAN'
                        elif 'String' in s_type: self.override_type = 'STRING'
                        elif 'Menu' in s_type: self.override_type = 'MENU'
                else:
                    inp = ovr.parent_group_ptr.inputs.get(self.input_name)
                    if inp:
                        s_type = inp.type
                        if s_type in ['VALUE', 'FLOAT']: self.override_type = 'FLOAT'
                        elif s_type == 'INT': self.override_type = 'INT'
                        elif s_type == 'BOOLEAN': self.override_type = 'BOOLEAN'
                        elif s_type == 'STRING': self.override_type = 'STRING'
                        elif s_type == 'MENU': self.override_type = 'MENU'
    except Exception: pass

def search_target_node_cb(self, context, edit_text):
    if not self.parent_group_ptr: return []
    res = []
    for node in self.parent_group_ptr.nodes:
        name = node.name
        if node.type == 'GROUP' and getattr(node, "node_tree", None):
            val = f"{name} [{node.node_tree.name}]"
        else:
            val = f"{name} [{node.type}]"
        if not edit_text or edit_text.lower() in val.lower():
            res.append(val)
    return res

def search_menu_items_cb(self, context, edit_text):
    try:
        ovr = None
        for p in context.scene.batch_stl_presets:
            for o in p.pinned_overrides:
                if self in o.inputs.values():
                    ovr = o; break
            if ovr: break
            for m in p.mappings:
                for o in m.node_overrides:
                    if self in o.inputs.values():
                        ovr = o; break
                if ovr: break
            if ovr: break

        if not ovr: return []

        items = []
        if ovr.parent_group_ptr:
            if ovr.override_target == 'MODIFIER':
                for node in ovr.parent_group_ptr.nodes:
                    if node.type == 'MENU_SWITCH' and hasattr(node, 'enum_items'):
                        for sock in node.inputs:
                            for link in sock.links:
                                if link.from_node.type == 'GROUP_INPUT' and link.from_socket.name == self.input_name:
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
                                        if link.from_node.type == 'GROUP_INPUT' and link.from_socket.name == self.input_name:
                                            items = [getattr(item, 'identifier', getattr(item, 'name', '')) for item in inner_node.enum_items]
                                            break
                                    if items: break
                            if items: break

        if not edit_text: return items
        return [item for item in items if edit_text.lower() in item.lower()]
    except Exception:
        return []

class BatchSTLLogLine(bpy.types.PropertyGroup):
    text: bpy.props.StringProperty()

class BatchSTLNodeInput(bpy.types.PropertyGroup):
    input_name: bpy.props.StringProperty(name="Input", default="", update=on_input_name_update, description="Name of the node group input socket or modifier property to override")
    override_type: bpy.props.EnumProperty(
        name="Type",
        items=(
            ('BOOLEAN', "Bool", "Boolean data type"),
            ('INT', "Int", "Integer data type"),
            ('FLOAT', "Float", "Floating-point data type"),
            ('STRING', "Str", "String text data type"),
            ('MENU', "Menu", "Menu or Enum data type")
        ),
        default='BOOLEAN',
        description="Data type of the override value"
    )
    value_bool: bpy.props.BoolProperty(name="Value", default=True, description="Boolean override value to apply")
    value_int: bpy.props.IntProperty(name="Value", default=0, description="Integer override value to apply")
    value_float: bpy.props.FloatProperty(name="Value", default=0.0, description="Float override value to apply")
    value_string: bpy.props.StringProperty(name="Value", default="", description="String override value to apply")
    value_menu: bpy.props.StringProperty(
        name="Value", default="",
        description="Menu or Enum override value to apply",
        search=search_menu_items_cb
    )
    use_tag: bpy.props.BoolProperty(name="Use Tag", default=False, description="Append tag to filename for this permutation")
    tag: bpy.props.StringProperty(name="Tag", default="", description="Custom string for naming or folder creation")
    use_dir: bpy.props.BoolProperty(name="Use Dir", default=False, description="Create a sub-directory for this specific input permutation")
    use_sweep: bpy.props.BoolProperty(name="Sweep", default=False, description="Enable automatic parameter sweeping across multiple states")
    sweep_range: bpy.props.StringProperty(name="Sweep Range", default="", description="For Int/Float: 'start step count' | For String: 'item1, item2'")

class BatchSTLNodeOverride(bpy.types.PropertyGroup):
    override_target: bpy.props.EnumProperty(
        name="Target",
        items=(('NODE', "Node", "Target an internal node"), ('MODIFIER', "Modifier", "Target a socket directly on the Modifier interface")),
        default='NODE', description="Target type to override"
    )
    parent_group_ptr: bpy.props.PointerProperty(type=bpy.types.NodeTree, name="Group")
    node_name: bpy.props.StringProperty(
        name="Node",
        default="",
        description="Target node name. Searchable list includes base group names.",
        search=search_target_node_cb
    )
    inputs: bpy.props.CollectionProperty(type=BatchSTLNodeInput, description="List of specific input sockets to override")

class BatchSTLExcludedObject(bpy.types.PropertyGroup):
    name: bpy.props.StringProperty(description="Name of the object to exclude from export")

class BatchSTLExportItem(bpy.types.PropertyGroup):
    collection_ptr: bpy.props.PointerProperty(type=bpy.types.Collection, name="Collection")
    use_tag: bpy.props.BoolProperty(name="Use Tag", default=True)
    tag: bpy.props.StringProperty(name="Tag", default="")
    sub_path: bpy.props.StringProperty(name="Sub-folder", default="")
    node_overrides: bpy.props.CollectionProperty(type=BatchSTLNodeOverride)
    use_filter: bpy.props.BoolProperty(name="Filter Objects", default=False)
    excluded_objects: bpy.props.CollectionProperty(type=BatchSTLExcludedObject)

class BatchSTLExportPreset(bpy.types.PropertyGroup):
    name: bpy.props.StringProperty(name="Preset Name", default="New Preset")
    preset_prefix: bpy.props.StringProperty(name="Preset Root Directory", default="")
    last_export_time: bpy.props.FloatProperty(name="Last Export Time", default=0.0)
    pinned_overrides: bpy.props.CollectionProperty(type=BatchSTLNodeOverride)
    mappings: bpy.props.CollectionProperty(type=BatchSTLExportItem)
    mapping_index: bpy.props.IntProperty(name="Mapping Index", default=0)
    is_exporting: bpy.props.BoolProperty(default=False)
    cancel_export: bpy.props.BoolProperty(default=False)
    export_progress: bpy.props.FloatProperty(name="Progress", default=0.0, min=0.0, max=1.0)
    export_status: bpy.props.StringProperty(default="")
    console_logs: bpy.props.CollectionProperty(type=BatchSTLLogLine)
    console_index: bpy.props.IntProperty(default=0)


# ==============================================================================
# === [ 4. OPERATORS ] ===
# ==============================================================================

class BATCH_STL_OT_export_presets_json(bpy.types.Operator, ExportHelper):
    bl_idname = "batch_stl.export_presets_json"
    bl_label = "Export JSON"
    bl_description = "Export all current batch export presets to a JSON configuration file"
    filename_ext = ".json"
    filter_glob: bpy.props.StringProperty(default="*.json", options={'HIDDEN'})

    def execute(self, context):
        with open(self.filepath, 'w') as f: json.dump([copy_preset_to_dict(p) for p in context.scene.batch_stl_presets], f, indent=4)
        return {'FINISHED'}

class BATCH_STL_OT_import_presets_json(bpy.types.Operator, ImportHelper):
    bl_idname = "batch_stl.import_presets_json"
    bl_label = "Import JSON"
    bl_options = {'REGISTER', 'UNDO'}
    bl_description = "Import batch export presets from a JSON configuration file"
    filename_ext = ".json"
    filter_glob: bpy.props.StringProperty(default="*.json", options={'HIDDEN'})

    def execute(self, context):
        with open(self.filepath, 'r') as f: data = json.load(f)
        for p_data in data: paste_preset_from_dict(context.scene.batch_stl_presets.add(), p_data)
        return {'FINISHED'}

class BATCH_STL_OT_clear_console(bpy.types.Operator):
    bl_idname = "batch_stl.clear_console"
    bl_label = "Clear Console"
    bl_description = "Clear the integrated console output log for the active preset"

    def execute(self, context):
        preset = get_active_preset(context.scene)
        if preset: preset.console_logs.clear()
        return {'FINISHED'}

class BATCH_STL_OT_preset_actions(bpy.types.Operator):
    bl_idname = "batch_stl.preset_actions"
    bl_label = "Preset Actions"
    bl_options = {'REGISTER', 'INTERNAL'}
    action: bpy.props.EnumProperty(items=(('ADD', "", ""), ('REMOVE', "", ""), ('UP', "", ""), ('DOWN', "", ""), ('COPY', "", ""), ('PASTE', "", "")))
    shift_pressed: bpy.props.BoolProperty(options={'HIDDEN', 'SKIP_SAVE'}, default=False)

    def invoke(self, context, event):
        self.shift_pressed = event.shift
        return self.execute(context)

    def execute(self, context):
        lst = context.scene.batch_stl_presets
        idx = context.scene.batch_stl_preset_index
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
        return {'FINISHED'}

class BATCH_STL_OT_mapping_actions(bpy.types.Operator):
    bl_idname = "batch_stl.mapping_actions"
    bl_label = "Mapping Actions"
    bl_options = {'REGISTER', 'INTERNAL'}
    action: bpy.props.EnumProperty(items=(('ADD', "", ""), ('REMOVE', "", ""), ('UP', "", ""), ('DOWN', "", ""), ('COPY', "", ""), ('PASTE', "", "")))
    shift_pressed: bpy.props.BoolProperty(options={'HIDDEN', 'SKIP_SAVE'}, default=False)

    def invoke(self, context, event):
        self.shift_pressed = event.shift
        return self.execute(context)

    def execute(self, context):
        preset = get_active_preset(context.scene)
        if not preset: return {'CANCELLED'}
        lst, idx = preset.mappings, preset.mapping_index
        if self.action == 'ADD': lst.add(); preset.mapping_index = len(lst) - 1
        elif self.action == 'REMOVE' and lst: lst.remove(idx); preset.mapping_index = max(0, idx - 1)
        elif self.action == 'UP' and idx > 0:
            lst.move(idx, 0 if self.shift_pressed else idx - 1)
            preset.mapping_index = 0 if self.shift_pressed else idx - 1
        elif self.action == 'DOWN' and idx < len(lst) - 1:
            lst.move(idx, len(lst) - 1 if self.shift_pressed else idx + 1)
            preset.mapping_index = len(lst) - 1 if self.shift_pressed else idx + 1
        elif self.action == 'COPY' and lst: _clipboard["mapping"] = copy_mapping_to_dict(lst[idx])
        elif self.action == 'PASTE' and _clipboard.get("mapping"): paste_mapping_from_dict(lst.add(), _clipboard["mapping"]); preset.mapping_index = len(lst) - 1

        if self.action != 'COPY': bpy.ops.ed.undo_push(message="Mapping Action")
        return {'FINISHED'}

class BATCH_STL_OT_override_actions(bpy.types.Operator):
    bl_idname = "batch_stl.override_actions"
    bl_label = "Override Actions"
    bl_options = {'REGISTER', 'INTERNAL'}
    action: bpy.props.EnumProperty(items=(('ADD', "", ""), ('REMOVE', "", ""), ('UP', "", ""), ('DOWN', "", ""), ('COPY', "", ""), ('PASTE', "", ""), ('PIN', "", ""), ('UNPIN', "", "")))
    override_index: bpy.props.IntProperty(default=-1)
    is_pinned: bpy.props.BoolProperty(default=False)
    shift_pressed: bpy.props.BoolProperty(options={'HIDDEN', 'SKIP_SAVE'}, default=False)

    def invoke(self, context, event):
        self.shift_pressed = event.shift
        return self.execute(context)

    def execute(self, context):
        preset = get_active_preset(context.scene)
        mapping = get_active_mapping(preset)
        if not preset: return {'CANCELLED'}
        lst = preset.pinned_overrides if self.is_pinned else (mapping.node_overrides if mapping else None)
        if lst is None: return {'CANCELLED'}

        idx = self.override_index
        if self.action == 'ADD': lst.add()
        elif self.action == 'REMOVE' and 0 <= idx < len(lst): lst.remove(idx)
        elif self.action == 'UP' and idx > 0: lst.move(idx, 0 if self.shift_pressed else idx - 1)
        elif self.action == 'DOWN' and 0 <= idx < len(lst) - 1: lst.move(idx, len(lst) - 1 if self.shift_pressed else idx + 1)
        elif self.action == 'COPY' and 0 <= idx < len(lst): _clipboard["override"] = copy_override_to_dict(lst[idx])
        elif self.action == 'PASTE' and _clipboard.get("override"):
            paste_override_from_dict(lst.add(), _clipboard["override"])
            if 0 <= idx < len(lst): lst.move(len(lst) - 1, idx + 1)
        elif self.action == 'PIN' and not self.is_pinned and 0 <= idx < len(lst):
            paste_override_from_dict(preset.pinned_overrides.add(), copy_override_to_dict(lst[idx]))
            lst.remove(idx)
        elif self.action == 'UNPIN' and self.is_pinned and 0 <= idx < len(lst):
            for m in preset.mappings: paste_override_from_dict(m.node_overrides.add(), copy_override_to_dict(lst[idx]))
            lst.remove(idx)

        if self.action != 'COPY': bpy.ops.ed.undo_push(message="Override Action")
        return {'FINISHED'}

class BATCH_STL_OT_input_actions(bpy.types.Operator):
    bl_idname = "batch_stl.input_actions"
    bl_label = "Input Actions"
    bl_options = {'REGISTER', 'INTERNAL'}
    action: bpy.props.EnumProperty(items=(('ADD', "", ""), ('REMOVE', "", ""), ('UP', "", ""), ('DOWN', "", ""), ('COPY', "", ""), ('PASTE', "", "")))
    override_index: bpy.props.IntProperty(default=-1)
    input_index: bpy.props.IntProperty(default=-1)
    is_pinned: bpy.props.BoolProperty(default=False)
    shift_pressed: bpy.props.BoolProperty(options={'HIDDEN', 'SKIP_SAVE'}, default=False)

    def invoke(self, context, event):
        self.shift_pressed = event.shift
        return self.execute(context)

    def execute(self, context):
        preset = get_active_preset(context.scene)
        mapping = get_active_mapping(preset)
        ovr_list = preset.pinned_overrides if self.is_pinned else (mapping.node_overrides if mapping else None)
        if not ovr_list or self.override_index < 0 or self.override_index >= len(ovr_list): return {'CANCELLED'}

        lst = ovr_list[self.override_index].inputs
        idx = self.input_index
        msg = None

        if self.action == 'ADD':
            if self.shift_pressed:
                ovr = ovr_list[self.override_index]
                if ovr.parent_group_ptr:
                    inputs_to_add = []
                    if ovr.override_target == 'NODE' and ovr.node_name:
                        n_name = ovr.node_name.split(" [")[0].strip()
                        node = ovr.parent_group_ptr.nodes.get(n_name)
                        if node:
                            for inp in node.inputs: inputs_to_add.append((inp.name, inp.type))
                    elif ovr.override_target == 'MODIFIER':
                        if hasattr(ovr.parent_group_ptr, "interface"):
                            for item in ovr.parent_group_ptr.interface.items_tree:
                                if getattr(item, "item_type", "") == 'SOCKET' and item.in_out == 'INPUT':
                                    inputs_to_add.append((item.name, getattr(item, "socket_type", "")))
                        else:
                            for inp in ovr.parent_group_ptr.inputs: inputs_to_add.append((inp.name, inp.type))

                    if inputs_to_add:
                        for name, s_type in inputs_to_add:
                            new_i = lst.add()
                            new_i.input_name = name
                            if 'Float' in s_type or s_type in ['VALUE', 'FLOAT']: new_i.override_type = 'FLOAT'
                            elif 'Int' in s_type or s_type == 'INT': new_i.override_type = 'INT'
                            elif 'Bool' in s_type or s_type == 'BOOLEAN': new_i.override_type = 'BOOLEAN'
                            elif 'String' in s_type or s_type == 'STRING': new_i.override_type = 'STRING'
                            elif 'Menu' in s_type or s_type == 'MENU': new_i.override_type = 'MENU'
                    else: lst.add()
                else: lst.add()
                msg = "Auto-Populate Sockets"
            else:
                lst.add()
                msg = "Add Input State"

        elif self.action == 'REMOVE' and 0 <= idx < len(lst):
            lst.remove(idx)
            msg = "Remove Input State"
        elif self.action == 'UP' and idx > 0:
            lst.move(idx, 0 if self.shift_pressed else idx - 1)
            msg = "Move Input Up"
        elif self.action == 'DOWN' and 0 <= idx < len(lst) - 1:
            lst.move(idx, len(lst) - 1 if self.shift_pressed else idx + 1)
            msg = "Move Input Down"
        elif self.action == 'COPY' and 0 <= idx < len(lst):
            i = lst[idx]
            if self.shift_pressed:
                # Shift+Copy = Duplicate into a new line below
                new_i = lst.add()
                orig_name = i.input_name

                for k in ["input_name", "override_type", "value_bool", "value_int", "value_float", "value_string", "value_menu", "use_tag", "tag", "use_dir"]:
                    setattr(new_i, k, getattr(i, k))
                new_i.sweep_range = getattr(i, "sweep_range", "")
                new_i.use_sweep = False

                lst.move(len(lst) - 1, idx + 1)

                # Automatically disable sweep for all items matching this target name
                for item in lst:
                    if item.input_name == orig_name:
                        item.use_sweep = False

                msg = "Duplicate Input Line"
            else:
                # Standard Copy
                _clipboard["input"] = {
                    "input_name": i.input_name, "override_type": i.override_type,
                    "value_bool": i.value_bool, "value_int": i.value_int, "value_float": i.value_float,
                    "value_string": i.value_string, "value_menu": i.value_menu,
                    "use_tag": i.use_tag, "tag": i.tag, "use_dir": i.use_dir,
                    "use_sweep": getattr(i, "use_sweep", False), "sweep_range": getattr(i, "sweep_range", "")
                }
                msg = "Copy Input State"
        elif self.action == 'PASTE' and _clipboard.get("input"):
            # Global Paste only (from the + / Paste row at the bottom)
            new_i = lst.add()
            for k, v in _clipboard["input"].items(): setattr(new_i, k, v)

            # Check if this pasted item matches an existing name; if so, disable sweep for that block
            count = sum(1 for item in lst if item.input_name == new_i.input_name)
            if count > 1:
                for item in lst:
                    if item.input_name == new_i.input_name:
                        item.use_sweep = False

            msg = "Paste Input State"

        if msg: bpy.ops.ed.undo_push(message=msg)
        return {'FINISHED'}

class BATCH_STL_OT_toggle_sweep(bpy.types.Operator):
    bl_idname = "batch_stl.toggle_sweep"
    bl_label = "Toggle Sweep"
    bl_options = {'REGISTER', 'INTERNAL'}
    override_index: bpy.props.IntProperty(default=-1)
    input_index: bpy.props.IntProperty(default=-1)
    is_pinned: bpy.props.BoolProperty(default=False)

    def invoke(self, context, event):
        preset = get_active_preset(context.scene)
        mapping = get_active_mapping(preset)
        ovr_list = preset.pinned_overrides if self.is_pinned else (mapping.node_overrides if mapping else None)
        if not ovr_list or self.override_index < 0 or self.override_index >= len(ovr_list): return {'CANCELLED'}
        ovr = ovr_list[self.override_index]
        if self.input_index < 0 or self.input_index >= len(ovr.inputs): return {'CANCELLED'}
        inp = ovr.inputs[self.input_index]

        if event.shift and inp.use_sweep:
            vals = parse_sweep_values(ovr, inp)
            if vals:
                inp.use_sweep = False
                def assign_val(target, val):
                    if target.override_type == 'BOOLEAN': target.value_bool = bool(val)
                    elif target.override_type == 'INT': target.value_int = int(val)
                    elif target.override_type == 'FLOAT': target.value_float = float(val)
                    elif target.override_type == 'STRING': target.value_string = str(val)
                    elif target.override_type == 'MENU': target.value_menu = str(val)
                assign_val(inp, vals[0])
                for i, v in enumerate(vals[1:]):
                    new_inp = ovr.inputs.add()
                    new_inp.input_name = inp.input_name
                    new_inp.override_type = inp.override_type
                    new_inp.use_tag = inp.use_tag
                    new_inp.tag = inp.tag
                    new_inp.use_dir = inp.use_dir
                    new_inp.use_sweep = False
                    assign_val(new_inp, v)
                    ovr.inputs.move(len(ovr.inputs) - 1, self.input_index + i + 1)
                bpy.ops.ed.undo_push(message="Expand Sweep Permutations")
            else:
                inp.use_sweep = False
                bpy.ops.ed.undo_push(message="Disable Sweep")
        else:
            inp.use_sweep = not inp.use_sweep
            if inp.use_sweep:
                to_remove = [j for j, other in enumerate(ovr.inputs) if other.input_name == inp.input_name and j != self.input_index]
                for j in reversed(to_remove):
                    ovr.inputs.remove(j)

                # Zero out explicitly stored parameters so sweep configurations do not save false data
                inp.value_string = ""
                inp.value_menu = ""
                inp.value_float = 0.0
                inp.value_int = 0
            bpy.ops.ed.undo_push(message="Enable Sweep" if inp.use_sweep else "Disable Sweep")

        return {'FINISHED'}

class BATCH_STL_OT_toggle_exclusion(bpy.types.Operator):
    bl_idname = "batch_stl.toggle_exclusion"
    bl_label = "Toggle Object Exclusion"
    bl_options = {'REGISTER', 'INTERNAL'}
    object_name: bpy.props.StringProperty()

    def execute(self, context):
        preset = get_active_preset(context.scene)
        mapping = get_active_mapping(preset)
        if mapping:
            idx = -1
            for i, e in enumerate(mapping.excluded_objects):
                if e.name == self.object_name:
                    idx = i; break
            if idx >= 0:
                mapping.excluded_objects.remove(idx)
                bpy.ops.ed.undo_push(message=f"Include '{self.object_name}' in Export")
            else:
                mapping.excluded_objects.add().name = self.object_name
                bpy.ops.ed.undo_push(message=f"Exclude '{self.object_name}' from Export")
        return {'FINISHED'}

class BATCH_STL_OT_cancel_export(bpy.types.Operator):
    bl_idname = "batch_stl.cancel_export"
    bl_label = "Cancel Export"
    preset_index: bpy.props.IntProperty(default=-1)

    def execute(self, context):
        if 0 <= self.preset_index < len(context.scene.batch_stl_presets):
            preset = context.scene.batch_stl_presets[self.preset_index]
            preset.cancel_export = True
            log = preset.console_logs.add()
            log.text = f"[!] Export cancelled manually for '{preset.name}'."
            preset.console_index = len(preset.console_logs) - 1
        return {'FINISHED'}

class EXPORT_OT_batch_stl_multi(bpy.types.Operator):
    bl_idname = "export_scene.batch_stl_multi"
    bl_label = "Export"
    bl_description = "Safely evaluate and batch export the mapped collections"
    bl_options = {"REGISTER"}
    preset_index: bpy.props.IntProperty(default=-1)

    @classmethod
    def poll(cls, context):
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
            self.report({'ERROR'}, "Missing Root Directory")
            return {"CANCELLED"}

        self.preset.console_logs.clear()
        context.scene.batch_stl_show_console = True

        has_overrides = bool(self.preset.pinned_overrides) or any(bool(m.node_overrides) for m in self.preset.mappings)
        verbose = scene.batch_stl_verbose_console

        if not has_overrides:
            root_dir = bpy.path.abspath(scene.batch_stl_root_dir)
            if self.preset.preset_prefix:
                root_dir = os.path.normpath(os.path.join(root_dir, self.preset.preset_prefix))

            total_objs = 0
            for mapping in self.preset.mappings:
                if not mapping.collection_ptr: continue
                if is_collection_excluded(context, mapping.collection_ptr): continue
                excluded_names = {e.name for e in mapping.excluded_objects} if mapping.use_filter else set()
                for obj in mapping.collection_ptr.all_objects:
                    if obj.type in {"MESH", "CURVE", "SURFACE", "META", "FONT"} and not obj.hide_get() and not obj.hide_viewport:
                        if obj.name not in excluded_names: total_objs += 1

            context.window_manager.progress_begin(0, max(1, total_objs))
            depsgraph = context.evaluated_depsgraph_get()
            exported_count = 0

            log_msg = f"\n=== STARTING SYNCHRONOUS EXPORT: {self.preset.name} ==="
            if verbose: print(log_msg)
            log_to_console(self.preset, log_msg)

            for mapping in self.preset.mappings:
                if not mapping.collection_ptr: continue
                if is_collection_excluded(context, mapping.collection_ptr): continue
                out_dir = os.path.normpath(os.path.join(root_dir, mapping.sub_path))
                os.makedirs(out_dir, exist_ok=True)
                excluded_names = {e.name for e in mapping.excluded_objects} if mapping.use_filter else set()

                for obj in mapping.collection_ptr.all_objects:
                    if obj.type in {"MESH", "CURVE", "SURFACE", "META", "FONT"} and not obj.hide_get() and not obj.hide_viewport:
                        if obj.name in excluded_names: continue
                        t_eval_start = time.perf_counter()
                        obj_eval = obj.evaluated_get(depsgraph)
                        try: mesh = obj_eval.to_mesh()
                        except RuntimeError: mesh = None
                        perf_msg = f"  ├─ Evaluated {obj.name} in {time.perf_counter()-t_eval_start:.4f}s"
                        if verbose: print(perf_msg)
                        log_to_console(self.preset, perf_msg)

                        if mesh:
                            base_tag = mapping.tag if mapping.use_tag and mapping.tag else ""
                            filepath = os.path.join(out_dir, f"{bpy.path.clean_name(obj.name)}{base_tag}.stl")
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

        self.preset.export_status = f"Spawning Worker... (0.0s)"
        t_spawn_start = time.perf_counter()

        self.temp_dir = tempfile.mkdtemp(prefix="fast_batch_stl_")
        self.temp_blend = os.path.join(self.temp_dir, "batch_stl_export_temp.blend")
        bpy.ops.wm.save_as_mainfile(filepath=self.temp_blend, copy=True, compress=False)

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

        self.q = queue.Queue()
        def enqueue_output(out, q):
            for line in iter(out.readline, ''):
                q.put(line)
            out.close()

        self.t = threading.Thread(target=enqueue_output, args=(self.process.stdout, self.q))
        self.t.daemon = True
        self.t.start()

        self.preset.is_exporting = True
        self.preset.cancel_export = False
        self.preset.export_progress = 0.0

        self._timer = context.window_manager.event_timer_add(0.05, window=context.window)
        context.window_manager.modal_handler_add(self)
        return {'RUNNING_MODAL'}

    def modal(self, context, event):
        try:
            if self.preset.cancel_export:
                self.cleanup(context)
                self.report({'WARNING'}, f"Export cancelled for {self.preset.name}.")
                log_to_console(self.preset, f"[!] Export cancelled manually for '{self.preset.name}'.")
                return {'CANCELLED'}

            if event.type == 'TIMER':
                elapsed = time.perf_counter() - self.export_start_time
                while True:
                    try: line = self.q.get_nowait()
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

                for area in context.screen.areas: area.tag_redraw()

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
        return {'PASS_THROUGH'}

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
            if hasattr(self, 'temp_blend') and os.path.exists(self.temp_blend): os.remove(self.temp_blend)
            if hasattr(self, 'temp_dir') and os.path.exists(self.temp_dir): os.rmdir(self.temp_dir)
        except Exception: pass


# ==============================================================================
# === [ 5. UI LISTS & PANELS ] ===
# ==============================================================================

class BATCH_STL_UL_presets(bpy.types.UIList):
    def draw_item(self, context, layout, data, item, icon, active_data, active_propname, index):
        row = layout.row(align=True)
        row.prop(item, "name", text="", emboss=False)
        row.prop(item, "preset_prefix", text="", emboss=False, icon='FILE_FOLDER')

        if item.is_exporting:
            row.prop(item, "export_progress", text=item.export_status, slider=True)
            cancel_op = row.operator("batch_stl.cancel_export", text="", icon='CANCEL')
            cancel_op.preset_index = index
        else:
            op = row.operator("export_scene.batch_stl_multi", text="", icon='EXPORT')
            op.preset_index = index

class BATCH_STL_UL_items(bpy.types.UIList):
    def draw_item(self, context, layout, data, item, icon, active_data, active_propname, index):
        row = layout.row(align=True)
        row.prop(item, "use_filter", text="", icon='FILTER')
        row.prop(item, "collection_ptr", text="")
        row.separator(factor=0.5)
        sub_row = row.row(align=True)
        sub_row.prop(item, "use_tag", text="", icon='BOOKMARKS')
        sub_row.separator(factor=0.5)
        tag_row = sub_row.row(align=True)
        tag_row.prop(item, "tag", text="", emboss=False)
        row.prop(item, "sub_path", text="", emboss=False, icon='FILE_FOLDER')

class BATCH_STL_UL_console_logs(bpy.types.UIList):
    def draw_item(self, context, layout, data, item, icon, active_data, active_propname, index):
        layout.label(text=item.text)

def draw_inline_controls(layout, operator_id, use_clipboard=False):
    row = layout.row(align=True)
    row.operator(operator_id, icon='ADD', text="").action = 'ADD'
    row.operator(operator_id, icon='REMOVE', text="").action = 'REMOVE'
    row.operator(operator_id, icon='TRIA_UP', text="").action = 'UP'
    row.operator(operator_id, icon='TRIA_DOWN', text="").action = 'DOWN'
    if use_clipboard:
        row.operator(operator_id, icon='COPYDOWN', text="").action = 'COPY'
        row.operator(operator_id, icon='PASTEDOWN', text="").action = 'PASTE'

def draw_override_block(layout, ovr, o_idx, is_pinned, freq_dict=None):
    if freq_dict is None: freq_dict = {}

    ovr_box = layout.box()
    header_box = ovr_box.box()
    row = header_box.row(align=True)
    row.prop(ovr, "parent_group_ptr", text="")
    if ovr.override_target == 'NODE' and ovr.parent_group_ptr:
        row.prop(ovr, "node_name", text="", icon='NODETREE')
    row.prop(ovr, "override_target", text="")

    for action, icon in [('UNPIN' if is_pinned else 'PIN', 'PINNED' if is_pinned else 'UNPINNED'), ('UP', 'TRIA_UP'), ('DOWN', 'TRIA_DOWN'), ('COPY', 'COPYDOWN'), ('REMOVE', 'X')]:
        op = row.operator("batch_stl.override_actions", text="", icon=icon)
        op.action, op.override_index, op.is_pinned = action, o_idx, is_pinned

    inputs_box = ovr_box.box()
    target_node = None
    is_valid_target = False

    if ovr.parent_group_ptr:
        if ovr.override_target == 'MODIFIER':
            is_valid_target = True
        elif ovr.override_target == 'NODE' and ovr.node_name:
            n_name = ovr.node_name.split(" [")[0].strip()
            target_node = ovr.parent_group_ptr.nodes.get(n_name)
            if target_node:
                is_valid_target = True

    if is_valid_target:
        ordered_groups = []
        seen = set()
        for i_idx, inp in enumerate(ovr.inputs):
            name = inp.input_name
            if name not in seen:
                seen.add(name)
                group = [(j, other_inp) for j, other_inp in enumerate(ovr.inputs) if other_inp.input_name == name]
                ordered_groups.append((name, group))

        for name, items in ordered_groups:
            is_grouped = len(items) > 1
            grp_box = inputs_box.box() if is_grouped else inputs_box

            for row_idx, (i_idx, inp) in enumerate(items):
                v_row = grp_box.row(align=True)
                split = v_row.split(factor=0.4)

                left_col = split.row(align=True)
                if row_idx == 0:
                    left_col.label(icon='FORWARD')
                    if ovr.override_target == 'NODE':
                        if target_node: left_col.prop_search(inp, "input_name", target_node, "inputs", text="")
                        else: left_col.prop(inp, "input_name", text="")
                    elif ovr.override_target == 'MODIFIER':
                        if hasattr(ovr.parent_group_ptr, "interface"): left_col.prop_search(inp, "input_name", ovr.parent_group_ptr.interface, "items_tree", text="")
                        else: left_col.prop_search(inp, "input_name", ovr.parent_group_ptr, "inputs", text="")
                    else: left_col.prop(inp, "input_name", text="")

                    op = left_col.operator("batch_stl.toggle_sweep", text="", icon='FILE_REFRESH', depress=inp.use_sweep)
                    op.override_index = o_idx
                    op.input_index = i_idx
                    op.is_pinned = is_pinned
                else:
                    # Provide empty spacing aligned with the initial target selection field
                    left_col.label(text="")

                right_col = split.row(align=True)

                if getattr(inp, "use_sweep", False):
                    if inp.override_type in ['INT', 'FLOAT', 'STRING']: right_col.prop(inp, "sweep_range", text="")
                    elif inp.override_type == 'BOOLEAN': right_col.label(text="True & False")
                    elif inp.override_type == 'MENU': right_col.label(text="All Menu Items")
                else:
                    if inp.override_type == 'BOOLEAN': right_col.prop(inp, "value_bool", text="True" if inp.value_bool else "False", toggle=True)
                    elif inp.override_type == 'INT': right_col.prop(inp, "value_int", text="")
                    elif inp.override_type == 'FLOAT': right_col.prop(inp, "value_float", text="")
                    elif inp.override_type == 'STRING': right_col.prop(inp, "value_string", text="")
                    elif inp.override_type == 'MENU': right_col.prop(inp, "value_menu", text="")

                key = (ovr.override_target, ovr.node_name, inp.input_name)
                if is_grouped or freq_dict.get(key, 0) > 1:
                    right_col.prop(inp, "use_dir", text="", icon='FILE_FOLDER')
                    right_col.prop(inp, "use_tag", text="", icon='BOOKMARKS')
                    right_col.prop(inp, "tag", text="")

                action_row = right_col.row(align=True)
                for action, icon in [('UP', 'TRIA_UP'), ('DOWN', 'TRIA_DOWN'), ('COPY', 'COPYDOWN'), ('REMOVE', 'TRASH')]:
                    op = action_row.operator("batch_stl.input_actions", text="", icon=icon)
                    op.action = action
                    op.override_index = o_idx
                    op.input_index = i_idx
                    op.is_pinned = is_pinned

        add_row = inputs_box.row(align=True)
        for action, icon in [('ADD', 'PLUS'), ('PASTE', 'PASTEDOWN')]:
            op = add_row.operator("batch_stl.input_actions", text="", icon=icon)
            op.action = action
            op.override_index = o_idx
            op.input_index = -1
            op.is_pinned = is_pinned
    else:
        inputs_box.label(text="Specify valid Group and Node/Modifier to add inputs.", icon='INFO')


class VIEW3D_PT_batch_export_stl_multi(bpy.types.Panel):
    bl_space_type = "VIEW_3D"
    bl_region_type = "UI"
    bl_category = "Export"
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

        total_mappings = len(active_preset.mappings)
        total_objects = 0
        total_preset_combos = 0

        for m in active_preset.mappings:
            m_combos = len(generate_override_combinations(list(active_preset.pinned_overrides) + list(m.node_overrides)))
            total_preset_combos += m_combos
            obj_count = 0
            if m.collection_ptr:
                excluded_names = {e.name for e in m.excluded_objects} if getattr(m, "use_filter", False) else set()
                for obj in m.collection_ptr.all_objects:
                    if obj.type in {"MESH", "CURVE", "SURFACE", "META", "FONT"} and not obj.hide_get() and not obj.hide_viewport:
                        if obj.name not in excluded_names: obj_count += 1
            total_objects += (obj_count * m_combos)

        layout.separator(factor=0.5)
        m_box = layout.box()
        m_header = m_box.row()
        icon_m = 'TRIA_DOWN' if scene.batch_stl_ui_mappings else 'TRIA_RIGHT'
        m_header.prop(scene, "batch_stl_ui_mappings", text="", icon=icon_m, emboss=False)

        m_title = f"{active_preset.name} | {total_mappings} collections | {total_preset_combos} combos | {total_objects} objects total"
        m_header.label(text=m_title, icon='OUTLINER_COLLECTION')
        draw_inline_controls(m_header, "batch_stl.mapping_actions", use_clipboard=True)

        if scene.batch_stl_ui_mappings:
            m_box.template_list("BATCH_STL_UL_items", "", active_preset, "mappings", active_preset, "mapping_index", rows=5)
            active_item = get_active_mapping(active_preset)
            if active_item and active_item.collection_ptr:
                m_box.separator()
                if active_item.use_filter:
                    filter_box = m_box.box()
                    f_header = filter_box.row()
                    icon_f = 'TRIA_DOWN' if scene.batch_stl_ui_exclude else 'TRIA_RIGHT'
                    f_header.prop(scene, "batch_stl_ui_exclude", text="", icon=icon_f, emboss=False)
                    f_header.label(text="Exclude Objects:", icon='FILTER')

                    if scene.batch_stl_ui_exclude:
                        col = filter_box.column(align=True)
                        for obj in active_item.collection_ptr.all_objects:
                            if obj.type not in {"MESH", "CURVE", "SURFACE", "META", "FONT"}: continue
                            is_excl = any(e.name == obj.name for e in active_item.excluded_objects)
                            icon_btn = 'CHECKBOX_DEHLT' if is_excl else 'CHECKBOX_HLT'
                            op = col.operator("batch_stl.toggle_exclusion", text=obj.name, icon=icon_btn, depress=not is_excl)
                            op.object_name = obj.name

        layout.separator()

        if get_active_mapping(active_preset):
            active_item = get_active_mapping(active_preset)
            all_ovrs = list(active_preset.pinned_overrides) + list(active_item.node_overrides)
            freq_dict = {}
            unique_targets = set()
            total_inputs = 0

            for o in all_ovrs:
                total_inputs += len(o.inputs)
                for i in o.inputs:
                    key = (o.override_target, o.node_name, i.input_name)
                    weight = 2 if getattr(i, "use_sweep", False) else 1
                    freq_dict[key] = freq_dict.get(key, 0) + weight
                    unique_targets.add(key)

            num_targets = len(unique_targets)
            num_combos = len(generate_override_combinations(all_ovrs))
            active_obj_count = 0
            if active_item.collection_ptr:
                excluded_names = {e.name for e in active_item.excluded_objects} if getattr(active_item, "use_filter", False) else set()
                for obj in active_item.collection_ptr.all_objects:
                    if obj.type in {"MESH", "CURVE", "SURFACE", "META", "FONT"} and not obj.hide_get() and not obj.hide_viewport:
                        if obj.name not in excluded_names: active_obj_count += 1

            mapping_total_objects = active_obj_count * num_combos
            c_name = active_item.collection_ptr.name if active_item.collection_ptr else "Unassigned"
            if active_item.tag: c_name += f" [{active_item.tag}]"

            metric_str = f"{c_name} | {num_targets} targets | {total_inputs} inputs | {num_combos} combos | {mapping_total_objects} objects"
            header = layout.row()
            header.label(text=metric_str, icon='MODIFIER')
            layout.separator()

            pinned_box = layout.box()
            p_header = pinned_box.row()
            icon_g = 'TRIA_DOWN' if scene.batch_stl_ui_global_ovr else 'TRIA_RIGHT'
            p_header.prop(scene, "batch_stl_ui_global_ovr", text="", icon=icon_g, emboss=False)
            p_header.label(text="Global Pinned Overrides:", icon='PINNED')

            p_actions = p_header.row(align=True)
            op = p_actions.operator("batch_stl.override_actions", text="", icon='ADD')
            op.action, op.override_index, op.is_pinned = 'ADD', -1, True
            op = p_actions.operator("batch_stl.override_actions", text="", icon='PASTEDOWN')
            op.action, op.override_index, op.is_pinned = 'PASTE', -1, True

            if scene.batch_stl_ui_global_ovr:
                for o_idx, ovr in enumerate(active_preset.pinned_overrides):
                    draw_override_block(pinned_box, ovr, o_idx, True, freq_dict)

            layout.separator(factor=0.5)
            local_box = layout.box()
            l_header = local_box.row()
            icon_l = 'TRIA_DOWN' if scene.batch_stl_ui_local_ovr else 'TRIA_RIGHT'
            l_header.prop(scene, "batch_stl_ui_local_ovr", text="", icon=icon_l, emboss=False)
            l_header.label(text="Local Overrides:", icon='UNPINNED')

            l_actions = l_header.row(align=True)
            op = l_actions.operator("batch_stl.override_actions", text="", icon='ADD')
            op.action, op.override_index, op.is_pinned = 'ADD', -1, False
            op = l_actions.operator("batch_stl.override_actions", text="", icon='PASTEDOWN')
            op.action, op.override_index, op.is_pinned = 'PASTE', -1, False

            if scene.batch_stl_ui_local_ovr:
                for o_idx, ovr in enumerate(active_item.node_overrides):
                    draw_override_block(local_box, ovr, o_idx, False, freq_dict)

        layout.separator()
        t_box = layout.box()
        t_header = t_box.row(align=True)
        icon_t = 'TRIA_DOWN' if scene.batch_stl_show_tree else 'TRIA_RIGHT'
        t_header.prop(scene, "batch_stl_show_tree", text="", icon=icon_t, emboss=False)
        t_header.label(text="Export Structure & Files", icon='OUTLINER_OB_EMPTY')

        if scene.batch_stl_show_tree and active_preset:
            tree_dict, duplicates = build_tree_dict(scene, active_preset)
            if duplicates:
                warn_box = t_box.box()
                warn_row = warn_box.row()
                warn_row.label(text=f"WARNING: {len(duplicates)} naming collisions detected! Files will be overwritten.", icon='ERROR')

            lines = get_tree_lines(tree_dict)
            col = t_box.column(align=True)
            for line_data in lines:
                row = col.row(align=True)
                row.scale_y = 0.85
                
                split = row.split(factor=0.6)
                
                left_col = split.row(align=True)
                left_col.label(text=line_data.get("prefix", "") + line_data.get("text", ""), icon=line_data.get("icon", 'NONE'))
                
                right_col = split.row(align=True)
                ptr = line_data.get("ptr")
                prop = line_data.get("prop")
                if ptr and prop:
                    right_col.prop(ptr, prop, text="")
                
                suffix = line_data.get("suffix", "")
                if suffix:
                    right_col.label(text=suffix)

        layout.separator()
        layout.prop(scene, "batch_stl_verbose_console", toggle=True, icon='CONSOLE')


# ==============================================================================
# === [ 6. REGISTRATION & LIFECYCLE ] ===
# ==============================================================================

@persistent
def reset_batch_stl_state(scene):
    """Force flush export UI lock on file load or crash recovery."""
    try:
        for p in bpy.context.scene.batch_stl_presets:
            p.is_exporting = False
            p.cancel_export = False
            p.export_progress = 0.0
            p.export_status = ""
    except Exception: pass

# --- EXPLICIT TOPOLOGICAL CLASSES TUPLE ---
# This strict ordering ensures properties map correctly before being called in UI.
classes = (
    # 1. Properties
    BatchSTLLogLine,
    BatchSTLNodeInput,
    BatchSTLNodeOverride,
    BatchSTLExcludedObject,
    BatchSTLExportItem,
    BatchSTLExportPreset,

    # 2. UI Lists
    BATCH_STL_UL_presets,
    BATCH_STL_UL_items,
    BATCH_STL_UL_console_logs,

    # 3. Operators
    BATCH_STL_OT_clear_console,
    BATCH_STL_OT_preset_actions,
    BATCH_STL_OT_mapping_actions,
    BATCH_STL_OT_override_actions,
    BATCH_STL_OT_input_actions,
    BATCH_STL_OT_toggle_sweep,
    BATCH_STL_OT_toggle_exclusion,
    BATCH_STL_OT_cancel_export,
    BATCH_STL_OT_export_presets_json,
    BATCH_STL_OT_import_presets_json,
    EXPORT_OT_batch_stl_multi,

    # 4. Panels
    VIEW3D_PT_batch_export_stl_multi,
)

def register():
    for cls in classes:
        bpy.utils.register_class(cls)

    bpy.types.Scene.batch_stl_root_dir = bpy.props.StringProperty(name="Root Export Dir", default="//", subtype="DIR_PATH", description="Master directory path on disk where all batch STL exports will be saved")
    bpy.types.Scene.batch_stl_presets = bpy.props.CollectionProperty(type=BatchSTLExportPreset, description="List of all batch export presets")
    bpy.types.Scene.batch_stl_preset_index = bpy.props.IntProperty(name="Active Preset", default=0, description="Select the active batch export preset to edit")
    bpy.types.Scene.batch_stl_verbose_console = bpy.props.BoolProperty(name="Verbose Console Output", default=False, description="Print granular timing statistics and evaluation logs to the system console during export")

    # UI Collapsible States
    bpy.types.Scene.batch_stl_ui_presets = bpy.props.BoolProperty(default=True)
    bpy.types.Scene.batch_stl_ui_mappings = bpy.props.BoolProperty(default=True)
    bpy.types.Scene.batch_stl_ui_global_ovr = bpy.props.BoolProperty(default=True)
    bpy.types.Scene.batch_stl_ui_local_ovr = bpy.props.BoolProperty(default=True)
    bpy.types.Scene.batch_stl_ui_exclude = bpy.props.BoolProperty(default=True)
    bpy.types.Scene.batch_stl_show_tree = bpy.props.BoolProperty(default=True)
    bpy.types.Scene.batch_stl_show_console = bpy.props.BoolProperty(default=False)

    reset_batch_stl_state(None)
    if reset_batch_stl_state not in bpy.app.handlers.load_post:
        bpy.app.handlers.load_post.append(reset_batch_stl_state)

def unregister():
    if reset_batch_stl_state in bpy.app.handlers.load_post:
        bpy.app.handlers.load_post.remove(reset_batch_stl_state)

    for cls in reversed(classes):
        try: bpy.utils.unregister_class(cls)
        except RuntimeError: pass

    properties_to_remove = [
        "batch_stl_root_dir", "batch_stl_presets", "batch_stl_preset_index",
        "batch_stl_verbose_console", "batch_stl_ui_presets", "batch_stl_ui_mappings",
        "batch_stl_ui_global_ovr", "batch_stl_ui_local_ovr", "batch_stl_ui_exclude",
        "batch_stl_show_tree", "batch_stl_show_console"
    ]

    for prop in properties_to_remove:
        if hasattr(bpy.types.Scene, prop):
            delattr(bpy.types.Scene, prop)


# ==============================================================================
# === [ 7. CLI EXECUTION BINDING ] ===
# ==============================================================================

if __name__ == "__main__":
    if "--batch-stl-headless" in sys.argv:
        if not hasattr(bpy.types.Scene, "batch_stl_root_dir"):
            register()

        idx = sys.argv.index("--batch-stl-headless")
        p_index = int(sys.argv[idx + 1])
        run_headless_export(p_index)
    else:
        if not hasattr(bpy.types.Scene, "batch_stl_root_dir"):
            register()
