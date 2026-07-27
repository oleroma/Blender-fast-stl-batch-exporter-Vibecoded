import os
import bpy
import struct


# --- FAST EXPORT FUNCTION ---

def write_fast_binary_stl(filepath, mesh, matrix_world):
    """Writes a Blender mesh directly to a Binary STL file."""
    mesh.calc_loop_triangles()
    tris = mesh.loop_triangles

    if len(tris) == 0:
        return # Skip empty meshes

    # Pre-transform vertices to world space to save time in the loop
    verts = [matrix_world @ v.co for v in mesh.vertices]

    # Calculate normal transformation matrix (inverse transpose)
    mat_norm = matrix_world.to_3x3().inverted_safe().transposed()

    with open(filepath, 'wb') as f:
        # STL Header (80 bytes)
        f.write(b'Batch STL Fast Export' + b'\x00' * 59)

        # Triangle Count (unsigned int)
        f.write(struct.pack('<I', len(tris)))

        for tri in tris:
            # Face Normal
            n = (mat_norm @ tri.normal).normalized()
            f.write(struct.pack('<3f', n.x, n.y, n.z))

            # 3 Vertices
            for loop_idx in tri.vertices:
                v = verts[loop_idx]
                f.write(struct.pack('<3f', v.x, v.y, v.z))

            # Attribute Byte Count (standard STL requirement)
            f.write(b'\x00\x00')


# --- HELPER FUNCTIONS ---

def find_layer_collection(layer_collection, collection_name):
    """Recursively search for a LayerCollection by its collection name."""
    if layer_collection.collection.name == collection_name:
        return layer_collection

    for child in layer_collection.children:
        result = find_layer_collection(child, collection_name)
        if result:
            return result

    return None

def get_enabled_objects_recursive(layer_coll):
    """Recursively gather all valid objects from a LayerCollection if it is enabled."""
    objects = []

    if layer_coll.exclude:
        return objects

    for obj in layer_coll.collection.objects:
        if obj.type in {"MESH", "CURVE", "SURFACE", "META", "FONT"}:
            objects.append(obj)

    for child in layer_coll.children:
        objects.extend(get_enabled_objects_recursive(child))

    return objects

def get_active_preset(scene):
    """Return the currently active BatchSTLExportPreset, or None if none exist."""
    presets = scene.batch_stl_presets
    index = scene.batch_stl_preset_index
    if presets and 0 <= index < len(presets):
        return presets[index]
    return None


# --- PROPERTIES ---

class BatchSTLExportItem(bpy.types.PropertyGroup):
    """Group of properties representing a single collection -> sub-path mapping."""

    collection_name: bpy.props.StringProperty(
        name="Collection",
        description="Select the root collection to export",
        default=""
    )

    sub_path: bpy.props.StringProperty(
        name="Sub-folder Path",
        description="Relative path appended to the Root Directory (e.g., 'parts/gears/')",
        default="",
    )


class BatchSTLExportPreset(bpy.types.PropertyGroup):
    """A named, self-contained set of collection -> sub-path mappings."""
    name: bpy.props.StringProperty(
        name="Preset Name",
        default="New Preset",
    )
    mappings: bpy.props.CollectionProperty(type=BatchSTLExportItem)
    mapping_index: bpy.props.IntProperty(
        name="Active Mapping Index",
        default=0,
    )


# --- PRESET OPERATORS ---

class BATCH_STL_OT_add_preset(bpy.types.Operator):
    bl_idname = "batch_stl.add_preset"
    bl_label = "Add Export Preset"
    bl_description = "Add a new, empty export preset"

    def execute(self, context):
        scene = context.scene
        preset = scene.batch_stl_presets.add()
        preset.name = f"Preset {len(scene.batch_stl_presets)}"
        scene.batch_stl_preset_index = len(scene.batch_stl_presets) - 1
        return {'FINISHED'}


class BATCH_STL_OT_remove_preset(bpy.types.Operator):
    bl_idname = "batch_stl.remove_preset"
    bl_label = "Remove Export Preset"
    bl_description = "Remove the active export preset"

    @classmethod
    def poll(cls, context):
        return len(context.scene.batch_stl_presets) > 0

    def execute(self, context):
        scene = context.scene
        scene.batch_stl_presets.remove(scene.batch_stl_preset_index)
        scene.batch_stl_preset_index = min(
            max(0, scene.batch_stl_preset_index - 1),
            len(scene.batch_stl_presets) - 1,
        )
        return {'FINISHED'}


class BATCH_STL_OT_duplicate_preset(bpy.types.Operator):
    bl_idname = "batch_stl.duplicate_preset"
    bl_label = "Duplicate Export Preset"
    bl_description = "Duplicate the active preset, copying all of its collection/sub-path mappings"

    @classmethod
    def poll(cls, context):
        return get_active_preset(context.scene) is not None

    def execute(self, context):
        scene = context.scene
        source = get_active_preset(scene)
        if source is None:
            return {'CANCELLED'}

        new_preset = scene.batch_stl_presets.add()
        new_preset.name = f"{source.name} Copy"

        for src_item in source.mappings:
            new_item = new_preset.mappings.add()
            new_item.collection_name = src_item.collection_name
            new_item.sub_path = src_item.sub_path

        scene.batch_stl_preset_index = len(scene.batch_stl_presets) - 1
        return {'FINISHED'}


class BATCH_STL_OT_move_preset(bpy.types.Operator):
    bl_idname = "batch_stl.move_preset"
    bl_label = "Move Export Preset"
    bl_description = "Move the active preset up or down"

    direction: bpy.props.EnumProperty(
        items=(
            ('UP', "Up", ""),
            ('DOWN', "Down", ""),
        )
    )

    @classmethod
    def poll(cls, context):
        return len(context.scene.batch_stl_presets) > 1

    def execute(self, context):
        scene = context.scene
        idx = scene.batch_stl_preset_index
        new_idx = idx - 1 if self.direction == 'UP' else idx + 1

        if 0 <= new_idx < len(scene.batch_stl_presets):
            scene.batch_stl_presets.move(idx, new_idx)
            scene.batch_stl_preset_index = new_idx

        return {'FINISHED'}


# --- MAPPING (COLLECTION / SUB-PATH) OPERATORS ---

class BATCH_STL_OT_add_item(bpy.types.Operator):
    bl_idname = "batch_stl.add_item"
    bl_label = "Add Export Mapping"
    bl_description = "Add a new collection to sub-path mapping to the active preset"

    @classmethod
    def poll(cls, context):
        return get_active_preset(context.scene) is not None

    def execute(self, context):
        preset = get_active_preset(context.scene)
        preset.mappings.add()
        preset.mapping_index = len(preset.mappings) - 1
        return {'FINISHED'}


class BATCH_STL_OT_remove_item(bpy.types.Operator):
    bl_idname = "batch_stl.remove_item"
    bl_label = "Remove Export Mapping"
    bl_description = "Remove the selected collection mapping from the active preset"

    @classmethod
    def poll(cls, context):
        preset = get_active_preset(context.scene)
        return preset is not None and len(preset.mappings) > 0

    def execute(self, context):
        preset = get_active_preset(context.scene)
        preset.mappings.remove(preset.mapping_index)
        preset.mapping_index = min(max(0, preset.mapping_index - 1), len(preset.mappings) - 1)
        return {'FINISHED'}


class BATCH_STL_OT_move_item(bpy.types.Operator):
    bl_idname = "batch_stl.move_item"
    bl_label = "Move Export Mapping"
    bl_description = "Move the selected collection mapping up or down"

    direction: bpy.props.EnumProperty(
        items=(
            ('UP', "Up", ""),
            ('DOWN', "Down", ""),
        )
    )

    @classmethod
    def poll(cls, context):
        preset = get_active_preset(context.scene)
        return preset is not None and len(preset.mappings) > 1

    def execute(self, context):
        preset = get_active_preset(context.scene)
        idx = preset.mapping_index
        new_idx = idx - 1 if self.direction == 'UP' else idx + 1

        if 0 <= new_idx < len(preset.mappings):
            preset.mappings.move(idx, new_idx)
            preset.mapping_index = new_idx

        return {'FINISHED'}


# --- FAST EXPORT OPERATOR ---

class EXPORT_OT_batch_stl_multi(bpy.types.Operator):
    bl_idname = "export_scene.batch_stl_multi"
    bl_label = "Batch Export STLs"
    bl_description = "Export the active preset's mapped collections to the Root Directory + Sub-path"
    bl_options = {"REGISTER"}

    @classmethod
    def poll(cls, context):
        return get_active_preset(context.scene) is not None

    def execute(self, context):
        scene = context.scene

        if not scene.batch_stl_root_dir:
            self.report({'ERROR'}, "Please select a Root Export Directory first.")
            return {"CANCELLED"}

        preset = get_active_preset(scene)
        if preset is None:
            self.report({'ERROR'}, "No active export preset. Add a preset first.")
            return {"CANCELLED"}

        root_dir = bpy.path.abspath(scene.batch_stl_root_dir)

        if context.active_object and context.mode != "OBJECT":
            bpy.ops.object.mode_set(mode="OBJECT")

        total_exported = 0

        # Grab the currently evaluated scene state
        depsgraph = context.evaluated_depsgraph_get()

        for item in preset.mappings:
            if not item.collection_name:
                continue

            if item.collection_name not in bpy.data.collections:
                self.report({'WARNING'}, f"Collection '{item.collection_name}' does not exist.")
                continue

            out_dir = os.path.normpath(os.path.join(root_dir, item.sub_path))
            os.makedirs(out_dir, exist_ok=True)

            root_layer_coll = find_layer_collection(context.view_layer.layer_collection, item.collection_name)

            if not root_layer_coll:
                self.report({'WARNING'}, f"Collection '{item.collection_name}' not found in View Layer.")
                continue

            objects_to_export = get_enabled_objects_recursive(root_layer_coll)
            objects_to_export = list(set(objects_to_export))

            for obj in objects_to_export:
                # Get the evaluated object based on viewport data
                obj_eval = obj.evaluated_get(depsgraph)

                try:
                    # Bake modifiers virtually to a raw mesh
                    mesh = obj_eval.to_mesh()
                except RuntimeError:
                    continue # Skip objects that fail to generate a mesh (e.g., Empties)

                if not mesh:
                    continue

                filepath = os.path.join(out_dir, f"{bpy.path.clean_name(obj.name)}.stl")

                # Write fast binary STL, completely bypassing bpy.ops
                write_fast_binary_stl(filepath, mesh, obj.matrix_world)

                # Clear the temporary mesh from memory
                obj_eval.to_mesh_clear()

                total_exported += 1

        self.report(
            {'INFO'},
            f"Successfully exported {total_exported} STLs using preset '{preset.name}' to {root_dir}",
        )
        return {"FINISHED"}


# --- UI LISTS ---

class BATCH_STL_UL_presets(bpy.types.UIList):
    """Custom UI List to draw the available export presets."""
    def draw_item(self, context, layout, data, item, icon, active_data, active_propname, index):
        if self.layout_type in {'DEFAULT', 'COMPACT'}:
            row = layout.row(align=True)
            row.prop(item, "name", text="", emboss=False, icon='PRESET')
            row.label(text=f"{len(item.mappings)} mapping(s)")
        elif self.layout_type == 'GRID':
            layout.alignment = 'CENTER'
            layout.label(text="", icon='PRESET')


class BATCH_STL_UL_items(bpy.types.UIList):
    """Custom UI List to draw the collection/sub-path pairs for the active preset."""
    def draw_item(self, context, layout, data, item, icon, active_data, active_propname, index):
        if self.layout_type in {'DEFAULT', 'COMPACT'}:
            if item.collection_name:
                layout.label(text=item.collection_name, icon='OUTLINER_COLLECTION')
                if item.sub_path:
                    layout.label(text=f"/{item.sub_path}", icon='FILE_FOLDER')
            else:
                layout.label(text="Assign a Collection", icon='ERROR')
        elif self.layout_type == 'GRID':
            layout.alignment = 'CENTER'
            layout.label(text="", icon='OUTLINER_COLLECTION')


# --- UI PANEL ---

class VIEW3D_PT_batch_export_stl_multi(bpy.types.Panel):
    bl_space_type = "VIEW_3D"
    bl_region_type = "UI"
    bl_category = "Export"
    bl_label = "Fast Batch STL Export"

    def draw(self, context):
        layout = self.layout
        scene = context.scene

        # Global Root Directory at the top (shared by every preset)
        layout.prop(scene, "batch_stl_root_dir")
        layout.separator()

        # --- Presets section ---
        layout.label(text="Export Presets:", icon='PRESET')
        row = layout.row()
        row.template_list(
            "BATCH_STL_UL_presets", "",
            scene, "batch_stl_presets",
            scene, "batch_stl_preset_index",
            rows=3,
        )

        col = row.column(align=True)
        col.operator("batch_stl.add_preset", icon='ADD', text="")
        col.operator("batch_stl.remove_preset", icon='REMOVE', text="")
        col.separator()
        col.operator("batch_stl.move_preset", icon='TRIA_UP', text="").direction = 'UP'
        col.operator("batch_stl.move_preset", icon='TRIA_DOWN', text="").direction = 'DOWN'
        col.separator()
        col.operator("batch_stl.duplicate_preset", icon='DUPLICATE', text="")

        layout.separator()

        active_preset = get_active_preset(scene)

        if active_preset is None:
            layout.label(text="Add a preset to begin.", icon='INFO')
            return

        # --- Mappings section (belongs to the active preset only) ---
        box = layout.box()
        box.label(text=f"Mappings for '{active_preset.name}':", icon='FILE_FOLDER')

        row = box.row()
        row.template_list(
            "BATCH_STL_UL_items", "",
            active_preset, "mappings",
            active_preset, "mapping_index",
            rows=4,
        )

        col = row.column(align=True)
        col.operator("batch_stl.add_item", icon='ADD', text="")
        col.operator("batch_stl.remove_item", icon='REMOVE', text="")
        col.separator()
        col.operator("batch_stl.move_item", icon='TRIA_UP', text="").direction = 'UP'
        col.operator("batch_stl.move_item", icon='TRIA_DOWN', text="").direction = 'DOWN'

        if active_preset.mappings and 0 <= active_preset.mapping_index < len(active_preset.mappings):
            active_item = active_preset.mappings[active_preset.mapping_index]

            sub_box = box.box()
            sub_box.prop_search(active_item, "collection_name", bpy.data, "collections", text="Collection")
            sub_box.prop(active_item, "sub_path")

        layout.separator()
        layout.operator(
            EXPORT_OT_batch_stl_multi.bl_idname,
            text=f"Export Active Preset ({active_preset.name})",
            icon="EXPORT",
        )


# --- REGISTRATION ---

classes = (
    BatchSTLExportItem,
    BatchSTLExportPreset,
    BATCH_STL_UL_items,
    BATCH_STL_UL_presets,
    BATCH_STL_OT_add_item,
    BATCH_STL_OT_remove_item,
    BATCH_STL_OT_move_item,
    BATCH_STL_OT_add_preset,
    BATCH_STL_OT_remove_preset,
    BATCH_STL_OT_move_preset,
    BATCH_STL_OT_duplicate_preset,
    EXPORT_OT_batch_stl_multi,
    VIEW3D_PT_batch_export_stl_multi,
)

def register():
    for cls in classes:
        bpy.utils.register_class(cls)

    bpy.types.Scene.batch_stl_root_dir = bpy.props.StringProperty(
        name="Root Export Directory",
        description="The base directory. Preset sub-paths will be added to this.",
        default="//",
        subtype="DIR_PATH",
    )

    bpy.types.Scene.batch_stl_presets = bpy.props.CollectionProperty(type=BatchSTLExportPreset)
    bpy.types.Scene.batch_stl_preset_index = bpy.props.IntProperty(name="Active Preset Index", default=0)

def unregister():
    for cls in reversed(classes):
        bpy.utils.unregister_class(cls)

    del bpy.types.Scene.batch_stl_root_dir
    del bpy.types.Scene.batch_stl_presets
    del bpy.types.Scene.batch_stl_preset_index
