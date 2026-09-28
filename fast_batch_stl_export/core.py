"""
Core Logic Module
Handles Dependency Graph mapping, node permutations, override combinations,
and directory tree generation for the export preview.
"""
import os
import itertools
import bpy

# ==============================================================================
# === GRAPH EVALUATION & OVERRIDES ===
# ==============================================================================

def is_collection_excluded(context, target_collection):
    # Recursively searches the view layer hierarchy to check if a collection is excluded (unchecked)
    # This prevents the exporter from processing hidden/disabled collections.
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
    # Retrieves the internal identifier (e.g. 'Socket_2') for a given UI name.
    # Checks for the `interface` attribute introduced in Blender 4.0+
    if hasattr(node_group, "interface"):
        for item in node_group.interface.items_tree:
            if getattr(item, "item_type", "") == 'SOCKET' and item.name == socket_name:
                return item.identifier
    else:
        # Fallback for Blender 3.x legacy node group inputs
        for inp in node_group.inputs:
            if inp.name == socket_name:
                return inp.identifier
    return None

def get_modifier_socket_default(node_group, socket_name):
    # Fetches the default fallback value of a modifier socket to reset it later
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
    # Reads the current value of a Geometry Nodes modifier input.
    # [FIX] Wrapped in a more specific try/except block to catch exact property lookup errors.
    try:
        if mod.is_property_set(ident):
            return mod[ident], True
    except (KeyError, TypeError, Exception):
        pass

    # Fallback to RNA properties struct
    if hasattr(mod, "properties") and hasattr(mod.properties, "inputs"):
        prop_input = getattr(mod.properties.inputs, ident, None)
        if prop_input is not None and hasattr(prop_input, "value"):
            return prop_input.value, True

    return None, False

def set_modifier_input(mod, ident, value):
    # Assigns a computed permutation value to the live modifier property
    try:
        mod[ident] = value
        return
    except (TypeError, KeyError, Exception):
        pass

    if hasattr(mod, "properties") and hasattr(mod.properties, "inputs"):
        prop_input = getattr(mod.properties.inputs, ident, None)
        if prop_input is not None and hasattr(prop_input, "value"):
            prop_input.value = value

def unset_modifier_input(mod, ident, default_val):
    # Reverts the modifier property back to its unset state or default value
    try:
        mod.property_unset(ident)
        return
    except (AttributeError, KeyError, Exception):
        pass

    if hasattr(mod, "properties") and hasattr(mod.properties, "inputs"):
        prop_input = getattr(mod.properties.inputs, ident, None)
        if prop_input is not None and hasattr(prop_input, "value") and default_val is not None:
            prop_input.value = default_val

def get_input_value(inp):
    # Casts the stored property group value to a native python type based on override_type
    if inp.override_type == 'BOOLEAN': return inp.value_bool
    elif inp.override_type == 'INT': return inp.value_int
    elif inp.override_type == 'FLOAT': return inp.value_float
    elif inp.override_type == 'STRING': return inp.value_string
    elif inp.override_type == 'MENU': return inp.value_menu
    return None

def get_override_signature(overrides):
    # Generates a hashable tuple representation of all current overrides.
    # Used for grouping batches of identical graph configurations to avoid redundant graph updates.
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

# ==============================================================================
# === PERMUTATION ENGINE (DATA ADAPTERS) ===
# ==============================================================================
# These classes isolate the live PropertyGroups from the abstract logic engine,
# preventing direct write-backs during calculation.

class TempMockInput:
    def __init__(self, name, o_type, val_obj):
        self.input_name = name
        self.override_type = o_type
        self.use_tag = val_obj.use_tag
        self.tag = val_obj.tag
        self.use_dir = val_obj.use_dir
        self.use_sweep = val_obj.use_sweep
        self.sweep_range = val_obj.sweep_range
        self._val_obj = val_obj
    @property
    def value_bool(self): return self._val_obj.value_bool
    @property
    def value_int(self): return self._val_obj.value_int
    @property
    def value_float(self): return self._val_obj.value_float
    @property
    def value_string(self): return self._val_obj.value_string
    @property
    def value_menu(self): return self._val_obj.value_menu

class TempMockOverride:
    def __init__(self, target, ptr, node_name, temp_inputs):
        self.override_target = target
        self.parent_group_ptr = ptr
        self.node_name = node_name
        self.inputs = temp_inputs

def get_flat_overrides(nodegroups):
    # Flattens the hierarchical NodeGroup -> Node -> Input -> Value structure into a single linear list
    overrides = []
    for ng in nodegroups:
        for node in ng.nodes:
            # Determine if this targets the top-level modifier interface or an internal node
            target = 'MODIFIER' if not node.name or node.name == "<Modifier Interface>" else 'NODE'
            temp_inputs = []
            for inp in node.inputs:
                for val in inp.values:
                    temp_inputs.append(TempMockInput(inp.name, inp.override_type, val))
            if temp_inputs:
                overrides.append(TempMockOverride(target, ng.group_ptr, node.name, temp_inputs))
    return overrides

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
    # Parses procedural string definitions (e.g., "0 10 5") into an array of values
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
                # Format: [Start] [Step Size] [Count]
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
        # Introspects the node tree to find valid enum identifiers to automatically sweep through all dropdown options
        items = []
        if ovr.parent_group_ptr:
            if ovr.override_target == 'MODIFIER':
                for node in ovr.parent_group_ptr.nodes:
                    if node.type == 'MENU_SWITCH' and hasattr(node, 'enum_items'):
                        for sock in node.inputs:
                            for link in sock.links:
                                if link.from_node.type == 'GROUP_INPUT' and link.from_socket.name == inp.input_name:
                                    items = [getattr(item, 'identifier', getattr(item, 'name', '')) for item in node.enum_items]
                                    break
                            if items: break
                    if items: break
            elif ovr.override_target == 'NODE' and ovr.node_name:
                n_name = ovr.node_name.split(" [")[0].strip()
                node = ovr.parent_group_ptr.nodes.get(n_name)
                if node:
                    if node.type == 'MENU_SWITCH' and hasattr(node, 'enum_items'):
                        items = [getattr(item, 'identifier', getattr(item, 'name', '')) for item in node.enum_items]
                    elif node.type == 'GROUP' and hasattr(node, 'node_tree') and node.node_tree:
                        for inner_node in node.node_tree.nodes:
                            if inner_node.type == 'MENU_SWITCH' and hasattr(inner_node, 'enum_items'):
                                for sock in inner_node.inputs:
                                    for link in sock.links:
                                        if link.from_node.type == 'GROUP_INPUT' and link.from_socket.name == inp.input_name:
                                            items = [getattr(item, 'identifier', getattr(item, 'name', '')) for item in inner_node.enum_items]
                                            break
                                    if items: break
                            if items: break
        return items if items else [""]
    return []

def generate_override_combinations(overrides):
    # Core mathematical Cartesian product generator.
    # Resolves all combinations of permutations requested by the user.
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
        has_sweep = any(getattr(inp, "use_sweep", False) for ovr, inp in pairs)

        for ovr, inp in pairs:
            if getattr(inp, "use_sweep", False):
                sweep_vals = parse_sweep_values(ovr, inp)
                for val in sweep_vals:
                    mock_inp = MockInput(inp, val)
                    if val not in value_groups: value_groups[val] = []
                    value_groups[val].append((ovr, mock_inp))
            elif not has_sweep:
                val = get_input_value(inp)
                mock_inp = MockInput(inp, val)
                if val not in value_groups: value_groups[val] = []
                value_groups[val].append((ovr, mock_inp))

        if value_groups:
            pools.append(list(value_groups.values()))

    if not pools: return [[]]

    # Calculate Cartesian product using itertools for efficiency
    combinations = list(itertools.product(*pools))

    flattened = []
    for combo in combinations:
        flat_combo = []
        for variation in combo: flat_combo.extend(variation)
        flattened.append(flat_combo)
    return flattened

def reconstruct_overrides_for_combo(combo):
    # Repackages a flattened combination back into override objects
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
    # Injects the computed values into the live Dependency Graph.
    # Returns global_states and mod_states tuples so it can be perfectly undone.
    global_states = []
    mod_states = []
    trees_to_update = set()

    for override in overrides:
        # Handling internal NODE overrides (globally impacts all instances of this node group)
        if override.override_target == 'NODE' and override.parent_group_ptr and override.node_name:
            parent_tree = override.parent_group_ptr
            n_name = override.node_name.split(" [")[0].strip()
            target_node = parent_tree.nodes.get(n_name)
            if not target_node: continue

            for inp in override.inputs:
                socket = target_node.inputs.get(inp.input_name)
                if not socket: continue
                # We record if this socket had a link, so we can sever it and re-link it later
                link_from = socket.links[0].from_socket if socket.is_linked else None
                global_states.append(('SOCKET', socket, socket.default_value, link_from, parent_tree))

                if not dry_run:
                    if socket.is_linked: parent_tree.links.remove(socket.links[0])
                    val = get_input_value(inp)
                    if val is not None: socket.default_value = val
            if not dry_run: trees_to_update.add(parent_tree)

        # Handling external MODIFIER overrides (locally impacts only specific objects)
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
                            if not dry_run:
                                set_modifier_input(mod, ident, val)

    if not dry_run:
        # Flag all affected graphs to recalculate
        for tree in trees_to_update: tree.update_tag()
        for obj in target_objects: obj.update_tag()

    return global_states, mod_states

def revert_overrides(global_states, mod_states, target_objects):
    # The crucial undo function. Restores the graph exactly as it was before permutation injection.
    for mod, ident, is_set, orig_val, default_val in mod_states:
        try:
            if is_set and orig_val is not None:
                set_modifier_input(mod, ident, orig_val)
            else:
                unset_modifier_input(mod, ident, default_val)
        # [FIX] Added catch for KeyError which happens if modifier deletes geometry dynamically
        except (ReferenceError, KeyError): pass

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
    # Helper to return the currently selected UI preset
    presets = scene.batch_stl_presets
    index = scene.batch_stl_preset_index
    if presets and 0 <= index < len(presets): return presets[index]
    return None

def get_active_collection(preset):
    # Helper to return the active collection mapping within a preset
    if preset and preset.collections and 0 <= preset.collection_index < len(preset.collections):
        return preset.collections[preset.collection_index]
    return None

def log_to_console(preset, text):
    # Custom logger writing directly to the addon's PropertyGroup UIList
    log = preset.console_logs.add()
    log.text = text
    preset.console_index = len(preset.console_logs) - 1
    # Cap list size to prevent memory leaks during massive batches
    if len(preset.console_logs) > 300:
        preset.console_logs.remove(0)
        preset.console_index = len(preset.console_logs) - 1

# ==============================================================================
# === TREE VISUALIZER LOGIC ===
# ==============================================================================

def build_tree_dict(scene, preset):
    # Analyzes all permutations and generates a virtual nested dictionary representing
    # the target filesystem output structure for the UI preview window.
    root_name = bpy.path.abspath(scene.batch_stl_root_dir) if scene.batch_stl_root_dir else "//"
    tree = {}
    all_filepaths = set()
    duplicates = set()

    current_root = tree
    if preset.preset_prefix:
        current_root[preset.preset_prefix] = {}
        current_root = current_root[preset.preset_prefix]

    for c in preset.collections:
        c_root = current_root
        c_root_path = []
        if c.sub_path:
            parts = c.sub_path.replace('\\', '/').split('/')
            for part in parts:
                if part:
                    if part not in c_root: c_root[part] = {}
                    c_root = c_root[part]
                    c_root_path.append(part)

        all_overrides = get_flat_overrides(preset.nodegroups) + get_flat_overrides(c.nodegroups)

        # Track frequency of permutations to determine suffix formatting
        freq_dict = {}
        for o in all_overrides:
            for i in o.inputs:
                key = (o.override_target, o.node_name, i.input_name)
                weight = 2 if getattr(i, "use_sweep", False) else 1
                freq_dict[key] = freq_dict.get(key, 0) + weight

        combinations = generate_override_combinations(all_overrides)
        valid_objs = []
        if c.collection_ptr:
            excluded_names = {e.name for e in c.excluded_objects} if getattr(c, "use_filter", False) else set()
            for obj in c.collection_ptr.all_objects:
                # Only operate on mesh-compatible datablocks
                if obj.type in {"MESH", "CURVE", "SURFACE", "META", "FONT"} and not obj.hide_get() and not obj.hide_viewport:
                    if obj.name not in excluded_names: valid_objs.append(obj)

        if not valid_objs: continue
        if not combinations: combinations = [[]]

        # Simulate file generation
        for combo in combinations:
            combo_root = c_root
            combo_suffix = ""
            combo_subpath = []
            processed_params = set()

            for ovr, inp in combo:
                param_key = (ovr.override_target, ovr.node_name, inp.input_name)
                if param_key not in processed_params:
                    val = get_input_value(inp)
                    val_str = str(val) if isinstance(val, (int, str)) else f"{val:g}" if isinstance(val, float) else str(val)

                    # Compute suffix tags based on user string configuration
                    if inp.tag:
                        if inp.tag.startswith("_"): naming_str = val_str + inp.tag
                        elif inp.tag.endswith("_"): naming_str = inp.tag + val_str
                        else: naming_str = inp.tag
                    else: naming_str = val_str

                    is_permutation = freq_dict.get(param_key, 0) > 1

                    if is_permutation and getattr(inp, "use_tag", False): combo_suffix += f"_{naming_str}"
                    if is_permutation and getattr(inp, "use_dir", False):
                        if naming_str not in combo_root: combo_root[naming_str] = {}
                        combo_root = combo_root[naming_str]
                        combo_subpath.append(naming_str)
                    processed_params.add(param_key)

            if '_files' not in combo_root: combo_root['_files'] = []
            base_tag = c.tag if getattr(c, "use_tag", False) and c.tag else ""
            final_tag = base_tag + combo_suffix

            full_dir_parts = [preset.preset_prefix] if preset.preset_prefix else []
            full_dir_parts.extend(c_root_path)
            full_dir_parts.extend(combo_subpath)
            dir_path_str = os.path.normpath(os.path.join(root_name, *full_dir_parts))

            # Store computed file names for collision detection
            for obj in valid_objs:
                filename = f"{bpy.path.clean_name(obj.name)}{final_tag}.stl"
                combo_root['_files'].append(filename)
                full_path = os.path.join(dir_path_str, filename)
                if full_path in all_filepaths: duplicates.add(full_path)
                else: all_filepaths.add(full_path)

    return {root_name: tree}, duplicates
