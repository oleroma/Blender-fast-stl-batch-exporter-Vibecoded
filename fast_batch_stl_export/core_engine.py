import itertools
import bpy

# --- GRAPH EVALUATION & OVERRIDE MATH ---
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
                start, step, count = float(parts[0]), float(parts[1]), int(parts[2])
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
            if param_key not in grouped_inputs: grouped_inputs[param_key] = []
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
    return [MockOverride(t, gp, nn, i) for (t, gp, nn), i in grouped.items()]

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
                                    # Cache the sockets before the link is destroyed
                                    to_socket = link.to_socket
                                    from_socket = link.from_socket

                                    global_states.append(('SOCKET', to_socket, to_socket.default_value, from_socket, parent_tree))
                                    if not dry_run:
                                        parent_tree.links.remove(link)
                                        # Use the cached socket reference to set the value
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
