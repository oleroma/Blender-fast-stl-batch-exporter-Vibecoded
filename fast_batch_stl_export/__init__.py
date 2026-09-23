import os
import json
import time
import itertools
import threading
import queue
import subprocess
import tempfile
import bpy
from bpy_extras.io_utils import ExportHelper, ImportHelper

# --- SESSION CLIPBOARD ---
_clipboard = {
    "preset": None,
    "mapping": None,
    "override": None,
    "input": None
}

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
    if inp.override_type == 'BOOLEAN':
        return [True, False]
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
            except ValueError:
                vals.append(0 if inp.override_type == 'INT' else 0.0)
        else:
            vals.append(0 if inp.override_type == 'INT' else 0.0)
        return vals
    elif inp.override_type == 'MENU':
        items = []
        if ovr.parent_group_ptr:
            if ovr.override_target == 'NODE' and ovr.node_name:
                node = ovr.parent_group_ptr.nodes.get(ovr.node_name)
                if node and hasattr(node, 'enum_items'):
                    items = [getattr(item, 'identifier', getattr(item, 'name', '')) for item in node.enum_items]
            elif ovr.override_target == 'NODE' and not ovr.node_name:
                for node in ovr.parent_group_ptr.nodes:
                    if node.type == 'MENU_SWITCH' and hasattr(node, 'enum_items'):
                        for sock in node.inputs:
                            for link in sock.links:
                                if link.from_node.type == 'GROUP_INPUT' and link.from_socket.name == inp.input_name:
                                    items = [getattr(item, 'identifier', getattr(item, 'name', '')) for item in node.enum_items]
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
        if override.override_target == 'NODE' and override.parent_group_ptr:
            parent_tree = override.parent_group_ptr

            if not override.node_name:
                for inp in override.inputs:
                    val = get_input_value(inp)
                    if val is None: continue

                    for node in parent_tree.nodes:
                        if node.type == 'GROUP_INPUT':
                            socket = node.outputs.get(inp.input_name)
                            if socket:
                                for link in list(socket.links):
                                    to_socket = link.to_socket
                                    from_socket = link.from_socket
                                    global_states.append(('SOCKET', to_socket, to_socket.default_value, from_socket, parent_tree))
                                    if not dry_run:
                                        parent_tree.links.remove(link)
                                        to_socket.default_value = val
                if not dry_run: trees_to_update.add(parent_tree)
                continue

            for n_name in [n.strip() for n in override.node_name.split(',') if n.strip()]:
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


# --- PROPERTIES ---

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
    value_menu: bpy.props.StringProperty(name="Value", default="", description="Menu or Enum override value to apply")

    use_tag: bpy.props.BoolProperty(name="Use Tag", default=False, description="Append tag to filename for this permutation. If tag is empty, appends the value.")
    tag: bpy.props.StringProperty(name="Tag", default="", description="Custom string for naming or folder creation")
    use_dir: bpy.props.BoolProperty(name="Use Dir", default=False, description="Create a sub-directory for this specific input permutation")

    use_sweep: bpy.props.BoolProperty(name="Sweep", default=False, description="Enable automatic parameter sweeping across multiple states")
    sweep_range: bpy.props.StringProperty(name="Sweep Range", default="", description="For Int/Float: 'start step count' (e.g. '1.0 0.5 5') | For String: 'item1, item2'")

class BatchSTLNodeOverride(bpy.types.PropertyGroup):
    override_target: bpy.props.EnumProperty(
        name="Target",
        items=(
            ('NODE', "Node", "Target an internal node within a Geometry Nodes group"),
            ('MODIFIER', "Modifier", "Target a socket directly on the Modifier interface")
        ),
        default='NODE',
        description="Target type to override"
    )
    parent_group_ptr: bpy.props.PointerProperty(type=bpy.types.NodeTree, name="Group", description="The parent node tree/group containing the target node or modifier interface")
    node_name: bpy.props.StringProperty(name="Node", default="", description="Exact name of the internal node to override")
    inputs: bpy.props.CollectionProperty(type=BatchSTLNodeInput, description="List of specific input sockets to override")

class BatchSTLExcludedObject(bpy.types.PropertyGroup):
    name: bpy.props.StringProperty(description="Name of the object to exclude from export")

class BatchSTLExportItem(bpy.types.PropertyGroup):
    collection_ptr: bpy.props.PointerProperty(type=bpy.types.Collection, name="Collection", description="Target collection containing the objects to be exported")
    use_tag: bpy.props.BoolProperty(name="Use Tag", default=True, description="Append the specified tag suffix to the exported STL filenames")
    tag: bpy.props.StringProperty(name="Tag", default="", description="Suffix tag string to append to the filename (e.g., '_v2')")
    sub_path: bpy.props.StringProperty(name="Sub-folder", default="", description="Sub-directory path where these STLs will be saved, relative to the preset root")
    node_overrides: bpy.props.CollectionProperty(type=BatchSTLNodeOverride, description="Collection of local overrides applied specifically to this mapped collection")

    use_filter: bpy.props.BoolProperty(name="Filter Objects", default=False, description="Enable to manually exclude specific objects from this collection during export")
    excluded_objects: bpy.props.CollectionProperty(type=BatchSTLExcludedObject, description="List of objects to skip during export")

class BatchSTLExportPreset(bpy.types.PropertyGroup):
    name: bpy.props.StringProperty(name="Preset Name", default="New Preset", description="Name of the batch export preset")
    preset_prefix: bpy.props.StringProperty(name="Preset Root Directory", default="", description="Root folder name for this preset, created inside the global export directory")
    pinned_overrides: bpy.props.CollectionProperty(type=BatchSTLNodeOverride, description="Global overrides applied to all mapped collections in this preset")
    mappings: bpy.props.CollectionProperty(type=BatchSTLExportItem, description="List of collections mapped to this preset for batch export")
    mapping_index: bpy.props.IntProperty(name="Mapping Index", default=0, description="Select the active collection mapping to edit its overrides")


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


# --- UI LISTS & OPERATORS ---

class BATCH_STL_UL_presets(bpy.types.UIList):
    def draw_item(self, context, layout, data, item, icon, active_data, active_propname, index):
        row = layout.row(align=True)
        row.prop(item, "name", text="", emboss=False, icon='PRESET')
        row.prop(item, "preset_prefix", text="", emboss=False, icon='FILE_FOLDER')
        op = row.operator("export_scene.batch_stl_multi", text="", icon='EXPORT')
        op.preset_index = index

class BATCH_STL_UL_items(bpy.types.UIList):
    def draw_item(self, context, layout, data, item, icon, active_data, active_propname, index):
        row = layout.row(align=True)
        row.prop(item, "collection_ptr", text="")
        row.separator(factor=0.5)
        sub_row = row.row(align=True)
        sub_row.prop(item, "use_tag", text="", icon='BOOKMARKS')
        sub_row.prop(item, "use_filter", text="", icon='FILTER')
        sub_row.separator(factor=0.5)
        tag_row = sub_row.row(align=True)
        tag_row.prop(item, "tag", text="", emboss=False)
        row.prop(item, "sub_path", text="", emboss=False, icon='FILE_FOLDER')


class BATCH_STL_OT_preset_actions(bpy.types.Operator):
    bl_idname = "batch_stl.preset_actions"
    bl_label = "Preset Actions"
    bl_options = {'REGISTER', 'INTERNAL'}
    action: bpy.props.EnumProperty(items=(('ADD', "", ""), ('REMOVE', "", ""), ('UP', "", ""), ('DOWN', "", ""), ('COPY', "", ""), ('PASTE', "", "")))
    shift_pressed: bpy.props.BoolProperty(options={'HIDDEN', 'SKIP_SAVE'}, default=False)

    @classmethod
    def description(cls, context, properties):
        return {
            'ADD': "Create a new batch export preset",
            'REMOVE': "Delete the currently selected preset",
            'UP': "Move preset up (Hold SHIFT to move to top)",
            'DOWN': "Move preset down (Hold SHIFT to move to bottom)",
            'COPY': "Copy preset configuration to clipboard",
            'PASTE': "Paste preset configuration from clipboard"
        }.get(properties.action, "Manage presets")

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

        if self.action != 'COPY':
            names = {'ADD': "Add Preset", 'REMOVE': "Remove Preset", 'UP': "Move Preset Up", 'DOWN': "Move Preset Down", 'PASTE': "Paste Preset"}
            bpy.ops.ed.undo_push(message=names.get(self.action, "Preset Action"))

        return {'FINISHED'}

class BATCH_STL_OT_mapping_actions(bpy.types.Operator):
    bl_idname = "batch_stl.mapping_actions"
    bl_label = "Mapping Actions"
    bl_options = {'REGISTER', 'INTERNAL'}
    action: bpy.props.EnumProperty(items=(('ADD', "", ""), ('REMOVE', "", ""), ('UP', "", ""), ('DOWN', "", ""), ('COPY', "", ""), ('PASTE', "", "")))
    shift_pressed: bpy.props.BoolProperty(options={'HIDDEN', 'SKIP_SAVE'}, default=False)

    @classmethod
    def description(cls, context, properties):
        return {
            'ADD': "Map a new collection to this preset",
            'REMOVE': "Remove the selected collection mapping",
            'UP': "Move mapping up (Hold SHIFT for top)",
            'DOWN': "Move mapping down (Hold SHIFT for bottom)",
            'COPY': "Copy mapping configuration to clipboard",
            'PASTE': "Paste mapping configuration from clipboard"
        }.get(properties.action, "Manage mappings")

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

        if self.action != 'COPY':
            names = {'ADD': "Add Mapping", 'REMOVE': "Remove Mapping", 'UP': "Move Mapping Up", 'DOWN': "Move Mapping Down", 'PASTE': "Paste Mapping"}
            bpy.ops.ed.undo_push(message=names.get(self.action, "Mapping Action"))

        return {'FINISHED'}

class BATCH_STL_OT_override_actions(bpy.types.Operator):
    bl_idname = "batch_stl.override_actions"
    bl_label = "Override Actions"
    bl_options = {'REGISTER', 'INTERNAL'}
    action: bpy.props.EnumProperty(items=(('ADD', "", ""), ('REMOVE', "", ""), ('UP', "", ""), ('DOWN', "", ""), ('COPY', "", ""), ('PASTE', "", ""), ('PIN', "", ""), ('UNPIN', "", "")))
    override_index: bpy.props.IntProperty(default=-1)
    is_pinned: bpy.props.BoolProperty(default=False)
    shift_pressed: bpy.props.BoolProperty(options={'HIDDEN', 'SKIP_SAVE'}, default=False)

    @classmethod
    def description(cls, context, properties):
        return {
            'ADD': "Add a new node/modifier override parameter",
            'REMOVE': "Delete this override parameter block",
            'UP': "Move override up (Hold SHIFT for top)",
            'DOWN': "Move override down (Hold SHIFT for bottom)",
            'COPY': "Copy override configuration to clipboard",
            'PASTE': "Paste override configuration from clipboard",
            'PIN': "Pin override globally to all mappings in this preset",
            'UNPIN': "Unpin and convert to local override"
        }.get(properties.action, "Manage overrides")

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

        if self.action != 'COPY':
            names = {'ADD': "Add Override", 'REMOVE': "Remove Override", 'UP': "Move Override Up", 'DOWN': "Move Override Down", 'PASTE': "Paste Override", 'PIN': "Pin Override", 'UNPIN': "Unpin Override"}
            bpy.ops.ed.undo_push(message=names.get(self.action, "Override Action"))

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

    @classmethod
    def description(cls, context, properties):
        return {
            'ADD': "Add a new permutation input state (Hold SHIFT to auto-populate all sockets from target)",
            'REMOVE': "Delete this input state",
            'UP': "Move input up (Hold SHIFT for top)",
            'DOWN': "Move input down (Hold SHIFT for bottom)",
            'COPY': "Copy input state to clipboard",
            'PASTE': "Paste input state from clipboard"
        }.get(properties.action, "Manage input states")

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
            msg = "Paste Input State"

        if msg:
            bpy.ops.ed.undo_push(message=msg)

        return {'FINISHED'}

class BATCH_STL_OT_toggle_sweep(bpy.types.Operator):
    bl_idname = "batch_stl.toggle_sweep"
    bl_label = "Toggle Sweep"
    bl_options = {'REGISTER', 'INTERNAL'}

    override_index: bpy.props.IntProperty(default=-1)
    input_index: bpy.props.IntProperty(default=-1)
    is_pinned: bpy.props.BoolProperty(default=False)

    @classmethod
    def description(cls, context, properties):
        return "Enable automatic parameter sweeping. Shift-Click while enabled to expand sweep into individual static inputs."

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
            bpy.ops.ed.undo_push(message="Enable Sweep" if inp.use_sweep else "Disable Sweep")

        return {'FINISHED'}

class BATCH_STL_OT_toggle_exclusion(bpy.types.Operator):
    bl_idname = "batch_stl.toggle_exclusion"
    bl_label = "Toggle Object Exclusion"
    bl_options = {'REGISTER', 'INTERNAL'}
    object_name: bpy.props.StringProperty()

    @classmethod
    def description(cls, context, properties):
        return f"Toggle export inclusion for '{properties.object_name}'"

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
    bl_description = "Abort the current batch export process"

    def execute(self, context):
        context.scene.cancel_export = True
        return {'FINISHED'}


# --- BATCH EXPORT OPERATOR (HEADLESS & SYNCHRONOUS MANAGER) ---

class EXPORT_OT_batch_stl_multi(bpy.types.Operator):
    bl_idname = "export_scene.batch_stl_multi"
    bl_label = "Export"
    bl_description = "Safely evaluate and batch export the mapped collections"
    bl_options = {"REGISTER"}
    preset_index: bpy.props.IntProperty(default=-1)

    _timer = None
    process = None
    total_operations = 1

    @classmethod
    def poll(cls, context):
        return len(context.scene.batch_stl_presets) > 0 and not context.scene.is_exporting

    def invoke(self, context, event):
        if context.scene.is_exporting: return {'CANCELLED'}

        scene = context.scene
        preset_idx = self.preset_index if self.preset_index >= 0 else scene.batch_stl_preset_index
        if preset_idx < 0 or preset_idx >= len(scene.batch_stl_presets): return {"CANCELLED"}
        preset = scene.batch_stl_presets[preset_idx]

        if not scene.batch_stl_root_dir:
            self.report({'ERROR'}, "Missing Root Directory")
            return {"CANCELLED"}

        # 0. Check for the presence of overrides to determine execution path
        has_overrides = bool(preset.pinned_overrides) or any(bool(m.node_overrides) for m in preset.mappings)

        if not has_overrides:
            # --- SYNCHRONOUS INLINE EXPORT ---
            root_dir = bpy.path.abspath(scene.batch_stl_root_dir)
            if preset.preset_prefix:
                root_dir = os.path.normpath(os.path.join(root_dir, preset.preset_prefix))

            # Count target objects for native cursor progress bar
            total_objs = 0
            for mapping in preset.mappings:
                if not mapping.collection_ptr: continue
                if is_collection_excluded(context, mapping.collection_ptr): continue
                excluded_names = {e.name for e in mapping.excluded_objects} if mapping.use_filter else set()
                for obj in mapping.collection_ptr.all_objects:
                    if obj.type in {"MESH", "CURVE", "SURFACE", "META", "FONT"} and not obj.hide_get() and not obj.hide_viewport:
                        if obj.name not in excluded_names: total_objs += 1

            context.window_manager.progress_begin(0, max(1, total_objs))

            depsgraph = context.evaluated_depsgraph_get()
            exported_count = 0

            for mapping in preset.mappings:
                if not mapping.collection_ptr: continue
                if is_collection_excluded(context, mapping.collection_ptr): continue

                out_dir = os.path.normpath(os.path.join(root_dir, mapping.sub_path))
                os.makedirs(out_dir, exist_ok=True)
                excluded_names = {e.name for e in mapping.excluded_objects} if mapping.use_filter else set()

                for obj in mapping.collection_ptr.all_objects:
                    if obj.type in {"MESH", "CURVE", "SURFACE", "META", "FONT"} and not obj.hide_get() and not obj.hide_viewport:
                        if obj.name in excluded_names: continue

                        obj_eval = obj.evaluated_get(depsgraph)
                        try: mesh = obj_eval.to_mesh()
                        except RuntimeError: mesh = None

                        if mesh:
                            base_tag = mapping.tag if mapping.use_tag and mapping.tag else ""
                            filepath = os.path.join(out_dir, f"{bpy.path.clean_name(obj.name)}{base_tag}.stl")
                            write_fast_binary_stl(filepath, mesh, obj.matrix_world)
                            obj_eval.to_mesh_clear()

                            exported_count += 1
                            context.window_manager.progress_update(exported_count)

            context.window_manager.progress_end()
            self.report({'INFO'}, f"Exported {exported_count} objects directly (No overrides found).")
            return {'FINISHED'}

        # --- HEADLESS EXPORT (With Overrides) ---

        # 1. Setup secure Temp Directory & File Copy
        self.temp_dir = tempfile.mkdtemp(prefix="fast_batch_stl_")
        self.temp_blend = os.path.join(self.temp_dir, "batch_stl_export_temp.blend")

        # Save an uncompressed copy for hyper-fast background handoff
        bpy.ops.wm.save_as_mainfile(filepath=self.temp_blend, copy=True, compress=False)

        # 2. Spawn Headless Subprocess with --factory-startup for instant boot
        cmd = [
            bpy.app.binary_path,
            "--factory-startup",
            "-b", self.temp_blend,
            "-P", __file__,
            "--", "--batch-stl-headless", str(preset_idx)
        ]

        try:
            self.process = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)
        except Exception as e:
            self.report({'ERROR'}, f"Failed to spawn headless Blender: {e}")
            self.cleanup(context)
            return {'CANCELLED'}

        # 3. Non-blocking stdout queue
        self.q = queue.Queue()
        def enqueue_output(out, q):
            for line in iter(out.readline, ''):
                q.put(line)
            out.close()

        self.t = threading.Thread(target=enqueue_output, args=(self.process.stdout, self.q))
        self.t.daemon = True
        self.t.start()

        # Initialize Modal States
        context.scene.is_exporting = True
        context.scene.cancel_export = False
        context.scene.export_progress = 0.0
        context.scene.export_status = f"Spawning Headless Instance for '{preset.name}'..."

        self._timer = context.window_manager.event_timer_add(0.05, window=context.window)
        context.window_manager.modal_handler_add(self)
        return {'RUNNING_MODAL'}

    def modal(self, context, event):
        # Esc Catch & Kill Switch
        if context.scene.cancel_export or (event.type == 'ESC' and event.value == 'PRESS'):
            print("\n[!] Export cancelled by user. Terminating headless instance...")
            if self.process: self.process.terminate()
            self.cleanup(context)
            self.report({'WARNING'}, "Export cancelled by user.")
            return {'CANCELLED'}

        if event.type == 'TIMER':
            # Drain non-blocking output queue
            while True:
                try: line = self.q.get_nowait()
                except queue.Empty: break
                else:
                    line = line.strip()
                    if line.startswith("BATCH_STL_TOTAL:"):
                        try: self.total_operations = int(line.split(":")[1])
                        except Exception: pass
                    elif line.startswith("BATCH_STL_PROGRESS:"):
                        try:
                            cur = int(line.split(":")[1])
                            context.scene.export_progress = cur / max(1, self.total_operations)
                            context.scene.export_status = f"Exporting: Object {cur} / {self.total_operations} (Press ESC to Cancel)"
                        except Exception: pass
                    elif line.startswith("BATCH_STL_DONE"):
                        # FAST EXIT: Actively terminate to skip Blender's slow C-level GC teardown
                        if self.process: self.process.terminate()
                        self.cleanup(context)
                        self.report({'INFO'}, "Batch Export Complete.")
                        for area in context.screen.areas: area.tag_redraw()
                        return {'FINISHED'}
                    elif line:
                        print(f"[Headless] {line}")

            # Fallback Check if subprocess finished unexpectedly
            if self.process.poll() is not None:
                self.cleanup(context)
                self.report({'INFO'}, "Batch Export Complete.")
                return {'FINISHED'}

        return {'PASS_THROUGH'}

    def cleanup(self, context=None):
        if context and self._timer:
            context.window_manager.event_timer_remove(self._timer)
            self._timer = None
        if context: context.scene.is_exporting = False

        try:
            if os.path.exists(self.temp_blend): os.remove(self.temp_blend)
            os.rmdir(self.temp_dir)
        except Exception as e:
            pass


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

def draw_override_block(layout, ovr, o_idx, is_pinned, freq_dict=None):
    if freq_dict is None: freq_dict = {}

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

        if ovr.override_target == 'NODE' and ovr.parent_group_ptr:
            if ovr.node_name:
                if target_node: irow.prop_search(inp, "input_name", target_node, "inputs", text="")
                else: irow.prop(inp, "input_name", text="")
            else:
                if hasattr(ovr.parent_group_ptr, "interface"):
                    irow.prop_search(inp, "input_name", ovr.parent_group_ptr.interface, "items_tree", text="")
                else:
                    irow.prop_search(inp, "input_name", ovr.parent_group_ptr, "inputs", text="")
        elif ovr.override_target == 'MODIFIER' and ovr.parent_group_ptr:
            if hasattr(ovr.parent_group_ptr, "interface"): irow.prop_search(inp, "input_name", ovr.parent_group_ptr.interface, "items_tree", text="")
            else: irow.prop_search(inp, "input_name", ovr.parent_group_ptr, "inputs", text="")
        else: irow.prop(inp, "input_name", text="")

        op = irow.operator("batch_stl.toggle_sweep", text="", icon='FILE_REFRESH', depress=inp.use_sweep)
        op.override_index = o_idx
        op.input_index = i_idx
        op.is_pinned = is_pinned

        if getattr(inp, "use_sweep", False):
            if inp.override_type in ['INT', 'FLOAT', 'STRING']: irow.prop(inp, "sweep_range", text="")
            elif inp.override_type == 'BOOLEAN': irow.label(text="True & False")
            elif inp.override_type == 'MENU': irow.label(text="All Menu Items")
        else:
            if inp.override_type == 'BOOLEAN': irow.prop(inp, "value_bool", text="True" if inp.value_bool else "False", toggle=True)
            elif inp.override_type == 'INT': irow.prop(inp, "value_int", text="")
            elif inp.override_type == 'FLOAT': irow.prop(inp, "value_float", text="")
            elif inp.override_type == 'STRING': irow.prop(inp, "value_string", text="")
            elif inp.override_type == 'MENU': irow.prop(inp, "value_menu", text="")

        key = (ovr.override_target, ovr.node_name, inp.input_name)
        if freq_dict.get(key, 0) > 1:
            irow.prop(inp, "use_tag", text="", icon='BOOKMARKS')
            irow.prop(inp, "tag", text="")
            irow.prop(inp, "use_dir", text="", icon='FILE_FOLDER')

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

        if scene.is_exporting:
            prog_box = layout.box()
            prog_box.label(text=scene.export_status, icon='INFO')
            prog_box.prop(scene, "export_progress", slider=True, text="")
            prog_box.operator("batch_stl.cancel_export", icon='CANCEL', text="Cancel Export (or ESC)")
            layout = layout.column()
            layout.enabled = False

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

        total_mappings = len(active_preset.mappings)
        total_preset_combos = sum(
            len(generate_override_combinations(list(active_preset.pinned_overrides) + list(m.node_overrides)))
            for m in active_preset.mappings
        )

        box = layout.box()
        m_header = box.row()
        m_header.label(text=f"Mappings ({total_mappings} items, {total_preset_combos} combos):", icon='OUTLINER_COLLECTION')
        draw_inline_controls(m_header, "batch_stl.mapping_actions", use_clipboard=True)
        box.template_list("BATCH_STL_UL_items", "", active_preset, "mappings", active_preset, "mapping_index", rows=5)

        active_item = get_active_mapping(active_preset)
        if active_item:

            if active_item.collection_ptr:
                layout.separator()
                if active_item.use_filter:
                    filter_box = layout.box()
                    f_header = filter_box.row()
                    f_header.label(text="Exclude Objects:", icon='FILTER')
                    col = filter_box.column(align=True)
                    for obj in active_item.collection_ptr.all_objects:
                        if obj.type not in {"MESH", "CURVE", "SURFACE", "META", "FONT"}: continue
                        is_excl = any(e.name == obj.name for e in active_item.excluded_objects)
                        icon = 'CHECKBOX_DEHLT' if is_excl else 'CHECKBOX_HLT'
                        op = col.operator("batch_stl.toggle_exclusion", text=obj.name, icon=icon, depress=not is_excl)
                        op.object_name = obj.name

            layout.separator()
            c_name = active_item.collection_ptr.name if active_item.collection_ptr else "Unassigned"
            if active_item.tag: c_name += f" [{active_item.tag}]"

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

            metric_str = f"{c_name}"
            if num_combos > 1: metric_str += f" | {num_combos} combos"
            metric_str += f" | {num_targets} targets | {total_inputs} inputs"

            header = layout.row()
            header.label(text=metric_str, icon='MODIFIER')

            layout.separator()
            pinned_box = layout.box()
            p_header = pinned_box.row()
            p_header.label(text="Global Pinned Overrides:", icon='PINNED')
            p_actions = p_header.row(align=True)
            op = p_actions.operator("batch_stl.override_actions", text="", icon='ADD')
            op.action, op.override_index, op.is_pinned = 'ADD', -1, True
            op = p_actions.operator("batch_stl.override_actions", text="", icon='PASTEDOWN')
            op.action, op.override_index, op.is_pinned = 'PASTE', -1, True

            for o_idx, ovr in enumerate(active_preset.pinned_overrides):
                draw_override_block(pinned_box, ovr, o_idx, True, freq_dict)

            layout.separator()
            local_box = layout.box()
            l_header = local_box.row()
            l_header.label(text="Local Overrides:", icon='UNPINNED')
            l_actions = l_header.row(align=True)
            op = l_actions.operator("batch_stl.override_actions", text="", icon='ADD')
            op.action, op.override_index, op.is_pinned = 'ADD', -1, False
            op = l_actions.operator("batch_stl.override_actions", text="", icon='PASTEDOWN')
            op.action, op.override_index, op.is_pinned = 'PASTE', -1, False

            for o_idx, ovr in enumerate(active_item.node_overrides):
                draw_override_block(local_box, ovr, o_idx, False, freq_dict)


# --- REGISTRATION ---

classes = (
    BatchSTLNodeInput, BatchSTLNodeOverride, BatchSTLExcludedObject, BatchSTLExportItem, BatchSTLExportPreset,
    BATCH_STL_UL_items, BATCH_STL_UL_presets,
    BATCH_STL_OT_preset_actions, BATCH_STL_OT_mapping_actions, BATCH_STL_OT_override_actions,
    BATCH_STL_OT_input_actions, BATCH_STL_OT_toggle_sweep, BATCH_STL_OT_toggle_exclusion,
    BATCH_STL_OT_cancel_export, BATCH_STL_OT_export_presets_json, BATCH_STL_OT_import_presets_json,
    EXPORT_OT_batch_stl_multi, VIEW3D_PT_batch_export_stl_multi,
)

def register():
    for cls in classes: bpy.utils.register_class(cls)

    bpy.types.Scene.batch_stl_root_dir = bpy.props.StringProperty(name="Root Export Dir", default="//", subtype="DIR_PATH", description="Master directory path on disk where all batch STL exports will be saved")
    bpy.types.Scene.batch_stl_presets = bpy.props.CollectionProperty(type=BatchSTLExportPreset, description="List of all batch export presets")
    bpy.types.Scene.batch_stl_preset_index = bpy.props.IntProperty(name="Active Preset", default=0, description="Select the active batch export preset to edit")

    bpy.types.Scene.is_exporting = bpy.props.BoolProperty(default=False)
    bpy.types.Scene.cancel_export = bpy.props.BoolProperty(default=False)
    bpy.types.Scene.export_progress = bpy.props.FloatProperty(name="Progress", default=0.0, min=0.0, max=1.0)
    bpy.types.Scene.export_status = bpy.props.StringProperty(default="")

def unregister():
    for cls in reversed(classes):
        try:
            bpy.utils.unregister_class(cls)
        except RuntimeError:
            pass

    properties_to_remove = [
        "batch_stl_root_dir",
        "batch_stl_presets",
        "batch_stl_preset_index",
        "is_exporting",
        "cancel_export",
        "export_progress",
        "export_status"
    ]

    for prop in properties_to_remove:
        if hasattr(bpy.types.Scene, prop):
            delattr(bpy.types.Scene, prop)


# =========================================================================================
# --- BINARY STL WRITER ---
# =========================================================================================

def write_fast_binary_stl(filepath, mesh, matrix_world):
    import struct
    import numpy as np

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
    with open(filepath, 'wb') as f:
        f.write(b'Batch STL Fast Export' + b'\x00' * 59)
        f.write(struct.pack('<I', num_tris))
        f.write(data.tobytes())

    t_write = time.perf_counter()
    print(f"  │         │    ├─ STL Write: Triangulate/Format: {t_format-t_start:.4f}s | Disk Write: {t_write-t_format:.4f}s")


# =========================================================================================
# --- HEADLESS EXPORT EXECUTION ROUTINE ---
# =========================================================================================

def run_headless_export(preset_index):
    import sys

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

    # Identify all potentially active objects across the entire preset
    active_export_objects = set()
    for mapping in preset.mappings:
        if mapping.collection_ptr and not is_collection_excluded(bpy.context, mapping.collection_ptr):
            active_export_objects.update(mapping.collection_ptr.all_objects)

    # Permanently mute modifiers on any object not involved in this export run
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

    # Pre-calculate absolute total export operations (Permutations * Objects)
    total_operations = 0
    for signature, mappings_in_batch in execution_batches.items():
        first_mapping = mappings_in_batch[0]
        all_overrides = list(preset.pinned_overrides) + list(first_mapping.node_overrides)
        combinations = generate_override_combinations(all_overrides)

        batch_obj_count = 0
        for m in mappings_in_batch:
            excluded_names = {e.name for e in m.excluded_objects} if m.use_filter else set()
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

                    if inp.use_tag: combo_suffix += f"_{naming_str}"
                    if inp.use_dir: combo_subpath = os.path.join(combo_subpath, naming_str)
                    processed_params.add(param_key)

            t_ovr = time.perf_counter()
            global_states = []
            mod_states = []

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

                    excluded_names = {e.name for e in mapping.excluded_objects} if mapping.use_filter else set()

                    for obj in mapping.collection_ptr.all_objects:
                        if obj.type in {"MESH", "CURVE", "SURFACE", "META", "FONT"} and not obj.hide_get() and not obj.hide_viewport:
                            if obj.name in excluded_names: continue

                            t_eval = time.perf_counter()
                            obj_eval = obj.evaluated_get(depsgraph)
                            try: mesh = obj_eval.to_mesh()
                            except RuntimeError: mesh = None

                            print(f"  │         ├─ Evaluated Mesh [{obj.name}]: {time.perf_counter() - t_eval:.4f}s")

                            if mesh:
                                base_tag = mapping.tag if mapping.use_tag and mapping.tag else ""
                                final_tag = base_tag + combo_suffix
                                filepath = os.path.join(out_dir, f"{bpy.path.clean_name(obj.name)}{final_tag}.stl")
                                write_fast_binary_stl(filepath, mesh, obj.matrix_world)
                                obj_eval.to_mesh_clear()

                                current_op_step += 1
                                print(f"BATCH_STL_PROGRESS:{current_op_step}", flush=True)

            finally:
                t_rev = time.perf_counter()
                revert_overrides(global_states, mod_states, batch_objects)
                bpy.context.view_layer.update()
                print(f"  │    │    ├─ Reverted permutation overrides: {time.perf_counter() - t_rev:.4f}s")

            # Crucial garbage collection for headless loop
            t_purge = time.perf_counter()
            bpy.ops.outliner.orphans_purge(do_local_ids=True, do_linked_ids=True, do_recursive=True)
            print(f"  │    │    ├─ RAM Purge: {time.perf_counter() - t_purge:.4f}s")

        print(f"  │    => Batch Total Time: {time.perf_counter() - t_batch_start:.4f}s\n")
        batch_counter += 1

    print(f"\n=== HEADLESS EXPORT COMPLETE: {time.perf_counter() - total_time_start:.4f}s Total ===\n")
    print("BATCH_STL_DONE", flush=True)
    sys.exit(0)


if __name__ == "__main__":
    import sys
    if "--batch-stl-headless" in sys.argv:
        if not hasattr(bpy.types.Scene, "batch_stl_root_dir"):
            register()

        idx = sys.argv.index("--batch-stl-headless")
        p_index = int(sys.argv[idx + 1])
        run_headless_export(p_index)
    else:
        if not hasattr(bpy.types.Scene, "batch_stl_root_dir"):
            register()
