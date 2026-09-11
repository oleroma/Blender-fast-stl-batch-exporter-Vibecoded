import os
import json
import bpy
import struct
from bpy_extras.io_utils import ExportHelper, ImportHelper

# --- SESSION CLIPBOARD ---
_clipboard = {
    "mapping": None,
    "override": None,
    "input": None
}

# --- FAST EXPORT FUNCTION ---

def write_fast_binary_stl(filepath, mesh, matrix_world):
    mesh.calc_loop_triangles()
    tris = mesh.loop_triangles

    if len(tris) == 0:
        return

    verts = [matrix_world @ v.co for v in mesh.vertices]
    mat_norm = matrix_world.to_3x3().inverted_safe().transposed()

    with open(filepath, 'wb') as f:
        f.write(b'Batch STL Fast Export' + b'\x00' * 59)
        f.write(struct.pack('<I', len(tris)))

        for tri in tris:
            n = (mat_norm @ tri.normal).normalized()
            f.write(struct.pack('<3f', n.x, n.y, n.z))

            for loop_idx in tri.vertices:
                v = verts[loop_idx]
                f.write(struct.pack('<3f', v.x, v.y, v.z))

            f.write(b'\x00\x00')

# --- HELPER FUNCTIONS ---

def find_layer_collection(layer_collection, collection_name):
    if layer_collection.collection.name == collection_name:
        return layer_collection
    for child in layer_collection.children:
        result = find_layer_collection(child, collection_name)
        if result:
            return result
    return None

def get_enabled_objects_recursive(layer_coll):
    objects = []
    if layer_coll.exclude:
        return objects
    for obj in layer_coll.collection.objects:
        if obj.type in {"MESH", "CURVE", "SURFACE", "META", "FONT"}:
            objects.append(obj)
    for child in layer_coll.children:
        objects.extend(get_enabled_objects_recursive(child))
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

def get_active_override(mapping):
    if mapping and mapping.node_overrides and 0 <= mapping.node_override_index < len(mapping.node_overrides):
        return mapping.node_overrides[mapping.node_override_index]
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

# --- MODERN MODIFIER INPUT HELPERS ---

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
    except TypeError:
        pass
    except Exception:
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

class BatchSTLNodeInput(bpy.types.PropertyGroup):
    input_name: bpy.props.StringProperty(name="Input Name", default="")
    override_type: bpy.props.EnumProperty(
        name="Type",
        items=(
            ('BOOLEAN', "Boolean", ""),
            ('INT', "Integer", ""),
            ('FLOAT', "Float", ""),
            ('STRING', "String", ""),
        ),
        default='BOOLEAN'
    )
    value_bool: bpy.props.BoolProperty(name="Value", default=True)
    value_int: bpy.props.IntProperty(name="Value", default=0)
    value_float: bpy.props.FloatProperty(name="Value", default=0.0)
    value_string: bpy.props.StringProperty(name="Value", default="")

class BatchSTLNodeOverride(bpy.types.PropertyGroup):
    override_target: bpy.props.EnumProperty(
        name="Target",
        items=(
            ('NODE', "Internal Node", "Override a specific node inside a group globally"),
            ('MODIFIER', "Modifier", "Override the geometry node modifier properties per-object")
        ),
        default='NODE'
    )
    parent_group: bpy.props.StringProperty(name="Node Group", default="")
    node_name: bpy.props.StringProperty(name="Node Name(s)", description="Comma-separated list of nodes", default="")

    inputs: bpy.props.CollectionProperty(type=BatchSTLNodeInput)
    input_index: bpy.props.IntProperty(default=0)
    show_inputs: bpy.props.BoolProperty(default=True)

class BatchSTLExportItem(bpy.types.PropertyGroup):
    collection_name: bpy.props.StringProperty(name="Collection", default="")
    sub_path: bpy.props.StringProperty(name="Sub-folder Path", default="")

    node_overrides: bpy.props.CollectionProperty(type=BatchSTLNodeOverride)
    node_override_index: bpy.props.IntProperty(default=0)
    show_overrides: bpy.props.BoolProperty(default=True)

class BatchSTLExportPreset(bpy.types.PropertyGroup):
    name: bpy.props.StringProperty(name="Preset Name", default="New Preset")
    preset_prefix: bpy.props.StringProperty(name="Preset Root Directory", default="")

    mappings: bpy.props.CollectionProperty(type=BatchSTLExportItem)
    mapping_index: bpy.props.IntProperty(default=0)
    show_mappings: bpy.props.BoolProperty(default=True)


# --- JSON IMPORT/EXPORT OPERATORS ---

class BATCH_STL_OT_export_presets_json(bpy.types.Operator, ExportHelper):
    bl_idname = "batch_stl.export_presets_json"
    bl_label = "Export Presets to JSON"
    bl_description = "Export all presets and their settings to a JSON file"
    filename_ext = ".json"

    filter_glob: bpy.props.StringProperty(default="*.json", options={'HIDDEN'})

    def execute(self, context):
        data = []
        for p in context.scene.batch_stl_presets:
            p_data = {"name": p.name, "preset_prefix": p.preset_prefix, "mappings": []}
            for m in p.mappings:
                m_data = {
                    "collection_name": m.collection_name,
                    "sub_path": m.sub_path,
                    "overrides": []
                }
                for o in m.node_overrides:
                    o_data = {
                        "override_target": o.override_target,
                        "parent_group": o.parent_group,
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
                            "value_string": i.value_string
                        })
                    m_data["overrides"].append(o_data)
                p_data["mappings"].append(m_data)
            data.append(p_data)

        try:
            with open(self.filepath, 'w') as f:
                json.dump(data, f, indent=4)
            self.report({'INFO'}, f"Exported presets to {self.filepath}")
        except Exception as e:
            self.report({'ERROR'}, f"Failed to export JSON: {e}")
            return {'CANCELLED'}

        return {'FINISHED'}

class BATCH_STL_OT_import_presets_json(bpy.types.Operator, ImportHelper):
    bl_idname = "batch_stl.import_presets_json"
    bl_label = "Import Presets from JSON"
    bl_description = "Import presets and their settings from a JSON file"
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
            p = lst.add()
            p.name = p_data.get("name", "Imported Preset")
            p.preset_prefix = p_data.get("preset_prefix", "")
            for m_data in p_data.get("mappings", []):
                m = p.mappings.add()
                m.collection_name = m_data.get("collection_name", "")
                m.sub_path = m_data.get("sub_path", "")

                for o_data in m_data.get("overrides", []):
                    o = m.node_overrides.add()
                    o.override_target = o_data.get("override_target", 'NODE')
                    o.parent_group = o_data.get("parent_group", "")
                    o.node_name = o_data.get("node_name", "")
                    for i_data in o_data.get("inputs", []):
                        i = o.inputs.add()
                        i.input_name = i_data.get("input_name", "")
                        i.override_type = i_data.get("override_type", "BOOLEAN")
                        i.value_bool = i_data.get("value_bool", True)
                        i.value_int = i_data.get("value_int", 0)
                        i.value_float = i_data.get("value_float", 0.0)
                        i.value_string = i_data.get("value_string", "")

        self.report({'INFO'}, f"Imported presets from {self.filepath}")
        return {'FINISHED'}

# --- PRESET OPERATORS ---

class BATCH_STL_OT_preset_actions(bpy.types.Operator):
    bl_idname = "batch_stl.preset_actions"
    bl_label = "Preset Actions"
    bl_description = "Manage export presets"

    @classmethod
    def description(cls, context, properties):
        if properties.action == 'ADD': return "Add a new export preset"
        if properties.action == 'REMOVE': return "Remove the selected preset"
        if properties.action == 'UP': return "Move the selected preset up in the list"
        if properties.action == 'DOWN': return "Move the selected preset down in the list"
        if properties.action == 'DUPLICATE': return "Duplicate the selected preset and its mappings"
        return "Manage export presets"

    action: bpy.props.EnumProperty(items=(
        ('ADD', "Add", ""), ('REMOVE', "Remove", ""), ('UP', "Up", ""),
        ('DOWN', "Down", ""), ('DUPLICATE', "Duplicate", "")
    ))

    def execute(self, context):
        scene = context.scene
        lst = scene.batch_stl_presets
        idx = scene.batch_stl_preset_index

        if self.action == 'ADD':
            item = lst.add()
            item.name = f"Preset {len(lst)}"
            scene.batch_stl_preset_index = len(lst) - 1
        elif self.action == 'REMOVE' and lst:
            lst.remove(idx)
            scene.batch_stl_preset_index = min(max(0, idx - 1), len(lst) - 1)
        elif self.action == 'UP' and idx > 0:
            lst.move(idx, idx - 1)
            scene.batch_stl_preset_index -= 1
        elif self.action == 'DOWN' and idx < len(lst) - 1:
            lst.move(idx, idx + 1)
            scene.batch_stl_preset_index += 1
        elif self.action == 'DUPLICATE' and lst:
            src = lst[idx]
            new_item = lst.add()
            new_item.name = f"{src.name} Copy"
            new_item.preset_prefix = src.preset_prefix
            for m in src.mappings:
                new_m = new_item.mappings.add()
                new_m.collection_name = m.collection_name
                new_m.sub_path = m.sub_path
                for o in m.node_overrides:
                    new_o = new_m.node_overrides.add()
                    new_o.override_target = o.override_target
                    new_o.parent_group = o.parent_group
                    new_o.node_name = o.node_name
                    for i in o.inputs:
                        new_i = new_o.inputs.add()
                        new_i.input_name = i.input_name
                        new_i.override_type = i.override_type
                        new_i.value_bool = i.value_bool
                        new_i.value_int = i.value_int
                        new_i.value_float = i.value_float
                        new_i.value_string = i.value_string
            scene.batch_stl_preset_index = len(lst) - 1
        return {'FINISHED'}

# --- MAPPING OPERATORS ---

def copy_mapping_to_dict(src):
    data = {
        "collection_name": src.collection_name,
        "sub_path": src.sub_path,
        "overrides": []
    }
    for o in src.node_overrides:
        o_data = {
            "override_target": o.override_target,
            "parent_group": o.parent_group,
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
                "value_string": i.value_string
            })
        data["overrides"].append(o_data)
    return data

def paste_mapping_from_dict(new_m, data):
    new_m.collection_name = data.get("collection_name", "")
    new_m.sub_path = data.get("sub_path", "")
    for o_data in data.get("overrides", []):
        new_o = new_m.node_overrides.add()
        new_o.override_target = o_data["override_target"]
        new_o.parent_group = o_data["parent_group"]
        new_o.node_name = o_data["node_name"]
        for i_data in o_data["inputs"]:
            new_i = new_o.inputs.add()
            new_i.input_name = i_data["input_name"]
            new_i.override_type = i_data["override_type"]
            new_i.value_bool = i_data["value_bool"]
            new_i.value_int = i_data["value_int"]
            new_i.value_float = i_data["value_float"]
            new_i.value_string = i_data["value_string"]

class BATCH_STL_OT_mapping_actions(bpy.types.Operator):
    bl_idname = "batch_stl.mapping_actions"
    bl_label = "Mapping Actions"
    bl_description = "Manage collection mappings"

    @classmethod
    def description(cls, context, properties):
        if properties.action == 'ADD': return "Add a new collection mapping to the preset"
        if properties.action == 'REMOVE': return "Remove the selected collection mapping"
        if properties.action == 'UP': return "Move the selected mapping up in the list"
        if properties.action == 'DOWN': return "Move the selected mapping down in the list"
        if properties.action == 'DUPLICATE': return "Duplicate the selected mapping and its overrides"
        if properties.action == 'COPY': return "Copy the selected mapping to the clipboard"
        if properties.action == 'PASTE': return "Paste a mapping from the clipboard"
        return "Manage collection mappings"

    action: bpy.props.EnumProperty(items=(
        ('ADD', "Add", ""), ('REMOVE', "Remove", ""),
        ('UP', "Up", ""), ('DOWN', "Down", ""),
        ('DUPLICATE', "Duplicate", ""), ('COPY', "Copy", ""), ('PASTE', "Paste", "")
    ))

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
            preset.mapping_index = min(max(0, idx - 1), len(lst) - 1)
        elif self.action == 'UP' and idx > 0:
            lst.move(idx, idx - 1)
            preset.mapping_index -= 1
        elif self.action == 'DOWN' and idx < len(lst) - 1:
            lst.move(idx, idx + 1)
            preset.mapping_index += 1
        elif self.action == 'DUPLICATE' and lst:
            src = lst[idx]
            new_item = lst.add()
            data = copy_mapping_to_dict(src)
            paste_mapping_from_dict(new_item, data)
            preset.mapping_index = len(lst) - 1
        elif self.action == 'COPY' and lst:
            _clipboard["mapping"] = copy_mapping_to_dict(lst[idx])
            self.report({'INFO'}, f"Copied Mapping: {lst[idx].collection_name}")
        elif self.action == 'PASTE' and _clipboard.get("mapping"):
            new_item = lst.add()
            paste_mapping_from_dict(new_item, _clipboard["mapping"])
            preset.mapping_index = len(lst) - 1
            self.report({'INFO'}, f"Pasted Mapping: {_clipboard['mapping']['collection_name']}")
        return {'FINISHED'}

# --- OVERRIDE OPERATORS ---

class BATCH_STL_OT_override_actions(bpy.types.Operator):
    bl_idname = "batch_stl.override_actions"
    bl_label = "Override Actions"
    bl_description = "Manage geometry node overrides"

    @classmethod
    def description(cls, context, properties):
        if properties.action == 'ADD': return "Add a new node override target"
        if properties.action == 'REMOVE': return "Remove the selected override target"
        if properties.action == 'UP': return "Move the selected override target up in the list"
        if properties.action == 'DOWN': return "Move the selected override target down in the list"
        if properties.action == 'DUPLICATE': return "Duplicate the selected override target and its inputs"
        if properties.action == 'COPY': return "Copy the selected override target to the clipboard"
        if properties.action == 'PASTE': return "Paste an override target from the clipboard"
        return "Manage geometry node overrides"

    action: bpy.props.EnumProperty(items=(
        ('ADD', "Add", ""), ('REMOVE', "Remove", ""),
        ('UP', "Up", ""), ('DOWN', "Down", ""),
        ('DUPLICATE', "Duplicate", ""), ('COPY', "Copy", ""), ('PASTE', "Paste", "")
    ))

    def execute(self, context):
        preset = get_active_preset(context.scene)
        mapping = get_active_mapping(preset)
        if not mapping: return {'CANCELLED'}

        lst = mapping.node_overrides
        idx = mapping.node_override_index

        if self.action == 'ADD':
            lst.add()
            mapping.node_override_index = len(lst) - 1
        elif self.action == 'REMOVE' and lst:
            lst.remove(idx)
            mapping.node_override_index = min(max(0, idx - 1), len(lst) - 1)
        elif self.action == 'UP' and idx > 0:
            lst.move(idx, idx - 1)
            mapping.node_override_index -= 1
        elif self.action == 'DOWN' and idx < len(lst) - 1:
            lst.move(idx, idx + 1)
            mapping.node_override_index += 1
        elif self.action == 'DUPLICATE' and lst:
            src = lst[idx]
            new_item = lst.add()
            new_item.override_target = src.override_target
            new_item.parent_group = src.parent_group
            new_item.node_name = src.node_name
            for i in src.inputs:
                new_i = new_item.inputs.add()
                new_i.input_name = i.input_name
                new_i.override_type = i.override_type
                new_i.value_bool = i.value_bool
                new_i.value_int = i.value_int
                new_i.value_float = i.value_float
                new_i.value_string = i.value_string
            mapping.node_override_index = len(lst) - 1
        elif self.action == 'COPY' and lst:
            src = lst[idx]
            data = {
                "override_target": src.override_target,
                "parent_group": src.parent_group,
                "node_name": src.node_name,
                "inputs": []
            }
            for i in src.inputs:
                data["inputs"].append({
                    "input_name": i.input_name,
                    "override_type": i.override_type,
                    "value_bool": i.value_bool,
                    "value_int": i.value_int,
                    "value_float": i.value_float,
                    "value_string": i.value_string
                })
            _clipboard["override"] = data
            self.report({'INFO'}, f"Copied Target: {src.parent_group}")
        elif self.action == 'PASTE' and _clipboard.get("override"):
            data = _clipboard["override"]
            new_item = lst.add()
            new_item.override_target = data["override_target"]
            new_item.parent_group = data["parent_group"]
            new_item.node_name = data["node_name"]
            for i_data in data["inputs"]:
                new_i = new_item.inputs.add()
                new_i.input_name = i_data["input_name"]
                new_i.override_type = i_data["override_type"]
                new_i.value_bool = i_data["value_bool"]
                new_i.value_int = i_data["value_int"]
                new_i.value_float = i_data["value_float"]
                new_i.value_string = i_data["value_string"]
            mapping.node_override_index = len(lst) - 1
            self.report({'INFO'}, f"Pasted Target: {data['parent_group']}")
        return {'FINISHED'}

# --- INPUT OPERATORS ---

class BATCH_STL_OT_input_actions(bpy.types.Operator):
    bl_idname = "batch_stl.input_actions"
    bl_label = "Input Actions"
    bl_description = "Manage input overrides"

    @classmethod
    def description(cls, context, properties):
        if properties.action == 'ADD': return "Add a new input configuration"
        if properties.action == 'REMOVE': return "Remove the selected input"
        if properties.action == 'UP': return "Move the selected input up in the list"
        if properties.action == 'DOWN': return "Move the selected input down in the list"
        if properties.action == 'DUPLICATE': return "Duplicate the selected input configuration"
        if properties.action == 'COPY': return "Copy the selected input configuration to the clipboard"
        if properties.action == 'PASTE': return "Paste an input configuration from the clipboard"
        return "Manage input overrides"

    action: bpy.props.EnumProperty(items=(
        ('ADD', "Add", ""), ('REMOVE', "Remove", ""),
        ('UP', "Up", ""), ('DOWN', "Down", ""),
        ('DUPLICATE', "Duplicate", ""),
        ('COPY', "Copy", ""), ('PASTE', "Paste", "")
    ))

    def execute(self, context):
        preset = get_active_preset(context.scene)
        mapping = get_active_mapping(preset)
        ovr = get_active_override(mapping)
        if not ovr: return {'CANCELLED'}

        lst = ovr.inputs
        idx = ovr.input_index

        if self.action == 'ADD':
            lst.add()
            ovr.input_index = len(lst) - 1
        elif self.action == 'REMOVE' and lst:
            lst.remove(idx)
            ovr.input_index = min(max(0, idx - 1), len(lst) - 1)
        elif self.action == 'UP' and idx > 0:
            lst.move(idx, idx - 1)
            ovr.input_index -= 1
        elif self.action == 'DOWN' and idx < len(lst) - 1:
            lst.move(idx, idx + 1)
            ovr.input_index += 1
        elif self.action == 'DUPLICATE' and lst:
            src = lst[idx]
            new_item = lst.add()
            new_item.input_name = src.input_name
            new_item.override_type = src.override_type
            new_item.value_bool = src.value_bool
            new_item.value_int = src.value_int
            new_item.value_float = src.value_float
            new_item.value_string = src.value_string
            ovr.input_index = len(lst) - 1
        elif self.action == 'COPY' and lst:
            src = lst[idx]
            _clipboard["input"] = {
                "input_name": src.input_name,
                "override_type": src.override_type,
                "value_bool": src.value_bool,
                "value_int": src.value_int,
                "value_float": src.value_float,
                "value_string": src.value_string
            }
            self.report({'INFO'}, f"Copied Input: {src.input_name}")
        elif self.action == 'PASTE' and _clipboard.get("input"):
            data = _clipboard["input"]
            new_item = lst.add()
            new_item.input_name = data["input_name"]
            new_item.override_type = data["override_type"]
            new_item.value_bool = data["value_bool"]
            new_item.value_int = data["value_int"]
            new_item.value_float = data["value_float"]
            new_item.value_string = data["value_string"]
            ovr.input_index = len(lst) - 1
            self.report({'INFO'}, f"Pasted Input: {data['input_name']}")

        return {'FINISHED'}

# --- FAST EXPORT OPERATOR ---

class EXPORT_OT_batch_stl_multi(bpy.types.Operator):
    bl_idname = "export_scene.batch_stl_multi"
    bl_label = "Batch Export STLs"
    bl_description = "Export collections to STL files using the configured preset overrides"
    bl_options = {"REGISTER"}

    preset_index: bpy.props.IntProperty(default=-1)

    @classmethod
    def poll(cls, context):
        return len(context.scene.batch_stl_presets) > 0

    def execute(self, context):
        scene = context.scene

        if self.preset_index >= 0 and self.preset_index < len(scene.batch_stl_presets):
            preset = scene.batch_stl_presets[self.preset_index]
        else:
            preset = get_active_preset(scene)

        if not preset:
            return {"CANCELLED"}

        if not scene.batch_stl_root_dir:
            self.report({'ERROR'}, "Please select a Root Export Directory first.")
            return {"CANCELLED"}

        root_dir = bpy.path.abspath(scene.batch_stl_root_dir)

        if preset.preset_prefix:
            root_dir = os.path.normpath(os.path.join(root_dir, preset.preset_prefix))

        if context.active_object and context.mode != "OBJECT":
            bpy.ops.object.mode_set(mode="OBJECT")

        total_exported = 0

        for item in preset.mappings:
            if not item.collection_name:
                continue

            out_dir = os.path.normpath(os.path.join(root_dir, item.sub_path))
            os.makedirs(out_dir, exist_ok=True)

            root_layer_coll = find_layer_collection(context.view_layer.layer_collection, item.collection_name)
            if not root_layer_coll:
                continue

            objects_to_export = list(set(get_enabled_objects_recursive(root_layer_coll)))
            if not objects_to_export:
                continue

            global_original_states = []

            try:
                # 1. Apply GLOBAL internal node overrides specific to THIS mapped collection
                for override in item.node_overrides:
                    if override.override_target != 'NODE' or not override.parent_group or not override.node_name:
                        continue

                    parent_tree = bpy.data.node_groups.get(override.parent_group)
                    if not parent_tree:
                        self.report({'WARNING'}, f"Node group '{override.parent_group}' not found. Skipping.")
                        continue

                    node_names = [n.strip() for n in override.node_name.split(',')]

                    for n_name in node_names:
                        if not n_name: continue

                        target_node = parent_tree.nodes.get(n_name)
                        if not target_node:
                            self.report({'WARNING'}, f"Node '{n_name}' not found in '{override.parent_group}'.")
                            continue

                        for inp in override.inputs:
                            if not inp.input_name: continue

                            socket = target_node.inputs.get(inp.input_name)
                            if not socket:
                                self.report({'WARNING'}, f"Input '{inp.input_name}' not found on node '{n_name}'.")
                                continue

                            link_from = socket.links[0].from_socket if socket.is_linked else None
                            global_original_states.append((socket, socket.default_value, link_from, parent_tree))

                            if socket.is_linked:
                                parent_tree.links.remove(socket.links[0])

                            if inp.override_type == 'BOOLEAN':
                                socket.default_value = inp.value_bool
                            elif inp.override_type == 'INT':
                                socket.default_value = inp.value_int
                            elif inp.override_type == 'FLOAT':
                                socket.default_value = inp.value_float
                            elif inp.override_type == 'STRING':
                                socket.default_value = inp.value_string

                if global_original_states:
                    context.view_layer.update()

                # 2. Iterate through objects and apply PER-OBJECT modifier overrides
                for obj in objects_to_export:
                    obj_mod_states = []

                    for override in item.node_overrides:
                        if override.override_target != 'MODIFIER' or not override.parent_group:
                            continue

                        for mod in obj.modifiers:
                            if mod.type == 'NODES' and mod.node_group and mod.node_group.name == override.parent_group:
                                for inp in override.inputs:
                                    ident = get_modifier_socket_identifier(mod.node_group, inp.input_name)
                                    if ident:
                                        orig_val, is_set = get_modifier_input(mod, ident)
                                        default_val = get_modifier_socket_default(mod.node_group, inp.input_name)

                                        obj_mod_states.append((mod, ident, is_set, orig_val, default_val))

                                        set_val = None
                                        if inp.override_type == 'BOOLEAN':
                                            set_val = inp.value_bool
                                        elif inp.override_type == 'INT':
                                            set_val = inp.value_int
                                        elif inp.override_type == 'FLOAT':
                                            set_val = inp.value_float
                                        elif inp.override_type == 'STRING':
                                            set_val = inp.value_string

                                        if set_val is not None:
                                            set_modifier_input(mod, ident, set_val)

                    if obj_mod_states:
                        obj.update_tag()
                        context.view_layer.update()

                    # Evaluate and Export Object
                    depsgraph = context.evaluated_depsgraph_get()
                    obj_eval = obj.evaluated_get(depsgraph)

                    try:
                        mesh = obj_eval.to_mesh()
                    except RuntimeError:
                        continue

                    if mesh:
                        filepath = os.path.join(out_dir, f"{bpy.path.clean_name(obj.name)}.stl")
                        write_fast_binary_stl(filepath, mesh, obj.matrix_world)
                        obj_eval.to_mesh_clear()
                        total_exported += 1

                    # 3. Restore PER-OBJECT modifier overrides immediately after exporting
                    for mod, ident, is_set, orig_val, default_val in obj_mod_states:
                        if is_set and orig_val is not None:
                            set_modifier_input(mod, ident, orig_val)
                        else:
                            unset_modifier_input(mod, ident, default_val)

                    if obj_mod_states:
                        obj.update_tag()
                        context.view_layer.update()

            except Exception as e:
                self.report({'ERROR'}, f"Export failed on {item.collection_name}: {str(e)}")

            finally:
                # 4. Restore GLOBAL internal node overrides before moving to next mapped collection
                for socket, original_val, link_from, parent_tree in global_original_states:
                    try:
                        socket.default_value = original_val
                        if link_from:
                            parent_tree.links.new(link_from, socket)
                    except Exception:
                        pass

                if global_original_states:
                    context.view_layer.update()

        if total_exported > 0:
            self.report({'INFO'}, f"Successfully exported {total_exported} STLs using '{preset.name}'")
        return {"FINISHED"}

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
        if item.collection_name:
            row.prop(item, "collection_name", text="", emboss=False, icon='OUTLINER_COLLECTION')
        else:
            row.label(text="Assign a Collection", icon='ERROR')

        row.prop(item, "sub_path", text="", emboss=False, icon='FILE_FOLDER')

class BATCH_STL_UL_overrides(bpy.types.UIList):
    def draw_item(self, context, layout, data, item, icon, active_data, active_propname, index):
        if item.override_target == 'MODIFIER':
            if item.parent_group:
                layout.label(text=f"Modifier: {item.parent_group} ({len(item.inputs)} inputs)", icon='MODIFIER')
            else:
                layout.label(text="Unassigned Modifier Target", icon='ERROR')
        else:
            if item.parent_group and item.node_name:
                layout.label(text=f"{item.parent_group} -> {item.node_name} ({len(item.inputs)} inputs)", icon='NODETREE')
            else:
                layout.label(text="Unassigned Node Target", icon='ERROR')

class BATCH_STL_UL_inputs(bpy.types.UIList):
    def draw_item(self, context, layout, data, item, icon, active_data, active_propname, index):
        if item.input_name:
            layout.label(text=item.input_name, icon='FORWARD')
            if item.override_type == 'BOOLEAN':
                layout.prop(item, "value_bool", text="")
            elif item.override_type == 'INT':
                layout.prop(item, "value_int", text="")
            elif item.override_type == 'FLOAT':
                layout.prop(item, "value_float", text="")
            elif item.override_type == 'STRING':
                layout.prop(item, "value_string", text="", emboss=False)
        else:
            layout.label(text="Unassigned Input", icon='ERROR')

# --- UI PANEL ---

def draw_list_controls(layout, operator_id, use_clipboard=False):
    col = layout.column(align=True)
    col.operator(operator_id, icon='ADD', text="").action = 'ADD'
    col.operator(operator_id, icon='REMOVE', text="").action = 'REMOVE'
    col.separator()
    col.operator(operator_id, icon='TRIA_UP', text="").action = 'UP'
    col.operator(operator_id, icon='TRIA_DOWN', text="").action = 'DOWN'
    col.separator()
    col.operator(operator_id, icon='DUPLICATE', text="").action = 'DUPLICATE'
    if use_clipboard:
        col.separator()
        col.operator(operator_id, icon='COPYDOWN', text="").action = 'COPY'
        col.operator(operator_id, icon='PASTEDOWN', text="").action = 'PASTE'

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

        row = layout.row()
        icon_presets = 'TRIA_DOWN' if scene.batch_stl_show_presets else 'TRIA_RIGHT'
        row.prop(scene, "batch_stl_show_presets", icon=icon_presets, icon_only=True, emboss=False)
        row.label(text="Export Presets:", icon='PRESET')

        row.operator("batch_stl.import_presets_json", text="", icon='IMPORT')
        row.operator("batch_stl.export_presets_json", text="", icon='EXPORT')

        if scene.batch_stl_show_presets:
            p_row = layout.row()
            p_row.template_list("BATCH_STL_UL_presets", "", scene, "batch_stl_presets", scene, "batch_stl_preset_index", rows=3)
            draw_list_controls(p_row, "batch_stl.preset_actions", use_clipboard=False)

        active_preset = get_active_preset(scene)
        if active_preset is None:
            return

        layout.separator()
        layout.prop(active_preset, "preset_prefix", icon='FILE_FOLDER')
        layout.separator()

        box = layout.box()
        header_row = box.row()
        icon_mappings = 'TRIA_DOWN' if active_preset.show_mappings else 'TRIA_RIGHT'
        header_row.prop(active_preset, "show_mappings", icon=icon_mappings, icon_only=True, emboss=False)
        header_row.label(text=f"Collections to Export:", icon='OUTLINER_COLLECTION')

        if active_preset.show_mappings:
            m_row = box.row()
            m_row.template_list("BATCH_STL_UL_items", "", active_preset, "mappings", active_preset, "mapping_index", rows=3)
            draw_list_controls(m_row, "batch_stl.mapping_actions", use_clipboard=True)

            active_item = get_active_mapping(active_preset)
            if active_item:
                sub_box = box.box()
                sub_box.prop_search(active_item, "collection_name", bpy.data, "collections", text="Collection")
                sub_box.prop(active_item, "sub_path")

                # NESTED OVERRIDES SECTION
                layout.separator()
                obox = layout.box()
                oheader_row = obox.row()
                icon_overrides = 'TRIA_DOWN' if active_item.show_overrides else 'TRIA_RIGHT'
                oheader_row.prop(active_item, "show_overrides", icon=icon_overrides, icon_only=True, emboss=False)
                oheader_row.label(text=f"Overrides for '{active_item.collection_name}':", icon='MODIFIER')

                if active_item.show_overrides:
                    orow = obox.row()
                    orow.template_list("BATCH_STL_UL_overrides", "", active_item, "node_overrides", active_item, "node_override_index", rows=3)
                    draw_list_controls(orow, "batch_stl.override_actions", use_clipboard=True)

                    active_ovr = get_active_override(active_item)
                    if active_ovr:
                        sub_obox = obox.box()
                        sub_obox.prop(active_ovr, "override_target", text="Target")
                        sub_obox.prop_search(active_ovr, "parent_group", bpy.data, "node_groups", text="Node Group")

                        if active_ovr.override_target == 'NODE':
                            sub_obox.prop(active_ovr, "node_name", text="Internal Node Name(s)")

                        sub_obox.separator()

                        iheader = sub_obox.row()
                        icon_inputs = 'TRIA_DOWN' if active_ovr.show_inputs else 'TRIA_RIGHT'
                        iheader.prop(active_ovr, "show_inputs", icon=icon_inputs, icon_only=True, emboss=False)
                        iheader.label(text="Inputs to Override:", icon='NODE_COMPOSITING')

                        if active_ovr.show_inputs:
                            irow = sub_obox.row()
                            irow.template_list("BATCH_STL_UL_inputs", "", active_ovr, "inputs", active_ovr, "input_index", rows=3)
                            draw_list_controls(irow, "batch_stl.input_actions", use_clipboard=True)

                            if active_ovr.inputs and 0 <= active_ovr.input_index < len(active_ovr.inputs):
                                active_inp = active_ovr.inputs[active_ovr.input_index]
                                ibox = sub_obox.box()
                                ibox.prop(active_inp, "input_name", text="Input Name")
                                ibox.prop(active_inp, "override_type", text="Type")

                                if active_inp.override_type == 'BOOLEAN':
                                    ibox.prop(active_inp, "value_bool")
                                elif active_inp.override_type == 'INT':
                                    ibox.prop(active_inp, "value_int")
                                elif active_inp.override_type == 'FLOAT':
                                    ibox.prop(active_inp, "value_float")
                                elif active_inp.override_type == 'STRING':
                                    ibox.prop(active_inp, "value_string")

# --- REGISTRATION ---

classes = (
    BatchSTLNodeInput,
    BatchSTLNodeOverride,
    BatchSTLExportItem,
    BatchSTLExportPreset,
    BATCH_STL_UL_items,
    BATCH_STL_UL_presets,
    BATCH_STL_UL_overrides,
    BATCH_STL_UL_inputs,
    BATCH_STL_OT_preset_actions,
    BATCH_STL_OT_mapping_actions,
    BATCH_STL_OT_override_actions,
    BATCH_STL_OT_input_actions,
    BATCH_STL_OT_export_presets_json,
    BATCH_STL_OT_import_presets_json,
    EXPORT_OT_batch_stl_multi,
    VIEW3D_PT_batch_export_stl_multi,
)

def register():
    for cls in classes:
        bpy.utils.register_class(cls)

    bpy.types.Scene.batch_stl_root_dir = bpy.props.StringProperty(
        name="Root Export Directory",
        default="//",
        subtype="DIR_PATH",
    )
    bpy.types.Scene.batch_stl_show_presets = bpy.props.BoolProperty(default=True)
    bpy.types.Scene.batch_stl_presets = bpy.props.CollectionProperty(type=BatchSTLExportPreset)
    bpy.types.Scene.batch_stl_preset_index = bpy.props.IntProperty(default=0)

def unregister():
    for cls in reversed(classes):
        bpy.utils.unregister_class(cls)

    del bpy.types.Scene.batch_stl_root_dir
    del bpy.types.Scene.batch_stl_show_presets
    del bpy.types.Scene.batch_stl_presets
    del bpy.types.Scene.batch_stl_preset_index
