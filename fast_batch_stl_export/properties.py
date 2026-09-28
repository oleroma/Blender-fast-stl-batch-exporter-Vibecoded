"""
Properties Module
Contains custom Blender data structures extending PropertyGroup, defining
how the addon configuration data is saved within the blend file.
"""
import bpy

def infer_input_type(group_ptr, node_name, input_name):
    # Cross-references the graph to dynamically update the override_type enum
    # based on the underlying socket type the user selected.
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
        # Check target node inputs
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
    # Triggered automatically by Blender when a user changes the name string property
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
            # Sync to actual type
            self.override_type = infer_input_type(found_ng.group_ptr, found_node.name, self.name)
            # Reset sweep settings as they might be incompatible with the new type
            for v in self.values: v.use_sweep = False
    except Exception: pass

def search_target_node_cb(self, context, edit_text):
    # Callback providing an autocomplete dropdown list of available nodes in the target Group
    if edit_text == self.name: edit_text = ""
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

    return res

def search_menu_items_cb(self, context, edit_text):
    # Extrapolates enum identifier options from `MENU_SWITCH` nodes deep inside the network tree
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
    # Defines a single console string line output
    text: bpy.props.StringProperty()

# [FIX] Added 'if context is not None' check to prevent infinite recursion loop
# if these properties trigger cascading updates.
def update_val_use_tag(self, context):
    if not self.use_tag and not self.use_dir:
        self.use_dir = True

def update_val_use_dir(self, context):
    if not self.use_dir and not self.use_tag:
        self.use_tag = True

# Hierarchical Property structures mapping 1:1 to UI Data Flow
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

    # Runtime Progress Tracking Variables
    is_exporting: bpy.props.BoolProperty(default=False)
    cancel_export: bpy.props.BoolProperty(default=False)
    export_progress: bpy.props.FloatProperty(name="Progress", default=0.0, min=0.0, max=1.0)
    export_status: bpy.props.StringProperty(default="")
    console_logs: bpy.props.CollectionProperty(type=BatchSTLLogLine)
    console_index: bpy.props.IntProperty(default=0)

classes = (
    BatchSTLLogLine,
    BatchSTLValue,
    BatchSTLInput,
    BatchSTLNode,
    BatchSTLNodeGroup,
    BatchSTLExcludedObject,
    BatchSTLCollection,
    BatchSTLExportPreset,
)
