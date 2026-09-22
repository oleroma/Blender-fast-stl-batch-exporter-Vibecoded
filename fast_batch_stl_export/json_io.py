import bpy
import json
from bpy_extras.io_utils import ExportHelper, ImportHelper

# --- DICT MAPPINGS ---
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

# --- OPERATORS ---
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
    bl_description = "Import batch export presets from a JSON configuration file"
    filename_ext = ".json"
    filter_glob: bpy.props.StringProperty(default="*.json", options={'HIDDEN'})

    def execute(self, context):
        with open(self.filepath, 'r') as f: data = json.load(f)
        for p_data in data: paste_preset_from_dict(context.scene.batch_stl_presets.add(), p_data)
        return {'FINISHED'}

classes = (BATCH_STL_OT_export_presets_json, BATCH_STL_OT_import_presets_json)
