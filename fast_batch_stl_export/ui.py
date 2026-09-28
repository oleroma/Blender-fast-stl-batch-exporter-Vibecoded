"""
User Interface Module
Registers UIList and Panel classes for the 3D viewport side-bar.
"""
import os
import json
import bpy

from .core import (
    get_active_preset, get_active_collection,
    generate_override_combinations, get_flat_overrides,
    build_tree_dict
)

# ==============================================================================
# === UI LISTS ===
# ==============================================================================

class BATCH_STL_UL_presets(bpy.types.UIList):
    def draw_item(self, context, layout, data, item, icon, active_data, active_propname, index):
        row = layout.row(align=True)
        row.prop(item, "name", text="", emboss=False)
        row.prop(item, "preset_prefix", text="", emboss=False, icon='FILE_FOLDER')

        if item.is_exporting:
            row.prop(item, "export_progress", text=item.export_status, slider=True)
            cancel_op = row.operator("batch_stl.cancel_export", text="", icon='CANCEL')
            cancel_op.preset_index = index
        else:
            op = row.operator("export_scene.batch_stl_multi", text="", icon='EXPORT')
            op.preset_index = index

class BATCH_STL_UL_collections(bpy.types.UIList):
    def draw_item(self, context, layout, data, item, icon, active_data, active_propname, index):
        row = layout.row(align=True)
        row.prop(item, "use_filter", text="", icon='FILTER')
        row.prop(item, "collection_ptr", text="")
        row.separator(factor=0.5)

        sub_row = row.row(align=True)
        sub_row.prop(item, "use_tag", text="", icon='BOOKMARKS')
        sub_row.separator(factor=0.5)

        tag_row = sub_row.row(align=True)
        tag_row.prop(item, "tag", text="", emboss=False)
        row.prop(item, "sub_path", text="", emboss=False, icon='FILE_FOLDER')

class BATCH_STL_UL_console_logs(bpy.types.UIList):
    def draw_item(self, context, layout, data, item, icon, active_data, active_propname, index):
        layout.label(text=item.text)

# ==============================================================================
# === DRAW HELPERS ===
# ==============================================================================

def draw_inline_controls(layout, operator_id, use_clipboard=False):
    # Generates standard macro list controls for appending and re-arranging items
    row = layout.row(align=True)
    row.operator(operator_id, icon='ADD', text="").action = 'ADD'
    row.operator(operator_id, icon='REMOVE', text="").action = 'REMOVE'
    row.operator(operator_id, icon='TRIA_UP', text="").action = 'UP'
    row.operator(operator_id, icon='TRIA_DOWN', text="").action = 'DOWN'
    if use_clipboard:
        row.operator(operator_id, icon='COPYDOWN', text="").action = 'COPY'
        row.operator(operator_id, icon='PASTEDOWN', text="").action = 'PASTE'


def draw_overrides_table(layout, scene, nodegroups, is_pinned, is_open_prop, title_text):
    # Recursively builds the dense table interface mapping Nodes -> Inputs -> Sweeps
    box = layout.box()

    header_row = box.row()
    is_open = getattr(scene, is_open_prop)
    icon_open = 'TRIA_DOWN' if is_open else 'TRIA_RIGHT'
    header_row.prop(scene, is_open_prop, text="", icon=icon_open, emboss=False)
    header_row.label(text=title_text, icon='PINNED' if is_pinned else 'UNPINNED')

    op_row = header_row.row(align=True)
    op = op_row.operator("batch_stl.table_action", text="", icon='ADD')
    op.action = 'ADD_GROUP'; op.is_pinned = is_pinned
    op = op_row.operator("batch_stl.table_action", text="", icon='PASTEDOWN')
    op.action = 'PASTE_GROUP'; op.is_pinned = is_pinned

    if not is_open:
        return

    if len(nodegroups) == 0:
        box.label(text="No overrides defined.")
        return

    for ng_idx, ng in enumerate(nodegroups):
        ng_box = box.box()
        ng_layout = ng_box.column()
        ng_row = ng_layout.row(align=True)

        op = ng_row.operator("batch_stl.table_action", text="", icon='ADD'); op.action = 'ADD_NODE'; op.is_pinned = is_pinned; op.ng_idx = ng_idx
        ng_row.prop(ng, "group_ptr", text="")

        if len(nodegroups) > 1:
            op = ng_row.operator("batch_stl.table_action", text="", icon='TRIA_UP'); op.action = 'MOVE_GROUP_UP'; op.is_pinned = is_pinned; op.ng_idx = ng_idx
            op = ng_row.operator("batch_stl.table_action", text="", icon='TRIA_DOWN'); op.action = 'MOVE_GROUP_DOWN'; op.is_pinned = is_pinned; op.ng_idx = ng_idx

        op = ng_row.operator("batch_stl.table_action", text="", icon='PINNED' if is_pinned else 'UNPINNED'); op.action = 'UNPIN_GROUP' if is_pinned else 'PIN_GROUP'; op.is_pinned = is_pinned; op.ng_idx = ng_idx
        op = ng_row.operator("batch_stl.table_action", text="", icon='COPYDOWN'); op.action = 'COPY_GROUP'; op.is_pinned = is_pinned; op.ng_idx = ng_idx
        op = ng_row.operator("batch_stl.table_action", text="", icon='TRASH'); op.action = 'DEL_GROUP'; op.is_pinned = is_pinned; op.ng_idx = ng_idx

        if not ng.nodes:
            continue

        n_split = ng_layout.split(factor=0.03)
        n_split.column()
        nodes_col = n_split.column()
        nodes_box = nodes_col.box() if len(ng.nodes) > 1 else nodes_col
        nodes_layout = nodes_box.column()

        for n_idx, node in enumerate(ng.nodes):
            node_container = nodes_layout.box()
            node_layout = node_container.column()

            n_row = node_layout.row(align=True)
            op = n_row.operator("batch_stl.table_action", text="", icon='ADD'); op.action = 'ADD_INPUT'; op.is_pinned = is_pinned; op.ng_idx = ng_idx; op.n_idx = n_idx
            n_row.prop(node, "name", text="", icon='NODETREE')

            if len(ng.nodes) > 1:
                op = n_row.operator("batch_stl.table_action", text="", icon='TRIA_UP'); op.action = 'MOVE_NODE_UP'; op.is_pinned = is_pinned; op.ng_idx = ng_idx; op.n_idx = n_idx
                op = n_row.operator("batch_stl.table_action", text="", icon='TRIA_DOWN'); op.action = 'MOVE_NODE_DOWN'; op.is_pinned = is_pinned; op.ng_idx = ng_idx; op.n_idx = n_idx
                op = n_row.operator("batch_stl.table_action", text="", icon='TRASH'); op.action = 'DEL_NODE'; op.is_pinned = is_pinned; op.ng_idx = ng_idx; op.n_idx = n_idx

            if not node.inputs:
                continue

            i_split = node_layout.split(factor=0.03)
            i_split.column()
            inputs_col = i_split.column()
            inputs_box = inputs_col.box()
            inputs_layout = inputs_box.column()

            for i_idx, inp in enumerate(node.inputs):
                input_layout = inputs_layout.column()

                if not inp.values:
                    i_row = input_layout.row(align=True)
                    s_main = i_row.split(factor=0.35, align=False)
                    c_inp = s_main.row(align=True)

                    op = c_inp.operator("batch_stl.table_action", text="", icon='ADD')
                    op.action = 'VALUE_ACTION'; op.is_pinned = is_pinned; op.ng_idx = ng_idx; op.n_idx = n_idx; op.i_idx = i_idx; op.v_idx = -1

                    is_mod = not node.name or node.name == "<Modifier Interface>"
                    if is_mod and ng.group_ptr and hasattr(ng.group_ptr, "interface"):
                        c_inp.prop_search(inp, "name", ng.group_ptr.interface, "items_tree", text="")
                    elif not is_mod and ng.group_ptr and node.name:
                        target_n = ng.group_ptr.nodes.get(node.name.split(" [")[0].strip())
                        if target_n: c_inp.prop_search(inp, "name", target_n, "inputs", text="")
                        else: c_inp.prop(inp, "name", text="")
                    else:
                        c_inp.prop(inp, "name", text="")

                    s_val = s_main.split(factor=0.5, align=False)
                    c_val = s_val.row(align=True)
                    c_dir = s_val.row(align=True)

                    if len(node.inputs) > 1:
                        op = c_dir.operator("batch_stl.table_action", text="", icon='TRIA_UP'); op.action = 'MOVE_INPUT_UP'; op.is_pinned = is_pinned; op.ng_idx = ng_idx; op.n_idx = n_idx; op.i_idx = i_idx
                        op = c_dir.operator("batch_stl.table_action", text="", icon='TRIA_DOWN'); op.action = 'MOVE_INPUT_DOWN'; op.is_pinned = is_pinned; op.ng_idx = ng_idx; op.n_idx = n_idx; op.i_idx = i_idx
                    op = c_dir.operator("batch_stl.table_action", text="", icon='TRASH'); op.action = 'DEL_INPUT'; op.is_pinned = is_pinned; op.ng_idx = ng_idx; op.n_idx = n_idx; op.i_idx = i_idx

                    continue

                for v_idx, val in enumerate(inp.values):
                    i_first = (v_idx == 0)
                    i_row = input_layout.row(align=True)

                    s_main = i_row.split(factor=0.35, align=False)
                    c_inp = s_main.row(align=True)

                    if i_first:
                        if getattr(val, "use_sweep", False):
                            op = c_inp.operator("batch_stl.table_action", text="", icon='FILE_REFRESH', depress=True)
                        else:
                            op = c_inp.operator("batch_stl.table_action", text="", icon='ADD')
                        op.action = 'VALUE_ACTION'; op.is_pinned = is_pinned; op.ng_idx = ng_idx; op.n_idx = n_idx; op.i_idx = i_idx; op.v_idx = 0

                        is_mod = not node.name or node.name == "<Modifier Interface>"
                        if is_mod and ng.group_ptr and hasattr(ng.group_ptr, "interface"):
                            c_inp.prop_search(inp, "name", ng.group_ptr.interface, "items_tree", text="")
                        elif not is_mod and ng.group_ptr and node.name:
                            target_n = ng.group_ptr.nodes.get(node.name.split(" [")[0].strip())
                            if target_n: c_inp.prop_search(inp, "name", target_n, "inputs", text="")
                            else: c_inp.prop(inp, "name", text="")
                        else:
                            c_inp.prop(inp, "name", text="")
                    else:
                        c_inp.alignment = 'RIGHT'

                    s_val = s_main.split(factor=0.5, align=False)
                    c_val = s_val.row(align=True)

                    if getattr(val, "use_sweep", False):
                        if inp.override_type in ['INT', 'FLOAT', 'STRING']:
                            c_val.prop(val, "sweep_range", text="")
                        elif inp.override_type == 'BOOLEAN':
                            sub = c_val.row(align=True)
                            sub.active = False
                            sub.operator("wm.context_set_string", text="True & False")
                        elif inp.override_type == 'MENU':
                            sub = c_val.row(align=True)
                            sub.active = False
                            sub.operator("wm.context_set_string", text="All values")
                    else:
                        if inp.override_type == 'BOOLEAN':
                            c_val.prop(val, "value_bool", text="True" if val.value_bool else "False", toggle=True)
                        elif inp.override_type == 'INT':
                            c_val.prop(val, "value_int", text="")
                        elif inp.override_type == 'FLOAT':
                            c_val.prop(val, "value_float", text="")
                        elif inp.override_type == 'STRING':
                            c_val.prop(val, "value_string", text="")
                        elif inp.override_type == 'MENU':
                            c_val.prop(val, "value_menu", text="")

                    c_dir = s_val.row(align=True)
                    c_dir.prop(val, "use_dir", text="", icon='FILE_FOLDER')
                    c_dir.prop(val, "use_tag", text="", icon='BOOKMARKS')
                    c_dir.prop(val, "tag", text="")

                    if i_first:
                        if len(node.inputs) > 1:
                            op = c_dir.operator("batch_stl.table_action", text="", icon='TRIA_UP'); op.action = 'MOVE_INPUT_UP'; op.is_pinned = is_pinned; op.ng_idx = ng_idx; op.n_idx = n_idx; op.i_idx = i_idx
                            op = c_dir.operator("batch_stl.table_action", text="", icon='TRIA_DOWN'); op.action = 'MOVE_INPUT_DOWN'; op.is_pinned = is_pinned; op.ng_idx = ng_idx; op.n_idx = n_idx; op.i_idx = i_idx

                        op = c_dir.operator("batch_stl.table_action", text="", icon='TRASH'); op.action = 'DEL_VALUE_OR_INPUT'; op.is_pinned = is_pinned; op.ng_idx = ng_idx; op.n_idx = n_idx; op.i_idx = i_idx; op.v_idx = 0
                    else:
                        if len(inp.values) > 1:
                            op = c_dir.operator("batch_stl.table_action", text="", icon='TRIA_UP'); op.action = 'MOVE_VALUE_UP'; op.is_pinned = is_pinned; op.ng_idx = ng_idx; op.n_idx = n_idx; op.i_idx = i_idx; op.v_idx = v_idx
                            op = c_dir.operator("batch_stl.table_action", text="", icon='TRIA_DOWN'); op.action = 'MOVE_VALUE_DOWN'; op.is_pinned = is_pinned; op.ng_idx = ng_idx; op.n_idx = n_idx; op.i_idx = i_idx; op.v_idx = v_idx
                        op = c_dir.operator("batch_stl.table_action", text="", icon='TRASH'); op.action = 'DEL_VALUE'; op.is_pinned = is_pinned; op.ng_idx = ng_idx; op.n_idx = n_idx; op.i_idx = i_idx; op.v_idx = v_idx


def draw_tree_dict(layout, tree_node, current_path="", toggled_list=None, duplicates=None, actual_path=""):
    # Recursive UI construction of the directory tree preview
    if toggled_list is None:
        try:
            toggled_list = json.loads(bpy.context.scene.batch_stl_collapsed_dirs)
        except Exception:
            toggled_list = []
    if duplicates is None:
        duplicates = set()

    dirs = [k for k in tree_node.keys() if k != '_files']
    files = tree_node.get('_files', [])

    for k in dirs:
        dir_path = current_path + "/" + k
        next_actual = os.path.normpath(os.path.join(actual_path, k)) if actual_path else os.path.normpath(k)

        default_expanded = (k == dirs[-1])
        is_expanded = default_expanded
        if dir_path in toggled_list:
            is_expanded = not default_expanded

        is_collapsed = not is_expanded

        split = layout.split(factor=0.005)
        split.column()
        col = split.column()

        box = col.box()
        row = box.row()

        icon = 'TRIA_RIGHT' if is_collapsed else 'TRIA_DOWN'
        op = row.operator("batch_stl.toggle_dir_tree", text="", icon=icon, emboss=False)
        op.dir_path = dir_path

        row.scale_y = 0.4
        row.label(text=str(k))
        if not is_collapsed and isinstance(tree_node[k], dict):
            draw_tree_dict(box, tree_node[k], dir_path, toggled_list, duplicates, next_actual)

    for f in files:
        split = layout.split(factor=0.025)
        split.column()
        col = split.column()

        row = col.row()
        row.scale_y = 0.4

        f_path = os.path.join(actual_path, f) if actual_path else f
        if f_path in duplicates:
            row.alert = True

        row.label(text=str(f))


# ==============================================================================
# === MAIN 3D VIEWPORT PANEL ===
# ==============================================================================

class VIEW3D_PT_batch_export_stl_multi(bpy.types.Panel):
    bl_space_type = "VIEW_3D"
    bl_region_type = "UI"
    bl_category = "Export"
    bl_label = "Fast Batch STL Export"

    def draw(self, context):
        layout = self.layout
        scene = context.scene

        dir_row = layout.row(align=True)
        dir_row.operator("batch_stl.import_presets_json", text="", icon='IMPORT')
        dir_row.operator("batch_stl.export_presets_json", text="", icon='EXPORT')
        dir_row.prop(scene, "batch_stl_show_console", text="", icon='CONSOLE', toggle=True)
        dir_row.prop(scene, "batch_stl_root_dir")

        active_preset = get_active_preset(scene)

        if scene.batch_stl_show_console:
            c_box = layout.box()
            c_box.label(text="Global Export Console Log", icon='CONSOLE')
            if active_preset:
                c_box.template_list("BATCH_STL_UL_console_logs", "", active_preset, "console_logs", active_preset, "console_index", rows=6)
                c_box.operator("batch_stl.clear_console", text="Clear Log", icon='TRASH')
            else:
                c_box.label(text="Select a preset to view logs.")

        layout.separator()

        p_box = layout.box()
        p_header = p_box.row()
        icon = 'TRIA_DOWN' if scene.batch_stl_ui_presets else 'TRIA_RIGHT'
        p_header.prop(scene, "batch_stl_ui_presets", text="", icon=icon, emboss=False)
        p_header.label(text="Presets", icon='PRESET')

        if active_preset: p_header.label(text=f"Last: {active_preset.last_export_time:.2f}s", icon='TIME')
        draw_inline_controls(p_header, "batch_stl.preset_actions", use_clipboard=True)

        if scene.batch_stl_ui_presets:
            p_box.template_list("BATCH_STL_UL_presets", "", scene, "batch_stl_presets", scene, "batch_stl_preset_index", rows=3)

        if not active_preset:
            layout.separator()
            layout.prop(scene, "batch_stl_verbose_console", toggle=True, icon='CONSOLE')
            return

        total_collections = len(active_preset.collections)
        total_objects = 0
        total_preset_combos = 0

        for c in active_preset.collections:
            c_combos = len(generate_override_combinations(get_flat_overrides(active_preset.nodegroups) + get_flat_overrides(c.nodegroups)))
            total_preset_combos += c_combos
            obj_count = 0
            if c.collection_ptr:
                excluded_names = {e.name for e in c.excluded_objects} if getattr(c, "use_filter", False) else set()
                for obj in c.collection_ptr.all_objects:
                    if obj.type in {"MESH", "CURVE", "SURFACE", "META", "FONT"} and not obj.hide_get() and not obj.hide_viewport:
                        if obj.name not in excluded_names: obj_count += 1
            total_objects += (obj_count * c_combos)

        layout.separator(factor=0.5)
        m_box = layout.box()
        m_header = m_box.row()
        icon_m = 'TRIA_DOWN' if scene.batch_stl_ui_collections else 'TRIA_RIGHT'
        m_header.prop(scene, "batch_stl_ui_collections", text="", icon=icon_m, emboss=False)

        m_title = f"{active_preset.name} | {total_collections} collections | {total_preset_combos} combos | {total_objects} objects total"
        m_header.label(text=m_title, icon='OUTLINER_COLLECTION')
        draw_inline_controls(m_header, "batch_stl.collection_actions", use_clipboard=True)

        if scene.batch_stl_ui_collections:
            m_box.template_list("BATCH_STL_UL_collections", "", active_preset, "collections", active_preset, "collection_index", rows=5)
            active_col = get_active_collection(active_preset)
            if active_col and active_col.collection_ptr:
                m_box.separator()
                if active_col.use_filter:
                    filter_box = m_box.box()
                    f_header = filter_box.row()
                    icon_f = 'TRIA_DOWN' if scene.batch_stl_ui_exclude else 'TRIA_RIGHT'
                    f_header.prop(scene, "batch_stl_ui_exclude", text="", icon=icon_f, emboss=False)
                    f_header.label(text="Exclude Objects:", icon='FILTER')

                    if scene.batch_stl_ui_exclude:
                        col = filter_box.column(align=True)
                        for obj in active_col.collection_ptr.all_objects:
                            if obj.type not in {"MESH", "CURVE", "SURFACE", "META", "FONT"}: continue
                            is_excl = any(e.name == obj.name for e in active_col.excluded_objects)
                            icon_btn = 'CHECKBOX_DEHLT' if is_excl else 'CHECKBOX_HLT'
                            op = col.operator("batch_stl.toggle_exclusion", text=obj.name, icon=icon_btn, depress=not is_excl)
                            op.object_name = obj.name

        layout.separator()

        if get_active_collection(active_preset):
            active_col = get_active_collection(active_preset)
            all_ovrs = get_flat_overrides(active_preset.nodegroups) + get_flat_overrides(active_col.nodegroups)
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
            active_obj_count = 0
            if active_col.collection_ptr:
                excluded_names = {e.name for e in active_col.excluded_objects} if getattr(active_col, "use_filter", False) else set()
                for obj in active_col.collection_ptr.all_objects:
                    if obj.type in {"MESH", "CURVE", "SURFACE", "META", "FONT"} and not obj.hide_get() and not obj.hide_viewport:
                        if obj.name not in excluded_names: active_obj_count += 1

            mapping_total_objects = active_obj_count * num_combos
            c_name = active_col.collection_ptr.name if active_col.collection_ptr else "Unassigned"
            if active_col.tag: c_name += f" [{active_col.tag}]"

            metric_str = f"{c_name} | {num_targets} targets | {total_inputs} inputs | {num_combos} combos | {mapping_total_objects} objects"
            header = layout.row()
            header.label(text=metric_str, icon='MODIFIER')
            layout.separator()

            draw_overrides_table(layout, scene, active_preset.nodegroups, True, "batch_stl_ui_global_ovr", "Global Pinned Overrides (Shared)")
            layout.separator(factor=0.5)

            draw_overrides_table(layout, scene, active_col.nodegroups, False, "batch_stl_ui_local_ovr", "Local Overrides (Specific to Collection)")
            layout.separator(factor=0.5)

            tip_box = layout.box()
            tip_header = tip_box.row()
            icon_tip = 'TRIA_DOWN' if scene.batch_stl_ui_tips else 'TRIA_RIGHT'
            tip_header.prop(scene, "batch_stl_ui_tips", text="", icon=icon_tip, emboss=False)
            tip_header.label(text="OVERRIDE INFO", icon='INFO')

            if scene.batch_stl_ui_tips:
                col = tip_box.column()
                col.label(text="Hierarchy: NodeGroup > Node > Input > Value.", icon='BLANK1')
                col.label(text="For modifier targets, leave Node blank or set as <Modifier Interface>", icon='BLANK1')
                col.separator()

                col.label(text="Sweep Mode (Shift-Click '+' button to toggle):", icon='FILE_REFRESH')
                col.label(text="  • Floats/Ints: Define start, step, and count", icon='BLANK1')
                col.label(text="  • Menus/Bools: Auto-iterates all values", icon='BLANK1')
                col.label(text="  • Shift-Click when active to populate all sweep values", icon='BLANK1')
                col.separator()

                col.label(text="Export Tools (Per Value):", icon='BLANK1')
                col.label(text="  • Folder Icon: Save this value's exports into a subfolder", icon='FILE_FOLDER')
                col.label(text="  • Bookmark Icon: Append/Prepend a tag to filename", icon='BOOKMARKS')

                col.label(text="Tag Formatting:", icon='BLANK1')
                col.label(text="  • [ tag ] replaces input value, [ _tag ] appends, [ tag_ ] prepends", icon='BLANK1')

        layout.separator()
        t_box = layout.box()
        t_header = t_box.row(align=True)
        icon_t = 'TRIA_DOWN' if scene.batch_stl_show_tree else 'TRIA_RIGHT'
        t_header.prop(scene, "batch_stl_show_tree", text="", icon=icon_t, emboss=False)
        t_header.label(text="Export Structure & Files", icon='OUTLINER_OB_EMPTY')

        if scene.batch_stl_show_tree and active_preset:
            tree_dict, duplicates = build_tree_dict(scene, active_preset)
            if duplicates:
                warn_box = t_box.box()
                warn_row = warn_box.row()
                warn_row.label(text=f"WARNING: {len(duplicates)} naming collisions detected! Files will be overwritten.", icon='ERROR')

            col = t_box.column(align=True)
            draw_tree_dict(col, tree_dict, duplicates=duplicates)

        layout.separator()
        layout.prop(scene, "batch_stl_verbose_console", toggle=True, icon='CONSOLE')

classes = (
    BATCH_STL_UL_presets,
    BATCH_STL_UL_collections,
    BATCH_STL_UL_console_logs,
    VIEW3D_PT_batch_export_stl_multi,
)
