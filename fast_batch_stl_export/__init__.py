"""
Fast Batch STL Exporter
Architecture: Single-File Monolithic (Optimized for Agentic Environments)
Data Hierarchy: Preset > Collection > NodeGroup > Node > Input > Value
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
    "collection": None
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

# --- PERMUTATION ENGINE (Adapters) ---
class TempMockInput:
    def __init__(self, name, o_type, val_obj):
        self.input_name = name
        self.override_type = o_type
        self.use_tag = val_obj.use_tag
        self.tag = val_obj.tag
        self.use_dir = val_obj.use_dir
        self.use_sweep = val_obj.use_sweep
        self.sweep_range = val_obj.sweep_range
        self._val_obj = val_obj
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

def get_flat_overrides(nodegroups):
    overrides = []
    for ng in nodegroups:
        for node in ng.nodes:
            target = 'MODIFIER' if not node.name or node.name == "<Modifier Interface>" else 'NODE'
            temp_inputs = []
            for inp in node.inputs:
                for val in inp.values:
                    temp_inputs.append(TempMockInput(inp.name, inp.override_type, val))
            if temp_inputs:
                overrides.append(TempMockOverride(target, ng.group_ptr, node.name, temp_inputs))
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
        has_sweep = any(getattr(inp, "use_sweep", False) for ovr, inp in pairs)

        for ovr, inp in pairs:
            if getattr(inp, "use_sweep", False):
                sweep_vals = parse_sweep_values(ovr, inp)
                for val in sweep_vals:
                    mock_inp = MockInput(inp, val)
                    if val not in value_groups: value_groups[val] = []
                    value_groups[val].append((ovr, mock_inp))
            elif not has_sweep:
                val = get_input_value(inp)
                mock_inp = MockInput(inp, val)
                if val not in value_groups: value_groups[val] = []
                value_groups[val].append((ovr, mock_inp))

        if value_groups:
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

def get_active_collection(preset):
    if preset and preset.collections and 0 <= preset.collection_index < len(preset.collections):
        return preset.collections[preset.collection_index]
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
    return {"group": ng.group_ptr.name if ng.group_ptr else "", "nodes": [copy_node_to_dict(n) for n in ng.nodes]}

def copy_collection_to_dict(c):
    return {
        "collection_name": c.collection_ptr.name if c.collection_ptr else "",
        "use_tag": c.use_tag, "tag": c.tag, "sub_path": c.sub_path,
        "use_filter": getattr(c, "use_filter", False),
        "excluded_objects": [e.name for e in c.excluded_objects],
        "nodegroups": [copy_ng_to_dict(ng) for ng in c.nodegroups]
    }

def copy_preset_to_dict(src):
    return {
        "name": src.name, "preset_prefix": src.preset_prefix,
        "nodegroups": [copy_ng_to_dict(ng) for ng in src.nodegroups],
        "collections": [copy_collection_to_dict(c) for c in src.collections]
    }

def paste_val_from_dict(new_v, data):
    for k, v in data.items(): setattr(new_v, k, v)

def paste_input_from_dict(new_i, data):
    new_i.name = data["name"]
    new_i.override_type = data.get("override_type", 'FLOAT')
    for v_data in data.get("values", []): paste_val_from_dict(new_i.values.add(), v_data)

def paste_node_from_dict(new_n, data):
    new_n.name = data["name"]
    for i_data in data.get("inputs", []): paste_input_from_dict(new_n.inputs.add(), i_data)

def paste_ng_from_dict(new_ng, data):
    g_name = data.get("group", "")
    new_ng.group_ptr = bpy.data.node_groups.get(g_name) if g_name else None
    for n_data in data.get("nodes", []): paste_node_from_dict(new_ng.nodes.add(), n_data)

def paste_collection_from_dict(new_c, data):
    c_name = data.get("collection_name", "")
    new_c.collection_ptr = bpy.data.collections.get(c_name) if c_name else None
    new_c.use_tag = data.get("use_tag", True)
    new_c.tag = data.get("tag", "")
    new_c.sub_path = data.get("sub_path", "")
    new_c.use_filter = data.get("use_filter", False)
    for obj_name in data.get("excluded_objects", []): new_c.excluded_objects.add().name = obj_name
    for ng_data in data.get("nodegroups", []): paste_ng_from_dict(new_c.nodegroups.add(), ng_data)

def paste_preset_from_dict(new_p, data):
    new_p.name = data.get("name", "Imported Preset")
    new_p.preset_prefix = data.get("preset_prefix", "")
    for ng_data in data.get("nodegroups", []): paste_ng_from_dict(new_p.nodegroups.add(), ng_data)
    for c_data in data.get("collections", []): paste_collection_from_dict(new_p.collections.add(), c_data)

# --- TREE VISUALIZER LOGIC ---
def build_tree_dict(scene, preset):
    root_name = bpy.path.abspath(scene.batch_stl_root_dir) if scene.batch_stl_root_dir else "//"
    tree = {}
    all_filepaths = set()
    duplicates = set()

    current_root = tree
    if preset.preset_prefix:
        current_root[preset.preset_prefix] = {}
        current_root = current_root[preset.preset_prefix]

    for c in preset.collections:
        c_root = current_root
        c_root_path = []
        if c.sub_path:
            parts = c.sub_path.replace('\\', '/').split('/')
            for part in parts:
                if part:
                    if part not in c_root: c_root[part] = {}
                    c_root = c_root[part]
                    c_root_path.append(part)

        all_overrides = get_flat_overrides(preset.nodegroups) + get_flat_overrides(c.nodegroups)

        freq_dict = {}
        for o in all_overrides:
            for i in o.inputs:
                key = (o.override_target, o.node_name, i.input_name)
                weight = 2 if getattr(i, "use_sweep", False) else 1
                freq_dict[key] = freq_dict.get(key, 0) + weight

        combinations = generate_override_combinations(all_overrides)
        valid_objs = []
        if c.collection_ptr:
            excluded_names = {e.name for e in c.excluded_objects} if getattr(c, "use_filter", False) else set()
            for obj in c.collection_ptr.all_objects:
                if obj.type in {"MESH", "CURVE", "SURFACE", "META", "FONT"} and not obj.hide_get() and not obj.hide_viewport:
                    if obj.name not in excluded_names: valid_objs.append(obj)

        if not valid_objs: continue
        if not combinations: combinations = [[]]

        for combo in combinations:
            combo_root = c_root
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
            full_dir_parts.extend(combo_subpath)
            dir_path_str = os.path.normpath(os.path.join(root_name, *full_dir_parts))

            for obj in valid_objs:
                filename = f"{bpy.path.clean_name(obj.name)}{final_tag}.stl"
                combo_root['_files'].append(filename)
                full_path = os.path.join(dir_path_str, filename)
                if full_path in all_filepaths: duplicates.add(full_path)
                else: all_filepaths.add(full_path)

    return {root_name: tree}, duplicates

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
            draw_tree_dict(box, tree_node[k], dir_path, toggled_list, duplicates, next_actual)

    for f in files:
        split = layout.split(factor=0.025)
        split.column()
        col = split.column()

        row = col.row()
        row.scale_y = 0.4

        f_path = os.path.join(actual_path, f) if actual_path else f
        if f_path in duplicates:
            row.alert = True

        row.label(text=str(f))

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
    for c in preset.collections:
        if c.collection_ptr and not is_collection_excluded(bpy.context, c.collection_ptr):
            active_export_objects.update(c.collection_ptr.all_objects)

    muted_count = 0
    for obj in bpy.context.view_layer.objects:
        if obj not in active_export_objects:
            for mod in getattr(obj, 'modifiers', []):
                if mod.type == 'NODES' and mod.show_viewport:
                    mod.show_viewport = False
                    muted_count += 1

    print(f"    ├─ Permanently Muted {muted_count} unused GN modifiers to accelerate Graph evaluation in {time.perf_counter() - t_phase0_start:.4f}s")

    execution_batches = {}
    sig_pinned = get_override_signature(get_flat_overrides(preset.nodegroups))

    for c in preset.collections:
        if not c.collection_ptr: continue
        if is_collection_excluded(bpy.context, c.collection_ptr): continue
        sig_local = get_override_signature(get_flat_overrides(c.nodegroups))
        full_sig = sig_pinned + sig_local
        if full_sig not in execution_batches: execution_batches[full_sig] = []
        execution_batches[full_sig].append(c)

    if not execution_batches:
        print("  └─ No active collections to export.")
        print("BATCH_STL_DONE", flush=True)
        sys.exit(0)

    total_operations = 0
    for signature, c_in_batch in execution_batches.items():
        first_c = c_in_batch[0]
        all_overrides = get_flat_overrides(preset.nodegroups) + get_flat_overrides(first_c.nodegroups)
        combinations = generate_override_combinations(all_overrides)
        batch_obj_count = 0
        for m in c_in_batch:
            excluded_names = {e.name for e in m.excluded_objects} if getattr(m, "use_filter", False) else set()
            for obj in m.collection_ptr.all_objects:
                if obj.type in {"MESH", "CURVE", "SURFACE", "META", "FONT"} and not obj.hide_get() and not obj.hide_viewport:
                    if obj.name not in excluded_names:
                        batch_obj_count += 1
        total_operations += (batch_obj_count * len(combinations))

    print(f"BATCH_STL_TOTAL:{total_operations}", flush=True)

    current_op_step = 0
    batch_counter = 1

    for signature, c_in_batch in execution_batches.items():
        t_batch_start = time.perf_counter()
        first_c = c_in_batch[0]
        all_overrides = get_flat_overrides(preset.nodegroups) + get_flat_overrides(first_c.nodegroups)

        freq_dict = {}
        for o in all_overrides:
            for i in o.inputs:
                key = (o.override_target, o.node_name, i.input_name)
                weight = 2 if getattr(i, "use_sweep", False) else 1
                freq_dict[key] = freq_dict.get(key, 0) + weight

        combinations = generate_override_combinations(all_overrides)

        is_clean_batch = len(get_flat_overrides(first_c.nodegroups)) == 0
        batch_type = "Clean (Pinned Only)" if is_clean_batch else f"Dirty ({len(get_flat_overrides(first_c.nodegroups))} Local Overrides)"
        c_names = [f"{m.collection_ptr.name} [{m.tag}]" for m in c_in_batch]
        print(f"  ├─ Batch {batch_counter}/{len(execution_batches)} [{batch_type}]: Processing {len(c_names)} mapped instances with {len(combinations)} permutation(s)")

        batch_objects = set()
        for m in c_in_batch: batch_objects.update(m.collection_ptr.all_objects)

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
            global_states, mod_states = [], []

            try:
                active_overrides = reconstruct_overrides_for_combo(combo)
                global_states, mod_states = apply_overrides(active_overrides, batch_objects)
                bpy.context.view_layer.update()
                depsgraph = bpy.context.evaluated_depsgraph_get()
                print(f"  │    │    ├─ Applied & Synced Graph: {time.perf_counter() - t_ovr:.4f}s")

                for c in c_in_batch:
                    out_dir = os.path.normpath(os.path.join(root_dir, c.sub_path, combo_subpath))
                    os.makedirs(out_dir, exist_ok=True)
                    print(f"  │    │    ├─ Exporting: {c.collection_ptr.name}{' ['+c.tag+']' if c.tag else ''}")

                    excluded_names = {e.name for e in c.excluded_objects} if getattr(c, "use_filter", False) else set()
                    for obj in c.collection_ptr.all_objects:
                        if obj.type in {"MESH", "CURVE", "SURFACE", "META", "FONT"} and not obj.hide_get() and not obj.hide_viewport:
                            if obj.name in excluded_names: continue
                            t_eval = time.perf_counter()
                            obj_eval = obj.evaluated_get(depsgraph)
                            try: mesh = obj_eval.to_mesh()
                            except RuntimeError: mesh = None
                            print(f"  │         ├─ Evaluated Mesh [{obj.name}]: {time.perf_counter() - t_eval:.4f}s")

                            if mesh:
                                base_tag = c.tag if getattr(c, "use_tag", False) and c.tag else ""
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

def on_input_name_update(self, context):
    try:
        found_ng, found_node = None, None
        for p in context.scene.batch_stl_presets:
            for ng in p.nodegroups:
                for n in ng.nodes:
                    if self in n.inputs.values(): found_ng, found_node = ng, n; break
                if found_ng: break
            if found_ng: break
            for c in p.collections:
                for ng in c.nodegroups:
                    for n in ng.nodes:
                        if self in n.inputs.values(): found_ng, found_node = ng, n; break
                    if found_ng: break
                if found_ng: break

        if found_ng and found_node:
            self.override_type = infer_input_type(found_ng.group_ptr, found_node.name, self.name)
            for v in self.values: v.use_sweep = False
    except Exception: pass

def search_target_node_cb(self, context, edit_text):
    res = ["<Modifier Interface>"]
    found_ng = None
    for p in context.scene.batch_stl_presets:
        for ng in p.nodegroups:
            if self in ng.nodes.values(): found_ng = ng; break
        if found_ng: break
        for c in p.collections:
            for ng in c.nodegroups:
                if self in ng.nodes.values(): found_ng = ng; break
            if found_ng: break

    if found_ng and found_ng.group_ptr:
        for node in found_ng.group_ptr.nodes:
            name = node.name
            if node.type == 'GROUP' and getattr(node, "node_tree", None):
                val = f"{name} [{node.node_tree.name}]"
            else:
                val = f"{name} [{node.type}]"
            if not edit_text or edit_text.lower() in val.lower():
                res.append(val)

    if edit_text == self.name: edit_text = ""
    return res

def search_menu_items_cb(self, context, edit_text):
    found_inp, found_n, found_ng = None, None, None
    for p in context.scene.batch_stl_presets:
        for ng in p.nodegroups:
            for n in ng.nodes:
                for i in n.inputs:
                    if self in i.values.values(): found_inp, found_n, found_ng = i, n, ng; break
                if found_inp: break
            if found_inp: break
        if found_inp: break
        for c in p.collections:
            for ng in c.nodegroups:
                for n in ng.nodes:
                    for i in n.inputs:
                        if self in i.values.values(): found_inp, found_n, found_ng = i, n, ng; break
                    if found_inp: break
                if found_inp: break
            if found_inp: break

    items = []
    if found_ng and found_ng.group_ptr and found_inp:
        is_mod = not found_n.name or found_n.name == "<Modifier Interface>"
        if is_mod:
            for node in found_ng.group_ptr.nodes:
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
            node = found_ng.group_ptr.nodes.get(n_name)
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

def update_val_use_tag(self, context):
    if not self.use_tag and not self.use_dir: self.use_dir = True

def update_val_use_dir(self, context):
    if not self.use_dir and not self.use_tag: self.use_tag = True

class BatchSTLValue(bpy.types.PropertyGroup):
    value_bool: bpy.props.BoolProperty(name="Value", default=True)
    value_int: bpy.props.IntProperty(name="Value", default=0)
    value_float: bpy.props.FloatProperty(name="Value", default=0.0)
    value_string: bpy.props.StringProperty(name="Value", default="")
    value_menu: bpy.props.StringProperty(name="Value", default="", search=search_menu_items_cb)

    use_tag: bpy.props.BoolProperty(name="Use Tag", default=False, update=update_val_use_tag)
    tag: bpy.props.StringProperty(name="Tag", default="")
    use_dir: bpy.props.BoolProperty(name="Use Dir", default=True, update=update_val_use_dir)

    use_sweep: bpy.props.BoolProperty(name="Sweep", default=False)
    sweep_range: bpy.props.StringProperty(name="Sweep Range", default="")

class BatchSTLInput(bpy.types.PropertyGroup):
    name: bpy.props.StringProperty(name="Input Socket", default="", update=on_input_name_update)
    override_type: bpy.props.StringProperty(default='FLOAT')
    values: bpy.props.CollectionProperty(type=BatchSTLValue)

class BatchSTLNode(bpy.types.PropertyGroup):
    name: bpy.props.StringProperty(name="Target Node", default="", search=search_target_node_cb, description="Select <Modifier Interface> to target the modifier directly")
    inputs: bpy.props.CollectionProperty(type=BatchSTLInput)

class BatchSTLNodeGroup(bpy.types.PropertyGroup):
    group_ptr: bpy.props.PointerProperty(type=bpy.types.NodeTree, name="Node Group")
    nodes: bpy.props.CollectionProperty(type=BatchSTLNode)

class BatchSTLExcludedObject(bpy.types.PropertyGroup):
    name: bpy.props.StringProperty()

class BatchSTLCollection(bpy.types.PropertyGroup):
    collection_ptr: bpy.props.PointerProperty(type=bpy.types.Collection, name="Collection")
    use_tag: bpy.props.BoolProperty(name="Use Tag", default=True)
    tag: bpy.props.StringProperty(name="Tag", default="")
    sub_path: bpy.props.StringProperty(name="Sub-folder", default="")
    use_filter: bpy.props.BoolProperty(name="Filter Objects", default=False)
    excluded_objects: bpy.props.CollectionProperty(type=BatchSTLExcludedObject)
    nodegroups: bpy.props.CollectionProperty(type=BatchSTLNodeGroup)

class BatchSTLExportPreset(bpy.types.PropertyGroup):
    name: bpy.props.StringProperty(name="Preset Name", default="New Preset")
    preset_prefix: bpy.props.StringProperty(name="Preset Root Directory", default="")
    last_export_time: bpy.props.FloatProperty(name="Last Export Time", default=0.0)

    nodegroups: bpy.props.CollectionProperty(type=BatchSTLNodeGroup)
    collections: bpy.props.CollectionProperty(type=BatchSTLCollection)
    collection_index: bpy.props.IntProperty(name="Collection Index", default=0)

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
    filename_ext = ".json"
    filter_glob: bpy.props.StringProperty(default="*.json", options={'HIDDEN'})

    def execute(self, context):
        with open(self.filepath, 'w') as f: json.dump([copy_preset_to_dict(p) for p in context.scene.batch_stl_presets], f, indent=4)
        return {'FINISHED'}

class BATCH_STL_OT_import_presets_json(bpy.types.Operator, ImportHelper):
    bl_idname = "batch_stl.import_presets_json"
    bl_label = "Import JSON"
    bl_options = {'REGISTER', 'UNDO'}
    filename_ext = ".json"
    filter_glob: bpy.props.StringProperty(default="*.json", options={'HIDDEN'})

    def execute(self, context):
        with open(self.filepath, 'r') as f: data = json.load(f)
        for p_data in data: paste_preset_from_dict(context.scene.batch_stl_presets.add(), p_data)
        return {'FINISHED'}

class BATCH_STL_OT_clear_console(bpy.types.Operator):
    bl_idname = "batch_stl.clear_console"
    bl_label = "Clear Console"

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

class BATCH_STL_OT_collection_actions(bpy.types.Operator):
    bl_idname = "batch_stl.collection_actions"
    bl_label = "Collection Actions"
    bl_options = {'REGISTER', 'INTERNAL'}
    action: bpy.props.EnumProperty(items=(('ADD', "", ""), ('REMOVE', "", ""), ('UP', "", ""), ('DOWN', "", ""), ('COPY', "", ""), ('PASTE', "", "")))
    shift_pressed: bpy.props.BoolProperty(options={'HIDDEN', 'SKIP_SAVE'}, default=False)

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
        return {'FINISHED'}


class BATCH_STL_OT_table_action(bpy.types.Operator):
    bl_idname = "batch_stl.table_action"
    bl_label = "Table Action"
    bl_options = {'REGISTER', 'UNDO'}

    action: bpy.props.StringProperty()
    is_pinned: bpy.props.BoolProperty()
    ng_idx: bpy.props.IntProperty(default=-1)
    n_idx: bpy.props.IntProperty(default=-1)
    i_idx: bpy.props.IntProperty(default=-1)
    v_idx: bpy.props.IntProperty(default=-1)
    shift_pressed: bpy.props.BoolProperty(options={'HIDDEN', 'SKIP_SAVE'}, default=False)

    def invoke(self, context, event):
        self.shift_pressed = event.shift
        return self.execute(context)

    def execute(self, context):
        preset = get_active_preset(context.scene)
        if not preset: return {'CANCELLED'}

        ng_list = preset.nodegroups if self.is_pinned else get_active_collection(preset).nodegroups

        if self.action == 'ADD_GROUP':
            ng = ng_list.add()
            node = ng.nodes.add()
            node.name = "<Modifier Interface>"
            inp = node.inputs.add()
            inp.values.add()
        elif self.action == 'DEL_GROUP':
            ng_list.remove(self.ng_idx)

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
            if self.shift_pressed and ng.group_ptr:
                is_mod = not node.name or node.name == "<Modifier Interface>"
                source_inputs = []
                if is_mod and hasattr(ng.group_ptr, "interface"):
                    for item in ng.group_ptr.interface.items_tree:
                        if getattr(item, "item_type", "SOCKET") == 'SOCKET' and getattr(item, "in_out", "INPUT") == 'INPUT':
                            source_inputs.append(item.name)
                elif not is_mod and node.name:
                    target_n = ng.group_ptr.nodes.get(node.name.split(" [")[0].strip())
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
                        return {'FINISHED'}

            inp = node.inputs.add()
            inp.values.add()
        elif self.action == 'DEL_INPUT':
            ng_list[self.ng_idx].nodes[self.n_idx].inputs.remove(self.i_idx)

        elif self.action == 'DEL_VALUE':
            ng_list[self.ng_idx].nodes[self.n_idx].inputs[self.i_idx].values.remove(self.v_idx)

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
                        if inp_obj.override_type in ['FLOAT', 'INT', 'MENU']:
                            ng_obj = ng_list[self.ng_idx]
                            node_obj = ng_obj.nodes[self.n_idx]
                            target = 'MODIFIER' if not node_obj.name or node_obj.name == "<Modifier Interface>" else 'NODE'
                            
                            temp_inp = TempMockInput(inp_obj.name, inp_obj.override_type, val)
                            temp_ovr = TempMockOverride(target, ng_obj.group_ptr, node_obj.name, [temp_inp])
                            
                            parsed_vals = parse_sweep_values(temp_ovr, temp_inp)
                            if parsed_vals:
                                first_val = parsed_vals[0]
                                if inp_obj.override_type == 'FLOAT': val.value_float = first_val
                                elif inp_obj.override_type == 'INT': val.value_int = first_val
                                elif inp_obj.override_type == 'MENU': val.value_menu = str(first_val)
                                
                                for p_val in parsed_vals[1:]:
                                    new_val = vals.add()
                                    new_val.use_sweep = False
                                    if inp_obj.override_type == 'FLOAT': new_val.value_float = p_val
                                    elif inp_obj.override_type == 'INT': new_val.value_int = p_val
                                    elif inp_obj.override_type == 'MENU': new_val.value_menu = str(p_val)

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

        return {'FINISHED'}


class BATCH_STL_OT_toggle_exclusion(bpy.types.Operator):
    bl_idname = "batch_stl.toggle_exclusion"
    bl_label = "Toggle Object Exclusion"
    bl_options = {'REGISTER', 'INTERNAL'}
    object_name: bpy.props.StringProperty()

    def execute(self, context):
        preset = get_active_preset(context.scene)
        collection = get_active_collection(preset)
        if collection:
            idx = -1
            for i, e in enumerate(collection.excluded_objects):
                if e.name == self.object_name:
                    idx = i; break
            if idx >= 0:
                collection.excluded_objects.remove(idx)
                bpy.ops.ed.undo_push(message=f"Include '{self.object_name}' in Export")
            else:
                collection.excluded_objects.add().name = self.object_name
                bpy.ops.ed.undo_push(message=f"Exclude '{self.object_name}' from Export")
        return {'FINISHED'}

class BATCH_STL_OT_toggle_dir_tree(bpy.types.Operator):
    bl_idname = "batch_stl.toggle_dir_tree"
    bl_label = "Toggle Directory Tree"
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

        has_overrides = bool(self.preset.nodegroups) or any(bool(c.nodegroups) for c in self.preset.collections)
        verbose = scene.batch_stl_verbose_console

        if not has_overrides:
            root_dir = bpy.path.abspath(scene.batch_stl_root_dir)
            if self.preset.preset_prefix:
                root_dir = os.path.normpath(os.path.join(root_dir, self.preset.preset_prefix))

            total_objs = 0
            for c in self.preset.collections:
                if not c.collection_ptr: continue
                if is_collection_excluded(context, c.collection_ptr): continue
                excluded_names = {e.name for e in c.excluded_objects} if c.use_filter else set()
                for obj in c.collection_ptr.all_objects:
                    if obj.type in {"MESH", "CURVE", "SURFACE", "META", "FONT"} and not obj.hide_get() and not obj.hide_viewport:
                        if obj.name not in excluded_names: total_objs += 1

            context.window_manager.progress_begin(0, max(1, total_objs))
            depsgraph = context.evaluated_depsgraph_get()
            exported_count = 0

            log_msg = f"\n=== STARTING SYNCHRONOUS EXPORT: {self.preset.name} ==="
            if verbose: print(log_msg)
            log_to_console(self.preset, log_msg)

            for c in self.preset.collections:
                if not c.collection_ptr: continue
                if is_collection_excluded(context, c.collection_ptr): continue
                out_dir = os.path.normpath(os.path.join(root_dir, c.sub_path))
                os.makedirs(out_dir, exist_ok=True)
                excluded_names = {e.name for e in c.excluded_objects} if c.use_filter else set()

                for obj in c.collection_ptr.all_objects:
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
                            base_tag = c.tag if c.use_tag and c.tag else ""
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

class BATCH_STL_UL_collections(bpy.types.UIList):
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


def draw_table_row(layout, ng, node, inp, val, is_pinned, ng_idx, n_idx, i_idx, v_idx, show_ng, show_n, show_i, show_v):
    row = layout.row(align=True)

    # 1. Node Group
    s1 = row.split(factor=0.15)
    c1 = s1.row(align=True)
    if show_ng:
        if ng: c1.prop(ng, "group_ptr", text="")
        op = c1.operator("batch_stl.table_action", text="", icon='ADD'); op.action = 'ADD_GROUP'; op.is_pinned = is_pinned
    else:
        c1.label(text="")

    # 2. Node
    s2 = s1.split(factor=0.15)
    c2 = s2.row(align=True)
    if show_n:
        if node: c2.prop(node, "name", text="", icon='NODETREE')
        if ng:
            op = c2.operator("batch_stl.table_action", text="", icon='ADD'); op.action = 'ADD_NODE'; op.is_pinned = is_pinned; op.ng_idx = ng_idx
    else:
        c2.label(text="")

    # 3. Input
    s3 = s2.split(factor=0.30)
    c3 = s3.row(align=True)
    if show_i:
        if inp:
            is_mod = not node.name or node.name == "<Modifier Interface>"
            if is_mod and ng.group_ptr and hasattr(ng.group_ptr, "interface"):
                c3.prop_search(inp, "name", ng.group_ptr.interface, "items_tree", text="")
            elif not is_mod and ng.group_ptr and node.name:
                target_n = ng.group_ptr.nodes.get(node.name.split(" [")[0].strip())
                if target_n: c3.prop_search(inp, "name", target_n, "inputs", text="")
                else: c3.prop(inp, "name", text="")
            else:
                c3.prop(inp, "name", text="")

        if node:
            op = c3.operator("batch_stl.table_action", text="", icon='ADD'); op.action = 'ADD_INPUT'; op.is_pinned = is_pinned; op.ng_idx = ng_idx; op.n_idx = n_idx
    else:
        c3.label(text="")

    # 4. Value
    s4 = s3.split(factor=0.8)
    c4 = s4.row(align=True)
    if val and inp:
        if getattr(val, "use_sweep", False):
            if inp.override_type in ['INT', 'FLOAT', 'STRING']: c4.prop(val, "sweep_range", text="")
            elif inp.override_type == 'BOOLEAN': c4.label(text="True & False")
            elif inp.override_type == 'MENU': c4.label(text="All values")
        else:
            if inp.override_type == 'BOOLEAN': c4.prop(val, "value_bool", text="True" if val.value_bool else "False", toggle=True)
            elif inp.override_type == 'INT': c4.prop(val, "value_int", text="")
            elif inp.override_type == 'FLOAT': c4.prop(val, "value_float", text="")
            elif inp.override_type == 'STRING': c4.prop(val, "value_string", text="")
            elif inp.override_type == 'MENU': c4.prop(val, "value_menu", text="")
        c4.prop(val, "use_dir", text="", icon='FILE_FOLDER')
        c4.prop(val, "use_tag", text="", icon='BOOKMARKS')
        c4.prop(val, "tag", text="")


    if inp:
        if val and getattr(val, "use_sweep", False):
            op = c4.operator("batch_stl.table_action", text="", icon='FILE_REFRESH', depress=True)
        else:
            op = c4.operator("batch_stl.table_action", text="", icon='ADD')
            
        op.action = 'VALUE_ACTION'
        op.is_pinned = is_pinned; op.ng_idx = ng_idx; op.n_idx = n_idx; op.i_idx = i_idx; op.v_idx = v_idx if val else -1
    else:
        c4.label(text="")

    # 5. Actions
    c5 = s4.row(align=True)

    action_type = None
    if show_ng: action_type = 'GROUP'
    elif show_n: action_type = 'NODE'
    elif show_i: action_type = 'INPUT'
    elif show_v or (val and inp): action_type = 'VALUE'

    if action_type:
        op = c5.operator("batch_stl.table_action", text="", icon='TRIA_UP')
        op.action = f'MOVE_{action_type}_UP'
        op.is_pinned = is_pinned; op.ng_idx = ng_idx; op.n_idx = n_idx; op.i_idx = i_idx; op.v_idx = v_idx

        op = c5.operator("batch_stl.table_action", text="", icon='TRIA_DOWN')
        op.action = f'MOVE_{action_type}_DOWN'
        op.is_pinned = is_pinned; op.ng_idx = ng_idx; op.n_idx = n_idx; op.i_idx = i_idx; op.v_idx = v_idx

        op = c5.operator("batch_stl.table_action", text="", icon='TRASH')
        op.action = f'DEL_{action_type}'
        op.is_pinned = is_pinned; op.ng_idx = ng_idx; op.n_idx = n_idx; op.i_idx = i_idx; op.v_idx = v_idx


def draw_overrides_table(layout, nodegroups, is_pinned):
    box = layout.box()

    header_row = box.row()
    header_row.label(text="Global Pinned Overrides" if is_pinned else "Local Overrides", icon='PINNED' if is_pinned else 'UNPINNED')
    op = header_row.operator("batch_stl.table_action", text="Add Node Group", icon='ADD')
    op.action = 'ADD_GROUP'; op.is_pinned = is_pinned

    if len(nodegroups) == 0:
        box.label(text="No overrides defined.")
        return

    # Table Header
    h_row = box.row(align=True)
    s1 = h_row.split(factor=0.15); s1.label(text="Node Group")
    s2 = s1.split(factor=0.18); s2.label(text="Target Node")
    s3 = s2.split(factor=0.35); s3.label(text="Input Socket")
    s4 = s3.split(factor=0.8); s4.label(text="Value & Options")
    s4.label(text="Actions")

    for ng_idx, ng in enumerate(nodegroups):
        ng_first = True
        if not ng.nodes:
            draw_table_row(box, ng, None, None, None, is_pinned, ng_idx, -1, -1, -1, ng_first, True, True, True)
            continue

        for n_idx, node in enumerate(ng.nodes):
            n_first = True
            if not node.inputs:
                draw_table_row(box, ng, node, None, None, is_pinned, ng_idx, n_idx, -1, -1, ng_first, n_first, True, True)
                ng_first = False
                continue

            for i_idx, inp in enumerate(node.inputs):
                i_first = True
                if not inp.values:
                    draw_table_row(box, ng, node, inp, None, is_pinned, ng_idx, n_idx, i_idx, -1, ng_first, n_first, i_first, True)
                    ng_first = False; n_first = False
                    continue

                for v_idx, val in enumerate(inp.values):
                    draw_table_row(box, ng, node, inp, val, is_pinned, ng_idx, n_idx, i_idx, v_idx, ng_first, n_first, i_first, v_idx == 0)
                    ng_first = False; n_first = False; i_first = False


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

        total_collections = len(active_preset.collections)
        total_objects = 0
        total_preset_combos = 0

        for c in active_preset.collections:
            c_combos = len(generate_override_combinations(get_flat_overrides(active_preset.nodegroups) + get_flat_overrides(c.nodegroups)))
            total_preset_combos += c_combos
            obj_count = 0
            if c.collection_ptr:
                excluded_names = {e.name for e in c.excluded_objects} if getattr(c, "use_filter", False) else set()
                for obj in c.collection_ptr.all_objects:
                    if obj.type in {"MESH", "CURVE", "SURFACE", "META", "FONT"} and not obj.hide_get() and not obj.hide_viewport:
                        if obj.name not in excluded_names: obj_count += 1
            total_objects += (obj_count * c_combos)

        layout.separator(factor=0.5)
        m_box = layout.box()
        m_header = m_box.row()
        icon_m = 'TRIA_DOWN' if scene.batch_stl_ui_collections else 'TRIA_RIGHT'
        m_header.prop(scene, "batch_stl_ui_collections", text="", icon=icon_m, emboss=False)

        m_title = f"{active_preset.name} | {total_collections} collections | {total_preset_combos} combos | {total_objects} objects total"
        m_header.label(text=m_title, icon='OUTLINER_COLLECTION')
        draw_inline_controls(m_header, "batch_stl.collection_actions", use_clipboard=True)

        if scene.batch_stl_ui_collections:
            m_box.template_list("BATCH_STL_UL_collections", "", active_preset, "collections", active_preset, "collection_index", rows=5)
            active_col = get_active_collection(active_preset)
            if active_col and active_col.collection_ptr:
                m_box.separator()
                if active_col.use_filter:
                    filter_box = m_box.box()
                    f_header = filter_box.row()
                    icon_f = 'TRIA_DOWN' if scene.batch_stl_ui_exclude else 'TRIA_RIGHT'
                    f_header.prop(scene, "batch_stl_ui_exclude", text="", icon=icon_f, emboss=False)
                    f_header.label(text="Exclude Objects:", icon='FILTER')

                    if scene.batch_stl_ui_exclude:
                        col = filter_box.column(align=True)
                        for obj in active_col.collection_ptr.all_objects:
                            if obj.type not in {"MESH", "CURVE", "SURFACE", "META", "FONT"}: continue
                            is_excl = any(e.name == obj.name for e in active_col.excluded_objects)
                            icon_btn = 'CHECKBOX_DEHLT' if is_excl else 'CHECKBOX_HLT'
                            op = col.operator("batch_stl.toggle_exclusion", text=obj.name, icon=icon_btn, depress=not is_excl)
                            op.object_name = obj.name

        layout.separator()

        if get_active_collection(active_preset):
            active_col = get_active_collection(active_preset)
            all_ovrs = get_flat_overrides(active_preset.nodegroups) + get_flat_overrides(active_col.nodegroups)
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
            if active_col.collection_ptr:
                excluded_names = {e.name for e in active_col.excluded_objects} if getattr(active_col, "use_filter", False) else set()
                for obj in active_col.collection_ptr.all_objects:
                    if obj.type in {"MESH", "CURVE", "SURFACE", "META", "FONT"} and not obj.hide_get() and not obj.hide_viewport:
                        if obj.name not in excluded_names: active_obj_count += 1

            mapping_total_objects = active_obj_count * num_combos
            c_name = active_col.collection_ptr.name if active_col.collection_ptr else "Unassigned"
            if active_col.tag: c_name += f" [{active_col.tag}]"

            metric_str = f"{c_name} | {num_targets} targets | {total_inputs} inputs | {num_combos} combos | {mapping_total_objects} objects"
            header = layout.row()
            header.label(text=metric_str, icon='MODIFIER')
            layout.separator()

            # Global Table
            g_header = layout.row()
            icon_g = 'TRIA_DOWN' if scene.batch_stl_ui_global_ovr else 'TRIA_RIGHT'
            g_header.prop(scene, "batch_stl_ui_global_ovr", text="", icon=icon_g, emboss=False)
            g_header.label(text="Global Pinned Overrides (Shared)")
            if scene.batch_stl_ui_global_ovr:
                draw_overrides_table(layout, active_preset.nodegroups, is_pinned=True)

            layout.separator(factor=0.5)

            # Local Table
            l_header = layout.row()
            icon_l = 'TRIA_DOWN' if scene.batch_stl_ui_local_ovr else 'TRIA_RIGHT'
            l_header.prop(scene, "batch_stl_ui_local_ovr", text="", icon=icon_l, emboss=False)
            l_header.label(text="Local Overrides (Specific to Collection)")
            if scene.batch_stl_ui_local_ovr:
                draw_overrides_table(layout, active_col.nodegroups, is_pinned=False)

            layout.separator(factor=0.5)
            tip_box = layout.box()
            tip_header = tip_box.row()
            icon_tip = 'TRIA_DOWN' if scene.batch_stl_ui_tips else 'TRIA_RIGHT'
            tip_header.prop(scene, "batch_stl_ui_tips", text="", icon=icon_tip, emboss=False)
            tip_header.label(text="OVERRIDE INFO", icon='INFO')

            if scene.batch_stl_ui_tips:
                col = tip_box.column()
                col.label(text="Hierarchy: NodeGroup > Node > Input > Value.", icon='BLANK1')
                col.label(text="For modifier targets, leave Node blank or set as <Modifier Interface>", icon='BLANK1')
                col.label(text="Types are auto-assigned when inputs are selected.", icon='BLANK1')
                col.label(text="Use Sweep button to iterate through values automatically:", icon='FILE_REFRESH')
                col.label(text="  • Floats/Ints: Specify start value, step size, and step count", icon='BLANK1')
                col.label(text="  • Menus/Bools: Automatically iterates through all values", icon='BLANK1')

                col.separator()
                col.label(text="Tag String Formatting:", icon='BLANK1')
                col.label(text="  • [ tag ] replaces input value with the tag", icon='BLANK1')
                col.label(text="  • [ _tag ] appends the tag to the input value", icon='BLANK1')
                col.label(text="  • [ tag_ ] prepends the tag to the input value", icon='BLANK1')

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

            col = t_box.column(align=True)
            draw_tree_dict(col, tree_dict, duplicates=duplicates)

        layout.separator()
        layout.prop(scene, "batch_stl_verbose_console", toggle=True, icon='CONSOLE')


# ==============================================================================
# === [ 6. REGISTRATION & LIFECYCLE ] ===
# ==============================================================================

@persistent
def reset_batch_stl_state(scene):
    try:
        for p in bpy.context.scene.batch_stl_presets:
            p.is_exporting = False
            p.cancel_export = False
            p.export_progress = 0.0
            p.export_status = ""
    except Exception: pass

classes = (
    # 1. Properties
    BatchSTLLogLine,
    BatchSTLValue,
    BatchSTLInput,
    BatchSTLNode,
    BatchSTLNodeGroup,
    BatchSTLExcludedObject,
    BatchSTLCollection,
    BatchSTLExportPreset,

    # 2. UI Lists
    BATCH_STL_UL_presets,
    BATCH_STL_UL_collections,
    BATCH_STL_UL_console_logs,

    # 3. Operators
    BATCH_STL_OT_clear_console,
    BATCH_STL_OT_preset_actions,
    BATCH_STL_OT_collection_actions,
    BATCH_STL_OT_table_action,
    BATCH_STL_OT_toggle_exclusion,
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

def register():
    for cls in classes:
        bpy.utils.register_class(cls)

    bpy.types.Scene.batch_stl_root_dir = bpy.props.StringProperty(name="Root Export Dir", default="//", subtype="DIR_PATH")
    bpy.types.Scene.batch_stl_presets = bpy.props.CollectionProperty(type=BatchSTLExportPreset)
    bpy.types.Scene.batch_stl_preset_index = bpy.props.IntProperty(name="Active Preset", default=0)
    bpy.types.Scene.batch_stl_verbose_console = bpy.props.BoolProperty(name="Verbose Console Output", default=False)

    bpy.types.Scene.batch_stl_ui_presets = bpy.props.BoolProperty(default=True)
    bpy.types.Scene.batch_stl_ui_collections = bpy.props.BoolProperty(default=True)
    bpy.types.Scene.batch_stl_ui_global_ovr = bpy.props.BoolProperty(default=True)
    bpy.types.Scene.batch_stl_ui_local_ovr = bpy.props.BoolProperty(default=True)
    bpy.types.Scene.batch_stl_ui_exclude = bpy.props.BoolProperty(default=True)
    bpy.types.Scene.batch_stl_ui_tips = bpy.props.BoolProperty(default=False)
    bpy.types.Scene.batch_stl_show_tree = bpy.props.BoolProperty(default=True, update=update_show_tree)
    bpy.types.Scene.batch_stl_show_console = bpy.props.BoolProperty(default=False)
    bpy.types.Scene.batch_stl_collapsed_dirs = bpy.props.StringProperty(default="[]")

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
        "batch_stl_verbose_console", "batch_stl_ui_presets", "batch_stl_ui_collections",
        "batch_stl_ui_global_ovr", "batch_stl_ui_local_ovr", "batch_stl_ui_exclude",
        "batch_stl_show_tree", "batch_stl_show_console", "batch_stl_collapsed_dirs",
        "batch_stl_ui_tips"
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
