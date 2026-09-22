import bpy
import os
import tempfile
import subprocess
import threading
import queue

class EXPORT_OT_batch_stl_multi(bpy.types.Operator):
    bl_idname = "export_scene.batch_stl_multi"
    bl_label = "Export"
    bl_description = "Launch a headless background instance to safely evaluate and batch export the mapped collections"
    bl_options = {"REGISTER"}
    preset_index: bpy.props.IntProperty(default=-1)

    _timer = None
    process = None
    total_combos = 1

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

        # Setup secure Temp Directory
        self.temp_dir = tempfile.mkdtemp(prefix="fast_batch_stl_")
        self.temp_blend = os.path.join(self.temp_dir, "batch_stl_export_temp.blend")

        # Save uncompressed for hyper-fast background handoff
        bpy.ops.wm.save_as_mainfile(filepath=self.temp_blend, copy=True, compress=False)

        # Spawn Headless Subprocess targeting the package's __init__.py[cite: 1]
        init_path = os.path.join(os.path.dirname(__file__), "__init__.py")
        cmd = [
                    bpy.app.binary_path,
                    "-b", self.temp_blend,
                    "--python-expr",
                    f"import bpy; bpy.ops.batch_stl.run_headless_internal(preset_index={preset_idx})"
                ]

        try:
                    self.process = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)
        except Exception as e:
            self.report({'ERROR'}, f"Failed to spawn headless Blender: {e}")
            self.cleanup(context)
            return {'CANCELLED'}

        # Non-blocking stdout queue
        self.q = queue.Queue()
        def enqueue_output(out, q):
            for line in iter(out.readline, ''): q.put(line)
            out.close()

        self.t = threading.Thread(target=enqueue_output, args=(self.process.stdout, self.q))
        self.t.daemon = True
        self.t.start()

        context.scene.is_exporting = True
        context.scene.cancel_export = False
        context.scene.export_progress = 0.0
        context.scene.export_status = f"Spawning Headless Instance for '{preset.name}'..."

        self._timer = context.window_manager.event_timer_add(0.05, window=context.window)
        context.window_manager.modal_handler_add(self)
        return {'RUNNING_MODAL'}

    def modal(self, context, event):
        if context.scene.cancel_export or (event.type == 'ESC' and event.value == 'PRESS'):
            print("\n[!] Export cancelled by user. Terminating headless instance...")
            if self.process: self.process.terminate()
            self.cleanup(context)
            self.report({'WARNING'}, "Export cancelled by user.")
            return {'CANCELLED'}

        if event.type == 'TIMER':
            while True:
                try: line = self.q.get_nowait()
                except queue.Empty: break
                else:
                    line = line.strip()
                    if line.startswith("BATCH_STL_TOTAL:"):
                        try: self.total_combos = int(line.split(":")[1])
                        except Exception: pass
                    elif line.startswith("BATCH_STL_PROGRESS:"):
                        try:
                            cur = int(line.split(":")[1])
                            context.scene.export_progress = cur / max(1, self.total_combos)
                            context.scene.export_status = f"Exporting: Permutation {cur} / {self.total_combos} (Press ESC to Cancel)"
                        except Exception: pass
                    elif line:
                        print(f"[Headless] {line}")

            if self.process.poll() is not None:
                self.cleanup(context)
                if self.process.returncode == 0: self.report({'INFO'}, "Batch Export Complete.")
                else: self.report({'ERROR'}, f"Headless export failed with return code {self.process.returncode}")
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
        except Exception: pass

classes = (EXPORT_OT_batch_stl_multi,)
