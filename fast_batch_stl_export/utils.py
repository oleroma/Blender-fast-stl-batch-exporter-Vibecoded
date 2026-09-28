"""
Utilities Module
Contains helper functions for JSON serialization and deep-copying Blender properties.
These are critical for the clipboard functionality and preset saving/loading.
"""
import bpy

# ==============================================================================
# === DICTIONARY EXPORT (SERIALIZATION) ===
# ==============================================================================

def copy_val_to_dict(v):
    # Converts a BatchSTLValue property group into a native Python dictionary
    # Handles dynamic attribute retrieval safely using getattr for optional properties
    return {
        "value_bool": v.value_bool,
        "value_int": v.value_int,
        "value_float": v.value_float,
        "value_string": v.value_string,
        "value_menu": v.value_menu,
        "use_tag": v.use_tag,
        "tag": v.tag,
        "use_dir": v.use_dir,
        "use_sweep": getattr(v, "use_sweep", False),
        "sweep_range": getattr(v, "sweep_range", "")
    }

def copy_input_to_dict(i):
    # Recursively serializes a BatchSTLInput and all its associated values
    return {
        "name": i.name,
        "override_type": i.override_type,
        "values": [copy_val_to_dict(v) for v in i.values]
    }

def copy_node_to_dict(n):
    # Serializes a Node target and its inputs
    return {
        "name": n.name,
        "inputs": [copy_input_to_dict(i) for i in n.inputs]
    }

def copy_ng_to_dict(ng):
    # Serializes a NodeGroup mapping. Extracts the name of the datablock pointer.
    return {
        "group": ng.group_ptr.name if ng.group_ptr else "",
        "nodes": [copy_node_to_dict(n) for n in ng.nodes]
    }

def copy_collection_to_dict(c):
    # Serializes a Collection mapping, including visibility filters and local overrides
    return {
        "collection_name": c.collection_ptr.name if c.collection_ptr else "",
        "use_tag": c.use_tag,
        "tag": c.tag,
        "sub_path": c.sub_path,
        "use_filter": getattr(c, "use_filter", False),
        "excluded_objects": [e.name for e in c.excluded_objects],
        "nodegroups": [copy_ng_to_dict(ng) for ng in c.nodegroups]
    }

def copy_preset_to_dict(src):
    # Serializes an entire Export Preset, including global groups and local collections
    return {
        "name": src.name,
        "preset_prefix": src.preset_prefix,
        "nodegroups": [copy_ng_to_dict(ng) for ng in src.nodegroups],
        "collections": [copy_collection_to_dict(c) for c in src.collections]
    }

# ==============================================================================
# === DICTIONARY IMPORT (DESERIALIZATION) ===
# ==============================================================================

def paste_val_from_dict(new_v, data):
    # Maps primitive dictionary values back into Blender PropertyGroup attributes
    for k, v in data.items():
        setattr(new_v, k, v)

def paste_input_from_dict(new_i, data):
    # Reconstructs an input socket mapping
    new_i.name = data["name"]
    # Fallback to 'FLOAT' if the override type is missing from older JSON versions
    new_i.override_type = data.get("override_type", 'FLOAT')
    # Iteratively add new elements to the CollectionProperty
    for v_data in data.get("values", []):
        paste_val_from_dict(new_i.values.add(), v_data)

def paste_node_from_dict(new_n, data):
    # Reconstructs a Node target
    new_n.name = data["name"]
    for i_data in data.get("inputs", []):
        paste_input_from_dict(new_n.inputs.add(), i_data)

def paste_ng_from_dict(new_ng, data):
    # Reconstructs a NodeGroup mapping and resolves the actual Blender datablock pointer by name
    g_name = data.get("group", "")
    new_ng.group_ptr = bpy.data.node_groups.get(g_name) if g_name else None
    for n_data in data.get("nodes", []):
        paste_node_from_dict(new_ng.nodes.add(), n_data)

def paste_collection_from_dict(new_c, data):
    # Reconstructs a Collection mapping and resolves the collection pointer
    c_name = data.get("collection_name", "")
    new_c.collection_ptr = bpy.data.collections.get(c_name) if c_name else None
    new_c.use_tag = data.get("use_tag", True)
    new_c.tag = data.get("tag", "")
    new_c.sub_path = data.get("sub_path", "")
    new_c.use_filter = data.get("use_filter", False)

    # Rebuild the exclusion list
    for obj_name in data.get("excluded_objects", []):
        new_c.excluded_objects.add().name = obj_name

    # Rebuild nested local NodeGroups
    for ng_data in data.get("nodegroups", []):
        paste_ng_from_dict(new_c.nodegroups.add(), ng_data)

def paste_preset_from_dict(new_p, data):
    # Restores an entire preset hierarchy from a JSON dictionary
    new_p.name = data.get("name", "Imported Preset")
    new_p.preset_prefix = data.get("preset_prefix", "")
    for ng_data in data.get("nodegroups", []):
        paste_ng_from_dict(new_p.nodegroups.add(), ng_data)
    for c_data in data.get("collections", []):
        paste_collection_from_dict(new_p.collections.add(), c_data)
