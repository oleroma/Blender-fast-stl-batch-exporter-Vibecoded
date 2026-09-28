"""
Exporter Engine
Contains the highly optimized Numpy binary STL writer and the headless subprocess logic.
"""
import os
import time
import sys
import struct
import numpy as np
import bpy

from .core import (
    is_collection_excluded,
    get_flat_overrides,
    get_override_signature,
    generate_override_combinations,
    get_input_value,
    reconstruct_overrides_for_combo,
    apply_overrides,
    revert_overrides
)

# ==============================================================================
# === BINARY STL WRITER ===
# ==============================================================================

def write_fast_binary_stl(filepath, mesh, matrix_world, verbose=False):
    # A memory-mapped Binary STL writer using pure NumPy.
    # Standard bpy exporters iterate linearly over python loops, which is slow.
    # This transforms mesh vertex arrays instantly using C backend operations.
    t_start = time.perf_counter()
    mesh.calc_loop_triangles()
    num_tris = len(mesh.loop_triangles)
    if num_tris == 0: return

    # Vectorized coordinate extraction
    verts = np.empty((len(mesh.vertices), 3), dtype=np.float32)
    mesh.vertices.foreach_get("co", verts.ravel())
    mat = np.array(matrix_world, dtype=np.float32)

    # 4D vector padding for standard 4x4 matrix multiplication
    verts_vec4 = np.c_[verts, np.ones(len(verts), dtype=np.float32)]
    verts = np.dot(verts_vec4, mat.T)[:, :3]

    # Vectorized face extraction
    tri_verts = np.empty((num_tris, 3), dtype=np.int32)
    mesh.loop_triangles.foreach_get("vertices", tri_verts.ravel())

    # Calculate the normal vectors for the triangles using the inverted transposed matrix
    # This ensures normals are scaled and rotated correctly in world space
    tri_normals = np.empty((num_tris, 3), dtype=np.float32)
    mesh.loop_triangles.foreach_get("normal", tri_normals.ravel())
    mat_norm = np.array(matrix_world.to_3x3().inverted_safe().transposed(), dtype=np.float32)
    tri_normals = np.dot(tri_normals, mat_norm.T)

    # Fast norm calculation avoiding divide-by-zero errors
    norms = np.linalg.norm(tri_normals, axis=1, keepdims=True)
    norms[norms == 0] = 1.0
    tri_normals /= norms

    # Define strict structural byte-array for STL specs
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
    if verbose:
        mb_size = (84 + (num_tris * 50)) / (1024 * 1024)
        print(f"  │         │    ├─ STL Memory Map: {num_tris} Tris | {mb_size:.2f} MB | Matrix T-Form: {t_format-t_start:.4f}s")

    # Fast IO Write using struct byte packaging
    with open(filepath, 'wb') as f:
        # 80-byte binary header
        f.write(b'Batch STL Fast Export' + b'\x00' * 59)
        # 4-byte unsigned int containing face count
        f.write(struct.pack('<I', num_tris))
        # Raw block stream of mapped vertices
        f.write(data.tobytes())

    t_write = time.perf_counter()
    if verbose:
        print(f"  │         │    ├─ Disk I/O Write: {t_write-t_format:.4f}s | Path: {os.path.basename(filepath)}")

# ==============================================================================
# === HEADLESS BACKGROUND WORKER ===
# ==============================================================================

def run_headless_export(preset_index):
    # This entire function executes inside an isolated Python subprocess instance
    # to avoid locking up Blender's UI thread. It communicates back via print statements
    # capturing stdout for real-time progress bars.
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

    # Pre-calculate active objects to skip evaluate operations on hidden meshes
    active_export_objects = set()
    for c in preset.collections:
        if c.collection_ptr and not is_collection_excluded(bpy.context, c.collection_ptr):
            active_export_objects.update(c.collection_ptr.all_objects)

    muted_count = 0
    # Mute heavy GN modifiers on inactive objects that might bog down the evaluator graph
    for obj in bpy.context.view_layer.objects:
        if obj not in active_export_objects:
            for mod in getattr(obj, 'modifiers', []):
                if mod.type == 'NODES' and mod.show_viewport:
                    mod.show_viewport = False
                    muted_count += 1

    print(f"    ├─ Permanently Muted {muted_count} unused GN modifiers to accelerate Graph evaluation in {time.perf_counter() - t_phase0_start:.4f}s")

    execution_batches = {}
    sig_pinned = get_override_signature(get_flat_overrides(preset.nodegroups))

    # Group collections into batches based on their override signature to minimize redundant Graph updates
    for c in preset.collections:
        if not c.collection_ptr: continue
        if is_collection_excluded(bpy.context, c.collection_ptr): continue
        sig_local = get_override_signature(get_flat_overrides(c.nodegroups))
        full_sig = sig_pinned + sig_local
        if full_sig not in execution_batches: execution_batches[full_sig] = []
        execution_batches[full_sig].append(c)

    if not execution_batches:
        print("  └─ No active collections to export.")
        print("BATCH_STL_DONE", flush=True)
        sys.exit(0)

    # Precalculate total ops for progress reporting back to Main Thread
    total_operations = 0
    for signature, c_in_batch in execution_batches.items():
        first_c = c_in_batch[0]
        all_overrides = get_flat_overrides(preset.nodegroups) + get_flat_overrides(first_c.nodegroups)
        combinations = generate_override_combinations(all_overrides)
        batch_obj_count = 0
        for m in c_in_batch:
            excluded_names = {e.name for e in m.excluded_objects} if getattr(m, "use_filter", False) else set()
            for obj in m.collection_ptr.all_objects:
                if obj.type in {"MESH", "CURVE", "SURFACE", "META", "FONT"} and not obj.hide_get() and not obj.hide_viewport:
                    if obj.name not in excluded_names:
                        batch_obj_count += 1
        total_operations += (batch_obj_count * len(combinations))

    print(f"BATCH_STL_TOTAL:{total_operations}", flush=True)

    current_op_step = 0
    batch_counter = 1

    # Execute Iteration batches
    for signature, c_in_batch in execution_batches.items():
        t_batch_start = time.perf_counter()
        first_c = c_in_batch[0]
        all_overrides = get_flat_overrides(preset.nodegroups) + get_flat_overrides(first_c.nodegroups)

        freq_dict = {}
        for o in all_overrides:
            for i in o.inputs:
                key = (o.override_target, o.node_name, i.input_name)
                weight = 2 if getattr(i, "use_sweep", False) else 1
                freq_dict[key] = freq_dict.get(key, 0) + weight

        combinations = generate_override_combinations(all_overrides)

        is_clean_batch = len(get_flat_overrides(first_c.nodegroups)) == 0
        batch_type = "Clean (Pinned Only)" if is_clean_batch else f"Dirty ({len(get_flat_overrides(first_c.nodegroups))} Local Overrides)"
        c_names = [f"{m.collection_ptr.name} [{m.tag}]" for m in c_in_batch]
        print(f"  ├─ Batch {batch_counter}/{len(execution_batches)} [{batch_type}]: Processing {len(c_names)} mapped instances with {len(combinations)} permutation(s)")

        batch_objects = set()
        for m in c_in_batch: batch_objects.update(m.collection_ptr.all_objects)

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

                    is_permutation = freq_dict.get(param_key, 0) > 1
                    if is_permutation and getattr(inp, "use_tag", False): combo_suffix += f"_{naming_str}"
                    if is_permutation and getattr(inp, "use_dir", False): combo_subpath = os.path.join(combo_subpath, naming_str)
                    processed_params.add(param_key)

            t_ovr = time.perf_counter()
            global_states, mod_states = [], []

            try:
                active_overrides = reconstruct_overrides_for_combo(combo)
                global_states, mod_states = apply_overrides(active_overrides, batch_objects)

                # Critical Update: Sync graph states so evaluator sees modifications
                bpy.context.view_layer.update()
                depsgraph = bpy.context.evaluated_depsgraph_get()
                print(f"  │    │    ├─ Applied & Synced Graph: {time.perf_counter() - t_ovr:.4f}s")

                for c in c_in_batch:
                    out_dir = os.path.normpath(os.path.join(root_dir, c.sub_path, combo_subpath))
                    os.makedirs(out_dir, exist_ok=True)
                    print(f"  │    │    ├─ Exporting: {c.collection_ptr.name}{' ['+c.tag+']' if c.tag else ''}")

                    excluded_names = {e.name for e in c.excluded_objects} if getattr(c, "use_filter", False) else set()
                    for obj in c.collection_ptr.all_objects:
                        if obj.type in {"MESH", "CURVE", "SURFACE", "META", "FONT"} and not obj.hide_get() and not obj.hide_viewport:
                            if obj.name in excluded_names: continue
                            t_eval = time.perf_counter()

                            # Retrieve the evaluated geometry from the modifier stack
                            obj_eval = obj.evaluated_get(depsgraph)
                            try: mesh = obj_eval.to_mesh()
                            except RuntimeError: mesh = None
                            print(f"  │         ├─ Evaluated Mesh [{obj.name}]: {time.perf_counter() - t_eval:.4f}s")

                            if mesh:
                                base_tag = c.tag if getattr(c, "use_tag", False) and c.tag else ""
                                final_tag = base_tag + combo_suffix
                                filepath = os.path.join(out_dir, f"{bpy.path.clean_name(obj.name)}{final_tag}.stl")
                                write_fast_binary_stl(filepath, mesh, obj.matrix_world, verbose=True)

                                # Clear immediately from RAM to prevent leaks on dense meshes
                                obj_eval.to_mesh_clear()
                                current_op_step += 1
                                print(f"BATCH_STL_PROGRESS:{current_op_step}", flush=True)

            finally:
                t_rev = time.perf_counter()
                revert_overrides(global_states, mod_states, batch_objects)
                bpy.context.view_layer.update()
                print(f"  │    │    ├─ Reverted permutation overrides: {time.perf_counter() - t_rev:.4f}s")

            t_purge = time.perf_counter()
            # Force outliner purge of orphaned mesh datablocks left over from `to_mesh()` calls
            bpy.ops.outliner.orphans_purge(do_local_ids=True, do_linked_ids=True, do_recursive=True)
            print(f"  │    │    ├─ RAM Purge: {time.perf_counter() - t_purge:.4f}s")

        print(f"  │    => Batch Iteration Total Time: {time.perf_counter() - t_batch_start:.4f}s\n")
        batch_counter += 1

    print(f"\n=== HEADLESS EXPORT COMPLETE: {time.perf_counter() - total_time_start:.4f}s Subprocess Execution ===\n")
    print("BATCH_STL_DONE", flush=True)
    sys.exit(0)
