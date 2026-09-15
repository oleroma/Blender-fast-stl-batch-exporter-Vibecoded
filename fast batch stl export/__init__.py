import os
import json
import bpy
import struct
import time
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

    t_tri = time.perf_counter()

    verts = np.empty((len(mesh.vertices), 3), dtype=np.float32)
    mesh.vertices.foreach_get("co", verts.ravel())
    mat = np.array(matrix_world, dtype=np.float32)
    verts_vec4 = np.c_[verts, np.ones(len(verts), dtype=np.float32)]
    verts = np.dot(verts_vec4, mat.T)[:, :3]

    tri_verts = np.empty((num_tris, 3), dtype=np.int32)
    mesh.loop_triangles.foreach_get("vertices", tri_verts.ravel())

    tri_normals = np.empty((num_tris, 3), dtype=np.float32)
    mesh.loop_triangles.foreach_get("normal", tri_normals.ravel())

    t_extract = time.perf_counter()

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

    print(f"      └─ STL Write Detail: Triangulate: {t_tri-t_start:.4f}s | Extract: {t_extract-t_tri:.4f}s | Format: {t_format-t_extract:.4f}s | Disk: {t_write-t_format:.4f}s")


# --- HELPER FUNCTIONS ---

def get_enabled_objects_recursive(collection, view_layer_objects):
    objects = []
    for obj in collection.objects:
        if obj.type in {"MESH", "CURVE", "SURFACE", "META", "FONT"}:
            if obj.name in view_layer_objects:
                objects.append(obj)
    for child in collection.children:
        objects.extend(get_enabled_objects_recursive(child, view_layer_objects))
    return objects

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
        if mod.is_property_set(ident):
            return mod[ident], True
    except Exception:
        pass

    if hasattr(mod, "properties") and hasattr(mod.properties, "inputs"):
        prop_input = getattr(mod.properties.inputs, ident, None)
        if prop_input is not None and hasattr(prop_input, "value"):
            return prop_input.value, True

    return None, False

def set_modifier_input(mod, ident, value):
    try:
        mod[ident] = value
        return
    except (TypeError, Exception):
        pass

    if hasattr(mod, "properties") and hasattr(mod.properties, "inputs"):
        prop_input = getattr(mod.properties.inputs, ident, None)
        if prop_input is not None and hasattr(prop_input, "value"):
            prop_input.value = value

def unset_modifier_input(mod, ident, default_val):
    try:
        mod.property_unset(ident)
        return
    except Exception:
        pass

    if hasattr(mod, "properties") and hasattr(mod.properties, "inputs"):
        prop_input = getattr(mod.properties.inputs, ident, None)
        if prop_input is not None and hasattr(prop_input, "value") and default_val is not None:
            prop_input.value = default_val

# --- PROPERTIES ---

def on_input_name_update(self, context):
    try:
        ovr = None
        for p in context.scene.batch_stl_presets:
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
                    socket = node.inputs[self.input_name]
                    s_type = getattr(socket, "type", "")
                    if s_type in ['VALUE', 'FLOAT']: self.override_type = 'FLOAT'
                    elif s_type == 'INT': self.override_type = 'INT'
                    elif s_type == 'BOOLEAN': self.override_type = 'BOOLEAN'
                    elif s_type == 'STRING': self.override_type = 'STRING'
                    elif s_type == 'MENU': self.override_type = 'MENU'
            elif ovr.override_target == 'MODIFIER':
                if hasattr(ovr.parent_group_ptr, "interface"):
                    for item in ovr.parent_group_ptr.interface.items_tree:
                        if getattr(item, "item_type", "") == 'SOCKET' and item.name == self.input_name:
                            s_type = getattr(item, "socket_type", "")
                            if 'Float' in s_type: self.override_type = 'FLOAT'
                            elif 'Int' in s_type: self.override_type = 'INT'
                            elif 'Bool' in s_type: self.override_type = 'BOOLEAN'
                            elif 'String' in s_type: self.override_type = 'STRING'
                            elif 'Menu' in s_type: self.override_type = 'MENU'
                            break
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
        items=(
            ('NODE', "Nodegroup", ""),
            ('MODIFIER', "Modifier", "")
        ),
        default='NODE'
    )
    parent_group_ptr: bpy.props.PointerProperty(type=bpy.types.NodeTree, name="Group")
    node_name: bpy.props.StringProperty(name="Node", default="")
    inputs: bpy.props.CollectionProperty(type=BatchSTLNodeInput)

class BatchSTLExportItem(bpy.types.PropertyGroup):
    collection_ptr: bpy.props.PointerProperty(type=bpy.types.Collection, name="Collection")
    tag: bpy.props.StringProperty(name="Tag", default="")
    sub_path: bpy.props.StringProperty(name="Sub-folder", default="")
    node_overrides: bpy.props.CollectionProperty(type=BatchSTLNodeOverride)

class BatchSTLExportPreset(bpy.types.PropertyGroup):
    name: bpy.props.StringProperty(name="Preset Name", default="New Preset")
    preset_prefix: bpy.props.StringProperty(name="Preset Root Directory", default="")
    mappings: bpy.props.CollectionProperty(type=BatchSTLExportItem)
    mapping_index: bpy.props.IntProperty(default=0)


# --- JSON & CLIPBOARD UTILS ---

def copy_preset_to_dict(src):
    p_data = {"name": src.name, "preset_prefix": src.preset_prefix, "mappings": []}
    for m in src.mappings:
        p_data["mappings"].append(copy_mapping_to_dict(m))
    return p_data

def paste_preset_from_dict(new_p, data):
    new_p.name = data.get("name", "Imported Preset")
    new_p.preset_prefix = data.get("preset_prefix", "")
    for m_data in data.get("mappings", []):
        paste_mapping_from_dict(new_p.mappings.add(), m_data)

def copy_mapping_to_dict(src):
    data = {
        "collection_name": src.collection_ptr.name if src.collection_ptr else "",
        "tag": src.tag,
        "sub_path": src.sub_path,
        "overrides": []
    }
    for o in src.node_overrides:
        o_data = {
            "override_target": o.override_target,
            "parent_group": o.parent_group_ptr.name if o.parent_group_ptr else "",
            "node_name": o.node_name,
            "inputs": []
        }
        for i in o.inputs:
            o_data["inputs"].append({
                "input_name": i.input_name,
                "override_type": i.override_type,
                "value_bool": i.value_bool,
                "value_int": i.value_int,
                "value_float": i.value_float,
                "value_string": i.value_string,
                "value_menu": i.value_menu
            })
        data["overrides"].append(o_data)
    return data

def paste_mapping_from_dict(new_m, data):
    c_name = data.get("collection_name", "")
    new_m.collection_ptr = bpy.data.collections.get(c_name) if c_name else None
    new_m.tag = data.get("tag", "")
    new_m.sub_path = data.get("sub_path", "")
    for o_data in data.get("overrides", []):
        new_o = new_m.node_overrides.add()
        new_o.override_target = o_data["override_target"]
        pg_name = o_data["parent_group"]
        new_o.parent_group_ptr = bpy.data.node_groups.get(pg_name) if pg_name else None
        new_o.node_name = o_data["node_name"]
        for i_data in o_data["inputs"]:
            new_i = new_o.inputs.add()
            new_i.input_name = i_data["input_name"]
            new_i.override_type = i_data["override_type"]
            new_i.value_bool = i_data["value_bool"]
            new_i.value_int = i_data["value_int"]
            new_i.value_float = i_data["value_float"]
            new_i.value_string = i_data["value_string"]
            new_i.value_menu = i_data.get("value_menu", "")

class BATCH_STL_OT_export_presets_json(bpy.types.Operator, ExportHelper):
    bl_idname = "batch_stl.export_presets_json"
    bl_label = "Export JSON"
    bl_description = "Export all presets to JSON"
    filename_ext = ".json"
    filter_glob: bpy.props.StringProperty(default="*.json", options={'HIDDEN'})

    def execute(self, context):
        data = []
        for p in context.scene.batch_stl_presets:
            data.append(copy_preset_to_dict(p))
        try:
            with open(self.filepath, 'w') as f:
                json.dump(data, f, indent=4)
        except Exception as e:
            self.report({'ERROR'}, f"Failed to export JSON: {e}")
            return {'CANCELLED'}
        return {'FINISHED'}

class BATCH_STL_OT_import_presets_json(bpy.types.Operator, ImportHelper):
    bl_idname = "batch_stl.import_presets_json"
    bl_label = "Import JSON"
    bl_description = "Import presets from JSON"
    filename_ext = ".json"
    filter_glob: bpy.props.StringProperty(default="*.json", options={'HIDDEN'})

    def execute(self, context):
        try:
            with open(self.filepath, 'r') as f:
                data = json.load(f)
        except Exception as e:
            self.report({'ERROR'}, f"Failed to read JSON: {e}")
            return {'CANCELLED'}

        lst = context.scene.batch_stl_presets
        for p_data in data:
            paste_preset_from_dict(lst.add(), p_data)
        return {'FINISHED'}

# --- UI LISTS ---

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
        row.prop(item, "tag", text="", emboss=False, icon='BOOKMARKS')
        row.prop(item, "sub_path", text="", emboss=False, icon='FILE_FOLDER')

# --- OPERATORS ---

class BATCH_STL_OT_preset_actions(bpy.types.Operator):
    bl_idname = "batch_stl.preset_actions"
    bl_label = "Preset Actions"

    @classmethod
    def description(cls, context, properties):
        action = properties.action
        if action == 'ADD': return "Add new preset"
        if action == 'REMOVE': return "Remove selected preset"
        if action == 'UP': return "Move preset up"
        if action == 'DOWN': return "Move preset down"
        if action == 'COPY': return "Copy selected preset to clipboard"
        if action == 'PASTE': return "Paste preset from clipboard"
        return "Action"

    action: bpy.props.EnumProperty(items=(('ADD', "", ""), ('REMOVE', "", ""), ('UP', "", ""), ('DOWN', "", ""), ('COPY', "", ""), ('PASTE', "", "")))

    def execute(self, context):
        lst = context.scene.batch_stl_presets
        idx = context.scene.batch_stl_preset_index
        if self.action == 'ADD':
            lst.add()
            context.scene.batch_stl_preset_index = len(lst) - 1
        elif self.action == 'REMOVE' and lst:
            lst.remove(idx)
            context.scene.batch_stl_preset_index = max(0, idx - 1)
        elif self.action == 'UP' and idx > 0:
            lst.move(idx, idx - 1)
            context.scene.batch_stl_preset_index -= 1
        elif self.action == 'DOWN' and idx < len(lst) - 1:
            lst.move(idx, idx + 1)
            context.scene.batch_stl_preset_index += 1
        elif self.action == 'COPY' and lst:
            _clipboard["preset"] = copy_preset_to_dict(lst[idx])
        elif self.action == 'PASTE' and _clipboard.get("preset"):
            paste_preset_from_dict(lst.add(), _clipboard["preset"])
            context.scene.batch_stl_preset_index = len(lst) - 1
        return {'FINISHED'}

class BATCH_STL_OT_mapping_actions(bpy.types.Operator):
    bl_idname = "batch_stl.mapping_actions"
    bl_label = "Mapping Actions"

    @classmethod
    def description(cls, context, properties):
        action = properties.action
        if action == 'ADD': return "Add new collection mapping"
        if action == 'REMOVE': return "Remove selected mapping"
        if action == 'UP': return "Move mapping up"
        if action == 'DOWN': return "Move mapping down"
        if action == 'COPY': return "Copy mapping to clipboard"
        if action == 'PASTE': return "Paste mapping from clipboard"
        return "Action"

    action: bpy.props.EnumProperty(items=(('ADD', "", ""), ('REMOVE', "", ""), ('UP', "", ""), ('DOWN', "", ""), ('COPY', "", ""), ('PASTE', "", "")))

    def execute(self, context):
        preset = get_active_preset(context.scene)
        if not preset: return {'CANCELLED'}
        lst = preset.mappings
        idx = preset.mapping_index

        if self.action == 'ADD':
            lst.add()
            preset.mapping_index = len(lst) - 1
        elif self.action == 'REMOVE' and lst:
            lst.remove(idx)
            preset.mapping_index = max(0, idx - 1)
        elif self.action == 'UP' and idx > 0:
            lst.move(idx, idx - 1)
            preset.mapping_index -= 1
        elif self.action == 'DOWN' and idx < len(lst) - 1:
            lst.move(idx, idx + 1)
            preset.mapping_index += 1
        elif self.action == 'COPY' and lst:
            _clipboard["mapping"] = copy_mapping_to_dict(lst[idx])
        elif self.action == 'PASTE' and _clipboard.get("mapping"):
            paste_mapping_from_dict(lst.add(), _clipboard["mapping"])
            preset.mapping_index = len(lst) - 1
        return {'FINISHED'}

class BATCH_STL_OT_override_actions(bpy.types.Operator):
    bl_idname = "batch_stl.override_actions"
    bl_label = "Override Actions"

    @classmethod
    def description(cls, context, properties):
        action = properties.action
        if action == 'ADD': return "Add new override target block"
        if action == 'REMOVE': return "Remove this override block"
        if action == 'UP': return "Move override block up"
        if action == 'DOWN': return "Move override block down"
        if action == 'COPY': return "Copy override block to clipboard"
        if action == 'PASTE': return "Paste override block from clipboard"
        return "Action"

    action: bpy.props.EnumProperty(items=(('ADD', "", ""), ('REMOVE', "", ""), ('UP', "", ""), ('DOWN', "", ""), ('COPY', "", ""), ('PASTE', "", "")))
    override_index: bpy.props.IntProperty(default=-1)

    def execute(self, context):
        preset = get_active_preset(context.scene)
        mapping = get_active_mapping(preset)
        if not mapping: return {'CANCELLED'}
        lst = mapping.node_overrides
        idx = self.override_index

        if self.action == 'ADD':
            lst.add()
        elif self.action == 'REMOVE' and 0 <= idx < len(lst):
            lst.remove(idx)
        elif self.action == 'UP' and idx > 0:
            lst.move(idx, idx - 1)
        elif self.action == 'DOWN' and 0 <= idx < len(lst) - 1:
            lst.move(idx, idx + 1)
        elif self.action == 'COPY' and 0 <= idx < len(lst):
            o = lst[idx]
            _clipboard["override"] = {
                "override_target": o.override_target,
                "parent_group": o.parent_group_ptr.name if o.parent_group_ptr else "",
                "node_name": o.node_name,
                "inputs": [{"input_name": i.input_name, "override_type": i.override_type, "value_bool": i.value_bool, "value_int": i.value_int, "value_float": i.value_float, "value_string": i.value_string, "value_menu": i.value_menu} for i in o.inputs]
            }
        elif self.action == 'PASTE' and _clipboard.get("override"):
            data = _clipboard["override"]
            new_o = lst.add()
            new_o.override_target = data["override_target"]
            pg_name = data["parent_group"]
            new_o.parent_group_ptr = bpy.data.node_groups.get(pg_name) if pg_name else None
            new_o.node_name = data["node_name"]
            for i_data in data["inputs"]:
                new_i = new_o.inputs.add()
                new_i.input_name = i_data["input_name"]
                new_i.override_type = i_data["override_type"]
                new_i.value_bool = i_data["value_bool"]
                new_i.value_int = i_data["value_int"]
                new_i.value_float = i_data["value_float"]
                new_i.value_string = i_data["value_string"]
                new_i.value_menu = i_data.get("value_menu", "")
            if 0 <= idx < len(lst):
                lst.move(len(lst) - 1, idx + 1)
        return {'FINISHED'}

class BATCH_STL_OT_input_actions(bpy.types.Operator):
    bl_idname = "batch_stl.input_actions"
    bl_label = "Input Actions"

    @classmethod
    def description(cls, context, properties):
        action = properties.action
        if action == 'ADD': return "Add new input to override"
        if action == 'REMOVE': return "Remove this input"
        if action == 'UP': return "Move input up"
        if action == 'DOWN': return "Move input down"
        if action == 'COPY': return "Copy input to clipboard"
        if action == 'PASTE': return "Paste input from clipboard"
        return "Action"

    action: bpy.props.EnumProperty(items=(('ADD', "", ""), ('REMOVE', "", ""), ('UP', "", ""), ('DOWN', "", ""), ('COPY', "", ""), ('PASTE', "", "")))
    override_index: bpy.props.IntProperty(default=-1)
    input_index: bpy.props.IntProperty(default=-1)

    def execute(self, context):
        preset = get_active_preset(context.scene)
        mapping = get_active_mapping(preset)
        if not mapping or self.override_index < 0 or self.override_index >= len(mapping.node_overrides):
            return {'CANCELLED'}

        ovr = mapping.node_overrides[self.override_index]
        lst = ovr.inputs
        idx = self.input_index

        if self.action == 'ADD':
            lst.add()
        elif self.action == 'REMOVE' and 0 <= idx < len(lst):
            lst.remove(idx)
        elif self.action == 'UP' and idx > 0:
            lst.move(idx, idx - 1)
        elif self.action == 'DOWN' and 0 <= idx < len(lst) - 1:
            lst.move(idx, idx + 1)
        elif self.action == 'COPY' and 0 <= idx < len(lst):
            i = lst[idx]
            _clipboard["input"] = {
                "input_name": i.input_name,
                "override_type": i.override_type,
                "value_bool": i.value_bool,
                "value_int": i.value_int,
                "value_float": i.value_float,
                "value_string": i.value_string,
                "value_menu": i.value_menu
            }
        elif self.action == 'PASTE' and _clipboard.get("input"):
            data = _clipboard["input"]
            new_i = lst.add()
            new_i.input_name = data["input_name"]
            new_i.override_type = data["override_type"]
            new_i.value_bool = data["value_bool"]
            new_i.value_int = data["value_int"]
            new_i.value_float = data["value_float"]
            new_i.value_string = data["value_string"]
            new_i.value_menu = data.get("value_menu", "")
            if 0 <= idx < len(lst):
                lst.move(len(lst) - 1, idx + 1)
        return {'FINISHED'}

# --- EXPORT OPERATOR ---

class EXPORT_OT_batch_stl_multi(bpy.types.Operator):
    bl_idname = "export_scene.batch_stl_multi"
    bl_label = "Batch Export STLs"
    bl_options = {"REGISTER"}
    preset_index: bpy.props.IntProperty(default=-1)

    @classmethod
    def poll(cls, context):
        return len(context.scene.batch_stl_presets) > 0

    def execute(self, context):
        total_time_start = time.perf_counter()
        scene = context.scene
        preset = scene.batch_stl_presets[self.preset_index] if 0 <= self.preset_index < len(scene.batch_stl_presets) else get_active_preset(scene)
        if not preset or not scene.batch_stl_root_dir: return {"CANCELLED"}

        root_dir = bpy.path.abspath(scene.batch_stl_root_dir)
        if preset.preset_prefix: root_dir = os.path.normpath(os.path.join(root_dir, preset.preset_prefix))
        if context.active_object and context.mode != "OBJECT": bpy.ops.object.mode_set(mode="OBJECT")
        total_exported = 0

        # Pass the view layer objects map so we don't fetch it repeatedly
        view_layer_objects = context.view_layer.objects

        print(f"\n=== STARTING BATCH EXPORT: {preset.name} ===")

        for item in preset.mappings:
            if not item.collection_ptr: continue

            c_start = time.perf_counter()
            print(f"\n  Processing Collection: {item.collection_ptr.name}")

            out_dir = os.path.normpath(os.path.join(root_dir, item.sub_path))
            os.makedirs(out_dir, exist_ok=True)

            objects_to_export = list(set(get_enabled_objects_recursive(item.collection_ptr, view_layer_objects)))
            if not objects_to_export:
                print("    ├─ Skipped: No active/visible objects found in current View Layer.")
                continue

            global_original_states = []
            all_obj_mod_states = []

            try:
                # 1. Apply Global Node Overrides
                t0 = time.perf_counter()
                for override in item.node_overrides:
                    if override.override_target == 'NODE' and override.parent_group_ptr and override.node_name:
                        parent_tree = override.parent_group_ptr
                        for n_name in [n.strip() for n in override.node_name.split(',') if n.strip()]:
                            target_node = parent_tree.nodes.get(n_name)
                            if not target_node: continue
                            for inp in override.inputs:
                                socket = target_node.inputs.get(inp.input_name)
                                if not socket: continue
                                link_from = socket.links[0].from_socket if socket.is_linked else None
                                global_original_states.append((socket, socket.default_value, link_from, parent_tree))
                                if socket.is_linked: parent_tree.links.remove(socket.links[0])

                                # FIX: Map 'BOOLEAN' to 'value_bool' to prevent retrieving None
                                prop_name = 'value_bool' if inp.override_type == 'BOOLEAN' else f"value_{inp.override_type.lower()}"
                                val = getattr(inp, prop_name, None)

                                if val is not None: socket.default_value = val
                t1 = time.perf_counter()
                print(f"    ├─ Applied Global Node Overrides: {t1 - t0:.4f}s")

                # 2. Apply Modifier Overrides
                for obj in objects_to_export:
                    obj_changed = False
                    for override in item.node_overrides:
                        if override.override_target == 'MODIFIER' and override.parent_group_ptr:
                            for mod in obj.modifiers:
                                if mod.type == 'NODES' and mod.node_group == override.parent_group_ptr:
                                    for inp in override.inputs:
                                        ident = get_modifier_socket_identifier(mod.node_group, inp.input_name)
                                        if ident:
                                            orig_val, is_set = get_modifier_input(mod, ident)
                                            default_val = get_modifier_socket_default(mod.node_group, inp.input_name)
                                            all_obj_mod_states.append((mod, ident, is_set, orig_val, default_val))

                                            # FIX: Map 'BOOLEAN' to 'value_bool'
                                            prop_name = 'value_bool' if inp.override_type == 'BOOLEAN' else f"value_{inp.override_type.lower()}"
                                            val = getattr(inp, prop_name, None)

                                            if val is not None:
                                                set_modifier_input(mod, ident, val)
                                                obj_changed = True
                    if obj_changed:
                        obj.update_tag()

                t2 = time.perf_counter()
                print(f"    ├─ Applied Modifier Overrides: {t2 - t1:.4f}s")

                # 3. View Layer Update
                if global_original_states or all_obj_mod_states:
                    context.view_layer.update()

                t3 = time.perf_counter()
                print(f"    ├─ View Layer Update: {t3 - t2:.4f}s")

                # 4. Dependency Graph Retrieval
                depsgraph = context.evaluated_depsgraph_get()
                t4 = time.perf_counter()
                print(f"    ├─ Depsgraph Evaluated: {t4 - t3:.4f}s")

                # 5. Mesh Generation & Writing
                print(f"    ├─ Exporting {len(objects_to_export)} objects...")
                for obj in objects_to_export:
                    t_obj_start = time.perf_counter()
                    obj_eval = obj.evaluated_get(depsgraph)
                    t_eval = time.perf_counter()

                    try:
                        mesh = obj_eval.to_mesh()
                        t_to_mesh = time.perf_counter()

                        if mesh:
                            filepath = os.path.join(out_dir, f"{bpy.path.clean_name(obj.name)}{item.tag}.stl")
                            write_fast_binary_stl(filepath, mesh, obj.matrix_world)
                            obj_eval.to_mesh_clear()
                            total_exported += 1
                            t_written = time.perf_counter()

                            print(f"      ├─ {obj.name}: Eval {t_eval-t_obj_start:.4f}s | MeshGen {t_to_mesh-t_eval:.4f}s | TotalWrite {t_written-t_to_mesh:.4f}s")
                    except RuntimeError:
                        print(f"      ├─ {obj.name}: FAILED TO GENERATE MESH")

                t5 = time.perf_counter()

            finally:
                # 6. Revert States
                t_revert_start = time.perf_counter()
                for mod, ident, is_set, orig_val, default_val in all_obj_mod_states:
                    if is_set and orig_val is not None: set_modifier_input(mod, ident, orig_val)
                    else: unset_modifier_input(mod, ident, default_val)
                    mod.id_data.update_tag()

                for socket, original_val, link_from, parent_tree in global_original_states:
                    try:
                        socket.default_value = original_val
                        if link_from: parent_tree.links.new(link_from, socket)
                    except Exception: pass

                if global_original_states or all_obj_mod_states:
                    context.view_layer.update()

                t_revert_end = time.perf_counter()
                print(f"    └─ Reverted States & Final Update: {t_revert_end - t_revert_start:.4f}s")
                print(f"  Collection Finished in {t_revert_end - c_start:.4f}s")

        print(f"=== BATCH EXPORT COMPLETE: {time.perf_counter() - total_time_start:.4f}s Total ===")

        if total_exported > 0: self.report({'INFO'}, f"Exported {total_exported} STLs (Check console for timings)")
        return {"FINISHED"}

# --- UI PANEL ---

def draw_inline_controls(layout, operator_id, use_clipboard=False):
    row = layout.row(align=True)
    row.operator(operator_id, icon='ADD', text="").action = 'ADD'
    row.operator(operator_id, icon='REMOVE', text="").action = 'REMOVE'
    row.operator(operator_id, icon='TRIA_UP', text="").action = 'UP'
    row.operator(operator_id, icon='TRIA_DOWN', text="").action = 'DOWN'

    if use_clipboard:
        row.operator(operator_id, icon='COPYDOWN', text="").action = 'COPY'
        row.operator(operator_id, icon='PASTEDOWN', text="").action = 'PASTE'

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
            obox = layout.box()

            c_name = active_item.collection_ptr.name if active_item.collection_ptr else "Unassigned"
            if active_item.tag:
                c_name += f" [{active_item.tag}]"

            header = obox.row()
            header.label(text=f"Overrides for: {c_name}", icon='MODIFIER')

            h_actions = header.row(align=True)
            add_ovr = h_actions.operator("batch_stl.override_actions", text="", icon='ADD')
            add_ovr.action = 'ADD'
            add_ovr.override_index = -1

            paste_ovr = h_actions.operator("batch_stl.override_actions", text="", icon='PASTEDOWN')
            paste_ovr.action = 'PASTE'
            paste_ovr.override_index = -1

            if len(active_item.node_overrides) > 0:
                list_box = obox.box()

                for o_idx, ovr in enumerate(active_item.node_overrides):
                    ovr_box = list_box.box()

                    header_box = ovr_box.box()
                    row = header_box.row(align=True)

                    row.prop(ovr, "parent_group_ptr", text="")
                    if ovr.override_target == 'NODE' and ovr.parent_group_ptr:
                        row.prop_search(ovr, "node_name", ovr.parent_group_ptr, "nodes", text="", icon='NODETREE')

                    row.prop(ovr, "override_target", text="")

                    up_ovr = row.operator("batch_stl.override_actions", text="", icon='TRIA_UP')
                    up_ovr.action = 'UP'
                    up_ovr.override_index = o_idx

                    dn_ovr = row.operator("batch_stl.override_actions", text="", icon='TRIA_DOWN')
                    dn_ovr.action = 'DOWN'
                    dn_ovr.override_index = o_idx

                    cp_ovr = row.operator("batch_stl.override_actions", text="", icon='COPYDOWN')
                    cp_ovr.action = 'COPY'
                    cp_ovr.override_index = o_idx

                    rem_ovr = row.operator("batch_stl.override_actions", text="", icon='X')
                    rem_ovr.action = 'REMOVE'
                    rem_ovr.override_index = o_idx

                    inputs_box = ovr_box.box()

                    target_node = None
                    if ovr.override_target == 'NODE' and ovr.parent_group_ptr and ovr.node_name:
                        target_node = ovr.parent_group_ptr.nodes.get(ovr.node_name)

                    for i_idx, inp in enumerate(ovr.inputs):
                        irow = inputs_box.row(align=True)
                        irow.label(icon='FORWARD')

                        if target_node:
                            irow.prop_search(inp, "input_name", target_node, "inputs", text="")
                        elif ovr.override_target == 'MODIFIER' and ovr.parent_group_ptr:
                            if hasattr(ovr.parent_group_ptr, "interface"):
                                irow.prop_search(inp, "input_name", ovr.parent_group_ptr.interface, "items_tree", text="")
                            else:
                                irow.prop_search(inp, "input_name", ovr.parent_group_ptr, "inputs", text="")
                        else:
                            irow.prop(inp, "input_name", text="")

                        if inp.override_type == 'BOOLEAN':
                            irow.prop(inp, "value_bool", text="True" if inp.value_bool else "False", toggle=True)
                        elif inp.override_type == 'INT': irow.prop(inp, "value_int", text="")
                        elif inp.override_type == 'FLOAT': irow.prop(inp, "value_float", text="")
                        elif inp.override_type == 'STRING': irow.prop(inp, "value_string", text="")
                        elif inp.override_type == 'MENU': irow.prop(inp, "value_menu", text="")

                        irow.prop(inp, "override_type", text="")

                        up_inp = irow.operator("batch_stl.input_actions", text="", icon='TRIA_UP')
                        up_inp.action = 'UP'
                        up_inp.override_index = o_idx
                        up_inp.input_index = i_idx

                        dn_inp = irow.operator("batch_stl.input_actions", text="", icon='TRIA_DOWN')
                        dn_inp.action = 'DOWN'
                        dn_inp.override_index = o_idx
                        dn_inp.input_index = i_idx

                        cp_inp = irow.operator("batch_stl.input_actions", text="", icon='COPYDOWN')
                        cp_inp.action = 'COPY'
                        cp_inp.override_index = o_idx
                        cp_inp.input_index = i_idx

                        rem_inp = irow.operator("batch_stl.input_actions", text="", icon='TRASH')
                        rem_inp.action = 'REMOVE'
                        rem_inp.override_index = o_idx
                        rem_inp.input_index = i_idx

                    add_row = inputs_box.row(align=True)
                    add_inp = add_row.operator("batch_stl.input_actions", text="", icon='PLUS')
                    add_inp.action = 'ADD'
                    add_inp.override_index = o_idx

                    paste_inp = add_row.operator("batch_stl.input_actions", text="", icon='PASTEDOWN')
                    paste_inp.action = 'PASTE'
                    paste_inp.override_index = o_idx
                    paste_inp.input_index = -1

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
