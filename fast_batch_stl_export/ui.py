import bpy
from .properties import get_active_preset, get_active_mapping
from .core_engine import generate_override_combinations

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

            metric_str = f"{c_name}"
            num_combos = len(generate_override_combinations(all_ovrs))
            if num_combos > 1: metric_str += f" | {num_combos} combos"
            metric_str += f" | {len(unique_targets)} targets | {total_inputs} inputs"

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

classes = (BATCH_STL_UL_presets, BATCH_STL_UL_items, VIEW3D_PT_batch_export_stl_multi)
