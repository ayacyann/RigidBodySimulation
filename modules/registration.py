"""Registration order for the modular v1.1.2 add-on."""

import bpy

from .core import (
    _PROPERTY_TOOLTIP_TRANSLATIONS,
    _rbs_deferred_repair,
    _rbs_depsgraph_update_post,
    _rbs_frame_change_post,
    _rbs_load_post,
    _update_static_ik_helpers,
)
from .properties import RBS_ChainPreset, RBS_Settings
from .ik import (
    RBS_OT_create_ik_chain,
    RBS_OT_remove_ik,
    RBS_OT_remove_ik_selected,
)
from .chain import (
    RBS_OT_build_chain,
    RBS_OT_reset_body_positions,
    RBS_OT_create_damped_follow,
    RBS_OT_reset_chain_defaults,
    RBS_OT_add_chain_preset,
    RBS_OT_remove_chain_preset,
    RBS_OT_toggle_body_proxies,
    RBS_OT_remove_chain,
    RBS_OT_remove_chain_selected,
)
from .colliders import (
    RBS_OT_add_colliders,
    RBS_OT_toggle_colliders,
    RBS_OT_remove_colliders,
    RBS_OT_remove_colliders_selected,
)
from .rotation_transfer import (
    RBS_OT_add_rotation_transfer,
    RBS_OT_remove_rotation_transfer,
    RBS_OT_remove_all_rotation_transfer,
)
from .panel import RBS_PT_panel


CLASSES = (
    RBS_ChainPreset,
    RBS_Settings,
    RBS_OT_create_ik_chain,
    RBS_OT_build_chain,
    RBS_OT_reset_body_positions,
    RBS_OT_create_damped_follow,
    RBS_OT_reset_chain_defaults,
    RBS_OT_add_chain_preset,
    RBS_OT_remove_chain_preset,
    RBS_OT_remove_chain_selected,
    RBS_OT_add_colliders,
    RBS_OT_toggle_colliders,
    RBS_OT_toggle_body_proxies,
    RBS_OT_add_rotation_transfer,
    RBS_OT_remove_rotation_transfer,
    RBS_OT_remove_all_rotation_transfer,
    RBS_OT_remove_chain,
    RBS_OT_remove_colliders_selected,
    RBS_OT_remove_colliders,
    RBS_OT_remove_ik,
    RBS_OT_remove_ik_selected,
    RBS_PT_panel,
)


def register():
    addon_module = __package__.split(".")[0]
    try:
        bpy.app.translations.register(addon_module, _PROPERTY_TOOLTIP_TRANSLATIONS)
    except (AttributeError, RuntimeError, TypeError, ValueError):
        pass
    for cls in CLASSES:
        bpy.utils.register_class(cls)
    bpy.types.Scene.rbs_settings = bpy.props.PointerProperty(type=RBS_Settings)
    if _rbs_load_post not in bpy.app.handlers.load_post:
        bpy.app.handlers.load_post.append(_rbs_load_post)
    if _rbs_frame_change_post not in bpy.app.handlers.frame_change_post:
        bpy.app.handlers.frame_change_post.append(_rbs_frame_change_post)
    if _update_static_ik_helpers not in bpy.app.handlers.depsgraph_update_post:
        bpy.app.handlers.depsgraph_update_post.append(_update_static_ik_helpers)
    if _rbs_depsgraph_update_post not in bpy.app.handlers.depsgraph_update_post:
        bpy.app.handlers.depsgraph_update_post.append(_rbs_depsgraph_update_post)
    _rbs_load_post(None)
    try:
        bpy.app.timers.register(_rbs_deferred_repair, first_interval=0.1)
    except (RuntimeError, ValueError):
        pass


def unregister():
    if _rbs_load_post in bpy.app.handlers.load_post:
        bpy.app.handlers.load_post.remove(_rbs_load_post)
    if _rbs_frame_change_post in bpy.app.handlers.frame_change_post:
        bpy.app.handlers.frame_change_post.remove(_rbs_frame_change_post)
    if _update_static_ik_helpers in bpy.app.handlers.depsgraph_update_post:
        bpy.app.handlers.depsgraph_update_post.remove(_update_static_ik_helpers)
    if _rbs_depsgraph_update_post in bpy.app.handlers.depsgraph_update_post:
        bpy.app.handlers.depsgraph_update_post.remove(_rbs_depsgraph_update_post)
    try:
        if bpy.app.timers.is_registered(_rbs_deferred_repair):
            bpy.app.timers.unregister(_rbs_deferred_repair)
    except (AttributeError, RuntimeError, ValueError):
        pass
    if hasattr(bpy.types.Scene, "rbs_settings"):
        del bpy.types.Scene.rbs_settings
    for cls in reversed(CLASSES):
        bpy.utils.unregister_class(cls)
    try:
        bpy.app.translations.unregister(__package__.split(".")[0])
    except (AttributeError, RuntimeError, TypeError, ValueError):
        pass
