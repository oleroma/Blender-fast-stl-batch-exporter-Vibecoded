import bpy
from .properties import get_active_preset, get_active_mapping
from .json_io import copy_preset_to_dict, paste_preset_from_dict, copy_mapping_to_dict, paste_mapping_from_dict, copy_override_to_dict, paste_override_from_dict
from .core_engine import parse_sweep_values

_clipboard = {
    "preset": None,
    "mapping": None,
    "override": None,
    "input": None
}

class BATCH_STL_OT_preset_actions(bpy.types.Operator):
    bl_idname = "batch_stl.preset_actions"
    bl_label = "Preset Actions"
    action: bpy.props.EnumProperty(items=(('ADD', "", ""), ('REMOVE', "", ""), ('UP', "", ""), ('DOWN', "", ""), ('COPY', "", ""), ('PASTE', "", "")))
    shift_pressed: bpy.props.BoolProperty(options={'HIDDEN', 'SKIP_SAVE'}, default=False)

    def invoke(self, context, event):
        self.shift_pressed = event.shift
        return self.execute(context)

    def execute(self, context):
        lst = context.scene.batch_stl_presets
        idx = context.scene.batch_stl_preset_index
        if self.action == 'ADD': lst.add(); context.scene.batch_stl_preset_index = len(lst) - 1
        elif self.action == 'REMOVE' and lst: lst.remove(idx); context.scene.batch_stl_preset_index = max(0, idx - 1)
        elif self.action == 'UP' and idx > 0:
            lst.move(idx, 0 if self.shift_pressed else idx - 1)
            context.scene.batch_stl_preset_index = 0 if self.shift_pressed else idx - 1
        elif self.action == 'DOWN' and idx < len(lst) - 1:
            lst.move(idx, len(lst) - 1 if self.shift_pressed else idx + 1)
            context.scene.batch_stl_preset_index = len(lst) - 1 if self.shift_pressed else idx + 1
        elif self.action == 'COPY' and lst: _clipboard["preset"] = copy_preset_to_dict(lst[idx])
        elif self.action == 'PASTE' and _clipboard.get("preset"): paste_preset_from_dict(lst.add(), _clipboard["preset"]); context.scene.batch_stl_preset_index = len(lst) - 1
        return {'FINISHED'}

class BATCH_STL_OT_mapping_actions(bpy.types.Operator):
    bl_idname = "batch_stl.mapping_actions"
    bl_label = "Mapping Actions"
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
        return {'FINISHED'}

class BATCH_STL_OT_override_actions(bpy.types.Operator):
    bl_idname = "batch_stl.override_actions"
    bl_label = "Override Actions"
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
        return {'FINISHED'}

class BATCH_STL_OT_input_actions(bpy.types.Operator):
    bl_idname = "batch_stl.input_actions"
    bl_label = "Input Actions"
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

        if self.action == 'ADD':
            if self.shift_pressed:
                ovr = ovr_list[self.override_index]
                if ovr.parent_group_ptr:
                    inputs_to_add = []
                    if ovr.override_target == 'NODE' and ovr.node_name:
                        node = ovr.parent_group_ptr.nodes.get(ovr.node_name)
                        if node:
                            for inp in node.inputs: inputs_to_add.append((inp.name, inp.type))
                    else:
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
            else: lst.add()

        elif self.action == 'REMOVE' and 0 <= idx < len(lst): lst.remove(idx)
        elif self.action == 'UP' and idx > 0: lst.move(idx, 0 if self.shift_pressed else idx - 1)
        elif self.action == 'DOWN' and 0 <= idx < len(lst) - 1: lst.move(idx, len(lst) - 1 if self.shift_pressed else idx + 1)
        elif self.action == 'COPY' and 0 <= idx < len(lst):
            i = lst[idx]
            _clipboard["input"] = {
                "input_name": i.input_name, "override_type": i.override_type,
                "value_bool": i.value_bool, "value_int": i.value_int, "value_float": i.value_float,
                "value_string": i.value_string, "value_menu": i.value_menu,
                "use_tag": i.use_tag, "tag": i.tag, "use_dir": i.use_dir,
                "use_sweep": getattr(i, "use_sweep", False), "sweep_range": getattr(i, "sweep_range", "")
            }
        elif self.action == 'PASTE' and _clipboard.get("input"):
            new_i = lst.add()
            for k, v in _clipboard["input"].items(): setattr(new_i, k, v)
            if 0 <= idx < len(lst): lst.move(len(lst) - 1, idx + 1)
        return {'FINISHED'}

class BATCH_STL_OT_toggle_sweep(bpy.types.Operator):
    bl_idname = "batch_stl.toggle_sweep"
    bl_label = "Toggle Sweep"
    bl_options = {'UNDO', 'INTERNAL'}
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
            else: inp.use_sweep = False
        else: inp.use_sweep = not inp.use_sweep
        return {'FINISHED'}

class BATCH_STL_OT_toggle_exclusion(bpy.types.Operator):
    bl_idname = "batch_stl.toggle_exclusion"
    bl_label = "Toggle Object Exclusion"
    bl_options = {'UNDO', 'INTERNAL'}
    object_name: bpy.props.StringProperty()

    def execute(self, context):
        preset = get_active_preset(context.scene)
        mapping = get_active_mapping(preset)
        if mapping:
            idx = -1
            for i, e in enumerate(mapping.excluded_objects):
                if e.name == self.object_name:
                    idx = i; break
            if idx >= 0: mapping.excluded_objects.remove(idx)
            else: mapping.excluded_objects.add().name = self.object_name
        return {'FINISHED'}

class BATCH_STL_OT_cancel_export(bpy.types.Operator):
    bl_idname = "batch_stl.cancel_export"
    bl_label = "Cancel Export"
    def execute(self, context):
        context.scene.cancel_export = True
        return {'FINISHED'}

classes = (
    BATCH_STL_OT_preset_actions, BATCH_STL_OT_mapping_actions, BATCH_STL_OT_override_actions,
    BATCH_STL_OT_input_actions, BATCH_STL_OT_toggle_sweep, BATCH_STL_OT_toggle_exclusion,
    BATCH_STL_OT_cancel_export
)
