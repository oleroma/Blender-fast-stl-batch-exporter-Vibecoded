import os
import bpy
import struct

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

def get_active_override(preset):
    if preset and preset.node_overrides and 0 <= preset.node_override_index < len(preset.node_overrides):
        return preset.node_overrides[preset.node_override_index]
    return None

# --- PROPERTIES ---

class BatchSTLExportItem(bpy.types.PropertyGroup):
    collection_name: bpy.props.StringProperty(name="Collection", default="")
    sub_path: bpy.props.StringProperty(name="Sub-folder Path", default="")

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
    parent_group: bpy.props.StringProperty(name="Parent Group", default="")
    node_name: bpy.props.StringProperty(name="Node Name", default="")

    inputs: bpy.props.CollectionProperty(type=BatchSTLNodeInput)
    input_index: bpy.props.IntProperty(default=0)

    show_inputs: bpy.props.BoolProperty(default=True)

class BatchSTLExportPreset(bpy.types.PropertyGroup):
    name: bpy.props.StringProperty(name="Preset Name", default="New Preset")

    mappings: bpy.props.CollectionProperty(type=BatchSTLExportItem)
    mapping_index: bpy.props.IntProperty(default=0)

    node_overrides: bpy.props.CollectionProperty(type=BatchSTLNodeOverride)
    node_override_index: bpy.props.IntProperty(default=0)

    show_mappings: bpy.props.BoolProperty(default=True)
    show_overrides: bpy.props.BoolProperty(default=True)

# --- PRESET OPERATORS ---

class BATCH_STL_OT_preset_actions(bpy.types.Operator):
    bl_idname = "batch_stl.preset_actions"
    bl_label = "Preset Actions"
    action: bpy.props.EnumProperty(items=(('ADD', "Add", ""), ('REMOVE', "Remove", ""), ('UP', "Up", ""), ('DOWN', "Down", ""), ('DUPLICATE', "Duplicate", "")))

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
            for m in src.mappings:
                new_m = new_item.mappings.add()
                new_m.collection_name = m.collection_name
                new_m.sub_path = m.sub_path
            for o in src.node_overrides:
                new_o = new_item.node_overrides.add()
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

class BATCH_STL_OT_mapping_actions(bpy.types.Operator):
    bl_idname = "batch_stl.mapping_actions"
    bl_label = "Mapping Actions"
    action: bpy.props.EnumProperty(items=(('ADD', "Add", ""), ('REMOVE', "Remove", ""), ('UP', "Up", ""), ('DOWN', "Down", ""), ('DUPLICATE', "Duplicate", "")))

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
            new_item.collection_name = src.collection_name
            new_item.sub_path = src.sub_path
            preset.mapping_index = len(lst) - 1
        return {'FINISHED'}

# --- OVERRIDE OPERATORS ---

class BATCH_STL_OT_override_actions(bpy.types.Operator):
    bl_idname = "batch_stl.override_actions"
    bl_label = "Override Actions"
    action: bpy.props.EnumProperty(items=(('ADD', "Add", ""), ('REMOVE', "Remove", ""), ('UP', "Up", ""), ('DOWN', "Down", ""), ('DUPLICATE', "Duplicate", "")))

    def execute(self, context):
        preset = get_active_preset(context.scene)
        if not preset: return {'CANCELLED'}
        lst = preset.node_overrides
        idx = preset.node_override_index

        if self.action == 'ADD':
            lst.add()
            preset.node_override_index = len(lst) - 1
        elif self.action == 'REMOVE' and lst:
            lst.remove(idx)
            preset.node_override_index = min(max(0, idx - 1), len(lst) - 1)
        elif self.action == 'UP' and idx > 0:
            lst.move(idx, idx - 1)
            preset.node_override_index -= 1
        elif self.action == 'DOWN' and idx < len(lst) - 1:
            lst.move(idx, idx + 1)
            preset.node_override_index += 1
        elif self.action == 'DUPLICATE' and lst:
            src = lst[idx]
            new_item = lst.add()
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
            preset.node_override_index = len(lst) - 1
        return {'FINISHED'}

# --- INPUT OPERATORS ---

class BATCH_STL_OT_input_actions(bpy.types.Operator):
    bl_idname = "batch_stl.input_actions"
    bl_label = "Input Actions"
    action: bpy.props.EnumProperty(items=(('ADD', "Add", ""), ('REMOVE', "Remove", ""), ('UP', "Up", ""), ('DOWN', "Down", ""), ('DUPLICATE', "Duplicate", "")))

    def execute(self, context):
        preset = get_active_preset(context.scene)
        ovr = get_active_override(preset)
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
        return {'FINISHED'}

# --- FAST EXPORT OPERATOR ---

class EXPORT_OT_batch_stl_multi(bpy.types.Operator):
    bl_idname = "export_scene.batch_stl_multi"
    bl_label = "Batch Export STLs"
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

        if context.active_object and context.mode != "OBJECT":
            bpy.ops.object.mode_set(mode="OBJECT")

        original_states = []
        for override in preset.node_overrides:
            if not override.parent_group or not override.node_name:
                continue

            parent_tree = bpy.data.node_groups.get(override.parent_group)
            if not parent_tree:
                continue

            target_node = parent_tree.nodes.get(override.node_name)
            if not target_node:
                self.report({'WARNING'}, f"Node '{override.node_name}' not found in '{override.parent_group}'")
                continue

            for inp in override.inputs:
                if not inp.input_name:
                    continue

                socket = target_node.inputs.get(inp.input_name)
                if not socket:
                    self.report({'WARNING'}, f"Input '{inp.input_name}' not found on node '{override.node_name}'")
                    continue

                original_states.append((socket, socket.default_value))

                if inp.override_type == 'BOOLEAN':
                    socket.default_value = inp.value_bool
                elif inp.override_type == 'INT':
                    socket.default_value = inp.value_int
                elif inp.override_type == 'FLOAT':
                    socket.default_value = inp.value_float
                elif inp.override_type == 'STRING':
                    socket.default_value = inp.value_string

        if original_states:
            context.view_layer.update()

        total_exported = 0

        try:
            depsgraph = context.evaluated_depsgraph_get()

            for item in preset.mappings:
                if not item.collection_name:
                    continue

                out_dir = os.path.normpath(os.path.join(root_dir, item.sub_path))
                os.makedirs(out_dir, exist_ok=True)

                root_layer_coll = find_layer_collection(context.view_layer.layer_collection, item.collection_name)
                if not root_layer_coll:
                    continue

                objects_to_export = list(set(get_enabled_objects_recursive(root_layer_coll)))

                for obj in objects_to_export:
                    obj_eval = obj.evaluated_get(depsgraph)
                    try:
                        mesh = obj_eval.to_mesh()
                    except RuntimeError:
                        continue

                    if not mesh:
                        continue

                    filepath = os.path.join(out_dir, f"{bpy.path.clean_name(obj.name)}.stl")
                    write_fast_binary_stl(filepath, mesh, obj.matrix_world)
                    obj_eval.to_mesh_clear()
                    total_exported += 1

        finally:
            for socket, original_val in original_states:
                socket.default_value = original_val

            if original_states:
                context.view_layer.update()

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
        if item.collection_name:
            layout.label(text=item.collection_name, icon='OUTLINER_COLLECTION')
            if item.sub_path:
                layout.label(text=f"/{item.sub_path}", icon='FILE_FOLDER')
        else:
            layout.label(text="Assign a Collection", icon='ERROR')

class BATCH_STL_UL_overrides(bpy.types.UIList):
    def draw_item(self, context, layout, data, item, icon, active_data, active_propname, index):
        if item.parent_group and item.node_name:
            layout.label(text=f"{item.parent_group} -> {item.node_name} ({len(item.inputs)} inputs)", icon='NODETREE')
        else:
            layout.label(text="Unassigned Target Node", icon='ERROR')

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

def draw_list_controls(layout, operator_id):
    col = layout.column(align=True)
    col.operator(operator_id, icon='ADD', text="").action = 'ADD'
    col.operator(operator_id, icon='REMOVE', text="").action = 'REMOVE'
    col.separator()
    col.operator(operator_id, icon='TRIA_UP', text="").action = 'UP'
    col.operator(operator_id, icon='TRIA_DOWN', text="").action = 'DOWN'
    col.separator()
    col.operator(operator_id, icon='DUPLICATE', text="").action = 'DUPLICATE'

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

        if scene.batch_stl_show_presets:
            p_row = layout.row()
            p_row.template_list("BATCH_STL_UL_presets", "", scene, "batch_stl_presets", scene, "batch_stl_preset_index", rows=3)
            draw_list_controls(p_row, "batch_stl.preset_actions")

        active_preset = get_active_preset(scene)
        if active_preset is None:
            return

        layout.separator()

        box = layout.box()
        header_row = box.row()
        icon_mappings = 'TRIA_DOWN' if active_preset.show_mappings else 'TRIA_RIGHT'
        header_row.prop(active_preset, "show_mappings", icon=icon_mappings, icon_only=True, emboss=False)
        header_row.label(text=f"Collections to Export:", icon='OUTLINER_COLLECTION')

        if active_preset.show_mappings:
            m_row = box.row()
            m_row.template_list("BATCH_STL_UL_items", "", active_preset, "mappings", active_preset, "mapping_index", rows=3)
            draw_list_controls(m_row, "batch_stl.mapping_actions")

            if active_preset.mappings and 0 <= active_preset.mapping_index < len(active_preset.mappings):
                active_item = active_preset.mappings[active_preset.mapping_index]
                sub_box = box.box()
                sub_box.prop_search(active_item, "collection_name", bpy.data, "collections", text="Collection")
                sub_box.prop(active_item, "sub_path")

        layout.separator()

        obox = layout.box()
        oheader_row = obox.row()
        icon_overrides = 'TRIA_DOWN' if active_preset.show_overrides else 'TRIA_RIGHT'
        oheader_row.prop(active_preset, "show_overrides", icon=icon_overrides, icon_only=True, emboss=False)
        oheader_row.label(text=f"Node Instance Targets:", icon='MODIFIER')

        if active_preset.show_overrides:
            orow = obox.row()
            orow.template_list("BATCH_STL_UL_overrides", "", active_preset, "node_overrides", active_preset, "node_override_index", rows=3)
            draw_list_controls(orow, "batch_stl.override_actions")

            active_ovr = get_active_override(active_preset)
            if active_ovr:
                sub_obox = obox.box()
                sub_obox.prop_search(active_ovr, "parent_group", bpy.data, "node_groups", text="Parent Group")
                sub_obox.prop(active_ovr, "node_name", text="Target Node Name")

                sub_obox.separator()

                iheader = sub_obox.row()
                icon_inputs = 'TRIA_DOWN' if active_ovr.show_inputs else 'TRIA_RIGHT'
                iheader.prop(active_ovr, "show_inputs", icon=icon_inputs, icon_only=True, emboss=False)
                iheader.label(text="Inputs to Override:", icon='NODE_COMPOSITING')

                if active_ovr.show_inputs:
                    irow = sub_obox.row()
                    irow.template_list("BATCH_STL_UL_inputs", "", active_ovr, "inputs", active_ovr, "input_index", rows=3)
                    draw_list_controls(irow, "batch_stl.input_actions")

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
    BatchSTLExportItem,
    BatchSTLNodeInput,
    BatchSTLNodeOverride,
    BatchSTLExportPreset,
    BATCH_STL_UL_items,
    BATCH_STL_UL_presets,
    BATCH_STL_UL_overrides,
    BATCH_STL_UL_inputs,
    BATCH_STL_OT_preset_actions,
    BATCH_STL_OT_mapping_actions,
    BATCH_STL_OT_override_actions,
    BATCH_STL_OT_input_actions,
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
