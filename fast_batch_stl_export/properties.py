import bpy

# --- UTILITY GETTERS ---
def get_active_preset(scene):
    presets = scene.batch_stl_presets
    index = scene.batch_stl_preset_index
    if presets and 0 <= index < len(presets): return presets[index]
    return None

def get_active_mapping(preset):
    if preset and preset.mappings and 0 <= preset.mapping_index < len(preset.mappings):
        return preset.mappings[preset.mapping_index]
    return None

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

        if ovr and ovr.parent_group_ptr:
            if ovr.override_target == 'NODE':
                if ovr.node_name:
                    node = ovr.parent_group_ptr.nodes.get(ovr.node_name)
                    if node and self.input_name in node.inputs:
                        s_type = node.inputs[self.input_name].type
                        if s_type in ['VALUE', 'FLOAT']: self.override_type = 'FLOAT'
                        elif s_type == 'INT': self.override_type = 'INT'
                        elif s_type == 'BOOLEAN': self.override_type = 'BOOLEAN'
                        elif s_type == 'STRING': self.override_type = 'STRING'
                        elif s_type == 'MENU': self.override_type = 'MENU'
                else:
                    if hasattr(ovr.parent_group_ptr, "interface"):
                        item = ovr.parent_group_ptr.interface.items_tree.get(self.input_name)
                        if item:
                            s_type = getattr(item, "socket_type", "")
                            if 'Float' in s_type: self.override_type = 'FLOAT'
                            elif 'Int' in s_type: self.override_type = 'INT'
                            elif 'Bool' in s_type: self.override_type = 'BOOLEAN'
                            elif 'String' in s_type: self.override_type = 'STRING'
                            elif 'Menu' in s_type: self.override_type = 'MENU'
    except Exception: pass

# --- PROPERTY GROUPS ---
class BatchSTLNodeInput(bpy.types.PropertyGroup):
    input_name: bpy.props.StringProperty(name="Input", default="", update=on_input_name_update, description="Name of the socket or modifier property")
    override_type: bpy.props.EnumProperty(
        name="Type",
        items=(
            ('BOOLEAN', "Bool", "Boolean data type"), ('INT', "Int", "Integer data type"),
            ('FLOAT', "Float", "Floating-point data type"), ('STRING', "Str", "String text data type"),
            ('MENU', "Menu", "Menu or Enum data type")
        ),
        default='BOOLEAN'
    )
    value_bool: bpy.props.BoolProperty(name="Value", default=True)
    value_int: bpy.props.IntProperty(name="Value", default=0)
    value_float: bpy.props.FloatProperty(name="Value", default=0.0)
    value_string: bpy.props.StringProperty(name="Value", default="")
    value_menu: bpy.props.StringProperty(name="Value", default="")

    use_tag: bpy.props.BoolProperty(name="Use Tag", default=False)
    tag: bpy.props.StringProperty(name="Tag", default="")
    use_dir: bpy.props.BoolProperty(name="Use Dir", default=False)

    use_sweep: bpy.props.BoolProperty(name="Sweep", default=False)
    sweep_range: bpy.props.StringProperty(name="Sweep Range", default="")

class BatchSTLNodeOverride(bpy.types.PropertyGroup):
    override_target: bpy.props.EnumProperty(
        name="Target",
        items=(('NODE', "Node", ""), ('MODIFIER', "Modifier", "")),
        default='NODE'
    )
    parent_group_ptr: bpy.props.PointerProperty(type=bpy.types.NodeTree, name="Group")
    node_name: bpy.props.StringProperty(name="Node", default="")
    inputs: bpy.props.CollectionProperty(type=BatchSTLNodeInput)

class BatchSTLExcludedObject(bpy.types.PropertyGroup):
    name: bpy.props.StringProperty()

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
    pinned_overrides: bpy.props.CollectionProperty(type=BatchSTLNodeOverride)
    mappings: bpy.props.CollectionProperty(type=BatchSTLExportItem)
    mapping_index: bpy.props.IntProperty(name="Mapping Index", default=0)

classes = (
    BatchSTLNodeInput, BatchSTLNodeOverride, BatchSTLExcludedObject,
    BatchSTLExportItem, BatchSTLExportPreset
)

def register_properties():
    bpy.types.Scene.batch_stl_root_dir = bpy.props.StringProperty(name="Root Export Dir", default="//", subtype="DIR_PATH")
    bpy.types.Scene.batch_stl_presets = bpy.props.CollectionProperty(type=BatchSTLExportPreset)
    bpy.types.Scene.batch_stl_preset_index = bpy.props.IntProperty(name="Active Preset", default=0)
    bpy.types.Scene.is_exporting = bpy.props.BoolProperty(default=False)
    bpy.types.Scene.cancel_export = bpy.props.BoolProperty(default=False)
    bpy.types.Scene.export_progress = bpy.props.FloatProperty(name="Progress", default=0.0, min=0.0, max=1.0)
    bpy.types.Scene.export_status = bpy.props.StringProperty(default="")

def unregister_properties():
    for prop in ["batch_stl_root_dir", "batch_stl_presets", "batch_stl_preset_index", "is_exporting", "cancel_export", "export_progress", "export_status"]:
        if hasattr(bpy.types.Scene, prop):
            delattr(bpy.types.Scene, prop)
