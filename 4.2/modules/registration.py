"""Registration order for the modular v1.1.3 add-on."""

import bpy

from .core import (
    _PROPERTY_TOOLTIP_TRANSLATIONS,
    _rbs_deferred_repair,
    _rbs_depsgraph_update_post,
    _rbs_frame_change_post,
    _rbs_load_post,
    _update_static_ik_helpers,
)
from .properties import AYARBS_ChainPreset, AYARBS_Settings
from .ik import (
    AYARBS_OT_create_ik_chain,
    AYARBS_OT_remove_ik,
    AYARBS_OT_remove_ik_selected,
)
from .chain import (
    AYARBS_OT_build_chain,
    AYARBS_OT_reset_body_positions,
    AYARBS_OT_optimize_baked_preview,
    AYARBS_OT_create_damped_follow,
    AYARBS_OT_reset_chain_defaults,
    AYARBS_OT_add_chain_preset,
    AYARBS_OT_remove_chain_preset,
    AYARBS_OT_toggle_body_proxies,
    AYARBS_OT_remove_chain,
    AYARBS_OT_remove_chain_selected,
)
from .colliders import (
    AYARBS_OT_add_colliders,
    AYARBS_OT_toggle_colliders,
    AYARBS_OT_remove_colliders,
    AYARBS_OT_remove_colliders_selected,
)
from .rotation_transfer import (
    AYARBS_OT_add_rotation_transfer,
    AYARBS_OT_remove_rotation_transfer,
    AYARBS_OT_remove_all_rotation_transfer,
)
from .panel import AYARBS_PT_panel


CLASSES = (
    AYARBS_ChainPreset,
    AYARBS_Settings,
    AYARBS_OT_create_ik_chain,
    AYARBS_OT_build_chain,
    AYARBS_OT_reset_body_positions,
    AYARBS_OT_optimize_baked_preview,
    AYARBS_OT_create_damped_follow,
    AYARBS_OT_reset_chain_defaults,
    AYARBS_OT_add_chain_preset,
    AYARBS_OT_remove_chain_preset,
    AYARBS_OT_remove_chain_selected,
    AYARBS_OT_add_colliders,
    AYARBS_OT_toggle_colliders,
    AYARBS_OT_toggle_body_proxies,
    AYARBS_OT_add_rotation_transfer,
    AYARBS_OT_remove_rotation_transfer,
    AYARBS_OT_remove_all_rotation_transfer,
    AYARBS_OT_remove_chain,
    AYARBS_OT_remove_colliders_selected,
    AYARBS_OT_remove_colliders,
    AYARBS_OT_remove_ik,
    AYARBS_OT_remove_ik_selected,
    AYARBS_PT_panel,
)


_OWN_PACKAGE_PREFIXES = (
    "RigidBodySimulation",
    "rigid_body_simulation",
)


def _is_own_class(cls):
    """Return whether an RNA class belongs to this add-on's package."""
    module = str(getattr(cls, "__module__", "") or "")
    if any(module == prefix or module.startswith(prefix + ".") for prefix in _OWN_PACKAGE_PREFIXES):
        return True
    # Blender 4.2+ extensions are imported below
    # ``bl_ext.<repository>.<extension_id>``. The repository name is user
    # configurable, so match the stable extension id instead of a fixed
    # repository prefix.
    return module.startswith("bl_ext.") and (
        ".rigid_body_simulation" in module
        and (
            module.endswith(".rigid_body_simulation")
            or ".rigid_body_simulation." in module
        )
        or ".RigidBodySimulation" in module
        and (
            module.endswith(".RigidBodySimulation")
            or ".RigidBodySimulation." in module
        )
    )


def _is_own_handler(handler):
    """Return whether a Blender handler belongs to this add-on package."""
    return _is_own_class(handler)


def _find_registered_class(cls):
    """Find a registered RNA class, including Operator RNA aliases."""
    class_names = [cls.__name__]
    bl_idname = str(getattr(cls, "bl_idname", "") or "")
    if "." in bl_idname:
        operator_group, operator_name = bl_idname.split(".", 1)
        class_names.insert(0, f"{operator_group.upper()}_OT_{operator_name}")

    property_group = getattr(bpy.types, "PropertyGroup", None)
    getter = getattr(property_group, "bl_rna_get_subclass_py", None)
    for class_name in class_names:
        registered = getattr(bpy.types, class_name, None)
        if registered is not None:
            return registered
        if getter is not None:
            try:
                registered = getter(class_name, None)
            except (RuntimeError, TypeError, ValueError):
                registered = None
            if registered is not None:
                return registered
    return None


def _remove_own_handlers():
    """Remove handlers left by an earlier hot-reloaded copy of this add-on."""
    handler_lists = (
        bpy.app.handlers.load_post,
        bpy.app.handlers.frame_change_post,
        bpy.app.handlers.depsgraph_update_post,
    )
    for handlers in handler_lists:
        for handler in tuple(handlers):
            if _is_own_handler(handler):
                try:
                    handlers.remove(handler)
                except (ValueError, RuntimeError):
                    pass

    try:
        if bpy.app.timers.is_registered(_rbs_deferred_repair):
            bpy.app.timers.unregister(_rbs_deferred_repair)
    except (AttributeError, RuntimeError, ValueError):
        pass


def _remove_scene_property():
    """Remove only this add-on's Scene pointer property, if present."""
    try:
        property_def = bpy.types.Scene.bl_rna.properties.get("rbs_settings")
        fixed_type = getattr(property_def, "fixed_type", None)
        fixed_name = str(
            getattr(fixed_type, "__name__", None)
            or getattr(fixed_type, "identifier", "")
            or ""
        )
        if not (
            _is_own_class(fixed_type)
            and fixed_name.startswith(("AYARBS_", "RBS_"))
        ):
            return
        del bpy.types.Scene.rbs_settings
    except (AttributeError, RuntimeError, TypeError):
        pass


def _unregister_own_stale_classes():
    """Make repeated enable/reload idempotent without touching other add-ons.

    Blender keeps RNA classes alive when an older copy was reloaded without a
    matching ``unregister()`` call. Looking up classes by their explicit AYA
    namespace lets us remove only classes owned by this add-on; no generic
    ``bl_rna`` scan or foreign ``RBS_*`` class is ever unloaded.
    """
    _remove_scene_property()
    _remove_own_handlers()
    for cls in reversed(CLASSES):
        if not cls.__name__.startswith("AYARBS_"):
            continue
        registered = _find_registered_class(cls)
        if registered is None or not _is_own_class(registered):
            continue
        try:
            bpy.utils.unregister_class(registered)
        except (RuntimeError, TypeError, ValueError):
            # The class may belong to a different reload generation or may
            # already have been removed by Blender during extension refresh.
            pass


def register():
    # A Blender extension can be enabled repeatedly without a clean Python
    # interpreter restart. Clear only our own stale RNA classes first.
    _unregister_own_stale_classes()
    addon_module = __package__.split(".")[0]
    try:
        bpy.app.translations.register(addon_module, _PROPERTY_TOOLTIP_TRANSLATIONS)
    except (AttributeError, RuntimeError, TypeError, ValueError):
        pass
    for cls in CLASSES:
        bpy.utils.register_class(cls)
    bpy.types.Scene.rbs_settings = bpy.props.PointerProperty(type=AYARBS_Settings)
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
    _remove_own_handlers()
    _remove_scene_property()
    for cls in reversed(CLASSES):
        if not cls.__name__.startswith("AYARBS_"):
            continue
        try:
            bpy.utils.unregister_class(cls)
        except (RuntimeError, TypeError, ValueError):
            pass

    try:
        bpy.app.translations.unregister(__package__.split(".")[0])
    except (AttributeError, RuntimeError, TypeError, ValueError):
        pass
