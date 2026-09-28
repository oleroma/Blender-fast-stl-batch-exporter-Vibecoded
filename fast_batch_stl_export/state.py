"""
State Management Module
Stores global variables and runtime state to prevent circular imports
between operators and UI panels. Optimized for agentic context.
"""

# Dictionary for holding clipboard data across the addon.
# [FIX] Consolidated the two separate _clipboard declarations from the original
# monolithic file into a single, structured dictionary to prevent overwriting bugs
# during copy/paste operations across NodeGroups and Collections.
clipboard = {
    "preset": None,       # Stores a copied Export Preset dictionary
    "collection": None,   # Stores a copied Collection mapping dictionary
    "nodegroup": None     # Stores a copied NodeGroup override dictionary
}
