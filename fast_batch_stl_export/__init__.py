import os
import json
import time
import struct
import bpy
import numpy as np
from bpy_extras.io_utils import ExportHelper, ImportHelper

# --- SESSION CLIPBOARD ---
_clipboard = {
    "preset": None,
    "mapping": None,
    "override": None,
    "input": None
}

# --- FAST EXPORT FUNCTION ---

def write_fast_binary_stl(filepath, mesh, matrix_world):
    t_start = time.perf_counter()

    mesh.calc_loop_triangles()
    num_tris = len(mesh.loop_triangles)
    if num_tris == 0:
        return

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
        ('normals', np.float32, (3,)),
        ('v0', np.float32, (3,)),
        ('v1', np.float32, (3,)),
        ('v2', np.float32, (3,)),
        ('attr', np.uint16)
    ])
    data = np.zeros(num_tris, dtype=stl_dtype)
    data['normals'] = tri_normals
    data['v0'] = verts[tri_verts[:, 0]]
    data['v1'] = verts[tri_verts[:, 1]]
    data['v2'] = verts[tri_verts[:, 2]]

    t_format = time.perf_counter()

    with open(filepath, 'wb') as f:
        f.write(b'Batch STL Fast Export' + b'\x00' * 59)
        f.write(struct.pack('<I', num_tris))
        f.write(data.tobytes())

    t_write = time.perf_counter()
    print(f"  │         ├─ STL Write: Triangulate/Format: {t_format-t_start:.4f}s | Disk Write: {t_write-t_format:.4f}s")


# --- STATE-HASH & OVERRIDE LOGIC ---

def is_collection_excluded(context, target_collection):
    """Recursively checks if a collection is excluded (unchecked) in the active view layer."""
    def traverse(layer_collection):
        if layer_collection.collection == target_collection:
            return layer_collection.exclude
        for child in layer_collection.children:
            result = traverse(child)
            if result is not None:
                return result
        return None

    result = traverse(context.view_layer.layer_collection)
    return result if result is not None else True # Treat as excluded if missing from view layer entirely

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

def apply_overrides(overrides, target_objects):
    global_states = []
    mod_states = []

    for override in overrides:
        if override.override_target == 'NODE' and override.parent_group_ptr and override.node_name:
            parent_tree = override.parent_group_ptr
            for n_name in [n.strip() for n in override.node_name.split(',') if n.strip()]:
                target_node = parent_tree.nodes.get(n_name)
                if not target_node: continue

                for inp in override.inputs:
                    socket = target_node.inputs.get(inp.input_name)
                    if not socket: continue

                    link_from = socket.links[0].from_socket if socket.is_linked else None
                    global_states.append(('SOCKET', socket, socket.default_value, link_from, parent_tree))

                    if socket.is_linked: parent_tree.links.remove(socket.links[0])

                    val = get_input_value(inp)
                    if val is not None: socket.default_value = val

            parent_tree.update_tag()

        elif override.override_target == 'MODIFIER' and override.parent_group_ptr:
            for obj in target_objects:
                obj_changed = False
                for mod in obj.modifiers:
                    if mod.type == 'NODES' and mod.node_group == override.parent_group_ptr:
                        for inp in override.inputs:
                            ident = get_modifier_socket_identifier(mod.node_group, inp.input_name)
                            if ident:
                                orig_val, is_set = get_modifier_input(mod, ident)
                                default_val = get_modifier_socket_default(mod.node_group, inp.input_name)
                                mod_states.append((mod, ident, is_set, orig_val, default_val))

                                val = get_input_value(inp)
                                if val is not None:
                                    set_modifier_input(mod, ident, val)
                                    obj_changed = True
                if obj_changed:
                    obj.update_tag()

    return global_states, mod_states

def revert_overrides(global_states, mod_states):
    for mod, ident, is_set, orig_val, default_val in mod_states:
        if is_set and orig_val is not None: set_modifier_input(mod, ident, orig_val)
        else: unset_modifier_input(mod, ident, default_val)
        mod.id_data.update_tag()

    for state in global_states:
        if state[0] == 'SOCKET':
            _, socket, original_val, link_from, parent_tree = state
            try:
                socket.default_value = original_val
                if link_from: parent_tree.links.new(link_from, socket)
                parent_tree.update_tag()
            except Exception: pass


def get_active_preset(scene):
    presets = scene.batch_stl_presets
    index = scene.batch_stl_preset_index
    if presets and 0 <= index < len(presets):
        return presets[index]
    return None

def get_active_mapping(preset):
    if preset and preset.mappings and 0 <= preset.mapping_index < len(preset.mappings):
        return preset.mappings[preset.mapping_index]
    return None

# --- PROPERTIES ---

def on_input_name_update(self, context):
    try:
        ovr = None
        for p in context.scene.batch_stl_presets:
            for o in p.pinned_overrides:
                if self in o.inputs.values():
                    ovr = o
                    break
            if ovr: break
            for m in p.mappings:
                for o in m.node_overrides:
                    if self in o.inputs.values():
                        ovr = o
                        break
                if ovr: break
            if ovr: break

        if ovr and ovr.parent_group_ptr:
            if ovr.override_target == 'NODE' and ovr.node_name:
                node = ovr.parent_group_ptr.nodes.get(ovr.node_name)
                if node and self.input_name in node.inputs:
                    s_type = node.inputs[self.input_name].type
                    if s_type in ['VALUE', 'FLOAT']: self.override_type = 'FLOAT'
                    elif s_type == 'INT': self.override_type = 'INT'
                    elif s_type == 'BOOLEAN': self.override_type = 'BOOLEAN'
                    elif s_type == 'STRING': self.override_type = 'STRING'
                    elif s_type == 'MENU': self.override_type = 'MENU'
    except Exception:
        pass

class BatchSTLNodeInput(bpy.types.PropertyGroup):
    input_name: bpy.props.StringProperty(name="Input", default="", update=on_input_name_update)
    override_type: bpy.props.EnumProperty(
        name="Type",
        items=(
            ('BOOLEAN', "Bool", ""),
            ('INT', "Int", ""),
            ('FLOAT', "Float", ""),
            ('STRING', "Str", ""),
            ('MENU', "Menu", ""),
        ),
        default='BOOLEAN'
    )
    value_bool: bpy.props.BoolProperty(name="Value", default=True)
    value_int: bpy.props.IntProperty(name="Value", default=0)
    value_float: bpy.props.FloatProperty(name="Value", default=0.0)
    value_string: bpy.props.StringProperty(name="Value", default="")
    value_menu: bpy.props.StringProperty(name="Value", default="")

class BatchSTLNodeOverride(bpy.types.PropertyGroup):
    override_target: bpy.props.EnumProperty(
        name="Target",
        items=(('NODE', "Node", ""), ('MODIFIER', "Mod", "")),
        default='NODE'
    )
    parent_group_ptr: bpy.props.PointerProperty(type=bpy.types.NodeTree, name="Group")
    node_name: bpy.props.StringProperty(name="Node", default="")
    inputs: bpy.props.CollectionProperty(type=BatchSTLNodeInput)

class BatchSTLExportItem(bpy.types.PropertyGroup):
    collection_ptr: bpy.props.PointerProperty(type=bpy.types.Collection, name="Collection")
    use_tag: bpy.props.BoolProperty(name="Use Tag", default=True, description="Append tag to object name")
    tag: bpy.props.StringProperty(name="Tag", default="")
    sub_path: bpy.props.StringProperty(name="Sub-folder", default="")
    node_overrides: bpy.props.CollectionProperty(type=BatchSTLNodeOverride)

class BatchSTLExportPreset(bpy.types.PropertyGroup):
    name: bpy.props.StringProperty(name="Preset Name", default="New Preset")
    preset_prefix: bpy.props.StringProperty(name="Preset Root Directory", default="")
    pinned_overrides: bpy.props.CollectionProperty(type=BatchSTLNodeOverride)
    mappings: bpy.props.CollectionProperty(type=BatchSTLExportItem)
    mapping_index: bpy.props.IntProperty(default=0)


# --- JSON UTILS ---

def copy_override_to_dict(o):
    o_data = {
        "override_target": o.override_target,
        "parent_group": o.parent_group_ptr.name if o.parent_group_ptr else "",
        "node_name": o.node_name,
        "inputs": [{"input_name": i.input_name, "override_type": i.override_type, "value_bool": i.value_bool, "value_int": i.value_int, "value_float": i.value_float, "value_string": i.value_string, "value_menu": i.value_menu} for i in o.inputs]
    }
    return o_data

def paste_override_from_dict(new_o, data):
    new_o.override_target = data["override_target"]
    pg_name = data["parent_group"]
    new_o.parent_group_ptr = bpy.data.node_groups.get(pg_name) if pg_name else None
    new_o.node_name = data["node_name"]
    for i_data in data["inputs"]:
        new_i = new_o.inputs.add()
        for k, v in i_data.items(): setattr(new_i, k, v)

def copy_preset_to_dict(src):
    return {
        "name": src.name, "preset_prefix": src.preset_prefix,
        "pinned_overrides": [copy_override_to_dict(o) for o in src.pinned_overrides],
        "mappings": [{"collection_name": m.collection_ptr.name if m.collection_ptr else "", "use_tag": m.use_tag, "tag": m.tag, "sub_path": m.sub_path, "overrides": [copy_override_to_dict(o) for o in m.node_overrides]} for m in src.mappings]
    }

def paste_preset_from_dict(new_p, data):
    new_p.name = data.get("name", "Imported Preset")
    new_p.preset_prefix = data.get("preset_prefix", "")
    for o_data in data.get("pinned_overrides", []): paste_override_from_dict(new_p.pinned_overrides.add(), o_data)
    for m_data in data.get("mappings", []):
        new_m = new_p.mappings.add()
        c_name = m_data.get("collection_name", "")
        new_m.collection_ptr = bpy.data.collections.get(c_name) if c_name else None
        new_m.use_tag = m_data.get("use_tag", True)
        new_m.tag = m_data.get("tag", "")
        new_m.sub_path = m_data.get("sub_path", "")
        for o_data in m_data.get("overrides", []): paste_override_from_dict(new_m.node_overrides.add(), o_data)

class BATCH_STL_OT_export_presets_json(bpy.types.Operator, ExportHelper):
    bl_idname = "batch_stl.export_presets_json"
    bl_label = "Export JSON"
    filename_ext = ".json"
    filter_glob: bpy.props.StringProperty(default="*.json", options={'HIDDEN'})

    def execute(self, context):
        with open(self.filepath, 'w') as f:
            json.dump([copy_preset_to_dict(p) for p in context.scene.batch_stl_presets], f, indent=4)
        return {'FINISHED'}

class BATCH_STL_OT_import_presets_json(bpy.types.Operator, ImportHelper):
    bl_idname = "batch_stl.import_presets_json"
    bl_label = "Import JSON"
    filename_ext = ".json"
    filter_glob: bpy.props.StringProperty(default="*.json", options={'HIDDEN'})

    def execute(self, context):
        with open(self.filepath, 'r') as f:
            data = json.load(f)
        for p_data in data: paste_preset_from_dict(context.scene.batch_stl_presets.add(), p_data)
        return {'FINISHED'}

# --- UI LISTS & OPERATORS ---

class BATCH_STL_UL_presets(bpy.types.UIList):
    def draw_item(self, context, layout, data, item, icon, active_data, active_propname, index):
        row = layout.row(align=True)
        row.prop(item, "name", text="", emboss=False, icon='PRESET')
        op = row.operator("export_scene.batch_stl_multi", text="", icon='EXPORT')
        op.preset_index = index

class BATCH_STL_UL_items(bpy.types.UIList):
    def draw_item(self, context, layout, data, item, icon, active_data, active_propname, index):
        row = layout.row(align=True)
        row.prop(item, "collection_ptr", text="")
        sub_row = row.row(align=True)
        sub_row.prop(item, "use_tag", text="", icon='BOOKMARKS')
        tag_row = sub_row.row(align=True)
        tag_row.prop(item, "tag", text="")
        row.prop(item, "sub_path", text="", emboss=False, icon='FILE_FOLDER')

class BATCH_STL_OT_preset_actions(bpy.types.Operator):
    bl_idname = "batch_stl.preset_actions"
    bl_label = "Preset Actions"
    action: bpy.props.EnumProperty(items=(('ADD', "", ""), ('REMOVE', "", ""), ('UP', "", ""), ('DOWN', "", ""), ('COPY', "", ""), ('PASTE', "", "")))

    def execute(self, context):
        lst = context.scene.batch_stl_presets
        idx = context.scene.batch_stl_preset_index
        if self.action == 'ADD': lst.add(); context.scene.batch_stl_preset_index = len(lst) - 1
        elif self.action == 'REMOVE' and lst: lst.remove(idx); context.scene.batch_stl_preset_index = max(0, idx - 1)
        elif self.action == 'UP' and idx > 0: lst.move(idx, idx - 1); context.scene.batch_stl_preset_index -= 1
        elif self.action == 'DOWN' and idx < len(lst) - 1: lst.move(idx, idx + 1); context.scene.batch_stl_preset_index += 1
        elif self.action == 'COPY' and lst: _clipboard["preset"] = copy_preset_to_dict(lst[idx])
        elif self.action == 'PASTE' and _clipboard.get("preset"): paste_preset_from_dict(lst.add(), _clipboard["preset"]); context.scene.batch_stl_preset_index = len(lst) - 1
        return {'FINISHED'}

class BATCH_STL_OT_mapping_actions(bpy.types.Operator):
    bl_idname = "batch_stl.mapping_actions"
    bl_label = "Mapping Actions"
    action: bpy.props.EnumProperty(items=(('ADD', "", ""), ('REMOVE', "", ""), ('UP', "", ""), ('DOWN', "", ""), ('COPY', "", ""), ('PASTE', "", "")))

    def execute(self, context):
        preset = get_active_preset(context.scene)
        if not preset: return {'CANCELLED'}
        lst, idx = preset.mappings, preset.mapping_index
        if self.action == 'ADD': lst.add(); preset.mapping_index = len(lst) - 1
        elif self.action == 'REMOVE' and lst: lst.remove(idx); preset.mapping_index = max(0, idx - 1)
        elif self.action == 'UP' and idx > 0: lst.move(idx, idx - 1); preset.mapping_index -= 1
        elif self.action == 'DOWN' and idx < len(lst) - 1: lst.move(idx, idx + 1); preset.mapping_index += 1
        elif self.action == 'COPY' and lst: _clipboard["mapping"] = copy_mapping_to_dict(lst[idx])
        elif self.action == 'PASTE' and _clipboard.get("mapping"): paste_mapping_from_dict(lst.add(), _clipboard["mapping"]); preset.mapping_index = len(lst) - 1
        return {'FINISHED'}

class BATCH_STL_OT_override_actions(bpy.types.Operator):
    bl_idname = "batch_stl.override_actions"
    bl_label = "Override Actions"
    action: bpy.props.EnumProperty(items=(('ADD', "", ""), ('REMOVE', "", ""), ('UP', "", ""), ('DOWN', "", ""), ('COPY', "", ""), ('PASTE', "", ""), ('PIN', "", ""), ('UNPIN', "", "")))
    override_index: bpy.props.IntProperty(default=-1)
    is_pinned: bpy.props.BoolProperty(default=False)

    def execute(self, context):
        preset = get_active_preset(context.scene)
        mapping = get_active_mapping(preset)
        if not preset: return {'CANCELLED'}
        lst = preset.pinned_overrides if self.is_pinned else (mapping.node_overrides if mapping else None)
        if lst is None and self.action not in {'ADD', 'PASTE'}: return {'CANCELLED'}
        idx = self.override_index

        if self.action == 'ADD' and mapping: mapping.node_overrides.add()
        elif self.action == 'REMOVE' and 0 <= idx < len(lst): lst.remove(idx)
        elif self.action == 'UP' and idx > 0: lst.move(idx, idx - 1)
        elif self.action == 'DOWN' and 0 <= idx < len(lst) - 1: lst.move(idx, idx + 1)
        elif self.action == 'COPY' and 0 <= idx < len(lst): _clipboard["override"] = copy_override_to_dict(lst[idx])
        elif self.action == 'PASTE' and _clipboard.get("override"):
            target_lst = preset.pinned_overrides if self.is_pinned else (mapping.node_overrides if mapping else None)
            if target_lst is not None:
                paste_override_from_dict(target_lst.add(), _clipboard["override"])
                if 0 <= idx < len(target_lst): target_lst.move(len(target_lst) - 1, idx + 1)
        elif self.action == 'PIN' and not self.is_pinned and 0 <= idx < len(lst):
            paste_override_from_dict(preset.pinned_overrides.add(), copy_override_to_dict(lst[idx]))
            lst.remove(idx)
        elif self.action == 'UNPIN' and self.is_pinned and 0 <= idx < len(lst):
            for m in preset.mappings: paste_override_from_dict(m.node_overrides.add(), copy_override_to_dict(lst[idx]))
            lst.remove(idx)
        return {'FINISHED'}

class BATCH_STL_OT_input_actions(bpy.types.Operator):
    bl_idname = "batch_stl.input_actions"
    bl_label = "Input Actions"
    action: bpy.props.EnumProperty(items=(('ADD', "", ""), ('REMOVE', "", ""), ('UP', "", ""), ('DOWN', "", ""), ('COPY', "", ""), ('PASTE', "", "")))
    override_index: bpy.props.IntProperty(default=-1)
    input_index: bpy.props.IntProperty(default=-1)
    is_pinned: bpy.props.BoolProperty(default=False)

    def execute(self, context):
        preset = get_active_preset(context.scene)
        mapping = get_active_mapping(preset)
        ovr_list = preset.pinned_overrides if self.is_pinned else (mapping.node_overrides if mapping else None)
        if not ovr_list or self.override_index < 0 or self.override_index >= len(ovr_list): return {'CANCELLED'}
        lst, idx = ovr_list[self.override_index].inputs, self.input_index

        if self.action == 'ADD': lst.add()
        elif self.action == 'REMOVE' and 0 <= idx < len(lst): lst.remove(idx)
        elif self.action == 'UP' and idx > 0: lst.move(idx, idx - 1)
        elif self.action == 'DOWN' and 0 <= idx < len(lst) - 1: lst.move(idx, idx + 1)
        elif self.action == 'COPY' and 0 <= idx < len(lst):
            i = lst[idx]
            _clipboard["input"] = {"input_name": i.input_name, "override_type": i.override_type, "value_bool": i.value_bool, "value_int": i.value_int, "value_float": i.value_float, "value_string": i.value_string, "value_menu": i.value_menu}
        elif self.action == 'PASTE' and _clipboard.get("input"):
            new_i = lst.add()
            for k, v in _clipboard["input"].items(): setattr(new_i, k, v)
            if 0 <= idx < len(lst): lst.move(len(lst) - 1, idx + 1)
        return {'FINISHED'}


# --- BATCH EXPORT OPERATOR ---

class EXPORT_OT_batch_stl_multi(bpy.types.Operator):
    bl_idname = "export_scene.batch_stl_multi"
    bl_label = "Batch Export STLs"
    bl_options = {"REGISTER", "UNDO"}
    preset_index: bpy.props.IntProperty(default=-1)

    @classmethod
    def poll(cls, context):
        return len(context.scene.batch_stl_presets) > 0

    def execute(self, context):
        total_time_start = time.perf_counter()

        scene = context.scene
        preset = scene.batch_stl_presets[self.preset_index] if 0 <= self.preset_index < len(scene.batch_stl_presets) else get_active_preset(scene)

        if not preset or not scene.batch_stl_root_dir:
            self.report({'ERROR'}, "Missing Preset or Root Directory")
            return {"CANCELLED"}

        print(f"\n=== STARTING ISOLATED HASH-BATCHED EXPORT: {preset.name} ===")

        root_dir = bpy.path.abspath(scene.batch_stl_root_dir)
        if preset.preset_prefix:
            root_dir = os.path.normpath(os.path.join(root_dir, preset.preset_prefix))

        # =========================================================
        # PHASE 0: Absolute Depsgraph Isolation
        # =========================================================
        print("\n  [Phase 0] Absolute Depsgraph Isolation...")
        t_phase0_start = time.perf_counter()

        # Combine visible objects AND all mapped objects to guarantee we capture
        # the states of anything we might touch, even if excluded from view layer.
        objects_to_mute = set(context.view_layer.objects)
        for mapping in preset.mappings:
            if mapping.collection_ptr:
                objects_to_mute.update(mapping.collection_ptr.all_objects)

        original_mod_states = {}
        for obj in objects_to_mute:
            if hasattr(obj, 'modifiers'):
                for mod in obj.modifiers:
                    if mod.type == 'NODES':
                        original_mod_states[mod] = mod.show_viewport
                        mod.show_viewport = False

        print(f"    └─ Muted {len(original_mod_states)} modifiers across scene in {time.perf_counter() - t_phase0_start:.4f}s")

        def enable_modifiers(objects):
            for obj in objects:
                if hasattr(obj, 'modifiers'):
                    for mod in obj.modifiers:
                        if mod.type == 'NODES' and mod in original_mod_states:
                            mod.show_viewport = original_mod_states[mod]

        def disable_modifiers(objects):
            for obj in objects:
                if hasattr(obj, 'modifiers'):
                    for mod in obj.modifiers:
                        if mod.type == 'NODES' and mod in original_mod_states:
                            mod.show_viewport = False

        try:
            # 1. Compile execution batches by identical override signatures
            execution_batches = {}
            sig_pinned = get_override_signature(preset.pinned_overrides)

            for mapping in preset.mappings:
                if not mapping.collection_ptr: continue

                # Check View Layer visibility: Skip excluded collections
                if is_collection_excluded(context, mapping.collection_ptr):
                    print(f"  ├─ Skipping '{mapping.collection_ptr.name}' (Excluded from View Layer)")
                    continue

                sig_local = get_override_signature(mapping.node_overrides)
                full_sig = sig_pinned + sig_local

                if full_sig not in execution_batches:
                    execution_batches[full_sig] = []
                execution_batches[full_sig].append(mapping)

            if not execution_batches:
                print("  └─ No active collections to export (all mapped collections are excluded).")
                return {"FINISHED"}

            batch_counter = 1

            # 2. Process each unique signature state exactly once
            for signature, mappings_in_batch in execution_batches.items():
                t_batch_start = time.perf_counter()

                is_clean_batch = len(mappings_in_batch[0].node_overrides) == 0
                batch_type = "Clean (Pinned Only)" if is_clean_batch else f"Dirty ({len(mappings_in_batch[0].node_overrides)} Local Overrides)"

                collection_names = [m.collection_ptr.name for m in mappings_in_batch]
                print(f"  ├─ Batch {batch_counter}/{len(execution_batches)} [{batch_type}]: Processing {len(collection_names)} mapped instances -> {', '.join(collection_names)}")

                batch_objects = set()
                for m in mappings_in_batch:
                    batch_objects.update(m.collection_ptr.all_objects)

                first_mapping = mappings_in_batch[0]
                all_overrides = list(preset.pinned_overrides) + list(first_mapping.node_overrides)

                t_en = time.perf_counter()
                enable_modifiers(batch_objects)
                print(f"  │    ├─ Enabled Batch Modifiers: {time.perf_counter() - t_en:.4f}s")

                t_ovr = time.perf_counter()

                # Apply Direct Mutation (safely severing links)
                global_states, mod_states = apply_overrides(all_overrides, batch_objects)

                # Push the global Depsgraph update for the active view layer
                context.view_layer.update()
                depsgraph = context.evaluated_depsgraph_get()

                print(f"  │    ├─ Applied & Synced Graph: {time.perf_counter() - t_ovr:.4f}s")

                # 3. Export all mappings that share this evaluated state
                for mapping in mappings_in_batch:
                    self.export_mapping_fast(context, depsgraph, mapping, root_dir)

                t_rev = time.perf_counter()

                # Revert mutations, reconnect links, and re-sync
                revert_overrides(global_states, mod_states)
                context.view_layer.update()
                print(f"  │    ├─ Reverted batch overrides: {time.perf_counter() - t_rev:.4f}s")

                t_dis = time.perf_counter()
                disable_modifiers(batch_objects)
                print(f"  │    └─ Disabled Batch Modifiers: {time.perf_counter() - t_dis:.4f}s")

                print(f"  │    => Batch Total Time: {time.perf_counter() - t_batch_start:.4f}s\n")
                batch_counter += 1

        finally:
            # =========================================================
            # PHASE 4: Safe Restoration
            # =========================================================
            print("\n  [Phase 4] Safe State Restoration...")
            t_ph4_start = time.perf_counter()

            for mod, original_state in original_mod_states.items():
                try:
                    mod.show_viewport = original_state
                except ReferenceError:
                    pass
            print(f"    ├─ Restored {len(original_mod_states)} scene modifiers in {time.perf_counter() - t_ph4_start:.4f}s")

            t_final_upd = time.perf_counter()
            context.view_layer.update()
            print(f"    ├─ Final Depsgraph Recovery Update in {time.perf_counter() - t_final_upd:.4f}s")
            print(f"    └─ Phase 4 Total: {time.perf_counter() - t_ph4_start:.4f}s")

        print(f"\n=== BATCH EXPORT COMPLETE: {time.perf_counter() - total_time_start:.4f}s Total ===\n")
        self.report({'INFO'}, f"Batch Export Complete for {preset.name}. See console for timings.")
        return {"FINISHED"}

    def export_mapping_fast(self, context, depsgraph, mapping, root_dir):
        t_start = time.perf_counter()
        out_dir = os.path.normpath(os.path.join(root_dir, mapping.sub_path))
        os.makedirs(out_dir, exist_ok=True)

        print(f"  │    ├─ Exporting: {mapping.collection_ptr.name}{' ['+mapping.tag+']' if mapping.tag else ''}")

        for obj in mapping.collection_ptr.all_objects:
            if obj.type in {"MESH", "CURVE", "SURFACE", "META", "FONT"} and not obj.hide_get() and not obj.hide_viewport:
                t_eval = time.perf_counter()

                # Fetch dynamically fully-evaluated geometry directly from depsgraph
                obj_eval = obj.evaluated_get(depsgraph)
                try:
                    mesh = obj_eval.to_mesh()
                except RuntimeError:
                    mesh = None

                print(f"  │         ├─ Evaluated Mesh [{obj.name}]: {time.perf_counter() - t_eval:.4f}s")

                if mesh:
                    tag_str = mapping.tag if mapping.use_tag and mapping.tag else ""
                    filepath = os.path.join(out_dir, f"{bpy.path.clean_name(obj.name)}{tag_str}.stl")
                    write_fast_binary_stl(filepath, mesh, obj.matrix_world)
                    obj_eval.to_mesh_clear()


# --- UI PANELS ---

def draw_inline_controls(layout, operator_id, use_clipboard=False):
    row = layout.row(align=True)
    row.operator(operator_id, icon='ADD', text="").action = 'ADD'
    row.operator(operator_id, icon='REMOVE', text="").action = 'REMOVE'
    row.operator(operator_id, icon='TRIA_UP', text="").action = 'UP'
    row.operator(operator_id, icon='TRIA_DOWN', text="").action = 'DOWN'
    if use_clipboard:
        row.operator(operator_id, icon='COPYDOWN', text="").action = 'COPY'
        row.operator(operator_id, icon='PASTEDOWN', text="").action = 'PASTE'

def draw_override_block(layout, ovr, o_idx, is_pinned):
    ovr_box = layout.box()
    header_box = ovr_box.box()
    row = header_box.row(align=True)
    row.prop(ovr, "parent_group_ptr", text="")
    if ovr.override_target == 'NODE' and ovr.parent_group_ptr:
        row.prop_search(ovr, "node_name", ovr.parent_group_ptr, "nodes", text="", icon='NODETREE')
    row.prop(ovr, "override_target", text="")

    for action, icon in [('UNPIN' if is_pinned else 'PIN', 'PINNED' if is_pinned else 'UNPINNED'), ('UP', 'TRIA_UP'), ('DOWN', 'TRIA_DOWN'), ('COPY', 'COPYDOWN'), ('REMOVE', 'X')]:
        op = row.operator("batch_stl.override_actions", text="", icon=icon)
        op.action, op.override_index, op.is_pinned = action, o_idx, is_pinned

    inputs_box = ovr_box.box()
    target_node = ovr.parent_group_ptr.nodes.get(ovr.node_name) if ovr.override_target == 'NODE' and ovr.parent_group_ptr and ovr.node_name else None

    for i_idx, inp in enumerate(ovr.inputs):
        irow = inputs_box.row(align=True)
        irow.label(icon='FORWARD')
        if target_node: irow.prop_search(inp, "input_name", target_node, "inputs", text="")
        elif ovr.override_target == 'MODIFIER' and ovr.parent_group_ptr:
            if hasattr(ovr.parent_group_ptr, "interface"): irow.prop_search(inp, "input_name", ovr.parent_group_ptr.interface, "items_tree", text="")
            else: irow.prop_search(inp, "input_name", ovr.parent_group_ptr, "inputs", text="")
        else: irow.prop(inp, "input_name", text="")

        if inp.override_type == 'BOOLEAN': irow.prop(inp, "value_bool", text="True" if inp.value_bool else "False", toggle=True)
        elif inp.override_type == 'INT': irow.prop(inp, "value_int", text="")
        elif inp.override_type == 'FLOAT': irow.prop(inp, "value_float", text="")
        elif inp.override_type == 'STRING': irow.prop(inp, "value_string", text="")
        elif inp.override_type == 'MENU': irow.prop(inp, "value_menu", text="")

        irow.prop(inp, "override_type", text="")
        for action, icon in [('UP', 'TRIA_UP'), ('DOWN', 'TRIA_DOWN'), ('COPY', 'COPYDOWN'), ('REMOVE', 'TRASH')]:
            op = irow.operator("batch_stl.input_actions", text="", icon=icon)
            op.action, op.override_index, op.input_index, op.is_pinned = action, o_idx, i_idx, is_pinned

    add_row = inputs_box.row(align=True)
    for action, icon in [('ADD', 'PLUS'), ('PASTE', 'PASTEDOWN')]:
        op = add_row.operator("batch_stl.input_actions", text="", icon=icon)
        op.action, op.override_index, op.input_index, op.is_pinned = action, o_idx, -1, is_pinned

class VIEW3D_PT_batch_export_stl_multi(bpy.types.Panel):
    bl_space_type = "VIEW_3D"
    bl_region_type = "UI"
    bl_category = "Export"
    bl_label = "Fast Batch STL Export"

    def draw(self, context):
        layout = self.layout
        scene = context.scene

        layout.prop(scene, "batch_stl_root_dir")
        layout.separator()

        json_row = layout.row(align=True)
        json_row.alignment = 'LEFT'
        json_row.operator("batch_stl.import_presets_json", text="Import JSON", icon='IMPORT')
        json_row.operator("batch_stl.export_presets_json", text="Export JSON", icon='EXPORT')

        layout.separator()
        p_header = layout.row()
        p_header.label(text="Presets:", icon='PRESET')
        draw_inline_controls(p_header, "batch_stl.preset_actions", use_clipboard=True)
        layout.template_list("BATCH_STL_UL_presets", "", scene, "batch_stl_presets", scene, "batch_stl_preset_index", rows=3)

        active_preset = get_active_preset(scene)
        if not active_preset: return

        layout.prop(active_preset, "preset_prefix", icon='FILE_FOLDER')
        box = layout.box()
        m_header = box.row()
        m_header.label(text="Collections Mapping:", icon='OUTLINER_COLLECTION')
        draw_inline_controls(m_header, "batch_stl.mapping_actions", use_clipboard=True)
        box.template_list("BATCH_STL_UL_items", "", active_preset, "mappings", active_preset, "mapping_index", rows=5)

        active_item = get_active_mapping(active_preset)
        if active_item:
            layout.separator()
            c_name = active_item.collection_ptr.name if active_item.collection_ptr else "Unassigned"
            if active_item.tag: c_name += f" [{active_item.tag}]"

            header = layout.row()
            header.label(text=f"Overrides for: {c_name}", icon='MODIFIER')
            h_actions = header.row(align=True)
            for action, icon in [('ADD', 'ADD'), ('PASTE', 'PASTEDOWN')]:
                op = h_actions.operator("batch_stl.override_actions", text="", icon=icon)
                op.action, op.override_index, op.is_pinned = action, -1, False

            if len(active_preset.pinned_overrides) > 0:
                layout.separator()
                pinned_box = layout.box()
                pinned_box.label(text="Global Pinned Overrides:", icon='PINNED')
                for o_idx, ovr in enumerate(active_preset.pinned_overrides): draw_override_block(pinned_box, ovr, o_idx, True)

            if len(active_item.node_overrides) > 0:
                layout.separator()
                local_box = layout.box()
                local_box.label(text="Local Overrides:", icon='UNPINNED')
                for o_idx, ovr in enumerate(active_item.node_overrides): draw_override_block(local_box, ovr, o_idx, False)


# --- REGISTRATION ---

classes = (
    BatchSTLNodeInput, BatchSTLNodeOverride, BatchSTLExportItem, BatchSTLExportPreset,
    BATCH_STL_UL_items, BATCH_STL_UL_presets,
    BATCH_STL_OT_preset_actions, BATCH_STL_OT_mapping_actions, BATCH_STL_OT_override_actions,
    BATCH_STL_OT_input_actions, BATCH_STL_OT_export_presets_json, BATCH_STL_OT_import_presets_json,
    EXPORT_OT_batch_stl_multi, VIEW3D_PT_batch_export_stl_multi,
)

def register():
    for cls in classes: bpy.utils.register_class(cls)
    bpy.types.Scene.batch_stl_root_dir = bpy.props.StringProperty(name="Root Export Dir", default="//", subtype="DIR_PATH")
    bpy.types.Scene.batch_stl_presets = bpy.props.CollectionProperty(type=BatchSTLExportPreset)
    bpy.types.Scene.batch_stl_preset_index = bpy.props.IntProperty(default=0)

def unregister():
    for cls in reversed(classes): bpy.utils.unregister_class(cls)
    del bpy.types.Scene.batch_stl_root_dir
    del bpy.types.Scene.batch_stl_presets
    del bpy.types.Scene.batch_stl_preset_index

if __name__ == "__main__":
    register()
