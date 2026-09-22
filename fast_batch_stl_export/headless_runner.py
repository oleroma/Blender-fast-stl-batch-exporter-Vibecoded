import sys
import os
import time
import struct
import bpy
from .core_engine import (
    is_collection_excluded, get_override_signature,
    generate_override_combinations, reconstruct_overrides_for_combo,
    apply_overrides, revert_overrides, get_input_value
)

def write_fast_binary_stl(filepath, mesh, matrix_world):
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


def run_headless_export(preset_index):
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
    t_phase0_start = time.perf_counter()

    active_export_objects = set()
    for mapping in preset.mappings:
        if mapping.collection_ptr and not is_collection_excluded(bpy.context, mapping.collection_ptr):
            active_export_objects.update(mapping.collection_ptr.all_objects)

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
        if is_collection_excluded(bpy.context, mapping.collection_ptr): continue

        sig_local = get_override_signature(mapping.node_overrides)
        full_sig = sig_pinned + sig_local

        if full_sig not in execution_batches: execution_batches[full_sig] = []
        execution_batches[full_sig].append(mapping)

    if not execution_batches:
        sys.exit(0)

    total_combos = sum(len(generate_override_combinations(list(preset.pinned_overrides) + list(batch[0].node_overrides))) for batch in execution_batches.values())
    print(f"BATCH_STL_TOTAL:{total_combos}", flush=True)

    current_combo_step = 0
    batch_counter = 1

    for signature, mappings_in_batch in execution_batches.items():
        t_batch_start = time.perf_counter()
        first_mapping = mappings_in_batch[0]
        all_overrides = list(preset.pinned_overrides) + list(first_mapping.node_overrides)
        combinations = generate_override_combinations(all_overrides)

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

                    excluded_names = {e.name for e in mapping.excluded_objects} if mapping.use_filter else set()

                    for obj in mapping.collection_ptr.all_objects:
                        if obj.type in {"MESH", "CURVE", "SURFACE", "META", "FONT"} and not obj.hide_get() and not obj.hide_viewport:
                            if obj.name in excluded_names: continue

                            t_eval = time.perf_counter()
                            obj_eval = obj.evaluated_get(depsgraph)
                            try: mesh = obj_eval.to_mesh()
                            except RuntimeError: mesh = None

                            if mesh:
                                base_tag = mapping.tag if mapping.use_tag and mapping.tag else ""
                                final_tag = base_tag + combo_suffix
                                filepath = os.path.join(out_dir, f"{bpy.path.clean_name(obj.name)}{final_tag}.stl")
                                write_fast_binary_stl(filepath, mesh, obj.matrix_world)
                                obj_eval.to_mesh_clear()
            finally:
                t_rev = time.perf_counter()
                revert_overrides(global_states, mod_states, batch_objects)
                bpy.context.view_layer.update()

            t_purge = time.perf_counter()
            bpy.ops.outliner.orphans_purge(do_local_ids=True, do_linked_ids=True, do_recursive=True)

            current_combo_step += 1
            print(f"BATCH_STL_PROGRESS:{current_combo_step}", flush=True)

        batch_counter += 1
    bpy.ops.wm.quit_blender()

class BATCH_STL_OT_run_headless_internal(bpy.types.Operator):
    bl_idname = "batch_stl.run_headless_internal"
    bl_label = "Internal Headless Runner"
    bl_options = {'INTERNAL'}

    preset_index: bpy.props.IntProperty(default=0)

    def execute(self, context):
        # Calls your main routine with proper context unlocked
        run_headless_export(self.preset_index)
        return {'FINISHED'}

classes = (BATCH_STL_OT_run_headless_internal,)
