"""Rigid Body Simulation for selected armature bone chains.

The add-on creates ordinary Blender rigid-body objects and constraints, so the
result remains editable with Blender's native rigid-body tools.  The active
pose bone is treated as the animated root and is deliberately never converted
to a dynamic rigid body.
"""

import bpy
from bpy.app.handlers import persistent
from mathutils import Matrix, Quaternion, Vector
from math import pi, sin, cos
from bpy.props import (
    BoolProperty,
    CollectionProperty,
    EnumProperty,
    FloatProperty,
    PointerProperty,
    StringProperty,
)


bl_info = {
    "name": "Rigid Body Simulation",
    "author": "ayacyann",
    "version": (1, 1, 3),
    "blender": (3, 6, 0),
    "location": "View3D > Sidebar > Rigid Body Simulation",
    "description": "Create rigid-body simulation proxies for selected pose bones",
    "category": "Animation",
}


ROOT_COLLECTION = "RBS_Physics"
BODIES_COLLECTION = "RBS_Bodies"
COLLIDERS_COLLECTION = "RBS_Colliders"
IK_COLLECTION = "RBS_IK_Controls"
# IK shape metadata uses a fixed base size; the visible custom-shape scale is
# controlled by AYARBS_Settings.ik_shape_size in the sidebar panel.
IK_SHAPE_SIZE = 0.45
PREFIX = "RBS_"
CONSTRAINT_PREFIX = "RBS_CONSTRAINT_"
ROTATION_TRANSFER_PREFIX = "RBS_ROTATION_TRANSFER_"
DAMPED_FOLLOW_PREFIX = "RBS_DAMPED_FOLLOW_"
BODY_LIMIT_DISTANCE_PREFIX = "RBS_LIMIT_DISTANCE_"
LIMIT_TARGET_PREFIX = "RBS_LIMIT_TARGET_"
# Kept for migration of files made by the earlier object-level
# LIMIT_DISTANCE implementation. New chains use native rigid-body joints.
REFERENCE_ARMATURE_PREFIX = "RBS_REFERENCE_"
# Kept as metadata for older files. New chains do not expose a target
# head/tail because the MMD-style joint has no armature target.
BODY_LIMIT_DISTANCE_HEAD_TAIL = 0.5
BODY_LIMIT_DISTANCE_DEFAULT = 0.0001
SPRING_PARAMETER_SCALE = 10000.0
MAX_COLLISION_BITS = 20
# PMX/MMD rigid-body groups occupy Blender collections 0-15. Reserve the
# final Blender collection for this add-on so native MMDTools bodies do not
# collide with generated RBS bodies or colliders.
RBS_COLLISION_BIT = MAX_COLLISION_BITS - 1
RBS_COLLISION_BITS = tuple(index == RBS_COLLISION_BIT for index in range(MAX_COLLISION_BITS))
ALL_BITS = tuple(True for _ in range(MAX_COLLISION_BITS))
NO_BITS = tuple(False for _ in range(MAX_COLLISION_BITS))
BODY_PROXY_KINDS = frozenset({"BODY"})
_UPDATING_STATIC_IK = False
_UPDATING_INITIAL_FOLLOW = False
_UPDATING_DYNAMIC_ROOT_ANCHORS = False
_UPDATING_ROOT_FOLLOW_TARGETS = False
_DYNAMIC_ROOT_ANCHOR_NAMES = set()
_ROOT_FOLLOW_TARGET_NAMES = set()
_STATIC_IK_HELPER_NAMES = set()
_FOLLOW_RELEASE_NAMES = set()


def _is_chinese_ui(context=None):
    """Return True for Blender's Simplified or Traditional Chinese UI."""
    ctx = context or getattr(bpy, "context", None)
    preferences = getattr(ctx, "preferences", None)
    view = getattr(preferences, "view", None)
    language = str(getattr(view, "language", "en_US"))
    return language in {"zh_HANS", "zh_HANT", "zh_CN", "zh_TW"} or language.startswith("zh_")


def _ui_text(context, chinese, english):
    """Use the add-on's Simplified Chinese text for both Chinese UI modes."""
    return chinese if _is_chinese_ui(context) else english


def _migrate_spring_parameter_scale():
    """Convert pre-scale spring values to the 1-100 panel scale once."""
    for scene in getattr(bpy.data, "scenes", ()):
        settings = getattr(scene, "rbs_settings", None)
        if settings is None:
            continue
        targets = [settings]
        targets.extend(getattr(settings, "chain_presets", ()) or ())
        for item in targets:
            for name in ("spring_stiffness", "spring_damping"):
                try:
                    value = float(getattr(item, name))
                except (AttributeError, TypeError, ValueError):
                    continue
                if value > 100.0:
                    value /= SPRING_PARAMETER_SCALE
                value = min(max(value, 1.0), 100.0)
                try:
                    setattr(item, name, value)
                except (AttributeError, TypeError, ValueError):
                    pass


# Property descriptions are written in English so Blender's default locale
# has useful tooltips without requiring a translation table.  The add-on
# registers the Chinese entries below at runtime; both Chinese locales use
# the same simplified wording to match the panel labels.
_PROPERTY_TOOLTIPS = {
    "Body Radius": "刚体半径：每个刚体代理围绕对应骨骼的半径。",
    "Body Length Scale": "刚体长度比例：刚体代理长度相对于骨骼长度的比例。",
    "Mass": "质量：动态刚体的质量，数值越大越不容易被推动。",
    "Linear Damping": "线性阻尼：抑制刚体平移速度的阻尼强度。",
    "Angular Damping": "角阻尼：抑制刚体旋转速度的阻尼强度。",
    "Root Follow Strength": "根骨骼跟随强度：根骨骼跟随动画父物体移动的强度。",
    "Damped Follow Strength": "阻尼跟随强度：选中骨骼跟随第一根子骨骼刚体代理的约束影响。",
    "Chain Spring Stiffness": "链条弹簧刚度：面板值按 1–100 显示，创建刚体时内部乘以 10000。",
    "Chain Spring Damping": "链条弹簧阻尼：面板值按 1–100 显示，创建刚体时内部乘以 10000。",
    "Collider Radius": "碰撞体半径：骨骼碰撞体的半径或半厚度。",
    "Collider Shape": "碰撞形状：骨骼碰撞体使用的形状。",
    "Rigid-Body Chain Preset": "刚体链预设：选择一组预设的刚体链参数。",
    "Custom Preset Name": "自定义预设名称：保存用户刚体链参数时使用的名称。",
    "Show Colliders": "显示碰撞体：显示或隐藏碰撞体代理，隐藏只影响视图，物理仍然生效。",
    "Show Body Proxies": "显示刚体代理：显示或隐藏刚体代理，隐藏只影响视图，物理仍然生效。",
    "Toggle Bone Colliders": "显示或隐藏骨骼碰撞体代理。隐藏只影响视图，物理模拟仍然生效。",
    "Toggle Body Proxies": "显示或隐藏当前骨架的刚体代理。隐藏只影响视图，物理模拟仍然生效。",
}

_PROPERTY_TOOLTIP_TRANSLATIONS = {
    "zh_HANS": {("*", english): chinese for english, chinese in _PROPERTY_TOOLTIPS.items()},
    "zh_HANT": {("*", english): chinese for english, chinese in _PROPERTY_TOOLTIPS.items()},
    "zh_CN": {("*", english): chinese for english, chinese in _PROPERTY_TOOLTIPS.items()},
    "zh_TW": {("*", english): chinese for english, chinese in _PROPERTY_TOOLTIPS.items()},
}


def _current_scene():
    """Return the active scene even while Blender registers an add-on."""
    scene = getattr(bpy.context, "scene", None)
    if scene is not None:
        return scene
    return bpy.data.scenes[0] if bpy.data.scenes else None


def _is_reference_armature(armature):
    return bool(
        armature is not None
        and armature.type == "ARMATURE"
        and armature.get("rbs_reference_for")
    )


def _strip_simulation_constraints(armature):
    """Keep only user-authored pose constraints on a reference armature."""
    for pose_bone in armature.pose.bones:
        for constraint in list(pose_bone.constraints):
            if constraint.name.startswith(
                (f"{PREFIX}SIMULATION", DAMPED_FOLLOW_PREFIX)
            ):
                pose_bone.constraints.remove(constraint)


def _ensure_reference_armature(armature):
    """Return an animation-only armature used by BODY limit constraints.

    The visible armature is driven from BODY proxies.  Pointing a BODY's
    Limit Distance constraint back at that same armature creates a depsgraph
    cycle.  This copy shares the source action and user pose setup, but never
    receives generated simulation constraints.
    """
    if armature is None or armature.type != "ARMATURE":
        return armature
    if _is_reference_armature(armature):
        return armature
    name = f"{REFERENCE_ARMATURE_PREFIX}{armature.name}"
    reference = bpy.data.objects.get(name)
    if reference is None or reference.type != "ARMATURE":
        reference = armature.copy()
        reference.data = armature.data.copy()
        reference.name = name
        reference.data.name = f"{name}_DATA"
        for collection in list(reference.users_collection):
            collection.objects.unlink(reference)
        scene = _current_scene()
        if scene is not None:
            scene.collection.objects.link(reference)
    # Object.copy() normally carries animation data, but assigning the action
    # explicitly also repairs references made by early Blender versions.
    if armature.animation_data is not None and armature.animation_data.action is not None:
        reference.animation_data_create()
        reference.animation_data.action = armature.animation_data.action
    reference.matrix_world = armature.matrix_world.copy()
    reference.hide_viewport = True
    reference.hide_render = True
    try:
        reference.hide_set(True)
    except RuntimeError:
        pass
    reference["rbs_reference_for"] = armature.name
    reference["rbs_generated"] = True
    reference["rbs_kind"] = "REFERENCE_ARMATURE"
    _strip_simulation_constraints(reference)
    return reference

# These are deliberately kept as data rather than hidden operator branches so
# the same values are used by the menu, the add-preset operator, and tests.
_CHAIN_PRESET_FIELDS = (
    "chain_radius",
    "body_length_scale",
    "mass",
    "linear_damping",
    "angular_damping",
    "follow_strength",
    "spring_stiffness",
    "spring_damping",
)
_BUILTIN_CHAIN_PRESETS = {
    "DEFAULT": {
        "label": "默认",
        "chain_radius": 0.01,
        "body_length_scale": 0.85,
        "mass": 0.5,
        "linear_damping": 0.85,
        "angular_damping": 0.45,
        "follow_strength": 0.5,
        "spring_stiffness": 10.0,
        "spring_damping": 7.5,
    },
    "SKIRT": {
        "label": "裙摆",
        "chain_radius": 0.01,
        "body_length_scale": 0.85,
        "mass": 0.5,
        "linear_damping": 0.85,
        "angular_damping": 0.45,
        "follow_strength": 0.5,
        "spring_stiffness": 10.0,
        "spring_damping": 7.5,
    },
    "HAIR": {
        "label": "头发",
        "chain_radius": 0.02,
        "body_length_scale": 0.80,
        "mass": 1.5,
        "linear_damping": 0.95,
        "angular_damping": 0.55,
        "follow_strength": 0.5,
        "spring_stiffness": 15.0,
        "spring_damping": 10.0,
    },
    "RIBBON": {
        "label": "飘带",
        "chain_radius": 0.005,
        "body_length_scale": 0.85,
        "mass": 0.25,
        "linear_damping": 0.75,
        "angular_damping": 0.65,
        "follow_strength": 0.5,
        "spring_stiffness": 7.5,
        "spring_damping": 5.0,
    },
}

# Scene presets can be used to tune the three built-in entries without
# editing the add-on source.  Names are matched after lower-casing and
# removing separators, so both ``qun bai`` and ``qunbai`` work.
_BUILTIN_PRESET_ALIASES = {
    "DEFAULT": {"default", "默认", "moren"},
    "SKIRT": {"skirt", "qunbai", "裙摆", "裙擺"},
    "HAIR": {"hair", "toufa", "头发", "頭髮"},
    "RIBBON": {"ribbon", "piaodai", "飘带", "飄帶"},
}


def _normalize_preset_name(name):
    """Normalize user-entered preset names for stable alias matching."""
    return "".join(str(name or "").strip().lower().split()).replace("_", "").replace("-", "")


def _builtin_override(settings, key):
    """Return a scene preset that overrides a built-in entry, if present."""
    aliases = {_normalize_preset_name(value) for value in _BUILTIN_PRESET_ALIASES.get(key, ())}
    for index, preset in enumerate(getattr(settings, "chain_presets", ())):
        if _normalize_preset_name(getattr(preset, "preset_name", "")) in aliases:
            return index, preset
    return -1, None


def _collection(name, parent=None):
    """Get or create a collection and make sure it is linked to *parent*."""
    coll = bpy.data.collections.get(name)
    if coll is None:
        coll = bpy.data.collections.new(name)
    scene = _current_scene()
    owner = parent if parent is not None else scene.collection
    if owner.children.get(coll.name) is None:
        owner.children.link(coll)
    return coll


def physics_collections():
    root = _collection(ROOT_COLLECTION)
    bodies = _collection(BODIES_COLLECTION, root)
    colliders = _collection(COLLIDERS_COLLECTION, root)
    scene = _current_scene()
    world = scene.rigidbody_world if scene is not None else None
    if world is not None:
        # A single parent collection makes Blender evaluate both bodies and
        # passive colliders in the same rigid-body world.
        try:
            world.collection = root
        except (AttributeError, TypeError):
            pass
    return root, bodies, colliders


def _move_to_collection(obj, target):
    if target.objects.get(obj.name) is None:
        target.objects.link(obj)
    for coll in list(obj.users_collection):
        if coll != target:
            coll.objects.unlink(obj)


def _link_to_physics_root(obj):
    """Link a generated physics object directly to the world collection.

    Blender versions differ in whether a rigid-body world evaluates nested
    child collections consistently.  Keeping the object in its descriptive
    subcollection and linking it directly to ``RBS_Physics`` makes the world
    membership explicit without changing the outliner organization.
    """
    root = bpy.data.collections.get(ROOT_COLLECTION) or _collection(ROOT_COLLECTION)
    if root.objects.get(obj.name) is None:
        root.objects.link(obj)


def _migrate_generated_physics_objects(armature_name=None):
    """Bring proxies from older files into the active rigid-body world.

    Versions before the shared ``RBS_Physics`` collection could leave an
    existing body or collider only in a nested descriptive collection.  The
    object remains visible in the Outliner, but Blender versions differ in
    whether Bullet evaluates that nested collection when it is assigned as
    the rigid-body world's root.  Migrate all generated physics objects when
    an operator touches the scene so opening an old file does not require
    rebuilding every chain and collider.
    """
    root = bpy.data.collections.get(ROOT_COLLECTION) or _collection(ROOT_COLLECTION)
    kinds = BODY_PROXY_KINDS | {
        "COLLIDER", "ANCHOR", "DRIVER", "CONSTRAINT", "LIMIT_TARGET"
    }
    for obj in bpy.data.objects:
        kind = obj.get("rbs_kind")
        if not obj.get("rbs_generated"):
            # Very early builds did not write the custom metadata, but their
            # generated object names still used the stable RBS_* prefixes.
            if obj.name.startswith(f"{PREFIX}BODY_") and obj.rigid_body is not None:
                kind = "BODY"
            elif obj.name.startswith(f"{PREFIX}COLLIDER_") and obj.rigid_body is not None:
                kind = "COLLIDER"
            elif obj.name.startswith(f"{PREFIX}ANCHOR_") and obj.rigid_body is not None:
                kind = "ANCHOR"
            elif obj.name.startswith(f"{PREFIX}DRIVER_"):
                kind = "DRIVER"
            elif obj.name.startswith(CONSTRAINT_PREFIX) and obj.rigid_body_constraint is not None:
                kind = "CONSTRAINT"
            else:
                kind = None
        if kind not in kinds:
            continue
        if armature_name is not None and obj.get("rbs_armature") != armature_name:
            continue
        if not obj.get("rbs_generated"):
            obj["rbs_generated"] = True
            obj["rbs_kind"] = kind
        if root.objects.get(obj.name) is None:
            root.objects.link(obj)


def _remove_constraint_rigid_bodies():
    """Non-physics helpers must not also be active collision bodies."""
    removed = 0
    for obj in list(bpy.data.objects):
        if obj.get("rbs_kind") not in {"CONSTRAINT", "IK_SHAPE"} or obj.rigid_body is None:
            continue
        _activate(obj)
        try:
            bpy.ops.rigidbody.object_remove()
            removed += 1
        except RuntimeError:
            # The caller may be outside Object mode; the next physics repair
            # pass will retry once Blender has switched modes.
            pass
    return removed


def _remove_nonphysics_rigid_bodies():
    """Remove accidental rigid bodies from plugin display/helper objects.

    MMDTools baking can operate on the active collection and may attach a
    rigid-body component to a mesh that happens to be inside
    ``RBS_IK_Controls``. IK custom-shape meshes and constraint helpers are
    display-only; removing their rigid-body component is safe and leaves the
    mesh datablock intact. Generated bodies/colliders/anchors remain untouched.
    """
    restore_mode = None
    active_object = bpy.context.view_layer.objects.active
    if getattr(bpy.context, "mode", "OBJECT") != "OBJECT":
        try:
            if active_object is not None and active_object.type == "ARMATURE":
                restore_mode = active_object.mode
                bpy.ops.object.mode_set(mode="OBJECT")
            else:
                return 0
        except (RuntimeError, TypeError):
            return 0
    ik_collection = bpy.data.collections.get(IK_COLLECTION)
    ik_objects = set(ik_collection.all_objects) if ik_collection is not None else set()
    candidates = []
    for obj in list(bpy.data.objects):
        if obj.rigid_body is None:
            continue
        kind = obj.get("rbs_kind")
        generated_helper = kind in {"CONSTRAINT", "DRIVER", "IK_SHAPE"}
        in_ik_controls = obj in ik_objects
        if generated_helper or in_ik_controls:
            candidates.append(obj)
    if not candidates:
        if restore_mode is not None and active_object is not None:
            try:
                bpy.ops.object.mode_set(mode=restore_mode)
            except (RuntimeError, TypeError):
                pass
        return 0

    active = bpy.context.view_layer.objects.active
    selected_names = [obj.name for obj in bpy.context.selected_objects]
    removed = 0
    try:
        for obj in candidates:
            if bpy.data.objects.get(obj.name) is None or obj.rigid_body is None:
                continue
            _activate(obj)
            try:
                bpy.ops.rigidbody.object_remove()
                removed += 1
            except RuntimeError:
                pass
    finally:
        for obj in bpy.context.selected_objects:
            obj.select_set(False)
        for name in selected_names:
            selected = bpy.data.objects.get(name)
            if selected is not None:
                selected.select_set(True)
        if active is not None and bpy.data.objects.get(active.name) is not None:
            bpy.context.view_layer.objects.active = active
        if (
            restore_mode is not None
            and active is not None
            and bpy.data.objects.get(active.name) is not None
        ):
            try:
                bpy.ops.object.mode_set(mode=restore_mode)
            except (RuntimeError, TypeError):
                pass
    return removed
    scene = _current_scene()
    world = scene.rigidbody_world if scene is not None else None
    if world is not None:
        try:
            world.collection = root
        except (AttributeError, TypeError):
            pass


def _activate(obj):
    for other in bpy.context.selected_objects:
        other.select_set(False)
    obj.select_set(True)
    bpy.context.view_layer.objects.active = obj


def _add_rigidbody(obj, rb_type="ACTIVE"):
    """Add a rigid body using the operator, which is the supported API."""
    _activate(obj)
    if obj.rigid_body is None:
        bpy.ops.rigidbody.object_add()
    rb = obj.rigid_body
    rb.type = rb_type
    return rb


def _set_mask(rb, mask):
    for i, value in enumerate(mask):
        rb.collision_collections[i] = bool(value)


def _configure_collision_contact(rb, radius):
    """Keep small generated proxies from tunneling through passive colliders.

    Hair/ribbon proxies can be only a few millimetres wide.  Blender leaves
    the rigid-body margin disabled by default, which makes those thin boxes
    particularly easy to skip between Bullet steps.  Use a small, explicit
    margin that remains below the proxy radius so it improves contact without
    materially changing the visible collision size.
    """
    try:
        rb.enabled = True
        # Use the evaluated mesh source for saved files created by older
        # Blender builds.  DEFORM can retain a stale shape after reload even
        # when the object itself is no longer being deformed.
        if hasattr(rb, "mesh_source"):
            rb.mesh_source = "FINAL"
        rb.use_margin = True
        # Keep the margin below a millimetre.  It is only a numerical contact
        # aid; a large default margin would make the physical proxy visibly
        # larger than the displayed collider.
        rb.collision_margin = min(max(float(radius) * 0.02, 0.00025), 0.001)
        rb.use_deactivation = False
    except (AttributeError, TypeError, ValueError):
        # These properties are present in supported Blender versions.  Keep
        # the add-on usable with older 3.x builds that expose fewer settings.
        pass


def _configure_rigidbody_world_contact():
    """Use enough Bullet substeps for thin bone proxies to register contact."""
    scene = _current_scene()
    world = scene.rigidbody_world if scene is not None else None
    if world is None:
        return
    try:
        world.substeps_per_frame = max(int(world.substeps_per_frame), 20)
        world.solver_iterations = max(int(world.solver_iterations), 20)
    except (AttributeError, TypeError, ValueError):
        pass


def _refresh_rigidbody_collision_shape(obj, shape):
    """Rebuild a collider shape after its final parent/world matrix is set.

    Blender creates the Bullet shape lazily.  When a passive collider is
    parented to a pose bone after ``rigidbody.object_add``, the first shape can
    retain the pre-parent dimensions even though the mesh is now displayed at
    the final bone transform.  Cycling through a different shape forces
    Blender to rebuild the Bullet proxy from the evaluated object dimensions.
    The requested shape is restored before the object is used by the world.
    """
    rb = getattr(obj, "rigid_body", None)
    if rb is None:
        return
    target = str(shape)
    # Avoid a no-op assignment: Blender does not rebuild a shape when the enum
    # value is unchanged.  BOX is a cheap intermediate for all supported
    # collider shapes; use SPHERE for BOX itself.
    intermediate = "SPHERE" if target == "BOX" else "BOX"
    try:
        rb.collision_shape = intermediate
        rb.collision_shape = target
    except (RuntimeError, TypeError, ValueError):
        # Older Blender versions may reject an intermediate shape for a mesh
        # that has not been evaluated yet.  The final assignment still keeps
        # the requested API value and is safe to retry after the depsgraph
        # update below.
        rb.collision_shape = target
    bpy.context.view_layer.update()


def _normalize_body_collision_masks(armature_name=None):
    """Use one shared collision mask for generated dynamic bodies.

    Older versions assigned one of Blender's 20 collision layers to each
    bone.  That made long rigs and multiple chains consume the finite layer
    budget.  Generated bodies and colliders now use a dedicated RBS collection; self-collision
    for linked neighbors is handled by their rigid-body constraints and the
    configured body length.
    """
    for obj in bpy.data.objects:
        if not obj.get("rbs_generated") or obj.get("rbs_kind") not in {"BODY", "COLLIDER"}:
            continue
        if armature_name is not None and obj.get("rbs_armature") != armature_name:
            continue
        rb = obj.rigid_body
        if rb is not None:
            _set_mask(rb, RBS_COLLISION_BITS)
        # Retain this metadata for files created by earlier releases, but
        # make it explicit that no per-bone collision layer is in use.
        obj["rbs_collision_bit"] = RBS_COLLISION_BIT


def _proxy_collision_profile(obj):
    """Return the world axis, half length and radial radius of a proxy."""
    rb = getattr(obj, "rigid_body", None)
    shape = getattr(rb, "collision_shape", "BOX") if rb is not None else "BOX"
    rotation = obj.matrix_world.to_3x3().normalized()
    local_dimensions = getattr(getattr(obj, "data", None), "dimensions", obj.dimensions)
    scale = obj.scale
    dimensions = Vector(
        (
            abs(float(local_dimensions.x) * float(scale.x)),
            abs(float(local_dimensions.y) * float(scale.y)),
            abs(float(local_dimensions.z) * float(scale.z)),
        )
    )
    if obj.get("rbs_kind") == "BODY":
        axis = (rotation @ Vector((0.0, 1.0, 0.0))).normalized()
        half_length = dimensions.y * 0.5
        radial_radius = max(dimensions.x, dimensions.z) * 0.5
    elif shape == "CAPSULE":
        axis = (rotation @ Vector((0.0, 0.0, 1.0))).normalized()
        half_length = dimensions.z * 0.5
        radial_radius = max(dimensions.x, dimensions.y) * 0.5
    elif shape == "BOX":
        axis = (rotation @ Vector((0.0, 1.0, 0.0))).normalized()
        half_length = dimensions.y * 0.5
        radial_radius = max(dimensions.x, dimensions.z) * 0.5
    else:
        axis = (rotation @ Vector((0.0, 0.0, 1.0))).normalized()
        half_length = max(dimensions) * 0.5
        radial_radius = half_length
    return axis, half_length, radial_radius


def _stabilize_body_collider_overlaps(armature_name=None):
    """Separate severe initial body/collider overlaps before Bullet steps.

    Bullet can resolve ordinary contact, but two long proxies starting at the
    exact same center have no reliable contact normal.  The resulting impulse
    can launch a skirt chain sideways or leave it on the wrong side of a leg.
    Move only the dynamic body, by the smallest cross-section offset needed to
    establish a contact normal.  The body remains driven by its generated
    pose targets after this one-time initialization adjustment.
    """
    bodies = [
        obj for obj in bpy.data.objects
        if obj.get("rbs_generated") and obj.get("rbs_kind") == "BODY"
        and (armature_name is None or obj.get("rbs_armature") == armature_name)
    ]
    colliders = [
        obj for obj in bpy.data.objects
        if obj.get("rbs_generated") and obj.get("rbs_kind") == "COLLIDER"
        and (armature_name is None or obj.get("rbs_armature") == armature_name)
    ]
    if not bodies or not colliders:
        return
    changed = False
    for body in bodies:
        body_axis, body_half_length, body_radius = _proxy_collision_profile(body)
        for collider in colliders:
            collider_axis, collider_half_length, collider_radius = _proxy_collision_profile(collider)
            delta = body.matrix_world.translation - collider.matrix_world.translation
            axial_distance = abs(delta.dot(collider_axis))
            if axial_distance > body_half_length + collider_half_length:
                continue
            radial = delta - collider_axis * delta.dot(collider_axis)
            radial_distance = radial.length
            required = body_radius + collider_radius + 1e-4
            if radial_distance >= required:
                continue
            if radial_distance > 1e-6:
                direction = radial.normalized()
            else:
                direction = collider_axis.cross(body_axis)
                if direction.length <= 1e-6:
                    direction = collider_axis.cross(Vector((0.0, 0.0, 1.0)))
                if direction.length <= 1e-6:
                    direction = collider_axis.cross(Vector((0.0, 1.0, 0.0)))
                direction.normalize()
            offset = direction * (required - radial_distance)
            rb = body.rigid_body
            if rb is not None:
                rb.kinematic = True
            body.matrix_world.translation = body.matrix_world.translation + offset
            bpy.context.view_layer.update()
            if rb is not None:
                rb.kinematic = True
            body["rbs_overlap_adjusted"] = True
            scene = _current_scene()
            body["rbs_release_frame"] = int(scene.frame_current if scene else 0) + 1
            _FOLLOW_RELEASE_NAMES.add(body.name)
            changed = True
    if changed:
        bpy.context.view_layer.update()


def _set_display_visibility(obj, visible):
    """Hide a generated proxy without disabling its rigid-body simulation."""
    obj.hide_viewport = not visible
    obj.hide_render = not visible
    try:
        obj.hide_set(not visible)
    except RuntimeError:
        # hide_set can fail when called while a different view layer is active;
        # hide_viewport still gives the requested result in that case.
        pass


def _set_body_proxy_visibility(armature_name, visible):
    """Apply visibility to all rigid-body proxies belonging to one armature."""
    for obj in bpy.data.objects:
        if (
            obj.get("rbs_generated")
            and obj.get("rbs_armature") == armature_name
            and obj.get("rbs_kind") in BODY_PROXY_KINDS
        ):
            _set_display_visibility(obj, visible)


def _ensure_world():
    scene = _current_scene()
    if scene is None:
        return
    _migrate_spring_parameter_scale()
    if scene.rigidbody_world is None:
        try:
            bpy.ops.rigidbody.world_add()
        except RuntimeError:
            # object_add normally creates the world lazily; this is only a
            # fallback for unusual contexts.
            pass
    physics_collections()
    _migrate_generated_physics_objects()
    _migrate_dynamic_root_anchors()
    _migrate_root_damped_follow_targets()
    _rebuild_follow_object_cache()
    _migrate_body_limit_distance_constraints()
    for armature in (obj for obj in bpy.data.objects if obj.type == "ARMATURE"):
        ik_bones = _generated_ik_chain_bones(armature)
        if ik_bones:
            _restore_ik_colliders(armature)
    _configure_rigidbody_world_contact()


def _prepare_initial_follow_states(scene):
    """Migrate existing chains into the safe initial-frame follow state."""
    current_frame = int(getattr(scene, "frame_current", 0))
    for anchor in list(bpy.data.objects):
        if (
            anchor.get("rbs_kind") != "ANCHOR"
            or not anchor.get("rbs_generated")
            or not anchor.get("rbs_chain_id")
        ):
            continue
        armature = bpy.data.objects.get(anchor.get("rbs_armature", ""))
        pose_bone = (
            armature.pose.bones.get(anchor.get("rbs_bone", ""))
            if armature is not None and armature.type == "ARMATURE"
            else None
        )
        if anchor.get("rbs_initial_follow_pending"):
            continue
        anchor["rbs_initial_follow_pending"] = True
        anchor["rbs_initial_follow_frame"] = current_frame
        anchor["rbs_sync_anchor_matrix"] = _flatten_matrix(anchor.matrix_world)
        _FOLLOW_RELEASE_NAMES.add(anchor.name)
        for obj in _chain_objects(armature.name if armature else "", str(anchor.get("rbs_chain_id", ""))):
            if obj.get("rbs_kind") == "BODY" and obj.rigid_body is not None:
                obj.rigid_body.kinematic = True
                obj["rbs_initial_follow_pending"] = True
                obj["rbs_initial_follow_frame"] = current_frame
                _FOLLOW_RELEASE_NAMES.add(obj.name)


def _rigid_body_cache_is_baked(scene=None):
    scene = scene or _current_scene()
    world = getattr(scene, "rigidbody_world", None) if scene is not None else None
    point_cache = getattr(world, "point_cache", None) if world is not None else None
    return bool(getattr(point_cache, "is_baked", False))


@persistent
def _rbs_load_post(_dummy):
    """Repair generated proxies when a scene made by an older build loads."""
    # During addon_enable Blender supplies a restricted context without a
    # ``scene`` attribute.  Fall back to the first loaded scene in that case.
    scene = getattr(bpy.context, "scene", None)
    if scene is None:
        try:
            scene = bpy.data.scenes[0] if bpy.data.scenes else None
        except AttributeError:
            # _RestrictData is used while register() is called from
            # addon_utils; load_post will perform the repair once normal
            # Blender data access is available.
            return
    if scene is None:
        return
    _migrate_spring_parameter_scale()
    generated = any(obj.get("rbs_generated") for obj in bpy.data.objects)
    if not generated and scene.rigidbody_world is None:
        return
    physics_collections()
    _migrate_generated_physics_objects()
    _migrate_dynamic_root_anchors()
    _migrate_root_damped_follow_targets()
    _rebuild_follow_object_cache()
    _migrate_body_limit_distance_constraints()
    _remove_nonphysics_rigid_bodies()
    _normalize_body_collision_masks()
    _prepare_initial_follow_states(scene)
    for armature in (obj for obj in bpy.data.objects if obj.type == "ARMATURE"):
        ik_bones = _generated_ik_chain_bones(armature)
        if ik_bones:
            _restore_ik_colliders(armature)
    _configure_rigidbody_world_contact()


@persistent
def _rbs_frame_change_post(scene):
    """Release overlap and initial-follow proxies after the first frame."""
    if _rigid_body_cache_is_baked(scene):
        # A baked cache already contains body transforms and kinematic state.
        # Do not scan every object or touch any rigid-body property while
        # previewing it; only refresh the lightweight root target once.
        _update_root_follow_targets()
        return
    _update_dynamic_root_anchors()
    _sync_pending_initial_follow()
    current = int(getattr(scene, "frame_current", 0))
    # This set contains only the one-frame kinematic hand-off state. Cleanup
    # of accidental helpers/bodies remains in explicit repair/reset paths.
    for name in tuple(_FOLLOW_RELEASE_NAMES):
        obj = bpy.data.objects.get(name)
        if obj is None or obj.rigid_body is None:
            _FOLLOW_RELEASE_NAMES.discard(name)
            continue
        if obj.get("rbs_overlap_adjusted"):
            release_frame = int(obj.get("rbs_release_frame", current))
            if current >= release_frame:
                obj.rigid_body.kinematic = False
                try:
                    del obj["rbs_overlap_adjusted"]
                    del obj["rbs_release_frame"]
                except KeyError:
                    pass
        if obj.get("rbs_initial_follow_pending"):
            build_frame = int(obj.get("rbs_initial_follow_frame", current))
            if current != build_frame and not obj.get("rbs_overlap_adjusted"):
                obj.rigid_body.kinematic = False
                try:
                    del obj["rbs_initial_follow_pending"]
                    del obj["rbs_initial_follow_frame"]
                except KeyError:
                    pass
        if not obj.get("rbs_overlap_adjusted") and not obj.get("rbs_initial_follow_pending"):
            _FOLLOW_RELEASE_NAMES.discard(name)
    # ROOT_FOLLOW is intentionally updated once per frame. Updating it from
    # depsgraph_update_post can run dozens of times during one viewport frame,
    # especially while a rigid-body cache is being previewed.
    _update_root_follow_targets()


def _rbs_deferred_repair():
    """Run the active-scene migration after addon registration context ends."""
    if getattr(bpy.context, "scene", None) is None:
        return 0.1
    _rbs_load_post(None)
    active = bpy.context.object
    active_name = active.name if active is not None else None
    selected_names = [obj.name for obj in bpy.context.selected_objects]
    restore_mode = None
    if active is not None and active.type == "ARMATURE":
        restore_mode = active.mode
        if restore_mode != "OBJECT":
            try:
                bpy.ops.object.mode_set(mode="OBJECT")
            except RuntimeError:
                restore_mode = None
    _remove_constraint_rigid_bodies()
    _remove_nonphysics_rigid_bodies()
    if active_name:
        original_active = bpy.data.objects.get(active_name)
        if original_active is not None:
            for obj in bpy.context.selected_objects:
                obj.select_set(False)
            original_active.select_set(True)
            bpy.context.view_layer.objects.active = original_active
            for name in selected_names:
                selected = bpy.data.objects.get(name)
                if selected is not None:
                    selected.select_set(True)
    if restore_mode is not None and restore_mode != "OBJECT":
        try:
            bpy.ops.object.mode_set(mode=restore_mode)
        except RuntimeError:
            pass
    return None


def _bone_points(armature, pose_bone):
    head = armature.matrix_world @ pose_bone.head
    tail = armature.matrix_world @ pose_bone.tail
    delta = tail - head
    length = max(delta.length, 0.001)
    return head, tail, delta, length


def _bone_world_rotation(armature, pose_bone):
    """Return the current pose bone rotation, including bone roll."""
    world_matrix = armature.matrix_world @ pose_bone.matrix
    # Normalizing removes pose/armature scale from the rotation while keeping
    # the current orientation and roll. Proxy mesh dimensions are set in world
    # units separately, so applying scale here would double-scale the body.
    return world_matrix.to_3x3().normalized().to_quaternion()


def _make_box_mesh(name, radius, length, start_y=0.0):
    """Create a box along local Y; pass ``-length / 2`` for a centered mesh."""
    r = radius
    end_y = start_y + length
    verts = [
        (-r, start_y, -r), (r, start_y, -r), (r, start_y, r), (-r, start_y, r),
        (-r, end_y, -r), (r, end_y, -r), (r, end_y, r), (-r, end_y, r),
    ]
    faces = [
        (0, 1, 2, 3), (4, 7, 6, 5), (0, 4, 5, 1),
        (1, 5, 6, 2), (2, 6, 7, 3), (4, 0, 3, 7),
    ]
    mesh = bpy.data.meshes.new(name + "_Mesh")
    mesh.from_pydata(verts, [], faces)
    mesh.update()
    return mesh


def _make_capsule_mesh(name, radius, length, start_y=0.0, segments=16, hemisphere_rings=4):
    """Create a capsule along local +Z for Blender's CAPSULE rigid-body shape.

    ``length`` is the cylinder section length.  The caller supplies a
    centered ``start_y`` for backward compatibility with older generated
    files; the resulting mesh is converted to local Z before it is returned.
    """
    radius = max(radius, 0.001)
    length = max(length, 0.001)
    ring_specs = []
    # Lower hemisphere, from the lower pole to the head equator.
    for i in range(1, hemisphere_rings):
        angle = -pi * 0.5 + (pi * 0.5) * (i / hemisphere_rings)
        ring_specs.append((radius * sin(angle), radius * cos(angle)))
    ring_specs.append((0.0, radius))
    # Cylinder section ends at the tail equator.
    ring_specs.append((length, radius))
    # Upper hemisphere, from the tail equator to the upper pole.
    for i in range(1, hemisphere_rings):
        angle = (pi * 0.5) * (i / hemisphere_rings)
        ring_specs.append((length + radius * sin(angle), radius * cos(angle)))

    verts = [(0.0, start_y - radius, 0.0)]
    rings = []
    for y, ring_radius in ring_specs:
        ring = []
        for segment in range(segments):
            angle = 2.0 * pi * segment / segments
            ring.append(len(verts))
            verts.append((ring_radius * cos(angle), y + start_y, ring_radius * sin(angle)))
        rings.append(ring)
    top_pole = len(verts)
    verts.append((0.0, start_y + length + radius, 0.0))

    faces = []
    first = rings[0]
    for segment in range(segments):
        nxt = (segment + 1) % segments
        faces.append((0, first[nxt], first[segment]))
    for lower, upper in zip(rings, rings[1:]):
        for segment in range(segments):
            nxt = (segment + 1) % segments
            faces.append((lower[segment], lower[nxt], upper[nxt], upper[segment]))
    last = rings[-1]
    for segment in range(segments):
        nxt = (segment + 1) % segments
        faces.append((last[segment], last[nxt], top_pole))

    # The ring vertices are emitted counter-clockwise when viewed from the
    # outside, while the pole-to-ring winding above is opposite to Blender's
    # outward convention.  Flip every face so the visible capsule and any
    # optional mesh-based rigid-body fallback have outward normals.  Without
    # this, the capsule looks inside-out under solid shading and can produce
    # broken-looking highlights at the bone ends.
    faces = [tuple(reversed(face)) for face in faces]

    # Blender's CAPSULE rigid-body primitive is aligned to local Z.  The
    # capsule is built in Y above to retain the existing ring construction;
    # convert the mesh to Z and reverse winding once more because the axis
    # swap changes handedness.
    verts = [(x, z, y) for x, y, z in verts]
    faces = [tuple(reversed(face)) for face in faces]

    mesh = bpy.data.meshes.new(name + "_Mesh")
    mesh.from_pydata(verts, [], faces)
    mesh.update()
    return mesh


def _parent_to_pose_bone_preserve_world(obj, armature, pose_bone, world_matrix):
    """Parent an object to a pose bone without changing its world transform."""
    bpy.context.view_layer.update()
    obj.parent = armature
    obj.parent_type = "BONE"
    obj.parent_bone = pose_bone.name
    bpy.context.view_layer.update()
    obj.matrix_world = world_matrix
    bpy.context.view_layer.update()


def _bone_has_simulation_driver(pose_bone):
    """Return whether a pose bone is fed back from a simulated proxy.

    A collider parented to such a bone creates a dependency cycle in Blender:
    rigid-body world -> collider transform -> pose bone -> simulated body.
    Colliders on animated/non-simulated bones can still use normal bone
    parenting and follow the animation in real time.
    """
    return any(
        constraint.name.startswith(f"{PREFIX}SIMULATION")
        for constraint in pose_bone.constraints
    )


def _flatten_matrix(matrix):
    return [float(value) for row in matrix for value in row]


def _matrix_from_flat(raw):
    if raw is None or len(raw) != 16:
        return None
    try:
        return Matrix(
            tuple(tuple(float(raw[row * 4 + col]) for col in range(4)) for row in range(4))
        )
    except (TypeError, ValueError):
        return None


def _matrices_differ(a, b, epsilon=1.0e-7):
    return any(
        abs(float(a[row][col]) - float(b[row][col])) > epsilon
        for row in range(4)
        for col in range(4)
    )


def _rebuild_follow_object_cache():
    """Index only RBS follow objects so depsgraph callbacks avoid scene scans."""
    _DYNAMIC_ROOT_ANCHOR_NAMES.clear()
    _ROOT_FOLLOW_TARGET_NAMES.clear()
    _STATIC_IK_HELPER_NAMES.clear()
    _FOLLOW_RELEASE_NAMES.clear()
    for obj in bpy.data.objects:
        if obj.get("rbs_kind") == "ANCHOR" and obj.get("rbs_follow_mode") == "DYNAMIC_ROOT":
            _DYNAMIC_ROOT_ANCHOR_NAMES.add(obj.name)
        elif obj.get("rbs_kind") == "ROOT_FOLLOW":
            _ROOT_FOLLOW_TARGET_NAMES.add(obj.name)
        elif obj.get("rbs_follow_mode") == "STATIC_IK":
            _STATIC_IK_HELPER_NAMES.add(obj.name)
        if obj.get("rbs_overlap_adjusted") or obj.get("rbs_initial_follow_pending"):
            _FOLLOW_RELEASE_NAMES.add(obj.name)


def _chain_objects(armature_name, chain_id):
    return [
        obj for obj in bpy.data.objects
        if obj.get("rbs_generated")
        and obj.get("rbs_armature") == armature_name
        and obj.get("rbs_chain_id") == chain_id
        and obj.get("rbs_kind") in CHAIN_OBJECT_KINDS
    ]


def _detach_anchor_for_damped_follow(armature, pose_bone, anchor):
    """Detach a root anchor from the bone graph and follow it imperatively.

    Keeping a bone-parented anchor while the same root uses DAMPED_TRACK to a
    dynamic child creates a dependency cycle. The anchor remains kinematic,
    but its transform is updated from the evaluated pose in the depsgraph
    handler, so the root damped-follow constraint can stay active.
    """
    if anchor is None or pose_bone is None:
        return
    world = anchor.matrix_world.copy()
    bone_world = armature.matrix_world @ pose_bone.matrix
    anchor.parent = None
    anchor.matrix_world = world
    follow_matrix = bone_world.inverted() @ world
    anchor["rbs_follow_mode"] = "DYNAMIC_ROOT"
    anchor["rbs_follow_bone"] = pose_bone.name
    anchor["rbs_root_follow_matrix"] = _flatten_matrix(follow_matrix)


def _update_dynamic_root_anchors():
    global _UPDATING_DYNAMIC_ROOT_ANCHORS
    if _UPDATING_DYNAMIC_ROOT_ANCHORS:
        return
    _UPDATING_DYNAMIC_ROOT_ANCHORS = True
    try:
        for name in tuple(_DYNAMIC_ROOT_ANCHOR_NAMES):
            anchor = bpy.data.objects.get(name)
            if anchor is None:
                _DYNAMIC_ROOT_ANCHOR_NAMES.discard(name)
                continue
            if anchor.get("rbs_follow_mode") != "DYNAMIC_ROOT":
                continue
            armature = bpy.data.objects.get(anchor.get("rbs_armature", ""))
            # ``rbs_bone`` was used by the first dynamic-anchor prototype.
            # Prefer the explicit follow-bone metadata but keep old files
            # readable until the migration pass has run.
            follow_bone_name = str(
                anchor.get("rbs_follow_bone", "")
                or anchor.get("rbs_bone", "")
                or ""
            )
            pose_bone = (
                armature.pose.bones.get(follow_bone_name)
                if follow_bone_name
                and armature is not None
                and armature.type == "ARMATURE"
                else None
            )
            follow_matrix = _matrix_from_flat(anchor.get("rbs_root_follow_matrix"))
            if armature is None or armature.type != "ARMATURE" or follow_matrix is None:
                continue
            if pose_bone is not None:
                follow_base = armature.matrix_world @ pose_bone.matrix
            else:
                follow_base = armature.matrix_world.copy()
            target_matrix = follow_base @ follow_matrix
            if _matrices_differ(anchor.matrix_world, target_matrix):
                anchor.parent = None
                anchor.matrix_world = target_matrix
            if anchor.rigid_body is not None and not anchor.rigid_body.kinematic:
                # Mark the object as a kinematic input after the transform
                # update so Bullet consumes the new world matrix on the next
                # simulation step.
                anchor.rigid_body.kinematic = True
    finally:
        _UPDATING_DYNAMIC_ROOT_ANCHORS = False


def _migrate_dynamic_root_anchors():
    """Detach legacy bone-parented anchors and preserve their rest offset."""
    for anchor in bpy.data.objects:
        if (
            anchor.get("rbs_kind") != "ANCHOR"
            or not anchor.get("rbs_generated")
        ):
            continue
        armature = bpy.data.objects.get(anchor.get("rbs_armature", ""))
        if armature is None or armature.type != "ARMATURE":
            continue
        root_bone = armature.pose.bones.get(str(anchor.get("rbs_bone", "")))
        if root_bone is None:
            continue
        follow_bone = root_bone.parent
        if follow_bone is not None and _bone_has_simulation_driver(follow_bone):
            follow_bone = None
        follow_base = (
            armature.matrix_world @ follow_bone.matrix
            if follow_bone is not None
            else armature.matrix_world.copy()
        )
        follow_matrix = _matrix_from_flat(anchor.get("rbs_root_follow_matrix"))
        if anchor.parent is not None or follow_matrix is None:
            world = anchor.matrix_world.copy()
            anchor.parent = None
            anchor.parent_type = "OBJECT"
            anchor.parent_bone = ""
            anchor.matrix_world = world
            anchor["rbs_follow_mode"] = "DYNAMIC_ROOT"
            anchor["rbs_follow_bone"] = follow_bone.name if follow_bone else ""
            anchor["rbs_root_follow_matrix"] = _flatten_matrix(
                follow_base.inverted() @ world
            )
        elif anchor.get("rbs_follow_mode") != "DYNAMIC_ROOT":
            anchor["rbs_follow_mode"] = "DYNAMIC_ROOT"
            anchor["rbs_follow_bone"] = follow_bone.name if follow_bone else ""


def _make_root_follow_target(armature, root_bone, body, collection, chain_id=""):
    """Create a non-parented target for the root's damped-follow constraint."""
    name = f"{DAMPED_FOLLOW_PREFIX}{armature.name}_{root_bone.name}_TARGET"
    target = bpy.data.objects.get(name)
    if target is None or target.get("rbs_kind") != "ROOT_FOLLOW":
        target = bpy.data.objects.new(name, None)
        collection.objects.link(target)
        _link_to_physics_root(target)
    target.matrix_world = body.matrix_world.copy()
    target["rbs_generated"] = True
    target["rbs_kind"] = "ROOT_FOLLOW"
    target["rbs_armature"] = armature.name
    target["rbs_bone"] = root_bone.name
    target["rbs_source_body"] = body.name
    if chain_id:
        target["rbs_chain_id"] = chain_id
    _set_display_visibility(target, False)
    return target


def _update_root_follow_targets():
    """Copy body transforms to root follow targets outside the depsgraph graph."""
    global _UPDATING_ROOT_FOLLOW_TARGETS
    if _UPDATING_ROOT_FOLLOW_TARGETS:
        return
    _UPDATING_ROOT_FOLLOW_TARGETS = True
    try:
        for name in tuple(_ROOT_FOLLOW_TARGET_NAMES):
            target = bpy.data.objects.get(name)
            if target is None:
                _ROOT_FOLLOW_TARGET_NAMES.discard(name)
                continue
            if target.get("rbs_kind") != "ROOT_FOLLOW":
                continue
            body = bpy.data.objects.get(str(target.get("rbs_source_body", "")))
            if body is not None and _matrices_differ(target.matrix_world, body.matrix_world):
                target.matrix_world = body.matrix_world.copy()
    finally:
        _UPDATING_ROOT_FOLLOW_TARGETS = False


def _migrate_root_damped_follow_targets():
    """Break legacy root BODY targets by inserting ROOT_FOLLOW helpers."""
    _root, body_collection, _colliders = physics_collections()
    for armature in (obj for obj in bpy.data.objects if obj.type == "ARMATURE"):
        for pose_bone in armature.pose.bones:
            for constraint in pose_bone.constraints:
                if (
                    constraint.type != "DAMPED_TRACK"
                    or not constraint.name.startswith(DAMPED_FOLLOW_PREFIX)
                ):
                    continue
                body = getattr(constraint, "target", None)
                if (
                    body is None
                    or body.get("rbs_kind") != "BODY"
                    or body.get("rbs_armature") != armature.name
                ):
                    continue
                chain_id = str(body.get("rbs_chain_id", ""))
                helper = _make_root_follow_target(
                    armature,
                    pose_bone,
                    body,
                    body_collection,
                    chain_id=chain_id,
                )
                constraint.target = helper


def _sync_pending_initial_follow():
    """Rebase a chain only while its initial-follow hand-off is pending.

    Parent edits and animated Center/Spine transforms after that hand-off are
    intentionally left for the root spring. Rebasing the complete chain on
    every depsgraph update would keep all bodies kinematic and disable physics.
    """
    global _UPDATING_INITIAL_FOLLOW
    if _UPDATING_INITIAL_FOLLOW:
        return
    if not any(
        (
            (anchor := bpy.data.objects.get(name)) is not None
            and anchor.get("rbs_initial_follow_pending")
        )
        for name in tuple(_DYNAMIC_ROOT_ANCHOR_NAMES)
    ):
        return
    _UPDATING_INITIAL_FOLLOW = True
    try:
        for anchor in list(bpy.data.objects):
            if (
                anchor.get("rbs_kind") != "ANCHOR"
                or anchor.rigid_body is None
            ):
                continue
            chain_id = str(anchor.get("rbs_chain_id", ""))
            arm_name = str(anchor.get("rbs_armature", ""))
            if not chain_id or not arm_name:
                continue
            previous = _matrix_from_flat(anchor.get("rbs_sync_anchor_matrix"))
            current = anchor.matrix_world.copy()
            if previous is None:
                anchor["rbs_sync_anchor_matrix"] = _flatten_matrix(current)
                continue
            delta = current @ previous.inverted()
            changed = any(abs(float(delta[row][col]) - (1.0 if row == col else 0.0)) > 1.0e-6 for row in range(4) for col in range(4))
            if changed:
                current_frame = int(getattr(_current_scene(), "frame_current", 0))
                chain_objects = _chain_objects(arm_name, chain_id)
                # Rebase only during the build frame. Once the initial
                # follow hand-off has passed, an animated Center/Spine parent
                # must pull the root joint through its spring; rebasing the
                # complete chain every depsgraph update would keep every body
                # kinematic forever and eliminate simulation.
                rebase_initial = any(
                    obj.get("rbs_kind") == "BODY"
                    and obj.rigid_body is not None
                    and obj.get("rbs_initial_follow_pending")
                    and int(obj.get("rbs_initial_follow_frame", -1)) == current_frame
                    for obj in chain_objects
                )
                if rebase_initial:
                    for obj in chain_objects:
                        if obj == anchor:
                            continue
                        # The root constraint is parented to the anchor and
                        # will follow it automatically; other helpers need
                        # the same world-space delta during initialization.
                        if obj.parent == anchor:
                            continue
                        obj.matrix_world = delta @ obj.matrix_world
                        if obj.get("rbs_kind") == "BODY" and obj.rigid_body is not None:
                            obj.rigid_body.kinematic = True
                anchor["rbs_sync_anchor_matrix"] = _flatten_matrix(current)
    finally:
        _UPDATING_INITIAL_FOLLOW = False


@persistent
def _rbs_depsgraph_update_post(scene, _depsgraph):
    # A baked cache already contains the evaluated kinematic input. Skipping
    # anchor writes during preview prevents recursive depsgraph invalidation.
    if _rigid_body_cache_is_baked(scene):
        return
    _update_dynamic_root_anchors()
    _sync_pending_initial_follow()


def _freeze_colliders_on_simulated_bones(armature):
    """Detach colliders that would otherwise feed a simulated bone back into physics."""
    for obj in bpy.data.objects:
        if (
            obj.get("rbs_kind") != "COLLIDER"
            or obj.get("rbs_armature") != armature.name
            or obj.parent != armature
            or obj.parent_type != "BONE"
        ):
            continue
        pose_bone = armature.pose.bones.get(obj.get("rbs_bone", ""))
        if pose_bone is None or not _bone_has_simulation_driver(pose_bone):
            continue
        raw_matrix = obj.get("rbs_initial_world_matrix")
        if raw_matrix is not None and len(raw_matrix) == 16:
            world_matrix = Matrix(
                tuple(tuple(raw_matrix[row * 4 + col] for col in range(4)) for row in range(4))
            )
        else:
            # Legacy colliders may not have a snapshot; this fallback is only
            # used when the object predates cycle protection.
            world_matrix = obj.matrix_world.copy()
        obj.parent = None
        obj.matrix_world = world_matrix
        obj["rbs_follow_mode"] = "STATIC_SIMULATED_BONE"


def _restore_colliders_on_unsimulated_bones(armature):
    """Resume bone following after the rigid-body drivers have been removed."""
    for obj in bpy.data.objects:
        if (
            obj.get("rbs_kind") != "COLLIDER"
            or obj.get("rbs_armature") != armature.name
            or obj.get("rbs_follow_mode") != "STATIC_SIMULATED_BONE"
        ):
            continue
        pose_bone = armature.pose.bones.get(obj.get("rbs_bone", ""))
        if pose_bone is None or _bone_has_simulation_driver(pose_bone):
            continue
        world_matrix = obj.matrix_world.copy()
        _parent_to_pose_bone_preserve_world(obj, armature, pose_bone, world_matrix)
        obj["rbs_follow_mode"] = "BONE"
        if "rbs_ik_follow_matrix" in obj:
            del obj["rbs_ik_follow_matrix"]


def _restore_ik_colliders(armature):
    """Restore bone parenting for IK colliders with animated rigid bodies."""
    ik_bones = _generated_ik_chain_bones(armature)
    if not ik_bones:
        return
    for obj in bpy.data.objects:
        if (
            obj.get("rbs_kind") != "COLLIDER"
            or obj.get("rbs_armature") != armature.name
            or obj.get("rbs_bone") not in ik_bones
            or obj.get("rbs_follow_mode") != "STATIC_IK"
        ):
            continue
        pose_bone = armature.pose.bones.get(obj.get("rbs_bone", ""))
        if pose_bone is None or _bone_has_simulation_driver(pose_bone):
            continue
        world_matrix = obj.matrix_world.copy()
        _parent_to_pose_bone_preserve_world(obj, armature, pose_bone, world_matrix)
        obj["rbs_follow_mode"] = "BONE"
        if "rbs_ik_follow_matrix" in obj:
            del obj["rbs_ik_follow_matrix"]
        if obj.rigid_body is not None:
            obj.rigid_body.kinematic = True


def _freeze_helpers_on_ik_chain(armature, bone_names):
    """Detach IK-chain helpers that would feed a dependency cycle."""
    bone_names = set(bone_names)
    for obj in bpy.data.objects:
        if (
            not obj.get("rbs_generated")
            or obj.get("rbs_armature") != armature.name
            or obj.get("rbs_kind") != "ANCHOR"
            or obj.parent != armature
            or obj.parent_type != "BONE"
            or obj.parent_bone not in bone_names
        ):
            continue
        world_matrix = obj.matrix_world.copy()
        obj.parent = None
        obj.matrix_world = world_matrix
        obj["rbs_follow_mode"] = "STATIC_IK"
        pose_bone = armature.pose.bones.get(obj.get("rbs_bone", ""))
        if pose_bone is not None:
            bone_world = armature.matrix_world @ pose_bone.matrix
            follow_matrix = bone_world.inverted() @ world_matrix
            obj["rbs_ik_follow_matrix"] = [
                float(value) for row in follow_matrix for value in row
            ]
        _STATIC_IK_HELPER_NAMES.add(obj.name)


def _update_static_ik_helpers(_scene, _depsgraph):
    """Follow IK bones imperatively without creating a dependency cycle."""
    global _UPDATING_STATIC_IK
    if _UPDATING_STATIC_IK:
        return
    if not _STATIC_IK_HELPER_NAMES:
        return
    _UPDATING_STATIC_IK = True
    try:
        for name in tuple(_STATIC_IK_HELPER_NAMES):
            obj = bpy.data.objects.get(name)
            if obj is None:
                _STATIC_IK_HELPER_NAMES.discard(name)
                continue
            if obj.get("rbs_follow_mode") != "STATIC_IK":
                _STATIC_IK_HELPER_NAMES.discard(name)
                continue
            armature = bpy.data.objects.get(obj.get("rbs_armature", ""))
            pose_bone = (
                armature.pose.bones.get(obj.get("rbs_bone", ""))
                if armature is not None and armature.type == "ARMATURE"
                else None
            )
            raw = obj.get("rbs_ik_follow_matrix")
            if pose_bone is None or raw is None or len(raw) != 16:
                continue
            follow_matrix = Matrix(
                tuple(tuple(float(raw[row * 4 + col]) for col in range(4)) for row in range(4))
            )
            target_matrix = (armature.matrix_world @ pose_bone.matrix) @ follow_matrix
            if _matrices_differ(obj.matrix_world, target_matrix):
                obj.matrix_world = target_matrix
    finally:
        _UPDATING_STATIC_IK = False


def _restore_helpers_after_ik(armature):
    """Resume bone following for helpers after generated IK is removed."""
    for obj in bpy.data.objects:
        if (
            not obj.get("rbs_generated")
            or obj.get("rbs_armature") != armature.name
            or obj.get("rbs_follow_mode") != "STATIC_IK"
        ):
            continue
        pose_bone = armature.pose.bones.get(obj.get("rbs_bone", ""))
        if pose_bone is None or _bone_has_simulation_driver(pose_bone):
            continue
        world_matrix = obj.matrix_world.copy()
        _parent_to_pose_bone_preserve_world(obj, armature, pose_bone, world_matrix)
        obj["rbs_follow_mode"] = "BONE"
        _STATIC_IK_HELPER_NAMES.discard(obj.name)


def _generated_ik_chain_bones(armature):
    """Return bones participating in generated IK constraints on an armature."""
    result = set()
    for pose_bone in armature.pose.bones:
        for constraint in pose_bone.constraints:
            if constraint.type != "IK" or not constraint.name.startswith(f"{PREFIX}IK_"):
                continue
            result.add(pose_bone.name)
            current = pose_bone.parent
            for _index in range(max(constraint.chain_count - 1, 0)):
                if current is None:
                    break
                result.add(current.name)
                current = current.parent
    return result


def _make_body_proxy(
    armature,
    pose_bone,
    radius,
    length_scale,
    collection,
    mask,
    mass,
    lin_damp,
    ang_damp,
    initial_world_matrix=None,
):
    head, tail, _delta, full_length = _bone_points(armature, pose_bone)
    proxy_length = max(full_length * min(max(length_scale, 0.1), 1.0), 0.001)
    name = f"{PREFIX}BODY_{armature.name}_{pose_bone.name}"
    mesh = _make_box_mesh(name, radius, proxy_length, -proxy_length * 0.5)
    obj = bpy.data.objects.new(name, mesh)
    collection.objects.link(obj)
    _link_to_physics_root(obj)
    obj.rotation_mode = "QUATERNION"
    if initial_world_matrix is None:
        obj.rotation_quaternion = _bone_world_rotation(armature, pose_bone)
        obj.location = (head + tail) * 0.5
        bpy.context.view_layer.update()
        initial_world_matrix = obj.matrix_world.copy()
    else:
        initial_world_matrix = initial_world_matrix.copy()
        obj.matrix_world = initial_world_matrix
    # ``rigidbody.object_add`` evaluates the dependency graph immediately.
    # If the armature has keyed motion, that refresh can replace the freshly
    # assigned transform with a later evaluated pose before the chain is
    # finished.  Keep the exact pose sampled above and restore it after the
    # operator returns so the simulation starts where the bone is visible.
    rb = _add_rigidbody(obj, "ACTIVE")
    # Hold the body kinematic while the rest of this chain is assembled. The
    # rigid-body operator may evaluate a keyed armature during construction;
    # releasing it only after constraints and all initial transforms exist
    # prevents that transient evaluation from becoming the simulation state.
    rb.kinematic = True
    obj.matrix_world = initial_world_matrix
    bpy.context.view_layer.update()
    rb.collision_shape = "BOX"
    rb.mass = mass
    rb.linear_damping = lin_damp
    rb.angular_damping = ang_damp
    _configure_collision_contact(rb, radius)
    _set_mask(rb, mask)
    obj["rbs_generated"] = True
    obj["rbs_kind"] = "BODY"
    obj["rbs_armature"] = armature.name
    obj["rbs_bone"] = pose_bone.name
    obj["rbs_length_scale"] = length_scale
    obj["rbs_full_length"] = full_length
    # Per-bone collision layers are intentionally not used.  Keep the legacy
    # property at -1 so callers can distinguish the shared mask from old
    # files that allocated a unique bit to each bone.
    obj["rbs_collision_bit"] = RBS_COLLISION_BIT
    return obj


def _ensure_body_limit_distance_constraint(body, armature, bone_name, distance):
    """Keep a BODY origin within ``distance`` of its source bone midpoint.

    This uses Blender's object-level Limit Distance constraint rather than
    linear limits on the inter-body rigid-body constraint.  The latter limits
    the distance from a joint pivot and allows a long chain to drift as each
    segment accumulates error.  The target is always the source bone at
    ``head_tail=0.5`` and the midpoint setting is intentionally fixed.
    """
    if body is None or armature is None or not bone_name:
        return None
    constraints = [
        item for item in body.constraints
        if item.type == "LIMIT_DISTANCE"
    ]
    generated = next(
        (
            item for item in constraints
            if item.name.startswith(BODY_LIMIT_DISTANCE_PREFIX)
        ),
        None,
    )
    constraint = generated or (constraints[0] if constraints else body.constraints.new("LIMIT_DISTANCE"))
    constraint.name = f"{BODY_LIMIT_DISTANCE_PREFIX}{armature.name}_{bone_name}"
    for duplicate in constraints:
        if duplicate != constraint:
            body.constraints.remove(duplicate)
    # The visible armature receives RBS_SIMULATION constraints that are driven
    # by this BODY. Targeting it directly would close a depsgraph feedback
    # loop. The hidden reference keeps the same animated source pose while
    # remaining independent from the rigid-body result.
    constraint.target = _ensure_reference_armature(armature)
    constraint.subtarget = str(bone_name)
    constraint.head_tail = BODY_LIMIT_DISTANCE_HEAD_TAIL
    constraint.distance = max(float(distance), 0.0)
    constraint.limit_mode = "LIMITDIST_INSIDE"
    constraint.owner_space = "WORLD"
    constraint.target_space = "WORLD"
    constraint.influence = 1.0
    constraint.show_expanded = False
    body["rbs_limit_distance"] = constraint.distance
    body["rbs_limit_head_tail"] = BODY_LIMIT_DISTANCE_HEAD_TAIL
    body["rbs_limit_target"] = constraint.target.name if constraint.target else ""
    return constraint


def _ensure_body_rigidbody_limit(body, armature, bone_name, distance, collection, chain_id=""):
    """Add hard Bullet translation limits around an independent bone target."""
    reference = _ensure_reference_armature(armature)
    pose_bone = reference.pose.bones.get(bone_name)
    if pose_bone is None or body is None or body.rigid_body is None:
        return None
    target = _make_limit_target_proxy(
        armature, pose_bone, reference, collection, chain_id
    )
    if target is None:
        return None
    helper_name = f"{CONSTRAINT_PREFIX}LIMIT_{armature.name}_{bone_name}"
    helper = next(
        (
            obj for obj in bpy.data.objects
            if obj.get("rbs_generated")
            and obj.get("rbs_kind") == "CONSTRAINT"
            and obj.get("rbs_limit_constraint")
            and obj.get("rbs_armature") == armature.name
            and obj.get("rbs_bone") == bone_name
        ),
        None,
    )
    if helper is None:
        helper = _make_constraint(
            helper_name, target, body, body.matrix_world.translation,
            collection, 0.0, 0.0, True,
        )
    else:
        rigid_constraint = helper.rigid_body_constraint
        rigid_constraint.object1 = target
        rigid_constraint.object2 = body
    con = helper.rigid_body_constraint
    con.type = "GENERIC"
    con.disable_collisions = True
    con.use_limit_lin_x = True
    con.use_limit_lin_y = True
    con.use_limit_lin_z = True
    requested_distance = max(float(distance), 0.0)
    # Bullet's linear-limit solver keeps a small positional tolerance.  Leave
    # an internal margin so the measured BODY-to-reference distance does not
    # exceed the user-facing LIMIT_DISTANCE value.
    hard_distance = max(requested_distance * 0.9, 1.0e-6) if requested_distance > 0.0 else 0.0
    for axis in "xyz":
        setattr(con, f"limit_lin_{axis}_lower", -hard_distance)
        setattr(con, f"limit_lin_{axis}_upper", hard_distance)
        setattr(con, f"use_limit_ang_{axis}", False)
        setattr(con, f"use_spring_{axis}", False)
        setattr(con, f"use_spring_ang_{axis}", False)
    helper["rbs_generated"] = True
    helper["rbs_kind"] = "CONSTRAINT"
    helper["rbs_armature"] = armature.name
    helper["rbs_bone"] = bone_name
    helper["rbs_chain_id"] = chain_id
    helper["rbs_limit_constraint"] = True
    helper["rbs_limit_distance"] = requested_distance
    helper["rbs_limit_solver_distance"] = hard_distance
    return helper


def _migrate_body_limit_distance_constraints(armature_name=None, distance=None):
    """Migrate old object-level limits to native MMD-style joints.

    A generated BODY -> visible pose-bone LIMIT_DISTANCE edge is cyclic once
    that pose bone receives the rigid-body driver. Remove only generated
    legacy objects; user constraints and user armatures are left untouched.
    """
    for body in bpy.data.objects:
        if (
            body.get("rbs_kind") != "BODY"
            or body.rigid_body is None
            or not body.get("rbs_generated")
            or (
                armature_name is not None
                and body.get("rbs_armature") != armature_name
            )
        ):
            continue
        for constraint in list(body.constraints):
            if (
                constraint.type == "LIMIT_DISTANCE"
                and constraint.name.startswith(BODY_LIMIT_DISTANCE_PREFIX)
            ):
                body.constraints.remove(constraint)
    legacy_helpers = [
        obj for obj in bpy.data.objects
        if obj.get("rbs_generated")
        and (
            obj.get("rbs_kind") == "LIMIT_TARGET"
            or (
                obj.get("rbs_kind") == "CONSTRAINT"
                and obj.get("rbs_limit_constraint")
            )
        )
        and (
            armature_name is None
            or obj.get("rbs_armature") == armature_name
        )
    ]
    for obj in legacy_helpers:
        bpy.data.objects.remove(obj, do_unlink=True)
    # Delete only reference armatures generated by this add-on. An imported
    # second armature without this marker is never touched.
    references = [
        obj for obj in bpy.data.objects
        if obj.type == "ARMATURE"
        and obj.get("rbs_reference_for")
        and (
            armature_name is None
            or obj.get("rbs_reference_for") == armature_name
        )
    ]
    for obj in references:
        bpy.data.objects.remove(obj, do_unlink=True)


def _make_body_driver_targets(proxy, armature, pose_bone, full_length, collection):
    """Create hidden head/tail targets for driving a bone from a centered body."""
    targets = []
    for suffix, local_y in (("HEAD", -full_length * 0.5), ("TAIL", full_length * 0.5)):
        target = bpy.data.objects.new(
            f"{PREFIX}DRIVER_{armature.name}_{pose_bone.name}_{suffix}", None
        )
        collection.objects.link(target)
        _link_to_physics_root(target)
        target.parent = proxy
        target.location = (0.0, local_y, 0.0)
        target["rbs_generated"] = True
        target["rbs_kind"] = "DRIVER"
        target["rbs_armature"] = armature.name
        target["rbs_bone"] = pose_bone.name
        _set_display_visibility(target, False)
        targets.append(target)
    return targets[0], targets[1]


def _make_limit_target_proxy(armature, pose_bone, reference, collection, chain_id=""):
    """Create a hidden kinematic rigid body at a reference bone midpoint."""
    ref_bone = reference.pose.bones.get(pose_bone.name) if reference else None
    if ref_bone is None:
        return None
    head = reference.matrix_world @ ref_bone.head
    tail = reference.matrix_world @ ref_bone.tail
    midpoint = (head + tail) * 0.5
    name = f"{LIMIT_TARGET_PREFIX}{armature.name}_{pose_bone.name}"
    target = bpy.data.objects.get(name)
    if target is not None:
        if target.get("rbs_kind") != "LIMIT_TARGET":
            target = None
    if target is None:
        target = bpy.data.objects.new(name, _make_box_mesh(name, 0.005, 0.01))
        collection.objects.link(target)
        _link_to_physics_root(target)
    target.rotation_mode = "QUATERNION"
    target.rotation_quaternion = _bone_world_rotation(reference, ref_bone)
    target.location = midpoint
    bpy.context.view_layer.update()
    world_matrix = target.matrix_world.copy()
    if target.rigid_body is None:
        rb = _add_rigidbody(target, "PASSIVE")
    else:
        rb = target.rigid_body
        rb.type = "PASSIVE"
    target.matrix_world = world_matrix
    _parent_to_pose_bone_preserve_world(target, reference, ref_bone, world_matrix)
    rb.kinematic = True
    _set_mask(rb, NO_BITS)
    target["rbs_generated"] = True
    target["rbs_kind"] = "LIMIT_TARGET"
    target["rbs_armature"] = armature.name
    target["rbs_reference_armature"] = reference.name
    target["rbs_bone"] = pose_bone.name
    if chain_id:
        target["rbs_chain_id"] = chain_id
    _set_display_visibility(target, False)
    return target


def _make_anchor(armature, root_bone, collection, initial_world_matrix=None):
    _head, tail, _delta, _length = _bone_points(armature, root_bone)
    name = f"{PREFIX}ANCHOR_{armature.name}_{root_bone.name}"
    # Use a tiny mesh because Blender's rigidbody.object_add operator does not
    # accept Empty objects as rigid-body participants in all 4.x contexts.
    anchor = bpy.data.objects.new(name, _make_box_mesh(name, 0.04, 0.08))
    collection.objects.link(anchor)
    _link_to_physics_root(anchor)
    # The first dynamic child starts at the root bone tail. Placing the
    # kinematic anchor at the root head creates an initial spring offset and
    # makes the chain jump as soon as rigid-body evaluation starts.
    anchor.matrix_world.translation = tail
    # MMD-style root: the passive endpoint follows the *upstream* animation
    # bone, never the root pose bone that will DAMPED_TRACK the first BODY.
    # Do not parent the rigid-body object to that bone. Blender 5.2 evaluates
    # a bone-parented passive/kinematic body as a static Bullet transform, so
    # the visible object moves while the solver still sees the old position.
    # The follow relation is stored and applied imperatively by
    # ``_update_dynamic_root_anchors`` instead.
    upstream = root_bone.parent
    anchor.parent = None
    anchor.parent_type = "OBJECT"
    anchor.parent_bone = ""
    if initial_world_matrix is not None:
        initial_world_matrix = initial_world_matrix.copy()
        anchor.matrix_world = initial_world_matrix
    else:
        bpy.context.view_layer.update()
        anchor.matrix_world = Matrix.Identity(4)
        anchor.matrix_world.translation = tail
        initial_world_matrix = anchor.matrix_world.copy()
    rb = _add_rigidbody(anchor, "PASSIVE")
    anchor.matrix_world = initial_world_matrix
    bpy.context.view_layer.update()
    rb.kinematic = True
    _set_mask(rb, NO_BITS)
    _set_display_visibility(anchor, False)
    anchor["rbs_generated"] = True
    anchor["rbs_kind"] = "ANCHOR"
    anchor["rbs_armature"] = armature.name
    anchor["rbs_bone"] = root_bone.name
    if upstream is not None and not _bone_has_simulation_driver(upstream):
        follow_base = armature.matrix_world @ upstream.matrix
        follow_bone_name = upstream.name
    else:
        # A root whose parent is already simulated cannot safely be read by a
        # physics object. Follow only the armature object transform in that
        # case, avoiding a second dependency edge into the simulated pose.
        follow_base = armature.matrix_world.copy()
        follow_bone_name = ""
    anchor["rbs_follow_mode"] = "DYNAMIC_ROOT"
    anchor["rbs_follow_bone"] = follow_bone_name
    anchor["rbs_root_follow_matrix"] = _flatten_matrix(
        follow_base.inverted() @ initial_world_matrix
    )
    return anchor


def _make_constraint(name, object1, object2, location, collection, stiffness, damping, disable_collisions=True, max_distance=None):
    # Constraint operators do support Empty objects, but a small mesh proxy is
    # more reliable in background mode and remains easy to select in the UI.
    empty = bpy.data.objects.new(name, _make_box_mesh(name, 0.025, 0.05))
    empty.location = location
    collection.objects.link(empty)
    _link_to_physics_root(empty)
    # Constraint empties use a rigid-body *constraint*, not a rigid-body
    # object.  Adding an object rigid body here would make the empty collide
    # with the simulated bodies in some Blender versions.
    _activate(empty)
    bpy.ops.rigidbody.constraint_add()
    # Blender can retain/add a rigid-body component on the helper mesh when a
    # file was created by an older build.  The helper must only carry the
    # rigid-body constraint; an extra ACTIVE body would enter the collision
    # world and interfere with the actual chain/collider contact.
    if empty.rigid_body is not None:
        bpy.ops.rigidbody.object_remove()
    # The rigid-body constraint operator can reset an object's transform to
    # the 3D cursor.  Restore the requested world-space pivot after adding
    # the constraint so its spring endpoints are initialized at the bone
    # junction rather than at the scene origin.
    empty.location = location
    bpy.context.view_layer.update()
    con = empty.rigid_body_constraint
    con.type = "GENERIC_SPRING"
    con.object1 = object1
    con.object2 = object2
    con.disable_collisions = disable_collisions
    for axis in "xyz":
        setattr(con, f"use_spring_{axis}", True)
        setattr(con, f"spring_stiffness_{axis}", stiffness)
        setattr(con, f"spring_damping_{axis}", damping)
    for axis in "xyz":
        setattr(con, f"use_spring_ang_{axis}", True)
        setattr(con, f"spring_stiffness_ang_{axis}", stiffness)
        setattr(con, f"spring_damping_ang_{axis}", damping)
    if max_distance is not None:
        # MMD-style joints keep translation close to the rest-frame joint and
        # let the angular springs provide the visible chain motion. Unlike an
        # object LIMIT_DISTANCE target, this edge never reads a driven bone.
        distance = max(float(max_distance), 0.0)
        for axis in "xyz":
            setattr(con, f"use_limit_lin_{axis}", True)
            setattr(con, f"limit_lin_{axis}_lower", -distance)
            setattr(con, f"limit_lin_{axis}_upper", distance)
        empty["rbs_max_distance"] = distance
        empty["rbs_joint_limit"] = True
    _set_display_visibility(empty, False)
    empty["rbs_generated"] = True
    empty["rbs_kind"] = "CONSTRAINT"
    empty["rbs_armature"] = object1.get("rbs_armature", "")
    return empty


def _find_body_proxy(armature, bone_name):
    """Return the generated rigid-body proxy for one armature bone."""
    for obj in bpy.data.objects:
        if (
            obj.get("rbs_generated")
            and obj.get("rbs_kind") == "BODY"
            and obj.get("rbs_armature") == armature.name
            and obj.get("rbs_bone") == bone_name
            and obj.rigid_body is not None
        ):
            return obj
    return None


def _first_child_body_proxy(armature, pose_bone):
    """Return the proxy for the selected bone's first child, if it exists."""
    children = list(getattr(pose_bone, "children", ()) or ())
    if not children:
        return None, None
    child = children[0]
    return child, _find_body_proxy(armature, child.name)


def _ensure_damped_follow_constraint(armature, pose_bone, target, strength, overwrite=False, chain_id=""):
    """Create or update a DAMPED_TRACK to a child body proxy.

    The target is deliberately the first child rigid-body object. Existing
    generated constraints are reused so repeated presses do not stack them.
    """
    if pose_bone is None or target is None:
        return None, False
    damped = [constraint for constraint in pose_bone.constraints if constraint.type == "DAMPED_TRACK"]
    generated = next(
        (item for item in damped if item.name.startswith(DAMPED_FOLLOW_PREFIX)),
        None,
    )
    muted_existing = next(
        (
            item for item in damped
            if item.mute
            and getattr(item, "target", None) is not None
            and item.target.get("rbs_generated")
            and item.target.get("rbs_kind") in {"BODY", "ROOT_FOLLOW"}
            and item.target.get("rbs_armature") == armature.name
        ),
        None,
    )
    # A hidden/muted constraint already targeting an RBS BODY is reusable.
    # Unrelated user constraints are left untouched.
    if generated is None:
        generated = muted_existing
    # Automatic chain creation does not retarget an unrelated active user
    # constraint. It creates a separate chain-owned constraint in that case;
    # an already hidden/muted one is reused above. The explicit panel action
    # may intentionally reuse the first existing constraint.
    if generated is None and damped and overwrite:
        generated = damped[0]
    constraint = generated
    created = constraint is None
    if created:
        constraint = pose_bone.constraints.new("DAMPED_TRACK")
        constraint.name = f"{DAMPED_FOLLOW_PREFIX}{pose_bone.name}"
    constraint.target = target
    constraint.mute = False
    constraint.track_axis = "TRACK_Y"
    # Blender constraints do not support custom ID properties. Use the stable
    # generated name plus the target body's chain metadata for ownership.
    if not constraint.name.startswith(DAMPED_FOLLOW_PREFIX):
        constraint.name = f"{DAMPED_FOLLOW_PREFIX}{pose_bone.name}"
    constraint.target_space = "WORLD"
    constraint.owner_space = "WORLD"
    if created or overwrite:
        constraint.influence = max(0.0, min(float(strength), 1.0))
    return constraint, created


def _add_pose_driver(pose_bone, head_target, tail_target):
    for old in list(pose_bone.constraints):
        # Keep the IK module independent from rigid-body regeneration.  The
        # old implementation removed every generated-looking constraint and
        # silently deleted RBS_IK_* constraints when a chain was rebuilt.
        if old.name.startswith(f"{PREFIX}SIMULATION"):
            pose_bone.constraints.remove(old)
    location = pose_bone.constraints.new("COPY_LOCATION")
    location.name = f"{PREFIX}SIMULATION_LOCATION"
    location.target = head_target
    location.target_space = "WORLD"
    location.owner_space = "WORLD"
    location.influence = 1.0
    rotation = pose_bone.constraints.new("DAMPED_TRACK")
    rotation.name = f"{PREFIX}SIMULATION_ROTATION"
    rotation.target = tail_target
    rotation.target_space = "WORLD"
    rotation.owner_space = "WORLD"
    rotation.track_axis = "TRACK_Y"
    rotation.influence = 1.0
    return location


CHAIN_OBJECT_KINDS = frozenset(
    {"BODY", "ANCHOR", "CONSTRAINT", "DRIVER", "LIMIT_TARGET", "ROOT_FOLLOW"}
)


def _chain_id(armature, root_bone, chain):
    """Return a stable id for one generated chain.

    The id is stored on every proxy created for the chain.  It lets a rebuild
    replace only the selected chain while leaving unrelated chains intact.
    """
    names = ",".join(pb.name for pb in chain)
    return f"{armature.name}|{root_bone.name}|{names}"


def _remove_chain_damped_follow(armature, chain_id="", bone_names=()):
    """Remove only RBS damped-follow constraints belonging to a chain."""
    names = set(bone_names)
    removed = 0
    for pose_bone in armature.pose.bones:
        for constraint in list(pose_bone.constraints):
            if constraint.type != "DAMPED_TRACK":
                continue
            if not constraint.name.startswith(DAMPED_FOLLOW_PREFIX):
                continue
            target = getattr(constraint, "target", None)
            tagged_chain = str(target.get("rbs_chain_id", "")) if target is not None else ""
            tagged_arm = str(target.get("rbs_armature", armature.name)) if target is not None else armature.name
            tagged_bone = pose_bone.name
            if tagged_arm != armature.name:
                continue
            if chain_id and tagged_chain and tagged_chain != chain_id:
                continue
            if names and tagged_bone not in names:
                continue
            pose_bone.constraints.remove(constraint)
            removed += 1
    return removed


def _remove_chain_for_build(armature, chain, root_bone):
    """Remove generated proxies that overlap the chain being rebuilt.

    Older files may not have ``rbs_chain_id`` metadata, so the fallback uses
    the generated object's bone metadata.  Objects for other bones remain in
    place and continue simulating.
    """
    bone_names = {pb.name for pb in chain}
    root_name = root_bone.name
    chain_prefix = f"{armature.name}|{root_name}|"
    candidates = []
    for obj in bpy.data.objects:
        if (
            not obj.get("rbs_generated")
            or obj.get("rbs_armature") != armature.name
            or obj.get("rbs_kind") not in CHAIN_OBJECT_KINDS
        ):
            continue
        # A rebuild with the same root may use a different selected length.
        # Remove the prior complete chain in that case, including bodies that
        # are outside the current selection.
        if str(obj.get("rbs_chain_id", "")).startswith(chain_prefix):
            candidates.append(obj)
            continue
        kind = obj.get("rbs_kind")
        if kind == "ANCHOR":
            overlaps = obj.get("rbs_bone") == root_name
        elif kind in {"BODY", "DRIVER", "LIMIT_TARGET"}:
            overlaps = obj.get("rbs_bone") in bone_names
        else:
            overlaps = False
        if overlaps:
            candidates.append(obj)

    # Constraints do not carry a bone name.  Remove constraints attached to
    # an overlapping body/anchor, including old files without chain metadata.
    candidate_set = set(candidates)
    for obj in bpy.data.objects:
        if (
            obj.get("rbs_generated")
            and obj.get("rbs_armature") == armature.name
            and obj.get("rbs_kind") == "CONSTRAINT"
        ):
            rbcon = obj.rigid_body_constraint
            if rbcon and (rbcon.object1 in candidate_set or rbcon.object2 in candidate_set):
                candidates.append(obj)

    for pb in armature.pose.bones:
        for con in list(pb.constraints):
            if not con.name.startswith(f"{PREFIX}SIMULATION"):
                continue
            target = getattr(con, "target", None)
            # The selected bones are rebuilt directly.  A shortened rebuild
            # also needs to remove simulation constraints on old tail bones
            # whose driver targets are part of the replaced chain.
            if pb.name in bone_names or target in candidate_set:
                pb.constraints.remove(con)
    # Keep the chain-owned root DAMPED_TRACK while rebuilding. Its target is
    # replaced below, which lets a hidden constraint be reused instead of
    # creating a second one. Explicit delete operators remove it separately.
    for obj in set(candidates):
        bpy.data.objects.remove(obj, do_unlink=True)


def _remove_generated(armature_name=None, object_kinds=None, constraint_prefixes=()):
    """Remove selected generated objects and pose constraints in object mode."""
    for arm in [o for o in bpy.data.objects if o.type == "ARMATURE"]:
        if armature_name and arm.name != armature_name:
            continue
        for pb in arm.pose.bones:
            for con in list(pb.constraints):
                if any(con.name.startswith(prefix) for prefix in constraint_prefixes):
                    pb.constraints.remove(con)
    for obj in list(bpy.data.objects):
        if not obj.get("rbs_generated"):
            continue
        if armature_name and obj.get("rbs_armature") != armature_name:
            continue
        if object_kinds is not None and obj.get("rbs_kind") not in object_kinds:
            continue
        bpy.data.objects.remove(obj, do_unlink=True)
    # Remove empty generated collections only when clearing everything.
    if armature_name is None:
        for name in (ROOT_COLLECTION, BODIES_COLLECTION, COLLIDERS_COLLECTION):
            coll = bpy.data.collections.get(name)
            if coll is not None and not coll.objects:
                bpy.data.collections.remove(coll)


def _bone_is_selected(bone):
    """Read armature-bone selection across Blender 4.x and 5.x APIs."""
    value = getattr(bone, "select", None)
    if value is not None:
        return bool(value)
    getter = getattr(bone, "select_get", None)
    return bool(getter()) if callable(getter) else False


def _set_bone_selected(bone, selected):
    """Set armature-bone selection without relying on removed Bone.select."""
    setter = getattr(bone, "select_set", None)
    if callable(setter):
        setter(bool(selected))
        return
    try:
        bone.select = bool(selected)
    except AttributeError:
        pass

def _current_selected_bone_name(context, armature):
    """Return the active selected pose bone name for a targeted delete.

    The panel is also available outside Pose mode, so fall back to the active
    or uniquely selected armature data bone when no pose-bone context exists.
    """
    if context.object is not armature:
        return None
    pose_bone = getattr(context, "active_pose_bone", None)
    if pose_bone is not None:
        selected = context.selected_pose_bones or []
        if not selected or pose_bone in selected:
            return pose_bone.name
    active = getattr(armature.data.bones, "active", None)
    if active is not None and _bone_is_selected(active):
        return active.name
    selected = [bone for bone in armature.data.bones if _bone_is_selected(bone)]
    return selected[0].name if len(selected) == 1 else None


def _selected_bone_names(context, armature):
    """Return all selected bone names for targeted per-bone operations.

    Pose-bone selection must be read before an operator changes mode.  The
    data-bone fallback keeps the targeted delete usable when the panel is
    drawn outside Pose mode, while the existing active-bone helper remains
    the final compatibility fallback for older Blender contexts.
    """
    if context.object is not armature:
        return []

    selected_pose = getattr(context, "selected_pose_bones", None) or []
    names = [pose_bone.name for pose_bone in selected_pose]
    if names:
        return names

    selected_data = [bone.name for bone in armature.data.bones if _bone_is_selected(bone)]
    if selected_data:
        return selected_data

    active_name = _current_selected_bone_name(context, armature)
    return [active_name] if active_name else []


def _remove_selected_chain_generated(armature, bone_name):
    """Remove only chain proxies and simulation binding for one bone."""
    candidates = {
        obj for obj in bpy.data.objects
        if obj.get("rbs_generated")
        and obj.get("rbs_armature") == armature.name
        and obj.get("rbs_kind") in CHAIN_OBJECT_KINDS
        and obj.get("rbs_bone") == bone_name
    }
    # A constraint has no bone metadata; follow its linked body or anchor.
    for obj in bpy.data.objects:
        if (
            obj.get("rbs_generated")
            and obj.get("rbs_armature") == armature.name
            and obj.get("rbs_kind") == "CONSTRAINT"
            and obj.rigid_body_constraint is not None
            and (
                obj.rigid_body_constraint.object1 in candidates
                or obj.rigid_body_constraint.object2 in candidates
            )
        ):
            candidates.add(obj)

    for pose_bone in armature.pose.bones:
        for constraint in list(pose_bone.constraints):
            if not constraint.name.startswith(f"{PREFIX}SIMULATION"):
                continue
            if pose_bone.name == bone_name or getattr(constraint, "target", None) in candidates:
                pose_bone.constraints.remove(constraint)
    for obj in candidates:
        bpy.data.objects.remove(obj, do_unlink=True)
    _restore_colliders_on_unsimulated_bones(armature)
    return len(candidates)


def _remove_selected_colliders(armature, bone_name):
    """Remove generated passive colliders attached to one bone."""
    candidates = [
        obj for obj in bpy.data.objects
        if obj.get("rbs_generated")
        and obj.get("rbs_armature") == armature.name
        and obj.get("rbs_kind") == "COLLIDER"
        and obj.get("rbs_bone") == bone_name
    ]
    for obj in candidates:
        bpy.data.objects.remove(obj, do_unlink=True)
    return len(candidates)


def _ik_chain_bone_names(owner, constraint):
    """Return the bones covered by one IK constraint, including its target.

    ``owner`` is the pose bone that holds the constraint, so the solved chain
    can be walked upwards from it without a second collection lookup.
    """
    names = set()
    subtarget = str(getattr(constraint, "subtarget", "") or "")
    if subtarget:
        names.add(subtarget)
    if owner is None:
        return names
    names.add(owner.name)
    current = owner.parent
    for _index in range(max(constraint.chain_count - 1, 0)):
        if current is None:
            break
        names.add(current.name)
        current = current.parent
    return names


def _remove_ik_generated_for_bone(armature, bone_name):
    """Remove the IK solver generated for one selected bone.

    The selected bone may be the IK control (target), the bone that owns the IK
    constraint, or any bone inside the solved chain.  Only constraints created
    by this add-on are removed, and generated control bones are deleted while
    user-authored ``.ik`` bones are preserved.
    """
    removed_constraints = 0
    target_names = set()
    for pose_bone in armature.pose.bones:
        for constraint in list(pose_bone.constraints):
            if constraint.type != "IK" or not constraint.name.startswith(f"{PREFIX}IK_"):
                continue
            chain_names = _ik_chain_bone_names(pose_bone, constraint)
            if bone_name not in chain_names:
                continue
            # Read the target before removing the constraint; the Python
            # reference is invalidated as soon as the constraint is deleted.
            subtarget = str(getattr(constraint, "subtarget", "") or "")
            is_owner = pose_bone.name == bone_name
            pose_bone.constraints.remove(constraint)
            removed_constraints += 1
            if not subtarget:
                continue
            target_bone = armature.data.bones.get(subtarget)
            if target_bone is not None and target_bone.get("rbs_ik_generated"):
                target_names.add(subtarget)
            elif is_owner:
                target_names.add(subtarget)

    if not removed_constraints and not target_names:
        return 0

    # Remove generated control bones that are no longer referenced by any
    # add-on IK constraint, while keeping user-authored ``.ik`` bones intact.
    still_referenced = {
        str(getattr(constraint, "subtarget", ""))
        for pose_bone in armature.pose.bones
        for constraint in pose_bone.constraints
        if constraint.type == "IK" and constraint.name.startswith(f"{PREFIX}IK_")
    }
    remove_names = {
        name for name in target_names
        if name
        and name not in still_referenced
        and (armature.data.bones.get(name) is not None)
        and armature.data.bones[name].get("rbs_ik_generated")
    }

    old_mode = armature.mode
    if remove_names and old_mode != "EDIT":
        bpy.ops.object.mode_set(mode="EDIT")
    for name in remove_names:
        edit_bone = armature.data.edit_bones.get(name)
        if edit_bone is not None:
            armature.data.edit_bones.remove(edit_bone)
    if remove_names and old_mode != "EDIT":
        bpy.ops.object.mode_set(mode=old_mode)

    for obj in list(bpy.data.objects):
        if (
            obj.get("rbs_generated")
            and obj.get("rbs_armature") == armature.name
            and obj.get("rbs_kind") == "IK_SHAPE"
            and obj.get("rbs_bone") in remove_names
        ):
            bpy.data.objects.remove(obj, do_unlink=True)
    _restore_helpers_after_ik(armature)
    return len(remove_names) + removed_constraints


def _remove_ik_generated(armature):
    """Remove only generated IK constraints, target bones, and custom shapes."""
    for pb in armature.pose.bones:
        for con in list(pb.constraints):
            if con.name.startswith(f"{PREFIX}IK_"):
                pb.constraints.remove(con)

    generated_names = [
        bone.name for bone in armature.data.bones
        if bone.get("rbs_ik_generated")
    ]
    if generated_names:
        bpy.context.view_layer.objects.active = armature
        armature.select_set(True)
        bpy.ops.object.mode_set(mode="EDIT")
        for name in generated_names:
            edit_bone = armature.data.edit_bones.get(name)
            if edit_bone is not None:
                armature.data.edit_bones.remove(edit_bone)
        bpy.ops.object.mode_set(mode="OBJECT")

    for obj in list(bpy.data.objects):
        if (
            obj.get("rbs_generated")
            and obj.get("rbs_armature") == armature.name
            and obj.get("rbs_kind") == "IK_SHAPE"
        ):
            bpy.data.objects.remove(obj, do_unlink=True)


def _selected_chain(armature, root):
    selected = {pb.name: pb for pb in bpy.context.selected_pose_bones or []}
    if root.name not in selected:
        return []
    result = []
    for pb in selected.values():
        if pb == root:
            continue
        parent = pb.parent
        belongs = False
        while parent is not None:
            if parent == root:
                belongs = True
                break
            parent = parent.parent
        if belongs:
            result.append(pb)
    result.sort(key=lambda b: _bone_depth_from(b, root))
    return result


def _is_structural_rig_root(pose_bone):
    """Return whether a bone is a common global rig root boundary."""
    if pose_bone is None:
        return False
    normalized = str(pose_bone.name).strip().lower()
    normalized = normalized.replace("_", "").replace("-", "").replace(".", "")
    return normalized in {
        "root",
        "head",
        "center",
        "spine",
        "hips",
        "pelvis",
        "waist",
        "neck",
        "センター",
    }


def _automatic_chain_root(pose_bone):
    """Find the nearest connected chain root for a selected bone."""
    current = pose_bone
    while current is not None and current.parent is not None:
        parent = current.parent
        if _is_structural_rig_root(parent):
            break
        if len(getattr(parent, "children", ()) or ()) != 1:
            break
        current = parent
    return current


def _all_descendant_chain(root):
    """Return every descendant below *root*, ordered parent-to-child."""
    result = []
    queue = list(getattr(root, "children", ()) or ())
    while queue:
        current = queue.pop(0)
        result.append(current)
        queue.extend(getattr(current, "children", ()) or ())
    result.sort(key=lambda bone: _bone_depth_from(bone, root))
    return result


def _selected_chain_plans(armature, active_pose_bone, selected_pose_bones):
    """Return ``[(root, descendants), ...]`` for legacy or convenience mode."""
    selected = list(selected_pose_bones or [])
    if active_pose_bone is None or not selected:
        return []

    # Preserve the original active-root plus selected-descendants workflow
    # only when the selection contains exactly that one chain.
    legacy_chain = _selected_chain(armature, active_pose_bone)
    legacy_names = {active_pose_bone.name}
    legacy_names.update(pb.name for pb in legacy_chain)
    selected_names = {pb.name for pb in selected}
    if legacy_chain and selected_names == legacy_names:
        return [(active_pose_bone, legacy_chain)]

    roots = []
    seen = set()
    for pose_bone in selected:
        root = _automatic_chain_root(pose_bone)
        if root is None or root.name in seen:
            continue
        seen.add(root.name)
        chain = _all_descendant_chain(root)
        if chain:
            roots.append((root, chain))
    roots.sort(key=lambda item: _pose_bone_depth(item[0]))
    return roots


def _rotation_transfer_pairs(armature, active_pose_bone, selected_pose_bones):
    """Return ``(owner, source)`` pairs for rotation-transfer constraints.

    A selected chain may have either its root or its tip active.  Every
    following bone copies the preceding bone's local X rotation.  With one
    selected bone there is no chain to walk, so its immediate parent is used
    as the source; a root bone falls back to itself to keep the operation
    available on standalone root controls.
    """
    selected = {pb.name: pb for pb in selected_pose_bones}
    if active_pose_bone is None or active_pose_bone.name not in selected:
        return []
    if len(selected) == 1:
        source = active_pose_bone.parent or active_pose_bone
        return [(active_pose_bone, source)]

    ordered = _selected_rotation_chain(armature, active_pose_bone)
    if len(ordered) < 2:
        return []
    return [(owner, source) for source, owner in zip(ordered, ordered[1:])]


def _bone_depth_from(bone, root):
    depth = 0
    cur = bone
    while cur is not None and cur != root:
        depth += 1
        cur = cur.parent
    return depth


def _pose_bone_depth(pose_bone):
    """Return the hierarchy depth of a pose bone; the root is zero."""
    depth = 0
    current = pose_bone.parent
    while current is not None:
        depth += 1
        current = current.parent
    return depth


def _is_ik_target_bone(pose_bone):
    """Return True when a pose bone acts as a dedicated IK control bone."""
    bone = getattr(pose_bone, "bone", None)
    if bone is None:
        return False
    if bone.get("rbs_ik_generated"):
        return True
    name = bone.name.lower()
    return (
        name.endswith(".ik")
        or name.endswith("_ik")
        or name.startswith("ik_")
        or name.startswith("ik.")
    )


def _is_pose_descendant(pose_bone, ancestor):
    """Return True when pose_bone sits below ancestor in the rig."""
    current = pose_bone.parent
    while current is not None:
        if current == ancestor:
            return True
        current = current.parent
    return False


def _selected_ik_chain(active_pose_bone):
    """Return the legacy IK chain: the active bone and selected ancestors."""
    selected = {pb.name for pb in bpy.context.selected_pose_bones or []}
    if active_pose_bone is None or active_pose_bone.name not in selected:
        return []
    chain = [active_pose_bone]
    parent = active_pose_bone.parent
    while parent is not None and parent.name in selected:
        chain.append(parent)
        parent = parent.parent
    return chain


def _selected_ik_components(selected_pose_bones):
    """Return connected components of the selected pose-bone subgraph."""
    selected = {pb.name: pb for pb in (selected_pose_bones or []) if pb is not None}
    remaining = set(selected)
    components = []
    while remaining:
        start = remaining.pop()
        stack = [selected[start]]
        component = []
        while stack:
            pose_bone = stack.pop()
            component.append(pose_bone)
            neighbors = []
            parent = pose_bone.parent
            if parent is not None and parent.name in selected:
                neighbors.append(parent)
            neighbors.extend(child for child in pose_bone.children if child.name in selected)
            for neighbor in neighbors:
                if neighbor.name in remaining:
                    remaining.remove(neighbor.name)
                    stack.append(neighbor)
        component.sort(key=_pose_bone_depth)
        components.append(component)
    return components


def _selected_ik_two_island_chain(selected_pose_bones, active_pose_bone):
    """Validate and return ``(chain, reason)`` for the two-island workflow."""
    components = _selected_ik_components(selected_pose_bones)
    if len(components) != 2:
        return None, None
    active_component = next(
        (component for component in components if any(pb == active_pose_bone for pb in component)),
        None,
    )
    if active_component is None:
        return None, "活动骨骼必须属于选中骨骼"
    if len(active_component) != 1:
        return None, "两孤岛模式下活动项所在孤岛只能包含一个IK骨骼"
    chain_component = next(component for component in components if component is not active_component)
    chain_names = {pb.name for pb in chain_component}
    for pose_bone in chain_component:
        degree = int(pose_bone.parent is not None and pose_bone.parent.name in chain_names)
        degree += sum(child.name in chain_names for child in pose_bone.children)
        if degree > 2:
            return None, "IK骨骼链孤岛不能包含分支"
    tips = [
        pb for pb in chain_component
        if not any(child.name in chain_names for child in pb.children)
    ]
    roots = [
        pb for pb in chain_component
        if pb.parent is None or pb.parent.name not in chain_names
    ]
    if len(roots) != 1 or len(tips) != 1:
        return None, "另一个孤岛必须是连续的单链"
    ordered = sorted(chain_component, key=_pose_bone_depth)
    if ordered[-1] != tips[0] or any(cur.parent != prev for prev, cur in zip(ordered, ordered[1:])):
        return None, "另一个孤岛必须是连续的单链"
    return ordered, None


def _selected_rotation_chain(armature, active_pose_bone):
    """Return a selected linear bone chain ordered from root to tip."""
    selected = {
        pb.name: pb for pb in (bpy.context.selected_pose_bones or [])
    }
    if active_pose_bone is None or active_pose_bone.name not in selected:
        return []
    if len(selected) == 1:
        return [active_pose_bone]

    # The finger workflow usually leaves the tip active, while the rigid-body
    # workflow leaves the root active. Prefer the contiguous selected ancestor
    # chain when it exists, otherwise walk selected direct children from the
    # active root. This keeps branches out of a linear transfer chain.
    ancestors = [active_pose_bone]
    current = active_pose_bone.parent
    while current is not None and current.name in selected:
        ancestors.append(current)
        current = current.parent
    if len(ancestors) > 1:
        return list(reversed(ancestors))

    chain = [active_pose_bone]
    current = active_pose_bone
    while True:
        children = [child for child in current.children if child.name in selected]
        if not children:
            break
        # A rotation-transfer chain is linear. For an accidental branch use
        # the first child in armature order and leave the other selection
        # untouched rather than creating ambiguous constraints.
        current = children[0]
        chain.append(current)
    return chain


def _iter_pose_parents(pose_bone):
    current = pose_bone.parent
    while current is not None:
        yield current
        current = current.parent


def _make_ik_shape(armature, bone_name, size, collection):
    """Create a hidden rectangular prism used as an IK bone custom shape.

    Blender still draws an object's mesh when it is assigned to
    ``PoseBone.custom_shape`` even though the source object is hidden from the
    normal viewport.  Keeping the source object hidden prevents an unwanted
    duplicate cube in the scene while the custom shape remains selectable on
    the generated IK bone.
    """
    name = f"{PREFIX}IK_SHAPE_{armature.name}_{bone_name}"
    mesh = bpy.data.meshes.new(name + "_Mesh")
    half = 0.5
    verts = [
        (-half, -half, -half), (half, -half, -half), (half, half, -half), (-half, half, -half),
        (-half, -half, half), (half, -half, half), (half, half, half), (-half, half, half),
    ]
    faces = [
        (0, 1, 2, 3), (4, 7, 6, 5), (0, 4, 5, 1),
        (1, 5, 6, 2), (2, 6, 7, 3), (4, 0, 3, 7),
    ]
    mesh.from_pydata(verts, [], faces)
    mesh.update()
    shape = bpy.data.objects.new(name, mesh)
    collection.objects.link(shape)
    # Use a solid display for a clear rectangular IK handle.  The mesh is
    # stretched through ``custom_shape_scale_xyz`` when assigned below.
    shape.display_type = "SOLID"
    shape.hide_viewport = True
    shape.hide_render = True
    shape.hide_select = True
    shape["rbs_generated"] = True
    shape["rbs_kind"] = "IK_SHAPE"
    shape["rbs_armature"] = armature.name
    shape["rbs_bone"] = bone_name
    shape["rbs_ik_size"] = size
    return shape


def _chain_preset_items(self, context):
    """Build the dropdown entries from built-ins plus scene-persisted presets."""
    context = context or getattr(bpy, "context", None)
    english_labels = {
        "DEFAULT": "Default",
        "SKIRT": "Skirt",
        "HAIR": "Hair",
        "RIBBON": "Ribbon",
    }
    items = [
        (
            key,
            _ui_text(context, values["label"], english_labels.get(key, key.title())),
            _ui_text(
                context,
                f"应用{values['label']}刚体链参数",
                f"Apply {english_labels.get(key, key.title())} rigid-body chain settings",
            ),
        )
        for key, values in _BUILTIN_CHAIN_PRESETS.items()
    ]
    builtin_aliases = {
        alias
        for aliases in _BUILTIN_PRESET_ALIASES.values()
        for alias in (_normalize_preset_name(value) for value in aliases)
    }
    for index, preset in enumerate(getattr(self, "chain_presets", ())):
        label = preset.preset_name.strip() or _ui_text(
            context, f"自定义预设 {index + 1}", f"Custom Preset {index + 1}"
        )
        # A pinyin preset used as a built-in override is an implementation
        # detail and should not duplicate the three Chinese menu entries.
        normalized = _normalize_preset_name(label)
        if normalized in builtin_aliases:
            continue
        items.append(
            (
                f"USER_{index}",
                label,
                _ui_text(context, "应用用户保存的刚体链参数", "Apply saved user rigid-body chain settings"),
            )
        )
    return items


def _collider_shape_items(self, context):
    """Translate collider shape labels without changing their identifiers."""
    return [
        ("BOX", _ui_text(context, "盒体", "Box"), ""),
        ("CAPSULE", _ui_text(context, "胶囊", "Capsule"), ""),
        ("SPHERE", _ui_text(context, "球体", "Sphere"), ""),
    ]


def _apply_chain_preset(self, context):
    """Apply the selected built-in or scene-persisted chain preset."""
    key = self.chain_preset
    values = _BUILTIN_CHAIN_PRESETS.get(key)
    if values is not None:
        # User-tuned pinyin presets override the shipped values while the
        # menu continues to expose the four built-in entries.
        _index, override = _builtin_override(self, key)
        if override is not None:
            values = override
    if values is None and key.startswith("USER_"):
        try:
            index = int(key[5:])
        except (TypeError, ValueError):
            index = -1
        presets = getattr(self, "chain_presets", ())
        if 0 <= index < len(presets):
            values = presets[index]
    if values is None:
        return
    for field in _CHAIN_PRESET_FIELDS:
        if hasattr(values, field):
            value = getattr(values, field)
        else:
            value = values.get(field)
        if value is not None:
            setattr(self, field, value)


def _selected_user_preset_index(settings):
    """Return the selected user preset index, or ``-1`` for built-ins.

    Built-in entries (including their hidden pinyin override records) are
    intentionally protected from deletion.  Keeping this check in one helper
    makes the operator and the panel use the same rule.
    """
    key = str(getattr(settings, "chain_preset", "") or "")
    if not key.startswith("USER_"):
        return -1
    try:
        index = int(key[5:])
    except (TypeError, ValueError):
        return -1
    presets = getattr(settings, "chain_presets", ())
    return index if 0 <= index < len(presets) else -1


# Export private helpers too: feature modules and the compatibility facade
__all__ = [name for name in globals() if not name.startswith('__')]
