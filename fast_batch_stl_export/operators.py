"""
Operators Module
Defines interactive functions attached to UI elements, manipulating the PropertyGroups
or firing off execution commands (e.g., initiating export tasks).
"""
import json
import os
import time
import tempfile
import subprocess
import threading
import queue

import bpy
from bpy_extras.io_utils import ExportHelper, ImportHelper

from .state import clipboard
from .utils import (
    copy_preset_to_dict, paste_preset_from_dict,
    copy_collection_to_dict, paste_collection_from_dict,
    copy_ng_to_dict, paste_ng_from_dict
)
from .core import (
    get_active_preset, get_active_collection, log_to_console,
    is_collection_excluded, TempMockInput, TempMockOverride,
    parse_sweep_values
)
from .exporter import write_fast_binary_stl

# ==============================================================================
# === GLOBAL ADDON OPERATORS ===
# ==============================================================================

class BATCH_STL_OT_export_presets_json(bpy.types.Operator, ExportHelper):
    # Exports active presets to a human-readable JSON definition file for backups/sharing
    bl_idname = "batch_stl.export_presets_json"
    bl_label = "Export JSON"
    bl_description = "Export all presets to a JSON file"
    filename_ext = ".json"
    filter_glob: bpy.props.StringProperty(default="*.json", options={'HIDDEN'})

    def execute(self, context):
        with open(self.filepath, 'w') as f:
            json.dump([copy_preset_to_dict(p) for p in context.scene.batch_stl_presets], f, indent=4)
        return {'FINISHED'}

class BATCH_STL_OT_import_presets_json(bpy.types.Operator, ImportHelper):
    # Reads a JSON text file and generates Python PropertyGroup hierarchies inside the Blend scene
    bl_idname = "batch_stl.import_presets_json"
    bl_label = "Import JSON"
    bl_description = "Import presets from a JSON file"
    bl_options = {'REGISTER', 'UNDO'}
    filename_ext = ".json"
    filter_glob: bpy.props.StringProperty(default="*.json", options={'HIDDEN'})

    def execute(self, context):
        with open(self.filepath, 'r') as f: data = json.load(f)
        for p_data in data: paste_preset_from_dict(context.scene.batch_stl_presets.add(), p_data)
        return {'FINISHED'}

class BATCH_STL_OT_clear_console(bpy.types.Operator):
    bl_idname = "batch_stl.clear_console"
    bl_label = "Clear Console"
    bl_description = "Clear all console logs for the active preset"

    def execute(self, context):
        preset = get_active_preset(context.scene)
        if preset: preset.console_logs.clear()
        return {'FINISHED'}

class BATCH_STL_OT_preset_actions(bpy.types.Operator):
    # Multi-purpose operator driving UI list add/remove/up/down/copy/paste buttons
    bl_idname = "batch_stl.preset_actions"
    bl_label = "Preset Actions"
    bl_options = {'REGISTER', 'INTERNAL'}
    action: bpy.props.EnumProperty(items=(('ADD', "", ""), ('REMOVE', "", ""), ('UP', "", ""), ('DOWN', "", ""), ('COPY', "", ""), ('PASTE', "", "")))
    shift_pressed: bpy.props.BoolProperty(options={'HIDDEN', 'SKIP_SAVE'}, default=False)

    @classmethod
    def description(cls, context, properties):
        if properties.action == 'ADD': return "Create a new preset"
        elif properties.action == 'REMOVE': return "Remove the active preset"
        elif properties.action == 'UP': return "Move preset up (Shift-Click: Move to top)"
        elif properties.action == 'DOWN': return "Move preset down (Shift-Click: Move to bottom)"
        elif properties.action == 'COPY': return "Copy preset to clipboard"
        elif properties.action == 'PASTE': return "Paste preset from clipboard"
        return "Preset Actions"

    def invoke(self, context, event):
        self.shift_pressed = event.shift
        return self.execute(context)

    def execute(self, context):
        lst = context.scene.batch_stl_presets
        idx = context.scene.batch_stl_preset_index

        if self.action == 'ADD':
            lst.add()
            context.scene.batch_stl_preset_index = len(lst) - 1
        elif self.action == 'REMOVE' and lst:
            if not lst[idx].is_exporting:
                lst.remove(idx)
                context.scene.batch_stl_preset_index = max(0, idx - 1)
            else:
                self.report({'WARNING'}, "Cannot remove a preset while it is actively exporting.")
        elif self.action == 'UP' and idx > 0:
            lst.move(idx, 0 if self.shift_pressed else idx - 1)
            context.scene.batch_stl_preset_index = 0 if self.shift_pressed else idx - 1
        elif self.action == 'DOWN' and idx < len(lst) - 1:
            lst.move(idx, len(lst) - 1 if self.shift_pressed else idx + 1)
            context.scene.batch_stl_preset_index = len(lst) - 1 if self.shift_pressed else idx + 1
        elif self.action == 'COPY' and lst:
            clipboard["preset"] = copy_preset_to_dict(lst[idx])
        elif self.action == 'PASTE' and clipboard.get("preset"):
            paste_preset_from_dict(lst.add(), clipboard["preset"])
            context.scene.batch_stl_preset_index = len(lst) - 1

        if self.action != 'COPY': bpy.ops.ed.undo_push(message="Preset Action")
        return {'FINISHED'}

class BATCH_STL_OT_collection_actions(bpy.types.Operator):
    bl_idname = "batch_stl.collection_actions"
    bl_label = "Collection Actions"
    bl_options = {'REGISTER', 'INTERNAL'}
    action: bpy.props.EnumProperty(items=(('ADD', "", ""), ('REMOVE', "", ""), ('UP', "", ""), ('DOWN', "", ""), ('COPY', "", ""), ('PASTE', "", "")))
    shift_pressed: bpy.props.BoolProperty(options={'HIDDEN', 'SKIP_SAVE'}, default=False)

    @classmethod
    def description(cls, context, properties):
        if properties.action == 'ADD': return "Add a new collection"
        elif properties.action == 'REMOVE': return "Remove the active collection"
        elif properties.action == 'UP': return "Move collection up (Shift-Click: Move to top)"
        elif properties.action == 'DOWN': return "Move collection down (Shift-Click: Move to bottom)"
        elif properties.action == 'COPY': return "Copy collection to clipboard"
        elif properties.action == 'PASTE': return "Paste collection from clipboard"
        return "Collection Actions"

    def invoke(self, context, event):
        self.shift_pressed = event.shift
        return self.execute(context)

    def execute(self, context):
        preset = get_active_preset(context.scene)
        if not preset: return {'CANCELLED'}
        lst, idx = preset.collections, preset.collection_index

        if self.action == 'ADD':
            lst.add()
            preset.collection_index = len(lst) - 1
        elif self.action == 'REMOVE' and lst:
            lst.remove(idx)
            preset.collection_index = max(0, idx - 1)
        elif self.action == 'UP' and idx > 0:
            lst.move(idx, 0 if self.shift_pressed else idx - 1)
            preset.collection_index = 0 if self.shift_pressed else idx - 1
        elif self.action == 'DOWN' and idx < len(lst) - 1:
            lst.move(idx, len(lst) - 1 if self.shift_pressed else idx + 1)
            preset.collection_index = len(lst) - 1 if self.shift_pressed else idx + 1
        elif self.action == 'COPY' and lst:
            clipboard["collection"] = copy_collection_to_dict(lst[idx])
        elif self.action == 'PASTE' and clipboard.get("collection"):
            paste_collection_from_dict(lst.add(), clipboard["collection"])
            preset.collection_index = len(lst) - 1

        if self.action != 'COPY': bpy.ops.ed.undo_push(message="Collection Action")
        return {'FINISHED'}


class BATCH_STL_OT_table_action(bpy.types.Operator):
    # High-density operator mapping all interactions in the property table (add, copy, move, delete)
    bl_idname = "batch_stl.table_action"
    bl_label = "Table Action"
    bl_options = {'REGISTER', 'UNDO'}

    action: bpy.props.StringProperty()
    is_pinned: bpy.props.BoolProperty()
    ng_idx: bpy.props.IntProperty(default=-1)
    n_idx: bpy.props.IntProperty(default=-1)
    i_idx: bpy.props.IntProperty(default=-1)
    v_idx: bpy.props.IntProperty(default=-1)
    shift_pressed: bpy.props.BoolProperty(options={'HIDDEN', 'SKIP_SAVE'}, default=False)

    @classmethod
    def description(cls, context, properties):
        action = properties.action
        # Exposes dynamic tooltips based on what table action button is hovered
        if action == 'ADD_GROUP': return "Add a new Node Group override"
        elif action == 'DEL_GROUP': return "Delete this Node Group override"
        elif action == 'PIN_GROUP': return "Pin Group to Global Overrides (Shared across collections)"
        elif action == 'UNPIN_GROUP': return "Unpin Group to Local Overrides (Specific to collection)"
        elif action == 'COPY_GROUP': return "Copy Node Group to clipboard"
        elif action == 'PASTE_GROUP': return "Paste Node Group from clipboard"
        elif action == 'ADD_NODE': return "Add a new Node to override"
        elif action == 'DEL_NODE': return "Delete this Node"
        elif action == 'MOVE_GROUP_UP': return "Move Group Up"
        elif action == 'MOVE_GROUP_DOWN': return "Move Group Down"
        elif action == 'MOVE_NODE_UP': return "Move Node Up"
        elif action == 'MOVE_NODE_DOWN': return "Move Node Down"
        elif action == 'ADD_INPUT': return "Add an Input (Shift-Click: Auto-populate all inputs from Node/Modifier)"
        elif action == 'DEL_INPUT': return "Delete this Input"
        elif action == 'MOVE_INPUT_UP': return "Move Input Up"
        elif action == 'MOVE_INPUT_DOWN': return "Move Input Down"
        elif action == 'DEL_VALUE_OR_INPUT': return "Delete this Value or Input"
        elif action == 'DEL_VALUE': return "Delete this Value iteration"
        elif action == 'MOVE_VALUE_UP': return "Move Value Up"
        elif action == 'MOVE_VALUE_DOWN': return "Move Value Down"
        elif action == 'VALUE_ACTION':
            if properties.v_idx < 0: return "Add a Value iteration (Shift-Click: Toggle Sweep Mode)"
            else: return "Add a Value iteration (Shift-Click: Toggle Sweep / Populate values)"
        return "Table action"

    def invoke(self, context, event):
        self.shift_pressed = event.shift
        return self.execute(context)

    def execute(self, context):
        preset = get_active_preset(context.scene)
        if not preset: return {'CANCELLED'}

        ng_list = preset.nodegroups if self.is_pinned else get_active_collection(preset).nodegroups

        if self.action == 'ADD_GROUP':
            ng = ng_list.add()
            node = ng.nodes.add()
            node.name = "<Modifier Interface>"
            inp = node.inputs.add()
            inp.values.add()
        elif self.action == 'DEL_GROUP':
            ng_list.remove(self.ng_idx)
        elif self.action == 'PIN_GROUP':
            if not self.is_pinned:
                src_ng = ng_list[self.ng_idx]
                dst_ng = preset.nodegroups.add()
                paste_ng_from_dict(dst_ng, copy_ng_to_dict(src_ng))
                ng_list.remove(self.ng_idx)
        elif self.action == 'UNPIN_GROUP':
            if self.is_pinned:
                active_col = get_active_collection(preset)
                if active_col:
                    src_ng = ng_list[self.ng_idx]
                    dst_ng = active_col.nodegroups.add()
                    paste_ng_from_dict(dst_ng, copy_ng_to_dict(src_ng))
                    ng_list.remove(self.ng_idx)
        elif self.action == 'COPY_GROUP':
            clipboard["nodegroup"] = copy_ng_to_dict(ng_list[self.ng_idx])
        elif self.action == 'PASTE_GROUP':
            if clipboard.get("nodegroup"):
                paste_ng_from_dict(ng_list.add(), clipboard["nodegroup"])

        elif self.action == 'ADD_NODE':
            node = ng_list[self.ng_idx].nodes.add()
            node.name = "<Modifier Interface>"
            inp = node.inputs.add()
            inp.values.add()
        elif self.action == 'DEL_NODE':
            ng_list[self.ng_idx].nodes.remove(self.n_idx)

        elif self.action == 'ADD_INPUT':
            ng = ng_list[self.ng_idx]
            node = ng.nodes[self.n_idx]

            # Auto-populate shift click macro functionality
            if self.shift_pressed and ng.group_ptr:
                is_mod = not node.name or node.name == "<Modifier Interface>"
                source_inputs = []
                if is_mod and hasattr(ng.group_ptr, "interface"):
                    for item in ng.group_ptr.interface.items_tree:
                        if getattr(item, "item_type", "SOCKET") == 'SOCKET' and getattr(item, "in_out", "INPUT") == 'INPUT':
                            source_inputs.append(item.name)
                elif not is_mod and node.name:
                    target_n = ng.group_ptr.nodes.get(node.name.split(" [")[0].strip())
                    if target_n:
                        for i in target_n.inputs:
                            if not getattr(i, "is_unavailable", False) and not getattr(i, "hide", False):
                                source_inputs.append(i.name)

                if source_inputs:
                    existing_names = {i.name for i in node.inputs}
                    added = False
                    for s_name in source_inputs:
                        if s_name and s_name not in existing_names:
                            inp = node.inputs.add()
                            inp.name = s_name
                            inp.values.add()
                            added = True
                    if added:
                        return {'FINISHED'}

            inp = node.inputs.add()
            inp.values.add()
        elif self.action == 'DEL_INPUT':
            ng_list[self.ng_idx].nodes[self.n_idx].inputs.remove(self.i_idx)
        elif self.action == 'DEL_VALUE':
            ng_list[self.ng_idx].nodes[self.n_idx].inputs[self.i_idx].values.remove(self.v_idx)
        elif self.action == 'DEL_VALUE_OR_INPUT':
            inp = ng_list[self.ng_idx].nodes[self.n_idx].inputs[self.i_idx]
            if len(inp.values) > 1:
                inp.values.remove(self.v_idx)
            else:
                ng_list[self.ng_idx].nodes[self.n_idx].inputs.remove(self.i_idx)

        elif self.action in ['ADD_VALUE', 'TOGGLE_SWEEP', 'VALUE_ACTION']:
            vals = ng_list[self.ng_idx].nodes[self.n_idx].inputs[self.i_idx].values

            if self.v_idx < 0:
                vals.add()
            else:
                val = vals[self.v_idx]
                if not val.use_sweep:
                    if self.shift_pressed:
                        val.use_sweep = True
                        for j in reversed(range(len(vals))):
                            if j != self.v_idx: vals.remove(j)
                    else:
                        vals.add()
                else:
                    val.use_sweep = False
                    # Auto-populate specific values from sweep string string evaluation
                    if self.shift_pressed:
                        inp_obj = ng_list[self.ng_idx].nodes[self.n_idx].inputs[self.i_idx]
                        if inp_obj.override_type in ['FLOAT', 'INT', 'MENU']:
                            ng_obj = ng_list[self.ng_idx]
                            node_obj = ng_obj.nodes[self.n_idx]
                            target = 'MODIFIER' if not node_obj.name or node_obj.name == "<Modifier Interface>" else 'NODE'

                            temp_inp = TempMockInput(inp_obj.name, inp_obj.override_type, val)
                            temp_ovr = TempMockOverride(target, ng_obj.group_ptr, node_obj.name, [temp_inp])

                            parsed_vals = parse_sweep_values(temp_ovr, temp_inp)
                            if parsed_vals:
                                first_val = parsed_vals[0]
                                if inp_obj.override_type == 'FLOAT': val.value_float = first_val
                                elif inp_obj.override_type == 'INT': val.value_int = first_val
                                elif inp_obj.override_type == 'MENU': val.value_menu = str(first_val)

                                for p_val in parsed_vals[1:]:
                                    new_val = vals.add()
                                    new_val.use_sweep = False
                                    if inp_obj.override_type == 'FLOAT': new_val.value_float = p_val
                                    elif inp_obj.override_type == 'INT': new_val.value_int = p_val
                                    elif inp_obj.override_type == 'MENU': new_val.value_menu = str(p_val)

        # Basic List Rearranging
        elif self.action == 'MOVE_GROUP_UP':
            if self.ng_idx > 0: ng_list.move(self.ng_idx, self.ng_idx - 1)
        elif self.action == 'MOVE_GROUP_DOWN':
            if self.ng_idx < len(ng_list) - 1: ng_list.move(self.ng_idx, self.ng_idx + 1)
        elif self.action == 'MOVE_NODE_UP':
            nodes = ng_list[self.ng_idx].nodes
            if self.n_idx > 0: nodes.move(self.n_idx, self.n_idx - 1)
        elif self.action == 'MOVE_NODE_DOWN':
            nodes = ng_list[self.ng_idx].nodes
            if self.n_idx < len(nodes) - 1: nodes.move(self.n_idx, self.n_idx + 1)
        elif self.action == 'MOVE_INPUT_UP':
            inputs = ng_list[self.ng_idx].nodes[self.n_idx].inputs
            if self.i_idx > 0: inputs.move(self.i_idx, self.i_idx - 1)
        elif self.action == 'MOVE_INPUT_DOWN':
            inputs = ng_list[self.ng_idx].nodes[self.n_idx].inputs
            if self.i_idx < len(inputs) - 1: inputs.move(self.i_idx, self.i_idx + 1)
        elif self.action == 'MOVE_VALUE_UP':
            vals = ng_list[self.ng_idx].nodes[self.n_idx].inputs[self.i_idx].values
            if self.v_idx > 0: vals.move(self.v_idx, self.v_idx - 1)
        elif self.action == 'MOVE_VALUE_DOWN':
            vals = ng_list[self.ng_idx].nodes[self.n_idx].inputs[self.i_idx].values
            if self.v_idx < len(vals) - 1: vals.move(self.v_idx, self.v_idx + 1)

        return {'FINISHED'}

class BATCH_STL_OT_toggle_exclusion(bpy.types.Operator):
    bl_idname = "batch_stl.toggle_exclusion"
    bl_label = "Toggle Object Exclusion"
    bl_description = "Toggle whether this object is excluded from export"
    bl_options = {'REGISTER', 'INTERNAL'}
    object_name: bpy.props.StringProperty()

    def execute(self, context):
        preset = get_active_preset(context.scene)
        collection = get_active_collection(preset)
        if collection:
            idx = -1
            for i, e in enumerate(collection.excluded_objects):
                if e.name == self.object_name:
                    idx = i; break
            if idx >= 0:
                collection.excluded_objects.remove(idx)
                bpy.ops.ed.undo_push(message=f"Include '{self.object_name}' in Export")
            else:
                collection.excluded_objects.add().name = self.object_name
                bpy.ops.ed.undo_push(message=f"Exclude '{self.object_name}' from Export")
        return {'FINISHED'}

class BATCH_STL_OT_toggle_dir_tree(bpy.types.Operator):
    bl_idname = "batch_stl.toggle_dir_tree"
    bl_label = "Toggle Directory Tree"
    bl_description = "Expand or collapse this directory in the preview tree"
    bl_options = {'INTERNAL'}
    dir_path: bpy.props.StringProperty()

    def execute(self, context):
        scene = context.scene
        # [FIX] Safer JSON loading. If property string is corrupt, fail gracefully to an empty list
        try:
            collapsed = json.loads(scene.batch_stl_collapsed_dirs)
            if not isinstance(collapsed, list): collapsed = []
        except (json.JSONDecodeError, TypeError, ValueError):
            collapsed = []

        if self.dir_path in collapsed:
            collapsed.remove(self.dir_path)
        else:
            collapsed.append(self.dir_path)

        scene.batch_stl_collapsed_dirs = json.dumps(collapsed)
        return {'FINISHED'}

class BATCH_STL_OT_cancel_export(bpy.types.Operator):
    bl_idname = "batch_stl.cancel_export"
    bl_label = "Cancel Export"
    bl_description = "Cancel the ongoing background export"
    preset_index: bpy.props.IntProperty(default=-1)

    def execute(self, context):
        if 0 <= self.preset_index < len(context.scene.batch_stl_presets):
            preset = context.scene.batch_stl_presets[self.preset_index]
            preset.cancel_export = True
            log = preset.console_logs.add()
            log.text = f"[!] Export cancelled manually for '{preset.name}'."
            preset.console_index = len(preset.console_logs) - 1
        return {'FINISHED'}

# ==============================================================================
# === MASTER EXPORT LAUNCHER (MODAL) ===
# ==============================================================================

class EXPORT_OT_batch_stl_multi(bpy.types.Operator):
    bl_idname = "export_scene.batch_stl_multi"
    bl_label = "Export"
    bl_description = "Safely evaluate and batch export the mapped collections"
    bl_options = {"REGISTER"}
    preset_index: bpy.props.IntProperty(default=-1)

    @classmethod
    def poll(cls, context):
        return len(context.scene.batch_stl_presets) > 0

    def invoke(self, context, event):
        self._timer = None
        self.process = None
        self.total_operations = 1
        self.export_start_time = time.perf_counter()
        self.current_op = 0
        scene = context.scene

        self.preset_idx = self.preset_index if self.preset_index >= 0 else scene.batch_stl_preset_index
        if self.preset_idx < 0 or self.preset_idx >= len(scene.batch_stl_presets): return {"CANCELLED"}
        self.preset = scene.batch_stl_presets[self.preset_idx]

        if self.preset.is_exporting: return {'CANCELLED'}
        if not scene.batch_stl_root_dir:
            self.report({'ERROR'}, "Missing Root Directory")
            return {"CANCELLED"}

        self.preset.console_logs.clear()
        context.scene.batch_stl_show_console = True

        has_overrides = bool(self.preset.nodegroups) or any(bool(c.nodegroups) for c in self.preset.collections)
        verbose = scene.batch_stl_verbose_console

        # Route A: Synchronous Export (No overrides present)
        # Avoids subprocess overhead if we just want to export static objects.
        if not has_overrides:
            root_dir = bpy.path.abspath(scene.batch_stl_root_dir)
            if self.preset.preset_prefix:
                root_dir = os.path.normpath(os.path.join(root_dir, self.preset.preset_prefix))

            total_objs = 0
            for c in self.preset.collections:
                if not c.collection_ptr: continue
                if is_collection_excluded(context, c.collection_ptr): continue
                excluded_names = {e.name for e in c.excluded_objects} if c.use_filter else set()
                for obj in c.collection_ptr.all_objects:
                    if obj.type in {"MESH", "CURVE", "SURFACE", "META", "FONT"} and not obj.hide_get() and not obj.hide_viewport:
                        if obj.name not in excluded_names: total_objs += 1

            context.window_manager.progress_begin(0, max(1, total_objs))
            depsgraph = context.evaluated_depsgraph_get()
            exported_count = 0

            log_msg = f"\n=== STARTING SYNCHRONOUS EXPORT: {self.preset.name} ==="
            if verbose: print(log_msg)
            log_to_console(self.preset, log_msg)

            for c in self.preset.collections:
                if not c.collection_ptr: continue
                if is_collection_excluded(context, c.collection_ptr): continue
                out_dir = os.path.normpath(os.path.join(root_dir, c.sub_path))
                os.makedirs(out_dir, exist_ok=True)
                excluded_names = {e.name for e in c.excluded_objects} if c.use_filter else set()

                for obj in c.collection_ptr.all_objects:
                    if obj.type in {"MESH", "CURVE", "SURFACE", "META", "FONT"} and not obj.hide_get() and not obj.hide_viewport:
                        if obj.name in excluded_names: continue
                        t_eval_start = time.perf_counter()
                        obj_eval = obj.evaluated_get(depsgraph)
                        try: mesh = obj_eval.to_mesh()
                        except RuntimeError: mesh = None
                        perf_msg = f"  ├─ Evaluated {obj.name} in {time.perf_counter()-t_eval_start:.4f}s"
                        if verbose: print(perf_msg)
                        log_to_console(self.preset, perf_msg)

                        if mesh:
                            base_tag = c.tag if c.use_tag and c.tag else ""
                            base_name = f"{obj.name}{base_tag}"
                            filepath = os.path.join(out_dir, f"{bpy.path.clean_name(base_name)}.stl")
                            write_fast_binary_stl(filepath, mesh, obj.matrix_world, verbose=verbose)
                            obj_eval.to_mesh_clear()
                            exported_count += 1
                            context.window_manager.progress_update(exported_count)

            context.window_manager.progress_end()
            total_time = time.perf_counter() - self.export_start_time
            self.preset.last_export_time = total_time

            end_msg = f"=== SYNCHRONOUS EXPORT COMPLETE ({total_time:.4f}s) ==="
            if verbose: print(end_msg)
            log_to_console(self.preset, end_msg)
            self.report({'INFO'}, f"Exported {exported_count} objects directly in {total_time:.2f}s.")
            return {'FINISHED'}

        # Route B: Asynchronous Headless Subprocess (Permutations active)
        self.preset.export_status = f"Spawning Worker... (0.0s)"
        t_spawn_start = time.perf_counter()

        self.temp_dir = tempfile.mkdtemp(prefix="fast_batch_stl_")
        self.temp_blend = os.path.join(self.temp_dir, "batch_stl_export_temp.blend")
        # Snapshot the current project state so the headless blender evaluates the exact live data
        bpy.ops.wm.save_as_mainfile(filepath=self.temp_blend, copy=True, compress=False)

        # [FIX] Ensures the subprocess dynamically knows the module folder context
        # when calling `__init__.py` using `-P`.
        addon_dir = os.path.dirname(os.path.realpath(__file__))
        init_file = os.path.join(addon_dir, "__init__.py")

        cmd = [
            bpy.app.binary_path, "-b", self.temp_blend,
            "-P", init_file, "--", "--batch-stl-headless", str(self.preset_idx)
        ]

        try:
            self.process = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)
        except Exception as e:
            self.report({'ERROR'}, f"Failed to spawn headless Blender: {e}")
            self.cleanup(context)
            return {'CANCELLED'}

        spawn_time = time.perf_counter() - t_spawn_start
        spawn_msg = f"=== INITIATING HEADLESS EXPORT '{self.preset.name}' [{spawn_time:.4f}s Boot] ==="
        if verbose: print(f"\n{spawn_msg}")
        log_to_console(self.preset, spawn_msg)

        # Pipe subprocess stdout into a non-blocking queue string thread
        self.q = queue.Queue()
        def enqueue_output(out, q):
            for line in iter(out.readline, ''):
                q.put(line)
            out.close()

        self.t = threading.Thread(target=enqueue_output, args=(self.process.stdout, self.q))
        self.t.daemon = True
        self.t.start()

        self.preset.is_exporting = True
        self.preset.cancel_export = False
        self.preset.export_progress = 0.0

        # Modal execution timer allows Blender UI to remain responsive during operation
        self._timer = context.window_manager.event_timer_add(0.05, window=context.window)
        context.window_manager.modal_handler_add(self)
        return {'RUNNING_MODAL'}

    def modal(self, context, event):
        # [FIX] Wrapped in a broad Try/Finally equivalent inside the modal to guarantee
        # that modal timers are safely cleaned up even if UI throws an obscure layout error.
        try:
            if self.preset.cancel_export:
                self.cleanup(context)
                self.report({'WARNING'}, f"Export cancelled for {self.preset.name}.")
                log_to_console(self.preset, f"[!] Export cancelled manually for '{self.preset.name}'.")
                return {'CANCELLED'}

            if event.type == 'TIMER':
                elapsed = time.perf_counter() - self.export_start_time
                while True:
                    try: line = self.q.get_nowait()
                    except queue.Empty: break
                    else:
                        line = line.rstrip('\r\n')
                        # Listen for progress markers dumped by headless print statements
                        if line.startswith("BATCH_STL_TOTAL:"):
                            try: self.total_operations = int(line.split(":")[1])
                            except (ValueError, IndexError): pass
                        elif line.startswith("BATCH_STL_PROGRESS:"):
                            try:
                                self.current_op = int(line.split(":")[1])
                                self.preset.export_progress = self.current_op / max(1, self.total_operations)
                            except (ValueError, IndexError): pass
                        elif line.startswith("BATCH_STL_DONE"):
                            self.cleanup(context)
                            total_time = time.perf_counter() - self.export_start_time
                            self.preset.last_export_time = total_time
                            end_msg = f"=== BATCH EXPORT COMPLETE ({total_time:.4f}s) ==="
                            if context.scene.batch_stl_verbose_console: print(end_msg)
                            log_to_console(self.preset, end_msg)
                            self.report({'INFO'}, f"Batch Export {self.preset.name} Complete in {total_time:.2f}s.")
                            for area in context.screen.areas: area.tag_redraw()
                            return {'FINISHED'}
                        elif line:
                            if context.scene.batch_stl_verbose_console: print(f"[{self.preset.name}] {line}")
                            log_to_console(self.preset, f"[{self.preset.name}] {line}")

                if self.preset.is_exporting:
                    if self.total_operations > 1 or self.current_op > 0:
                        self.preset.export_status = f"Obj {self.current_op}/{self.total_operations} | {elapsed:.1f}s"
                    else:
                        self.preset.export_status = f"Spawning Worker... ({elapsed:.1f}s)"

                # Force UI redraw to visually move the progress bar component
                for area in context.screen.areas: area.tag_redraw()

                # Fail-safe timeout / crash detection
                if self.process and self.process.poll() is not None:
                    self.cleanup(context)
                    log_to_console(self.preset, f"[!] CRASH DETECTED: Worker died unexpectedly.")
                    self.report({'ERROR'}, f"Background worker crashed for preset {self.preset.name}.")
                    return {'CANCELLED'}
        except ReferenceError:
            self.cleanup(context)
            return {'CANCELLED'}
        except Exception as e:
            err_msg = f"[!] Fast Batch STL Error ({self.preset.name if hasattr(self, 'preset') else 'Unknown'}): {e}"
            print(f"\n{err_msg}")
            if hasattr(self, 'preset'): log_to_console(self.preset, err_msg)
            self.cleanup(context)
            self.report({'ERROR'}, "Unexpected error during batch export.")
            return {'CANCELLED'}

        return {'PASS_THROUGH'}

    def cleanup(self, context=None):
        # Tears down background processes and UI handlers safely.
        if context:
            if getattr(self, '_timer', None):
                context.window_manager.event_timer_remove(self._timer)
                self._timer = None
            try:
                self.preset.is_exporting = False
                self.preset.cancel_export = False
                self.preset.export_progress = 0.0
                self.preset.export_status = ""
            except ReferenceError: pass

        if getattr(self, 'process', None):
            try:
                if self.process.poll() is None: self.process.kill()
            except OSError: pass

        # Clean up temporary duplicate file
        try:
            if hasattr(self, 'temp_blend') and os.path.exists(self.temp_blend): os.remove(self.temp_blend)
            if hasattr(self, 'temp_dir') and os.path.exists(self.temp_dir): os.rmdir(self.temp_dir)
        except OSError: pass

classes = (
    BATCH_STL_OT_clear_console,
    BATCH_STL_OT_preset_actions,
    BATCH_STL_OT_collection_actions,
    BATCH_STL_OT_table_action,
    BATCH_STL_OT_toggle_exclusion,
    BATCH_STL_OT_toggle_dir_tree,
    BATCH_STL_OT_cancel_export,
    BATCH_STL_OT_export_presets_json,
    BATCH_STL_OT_import_presets_json,
    EXPORT_OT_batch_stl_multi,
)
