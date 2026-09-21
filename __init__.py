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
    "version": (1, 1, 0),
    "blender": (3, 6, 0),
    "location": "View3D > Sidebar > Rigid Body Simulation",
    "description": "Create rigid-body simulation proxies for selected pose bones",
    "category": "Animation",
}


ROOT_COLLECTION = "RBS_Physics"
BODIES_COLLECTION = "RBS_Bodies"
COLLIDERS_COLLECTION = "RBS_Colliders"
IK_COLLECTION = "RBS_IK_Controls"
# IK controls use a fixed base size.  Keep this in code so the panel does not
# expose another setting that needs to be tuned for every rig.
IK_SHAPE_SIZE = 0.45
PREFIX = "RBS_"
CONSTRAINT_PREFIX = "RBS_CONSTRAINT_"
ROTATION_TRANSFER_PREFIX = "RBS_ROTATION_TRANSFER_"
MAX_COLLISION_BITS = 20
ALL_BITS = tuple(True for _ in range(MAX_COLLISION_BITS))
NO_BITS = tuple(False for _ in range(MAX_COLLISION_BITS))
BODY_PROXY_KINDS = frozenset({"BODY"})
_UPDATING_STATIC_IK = False


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
    "Chain Spring Stiffness": "链条弹簧刚度：连接相邻刚体的弹簧恢复强度。",
    "Chain Spring Damping": "链条弹簧阻尼：抑制链条弹簧振动的阻尼强度。",
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
    "SKIRT": {
        "label": "裙摆",
        "chain_radius": 0.01,
        "body_length_scale": 0.85,
        "mass": 0.5,
        "linear_damping": 0.35,
        "angular_damping": 0.45,
        "follow_strength": 0.75,
        "spring_stiffness": 1750.0,
        "spring_damping": 50.0,
    },
    "HAIR": {
        "label": "头发",
        "chain_radius": 0.02,
        "body_length_scale": 0.80,
        "mass": 1.5,
        "linear_damping": 0.35,
        "angular_damping": 0.55,
        "follow_strength": 0.85,
        "spring_stiffness": 2000.0,
        "spring_damping": 65.0,
    },
    "RIBBON": {
        "label": "飘带",
        "chain_radius": 0.005,
        "body_length_scale": 0.85,
        "mass": 0.25,
        "linear_damping": 0.35,
        "angular_damping": 0.65,
        "follow_strength": 0.90,
        "spring_stiffness": 1200.0,
        "spring_damping": 55.0,
    },
}

# Scene presets can be used to tune the three built-in entries without
# editing the add-on source.  Names are matched after lower-casing and
# removing separators, so both ``qun bai`` and ``qunbai`` work.
_BUILTIN_PRESET_ALIASES = {
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
    kinds = BODY_PROXY_KINDS | {"COLLIDER", "ANCHOR", "DRIVER", "CONSTRAINT"}
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
    budget.  Bodies now use the normal all-collections mask; self-collision
    for linked neighbors is handled by their rigid-body constraints and the
    configured body length.
    """
    for obj in bpy.data.objects:
        if not obj.get("rbs_generated") or obj.get("rbs_kind") != "BODY":
            continue
        if armature_name is not None and obj.get("rbs_armature") != armature_name:
            continue
        rb = obj.rigid_body
        if rb is not None:
            _set_mask(rb, ALL_BITS)
        # Retain this metadata for files created by earlier releases, but
        # make it explicit that no per-bone collision layer is in use.
        obj["rbs_collision_bit"] = -1


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
    if scene.rigidbody_world is None:
        try:
            bpy.ops.rigidbody.world_add()
        except RuntimeError:
            # object_add normally creates the world lazily; this is only a
            # fallback for unusual contexts.
            pass
    physics_collections()
    _migrate_generated_physics_objects()
    for armature in (obj for obj in bpy.data.objects if obj.type == "ARMATURE"):
        ik_bones = _generated_ik_chain_bones(armature)
        if ik_bones:
            _restore_ik_colliders(armature)
    _configure_rigidbody_world_contact()


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
    generated = any(obj.get("rbs_generated") for obj in bpy.data.objects)
    if not generated and scene.rigidbody_world is None:
        return
    physics_collections()
    _migrate_generated_physics_objects()
    _normalize_body_collision_masks()
    for armature in (obj for obj in bpy.data.objects if obj.type == "ARMATURE"):
        ik_bones = _generated_ik_chain_bones(armature)
        if ik_bones:
            _restore_ik_colliders(armature)
    _configure_rigidbody_world_contact()


@persistent
def _rbs_frame_change_post(scene):
    """Release proxies held kinematic for one frame after overlap repair."""
    current = int(getattr(scene, "frame_current", 0))
    for obj in bpy.data.objects:
        if not obj.get("rbs_overlap_adjusted") or obj.rigid_body is None:
            continue
        release_frame = int(obj.get("rbs_release_frame", current))
        if current >= release_frame:
            obj.rigid_body.kinematic = False
            try:
                del obj["rbs_overlap_adjusted"]
                del obj["rbs_release_frame"]
            except KeyError:
                pass


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


def _update_static_ik_helpers(_scene, _depsgraph):
    """Follow IK bones imperatively without creating a dependency cycle."""
    global _UPDATING_STATIC_IK
    if _UPDATING_STATIC_IK:
        return
    _UPDATING_STATIC_IK = True
    try:
        for obj in bpy.data.objects:
            if obj.get("rbs_follow_mode") != "STATIC_IK":
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
            obj.matrix_world = (armature.matrix_world @ pose_bone.matrix) @ follow_matrix
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


def _make_body_proxy(armature, pose_bone, radius, length_scale, collection, mask, mass, lin_damp, ang_damp, initial_world_matrix=None):
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
    obj["rbs_collision_bit"] = -1
    return obj


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
    # Parent the kinematic helper to the animated root.  The helper is the
    # spring endpoint; the root pose bone itself receives no rigid body.
    anchor.parent = armature
    anchor.parent_type = "BONE"
    anchor.parent_bone = root_bone.name
    anchor.matrix_world = armature.matrix_world @ root_bone.matrix
    anchor.matrix_world.translation = tail
    if initial_world_matrix is not None:
        initial_world_matrix = initial_world_matrix.copy()
        anchor.matrix_world = initial_world_matrix
    else:
        bpy.context.view_layer.update()
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
    return anchor


def _make_constraint(name, object1, object2, location, collection, stiffness, damping, disable_collisions=True):
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
    _set_display_visibility(empty, False)
    empty["rbs_generated"] = True
    empty["rbs_kind"] = "CONSTRAINT"
    empty["rbs_armature"] = object1.get("rbs_armature", "")
    return empty


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


CHAIN_OBJECT_KINDS = frozenset({"BODY", "ANCHOR", "CONSTRAINT", "DRIVER"})


def _chain_id(armature, root_bone, chain):
    """Return a stable id for one generated chain.

    The id is stored on every proxy created for the chain.  It lets a rebuild
    replace only the selected chain while leaving unrelated chains intact.
    """
    names = ",".join(pb.name for pb in chain)
    return f"{armature.name}|{root_bone.name}|{names}"


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
        elif kind in {"BODY", "DRIVER"}:
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
    if active is not None and active.select:
        return active.name
    selected = [bone for bone in armature.data.bones if bone.select]
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

    selected_data = [bone.name for bone in armature.data.bones if bone.select]
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


def _remove_ik_generated_for_bone(armature, bone_name):
    """Remove one generated IK control selected directly or by its source bone."""
    target_names = {
        bone.name for bone in armature.data.bones
        if bone.get("rbs_ik_generated")
        and (bone.name == bone_name or bone.get("rbs_ik_source") == bone_name)
    }
    if not target_names:
        return 0

    removed_constraints = 0
    for pose_bone in armature.pose.bones:
        for constraint in list(pose_bone.constraints):
            if not constraint.name.startswith(f"{PREFIX}IK_"):
                continue
            if (
                getattr(constraint, "subtarget", "") in target_names
                or pose_bone.name in {
                    armature.data.bones[name].get("rbs_ik_source", "")
                    for name in target_names
                    if armature.data.bones.get(name) is not None
                }
            ):
                pose_bone.constraints.remove(constraint)
                removed_constraints += 1

    old_mode = armature.mode
    if old_mode != "EDIT":
        bpy.ops.object.mode_set(mode="EDIT")
    for name in target_names:
        edit_bone = armature.data.edit_bones.get(name)
        if edit_bone is not None:
            armature.data.edit_bones.remove(edit_bone)
    if old_mode != "EDIT":
        bpy.ops.object.mode_set(mode=old_mode)

    for obj in list(bpy.data.objects):
        if (
            obj.get("rbs_generated")
            and obj.get("rbs_armature") == armature.name
            and obj.get("rbs_kind") == "IK_SHAPE"
            and obj.get("rbs_bone") in target_names
        ):
            bpy.data.objects.remove(obj, do_unlink=True)
    _restore_helpers_after_ik(armature)
    return len(target_names) + removed_constraints


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


def _selected_ik_chain(active_pose_bone):
    """Return active bone plus its contiguous selected ancestors."""
    selected = {pb.name for pb in bpy.context.selected_pose_bones or []}
    if active_pose_bone is None or active_pose_bone.name not in selected:
        return []
    chain = [active_pose_bone]
    parent = active_pose_bone.parent
    while parent is not None and parent.name in selected:
        chain.append(parent)
        parent = parent.parent
    return chain


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
        # menu continues to expose the three Chinese built-in entries.
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


class RBS_ChainPreset(bpy.types.PropertyGroup):
    """A user preset stored in the .blend scene."""

    preset_name: StringProperty(name="预设名称", default="", description="Custom Preset Name")
    chain_radius: FloatProperty(default=0.01, min=0.001, unit="LENGTH", description="Body Radius")
    body_length_scale: FloatProperty(default=0.85, min=0.1, max=1.0, description="Body Length Scale")
    mass: FloatProperty(default=0.5, min=0.001, description="Mass")
    linear_damping: FloatProperty(default=0.35, min=0.0, max=1.0, description="Linear Damping")
    angular_damping: FloatProperty(default=0.45, min=0.0, max=1.0, description="Angular Damping")
    follow_strength: FloatProperty(default=0.75, min=0.0, max=1.0, description="Root Follow Strength")
    spring_stiffness: FloatProperty(default=1750.0, min=0.0, description="Chain Spring Stiffness")
    spring_damping: FloatProperty(default=50.0, min=0.0, description="Chain Spring Damping")


class RBS_Settings(bpy.types.PropertyGroup):
    chain_radius: FloatProperty(name="刚体半径", default=0.01, min=0.001, unit="LENGTH", description="Body Radius")
    body_length_scale: FloatProperty(name="刚体长度比例", default=0.85, min=0.1, max=1.0, description="Body Length Scale")
    # The first menu item is the built-in skirt preset; matching the property
    # defaults keeps the initial panel selection and visible values consistent.
    mass: FloatProperty(name="质量", default=0.5, min=0.001, description="Mass")
    linear_damping: FloatProperty(name="线性阻尼", default=0.35, min=0.0, max=1.0, description="Linear Damping")
    angular_damping: FloatProperty(name="角阻尼", default=0.45, min=0.0, max=1.0, description="Angular Damping")
    follow_strength: FloatProperty(name="根骨骼跟随强度", default=0.75, min=0.0, max=1.0, description="Root Follow Strength")
    spring_stiffness: FloatProperty(name="链条弹簧刚度", default=1750.0, min=0.0, description="Chain Spring Stiffness")
    spring_damping: FloatProperty(name="链条弹簧阻尼", default=50.0, min=0.0, description="Chain Spring Damping")
    collider_radius: FloatProperty(name="碰撞体半径", default=0.12, min=0.001, unit="LENGTH", description="Collider Radius")
    collider_shape: EnumProperty(
        name="碰撞形状",
        items=_collider_shape_items,
        description="Collider Shape",
        # EnumProperty callbacks use an integer item index for the default.
        # Keep CAPSULE as the second entry to preserve the previous default.
        default=1,
    )
    ik_shape_size: FloatProperty(name="IK方框大小比例", default=0.45, min=0.05, max=2.0)
    colliders_visible: BoolProperty(name="显示碰撞体", default=True, description="Show Colliders")
    bodies_visible: BoolProperty(name="显示刚体代理", default=True, description="Show Body Proxies")
    chain_preset: EnumProperty(
        name="刚体链预设",
        items=_chain_preset_items,
        description="Rigid-Body Chain Preset",
        update=_apply_chain_preset,
    )
    chain_preset_name: StringProperty(name="自定义预设名称", default="", description="Custom Preset Name")
    chain_presets: CollectionProperty(type=RBS_ChainPreset)


class RBS_OT_create_ik_chain(bpy.types.Operator):
    bl_idname = "rbs.create_ik_chain"
    bl_label = "创建IK骨骼链"
    bl_options = {"REGISTER", "UNDO"}

    @classmethod
    def poll(cls, context):
        return (
            context.object is not None
            and context.object.type == "ARMATURE"
            and context.object.mode == "POSE"
            and context.active_pose_bone is not None
        )

    def execute(self, context):
        arm = context.object
        active = context.active_pose_bone
        chain = _selected_ik_chain(active)
        if len(chain) < 2:
            self.report({"WARNING"}, "请至少选择活动骨骼和一根连续的父骨骼作为IK链")
            return {"CANCELLED"}

        active_name = active.name
        existing = [
            bone.name for bone in arm.data.bones
            if bone.get("rbs_ik_generated") and bone.get("rbs_ik_source") == active_name
        ]
        if existing:
            self.report({"WARNING"}, f"活动骨骼已经存在IK骨骼：{existing[0]}，请先删除IK骨骼")
            return {"CANCELLED"}

        settings = context.scene.rbs_settings
        selected_names = [pb.name for pb in context.selected_pose_bones or []]
        old_mode = arm.mode
        bpy.ops.object.mode_set(mode="OBJECT")
        bpy.context.view_layer.objects.active = arm
        arm.select_set(True)
        bpy.ops.object.mode_set(mode="EDIT")

        source = arm.data.edit_bones.get(active_name)
        if source is None:
            bpy.ops.object.mode_set(mode="POSE")
            self.report({"ERROR"}, "无法读取活动骨骼的编辑数据")
            return {"CANCELLED"}
        direction = source.tail - source.head
        length = max(direction.length, 0.1)
        direction.normalize()
        ik_name_base = f"{PREFIX}IK_{arm.name}_{active_name}"
        ik_bone = arm.data.edit_bones.new(ik_name_base)
        ik_bone.head = source.tail
        ik_bone.tail = source.tail + direction * length
        ik_bone.roll = source.roll
        ik_bone.use_deform = False
        ik_bone.parent = None
        ik_name = ik_bone.name
        bpy.ops.object.mode_set(mode="OBJECT")

        ik_data = arm.data.bones.get(ik_name)
        ik_data["rbs_ik_generated"] = True
        ik_data["rbs_ik_source"] = active_name
        ik_data["rbs_ik_chain_length"] = len(chain)
        try:
            ik_data.show_wire = True
        except AttributeError:
            pass

        root_collection = bpy.data.collections.get(ROOT_COLLECTION) or _collection(ROOT_COLLECTION)
        ik_collection = _collection(IK_COLLECTION, root_collection)
        shape = _make_ik_shape(arm, ik_name, length * IK_SHAPE_SIZE, ik_collection)

        bpy.ops.object.mode_set(mode="POSE")
        ik_pose = arm.pose.bones.get(ik_name)
        active_pose = arm.pose.bones.get(active_name)
        ik_pose.custom_shape = shape
        # IK controls are translation handles.  Locking rotation (including
        # the quaternion W component) makes a normal ``G`` transform move the
        # selected bone instead of allowing accidental rotation when the
        # handle is clicked away from its tail.  Scale is locked as well so
        # the rectangular display keeps its configured proportions.
        ik_pose.lock_rotation = (True, True, True)
        ik_pose.lock_rotation_w = True
        ik_pose.lock_rotations_4d = True
        ik_pose.lock_scale = (True, True, True)
        ik_pose.lock_location = (False, False, False)
        ik_pose.rotation_mode = "QUATERNION"
        # Match the control's initial orientation to the active bone's current
        # pose.  Its origin is kept at the active tail so the IK target starts
        # where the selected chain currently ends.
        target_matrix = active_pose.matrix.copy()
        target_matrix.translation = active_pose.tail
        ik_pose.matrix = target_matrix
        bpy.context.view_layer.update()
        # The generated target bone starts at the active bone's tail so it
        # remains a useful IK handle.  Offset only its custom shape back along
        # the local bone axis, placing the rectangular handle over the active
        # bone segment instead of in the endpoint circle.
        ik_pose.custom_shape_translation = (0.0, -length * 0.5, 0.0)
        # Use the tested proportions directly: a slim rectangular handle
        # whose local Y axis follows the IK bone.
        ik_pose.custom_shape_scale_xyz = (0.2, 1.0, 0.2)
        try:
            ik_pose.color.palette = "THEME_YELLOW"
        except (AttributeError, TypeError):
            pass
        # Bone-parented passive helpers are detached before the solver is
        # added. Otherwise an IK solver spanning a simulated chain can feed
        # the rigid-body world back through the root anchor/colliders.
        _freeze_helpers_on_ik_chain(arm, [pb.name for pb in chain])
        constraint = active_pose.constraints.new("IK")
        constraint.name = f"{PREFIX}IK_{ik_name}"
        constraint.target = arm
        constraint.subtarget = ik_name
        constraint.chain_count = len(chain)
        constraint.influence = 1.0
        _restore_ik_colliders(arm)

        for bone in arm.data.bones:
            bone.select = bone.name == ik_name or bone.name in selected_names
        arm.data.bones.active = ik_data
        if old_mode != "POSE":
            bpy.ops.object.mode_set(mode=old_mode)
        self.report({"INFO"}, f"已创建IK骨骼 {ik_name}，反向计算链长度为 {len(chain)}")
        return {"FINISHED"}


class RBS_OT_build_chain(bpy.types.Operator):
    bl_idname = "rbs.build_chain"
    bl_label = "生成选中骨骼链刚体"
    bl_options = {"REGISTER", "UNDO"}

    @classmethod
    def poll(cls, context):
        return (
            context.object is not None
            and context.object.type == "ARMATURE"
            and context.object.mode == "POSE"
            and context.active_pose_bone is not None
        )

    def execute(self, context):
        arm = context.object
        root_bone = context.active_pose_bone
        # Operators invoked from Pose mode may carry a stale evaluated pose
        # from the last animation update. Re-evaluate the current frame before
        # sampling any bone transforms used to initialize Bullet objects.
        current_frame = context.scene.frame_current
        context.scene.frame_set(current_frame)
        bpy.context.view_layer.update()
        chain = _selected_chain(arm, root_bone)
        if not chain:
            self.report({"WARNING"}, "请选中根骨骼及其子骨骼；活动骨骼作为根骨骼且不添加刚体")
            return {"CANCELLED"}
        settings = context.scene.rbs_settings
        selected_names = [pb.name for pb in context.selected_pose_bones or []]
        chain_id = _chain_id(arm, root_bone, chain)
        # Capture the visible pose before switching modes or adding rigid-body
        # objects. Those operators refresh keyed armatures immediately, so
        # using live pose coordinates below can otherwise start a chain at a
        # later animation pose.
        _root_head, root_tail, _root_delta, _root_length = _bone_points(arm, root_bone)
        root_tail = root_tail.copy()
        initial_anchor_matrix = Matrix(arm.matrix_world @ root_bone.matrix)
        # Accessing a pose matrix may invalidate other pose-bone wrappers;
        # refresh the requested frame once more before sampling child points.
        context.scene.frame_set(current_frame)
        bpy.context.view_layer.update()
        initial_body_matrices = {}
        for pb in chain:
            head, tail, _delta, _length = _bone_points(arm, pb)
            midpoint = (head + tail) * 0.5
            body_matrix = _bone_world_rotation(arm, pb).to_matrix().to_4x4()
            body_matrix.translation = midpoint
            initial_body_matrices[pb.name] = body_matrix
        initial_anchor_matrix.translation = root_tail
        _ensure_world()
        # Operators that create rigid bodies require Object mode.  Replace only
        # proxies overlapping this chain; unrelated chains remain simulated.
        bpy.ops.object.mode_set(mode="OBJECT")
        _remove_constraint_rigid_bodies()
        _remove_chain_for_build(arm, chain, root_bone)
        # Normalize proxies from older builds as well, so adding a new chain
        # cannot leave this armature split across per-bone collision layers.
        _normalize_body_collision_masks(arm.name)
        root_coll, body_coll, _collider_coll = physics_collections()
        anchor = _make_anchor(arm, root_bone, body_coll, initial_anchor_matrix)
        anchor["rbs_chain_id"] = chain_id
        existing_ik_bones = _generated_ik_chain_bones(arm)
        bodies = []
        driver_targets = []
        for index, pb in enumerate(chain):
            # All dynamic bodies share Blender's normal collision mask.  The
            # linked rigid-body constraints disable neighbor collisions, while
            # the configured body length keeps the remaining chain segments
            # from overlapping without consuming collision layers.
            mask = ALL_BITS
            body = _make_body_proxy(
                arm, pb, settings.chain_radius, settings.body_length_scale, body_coll, mask,
                settings.mass, settings.linear_damping, settings.angular_damping,
                initial_body_matrices.get(pb.name),
            )
            body["rbs_chain_id"] = chain_id
            bodies.append(body)
            targets = _make_body_driver_targets(body, arm, pb, body["rbs_full_length"], body_coll)
            for target in targets:
                target["rbs_chain_id"] = chain_id
            driver_targets.append(targets)
        # Anchor-to-first spring uses follow_strength as the user-facing
        # damping-follow control.  A zero value still leaves a small spring so
        # the chain remains connected to the animated root.
        follow = max(settings.follow_strength, 0.01)
        if bodies:
            constraint_obj = _make_constraint(
                f"{CONSTRAINT_PREFIX}{arm.name}_ROOT",
                anchor, bodies[0], driver_targets[0][0].matrix_world.translation,
                body_coll, settings.spring_stiffness * follow,
                settings.spring_damping / follow, True,
            )
            # The root constraint pivot must move with the animated root.  A
            # world-space constraint empty would stay at the creation pose,
            # so the dynamic chain would not follow later root animation even
            # though the passive anchor itself follows the bone.
            root_constraint_world = constraint_obj.matrix_world.copy()
            constraint_obj.parent = anchor
            constraint_obj.matrix_world = root_constraint_world
            constraint_obj["rbs_chain_id"] = chain_id
        for index in range(1, len(bodies)):
            parent = bodies[index - 1]
            child = bodies[index]
            constraint_obj = _make_constraint(
                f"{CONSTRAINT_PREFIX}{arm.name}_{chain[index - 1].name}_{chain[index].name}",
                parent, child, driver_targets[index][0].matrix_world.translation,
                body_coll, settings.spring_stiffness, settings.spring_damping, True,
            )
            constraint_obj["rbs_chain_id"] = chain_id
        for pb, (head_target, tail_target) in zip(chain, driver_targets):
            _add_pose_driver(arm.pose.bones[pb.name], head_target, tail_target)
        # Keep proxies kinematic until every joint and pose driver exists;
        # release the construction lock after the final pose update below.
        if existing_ik_bones:
            # Run after pose drivers exist so the helper can detect overlap
            # with the newly created rigid-body chain.
            _freeze_helpers_on_ik_chain(arm, existing_ik_bones)
        # Existing colliders on simulated bones must be frozen at their
        # generated pose; keeping a BONE parent here would make the rigid-body
        # world depend on the pose bone that is itself driven by that world.
        _freeze_colliders_on_simulated_bones(arm)
        _stabilize_body_collider_overlaps(arm.name)
        _set_body_proxy_visibility(arm.name, settings.bodies_visible)
        bpy.context.view_layer.objects.active = arm
        arm.select_set(True)
        bpy.ops.object.mode_set(mode="POSE")
        for pb in arm.data.bones:
            pb.select = pb.name in selected_names
        arm.data.bones.active = arm.data.bones.get(root_bone.name)
        for body in bodies:
            if not body.get("rbs_overlap_adjusted"):
                body.rigid_body.kinematic = False
        bpy.context.view_layer.update()
        self.report({"INFO"}, f"已生成 {len(chain)} 个动态刚体；根骨骼 {root_bone.name} 保持动画跟随")
        return {"FINISHED"}


class RBS_OT_reset_chain_defaults(bpy.types.Operator):
    bl_idname = "rbs.reset_chain_defaults"
    bl_label = "恢复刚体链默认参数"
    bl_options = {"REGISTER", "UNDO"}

    def execute(self, context):
        settings = context.scene.rbs_settings
        defaults = _BUILTIN_CHAIN_PRESETS["SKIRT"]
        for name in _CHAIN_PRESET_FIELDS:
            value = defaults[name]
            setattr(settings, name, value)
        settings.chain_preset = "SKIRT"
        self.report({"INFO"}, "刚体链参数已恢复裙摆预设")
        return {"FINISHED"}


class RBS_OT_add_chain_preset(bpy.types.Operator):
    bl_idname = "rbs.add_chain_preset"
    bl_label = "添加刚体链预设"
    bl_options = {"REGISTER", "UNDO"}

    @classmethod
    def poll(cls, context):
        return context.scene is not None and hasattr(context.scene, "rbs_settings")

    def execute(self, context):
        settings = context.scene.rbs_settings
        name = settings.chain_preset_name.strip()
        if not name:
            # An empty name means "overwrite the current preset".  This is
            # useful while tuning a built-in entry: selecting 裙摆/头发/飘带,
            # changing its values, and pressing 添加 stores the result in the
            # scene under its conventional pinyin alias.
            current = settings.chain_preset
            if current.startswith("USER_"):
                try:
                    preset_index = int(current[5:])
                except (TypeError, ValueError):
                    preset_index = -1
                if not (0 <= preset_index < len(settings.chain_presets)):
                    self.report({"WARNING"}, "当前预设无效，请先选择一个有效预设")
                    return {"CANCELLED"}
                preset = settings.chain_presets[preset_index]
                for field in _CHAIN_PRESET_FIELDS:
                    setattr(preset, field, getattr(settings, field))
                self.report({"INFO"}, f"已覆盖当前刚体链预设：{preset.preset_name or '未命名预设'}")
                return {"FINISHED"}

            if current not in _BUILTIN_CHAIN_PRESETS:
                self.report({"WARNING"}, "请先选择一个刚体链预设")
                return {"CANCELLED"}
            preset_index, preset = _builtin_override(settings, current)
            if preset is None:
                preset = settings.chain_presets.add()
                preset_index = len(settings.chain_presets) - 1
                # Keep the override hidden from the menu while retaining a
                # readable name in the .blend file for future edits.
                preset.preset_name = current.lower()
            for field in _CHAIN_PRESET_FIELDS:
                setattr(preset, field, getattr(settings, field))
            self.report({"INFO"}, f"已覆盖内置预设：{_BUILTIN_CHAIN_PRESETS[current]['label']}")
            return {"FINISHED"}

        # Reusing a name updates its values instead of creating ambiguous menu
        # entries.  The collection is part of the Scene, so it persists with
        # the .blend file without requiring an external preferences file.
        normalized_name = _normalize_preset_name(name)
        preset_index = next(
            (
                index
                for index, item in enumerate(settings.chain_presets)
                if item.preset_name == name
                or (
                    normalized_name
                    in {
                        _normalize_preset_name(value)
                        for aliases in _BUILTIN_PRESET_ALIASES.values()
                        for value in aliases
                    }
                    and _normalize_preset_name(item.preset_name) == normalized_name
                )
            ),
            -1,
        )
        if preset_index < 0:
            preset = settings.chain_presets.add()
            preset_index = len(settings.chain_presets) - 1
        else:
            preset = settings.chain_presets[preset_index]
        preset.preset_name = name
        for field in _CHAIN_PRESET_FIELDS:
            setattr(preset, field, getattr(settings, field))

        # Alias presets are consumed through their Chinese built-in entry and
        # therefore stay hidden from the dropdown.  Keep the enum on that
        # built-in item after saving instead of assigning an unavailable
        # ``USER_*`` value.
        alias_key = next(
            (
                key
                for key, aliases in _BUILTIN_PRESET_ALIASES.items()
                if _normalize_preset_name(name)
                in {_normalize_preset_name(value) for value in aliases}
            ),
            None,
        )
        settings.chain_preset = alias_key or f"USER_{preset_index}"
        settings.chain_preset_name = ""
        self.report({"INFO"}, f"已保存刚体链预设：{name}")
        return {"FINISHED"}


class RBS_OT_remove_chain_preset(bpy.types.Operator):
    bl_idname = "rbs.remove_chain_preset"
    bl_label = "删除刚体链预设"
    bl_options = {"REGISTER", "UNDO"}

    @classmethod
    def poll(cls, context):
        settings = getattr(getattr(context, "scene", None), "rbs_settings", None)
        return settings is not None and _selected_user_preset_index(settings) >= 0

    def execute(self, context):
        settings = context.scene.rbs_settings
        preset_index = _selected_user_preset_index(settings)
        if preset_index < 0:
            self.report({"WARNING"}, "基础预设不可删除，请先选择用户预设")
            return {"CANCELLED"}

        preset_name = settings.chain_presets[preset_index].preset_name.strip()
        settings.chain_presets.remove(preset_index)
        # The removed USER_<index> enum value is no longer valid.  Selecting a
        # built-in also restores a valid, deterministic parameter state.
        settings.chain_preset = "SKIRT"
        settings.chain_preset_name = ""
        self.report({"INFO"}, f"已删除刚体链预设：{preset_name or '未命名预设'}")
        return {"FINISHED"}


class RBS_OT_add_colliders(bpy.types.Operator):
    bl_idname = "rbs.add_colliders"
    bl_label = "为选中骨骼添加碰撞体"
    bl_options = {"REGISTER", "UNDO"}

    @classmethod
    def poll(cls, context):
        return context.object is not None and context.object.type == "ARMATURE" and context.object.mode == "POSE"

    def execute(self, context):
        arm = context.object
        selected = list(context.selected_pose_bones or [])
        if not selected:
            self.report({"WARNING"}, "请在姿态模式下选择需要作为碰撞体的骨骼")
            return {"CANCELLED"}
        settings = context.scene.rbs_settings
        _ensure_world()
        # Normalize chains created by older releases before adding a passive
        # collider.  This guarantees that every generated body can interact
        # with the collider even when the chain predates shared masks.
        _normalize_body_collision_masks(arm.name)
        _root, _bodies, collider_coll = physics_collections()
        bpy.ops.object.mode_set(mode="OBJECT")
        _remove_constraint_rigid_bodies()
        count = 0
        for pb in selected:
            for old in list(bpy.data.objects):
                if old.get("rbs_kind") == "COLLIDER" and old.get("rbs_armature") == arm.name and old.get("rbs_bone") == pb.name:
                    bpy.data.objects.remove(old, do_unlink=True)
            head, tail, delta, length = _bone_points(arm, pb)
            name = f"{PREFIX}COLLIDER_{arm.name}_{pb.name}"
            if settings.collider_shape == "SPHERE":
                bpy.ops.mesh.primitive_uv_sphere_add(segments=16, ring_count=8, radius=settings.collider_radius, location=(head + tail) * 0.5)
                obj = context.object
                obj.name = name
            elif settings.collider_shape == "CAPSULE":
                capsule_cylinder_length = max(
                    length - 2.0 * settings.collider_radius, 0.001
                )
                mesh = _make_capsule_mesh(
                    name,
                    settings.collider_radius,
                    capsule_cylinder_length,
                    start_y=-capsule_cylinder_length * 0.5,
                )
                obj = bpy.data.objects.new(name, mesh)
                collider_coll.objects.link(obj)
                obj.rotation_mode = "QUATERNION"
                # Bullet's capsule primitive is aligned to local Z.  Bone
                # transforms use local Y as the bone axis, so rotate local Z
                # into the bone's local Y before applying the bone rotation.
                bone_rotation = _bone_world_rotation(arm, pb)
                obj.rotation_quaternion = bone_rotation @ Quaternion(
                    (1.0, 0.0, 0.0), -pi * 0.5
                )
                obj.location = (head + tail) * 0.5
            else:
                mesh = _make_box_mesh(
                    name, settings.collider_radius, length, -length * 0.5
                )
                obj = bpy.data.objects.new(name, mesh)
                collider_coll.objects.link(obj)
                obj.rotation_mode = "QUATERNION"
                obj.rotation_quaternion = _bone_world_rotation(arm, pb)
                obj.location = (head + tail) * 0.5
            if obj.name not in collider_coll.objects:
                collider_coll.objects.link(obj)
            for coll in list(obj.users_collection):
                if coll not in {collider_coll, bpy.data.collections.get(ROOT_COLLECTION)}:
                    coll.objects.unlink(obj)
            _link_to_physics_root(obj)
            # Object transforms assigned above are evaluated lazily. Update
            # before taking the snapshot; otherwise the default origin matrix
            # can be captured and restored after bone parenting.
            bpy.context.view_layer.update()
            world_matrix = obj.matrix_world.copy()
            obj["rbs_initial_world_matrix"] = [value for row in world_matrix for value in row]
            rb = _add_rigidbody(obj, "PASSIVE")
            # Add the rigid body while the object is unparented. Blender's
            # rigidbody operator may refresh the object's evaluated transform;
            # parent it to the pose bone only afterwards and restore the exact
            # world matrix so the collider cannot jump to the origin.
            ik_influenced = pb.name in _generated_ik_chain_bones(arm)
            if _bone_has_simulation_driver(pb):
                # A collider on a simulated bone cannot follow that bone
                # without creating a depsgraph cycle. The same applies to a
                # bone solved by a generated IK chain: a BONE parent would
                # feed the passive collider back into the IK dependency graph.
                # Keep the generated pose as a stable passive collider.
                obj.parent = None
                obj.matrix_world = world_matrix
                obj["rbs_follow_mode"] = (
                    "STATIC_SIMULATED_BONE"
                )
            else:
                _parent_to_pose_bone_preserve_world(obj, arm, pb, world_matrix)
                obj["rbs_follow_mode"] = "BONE"
            # Parent transforms can change the evaluated world dimensions.
            # Rebuild Bullet's shape after parenting so physics and the
            # visible proxy share the exact same center and extents.
            _refresh_rigidbody_collision_shape(obj, settings.collider_shape)
            obj["rbs_collision_shape"] = settings.collider_shape
            obj["rbs_collision_dimensions"] = [float(value) for value in obj.dimensions]
            _configure_collision_contact(rb, settings.collider_radius)
            # Passive colliders driven by a pose or IK bone must be marked as
            # animated/kinematic so Bullet refreshes their transform each
            # frame instead of treating the creation pose as static.
            rb.kinematic = True
            rb.friction = 0.7
            rb.restitution = 0.0
            _set_mask(rb, ALL_BITS)
            obj["rbs_generated"] = True
            obj["rbs_kind"] = "COLLIDER"
            obj["rbs_armature"] = arm.name
            obj["rbs_bone"] = pb.name
            obj.hide_viewport = not settings.colliders_visible
            obj.hide_render = not settings.colliders_visible
            count += 1
        _stabilize_body_collider_overlaps(arm.name)
        bpy.context.view_layer.objects.active = arm
        arm.select_set(True)
        bpy.ops.object.mode_set(mode="POSE")
        self.report({"INFO"}, f"已添加 {count} 个被动碰撞体")
        return {"FINISHED"}


class RBS_OT_toggle_colliders(bpy.types.Operator):
    bl_idname = "rbs.toggle_colliders"
    bl_label = "快速隐藏/显示碰撞体"
    bl_description = "Toggle Bone Colliders"
    bl_options = {"REGISTER", "UNDO"}

    def execute(self, context):
        settings = context.scene.rbs_settings
        settings.colliders_visible = not settings.colliders_visible
        visible = settings.colliders_visible
        for obj in bpy.data.objects:
            if obj.get("rbs_kind") == "COLLIDER":
                _set_display_visibility(obj, visible)
        self.report({"INFO"}, "碰撞体已显示" if visible else "碰撞体已隐藏（物理仍然生效）")
        return {"FINISHED"}


class RBS_OT_toggle_body_proxies(bpy.types.Operator):
    bl_idname = "rbs.toggle_body_proxies"
    bl_label = "快速隐藏/显示当前骨架刚体代理"
    bl_description = "Toggle Body Proxies"
    bl_options = {"REGISTER", "UNDO"}

    @classmethod
    def poll(cls, context):
        return context.object is not None and context.object.type == "ARMATURE"

    def execute(self, context):
        arm = context.object
        settings = context.scene.rbs_settings
        settings.bodies_visible = not settings.bodies_visible
        _set_body_proxy_visibility(arm.name, settings.bodies_visible)
        state = "显示" if settings.bodies_visible else "隐藏"
        self.report({"INFO"}, f"当前骨架的刚体代理已{state}（物理仍然生效）")
        return {"FINISHED"}


class RBS_OT_remove_chain(bpy.types.Operator):
    bl_idname = "rbs.remove_chain"
    bl_label = "删除刚体链"
    bl_options = {"REGISTER", "UNDO"}

    def execute(self, context):
        arm = context.object if context.object and context.object.type == "ARMATURE" else None
        if arm is None:
            self.report({"WARNING"}, "请先选择一个骨架")
            return {"CANCELLED"}
        old_mode = arm.mode
        if old_mode != "OBJECT":
            bpy.ops.object.mode_set(mode="OBJECT")
        _remove_generated(
            arm.name,
            object_kinds=CHAIN_OBJECT_KINDS,
            constraint_prefixes=(f"{PREFIX}SIMULATION",),
        )
        _restore_colliders_on_unsimulated_bones(arm)
        if old_mode != "OBJECT":
            bpy.context.view_layer.objects.active = arm
            bpy.ops.object.mode_set(mode=old_mode)
        self.report({"INFO"}, "已删除当前骨架的刚体链")
        return {"FINISHED"}


class RBS_OT_remove_chain_selected(bpy.types.Operator):
    bl_idname = "rbs.remove_chain_selected"
    bl_label = "删除选中骨骼刚体"
    bl_options = {"REGISTER", "UNDO"}

    def execute(self, context):
        arm = context.object if context.object and context.object.type == "ARMATURE" else None
        if arm is None:
            self.report({"WARNING"}, "请先选择一个骨架")
            return {"CANCELLED"}
        bone_names = _selected_bone_names(context, arm)
        if not bone_names:
            self.report({"WARNING"}, "请在姿态模式下选择需要删除刚体的骨骼")
            return {"CANCELLED"}
        old_mode = arm.mode
        if old_mode != "OBJECT":
            bpy.ops.object.mode_set(mode="OBJECT")
        removed = sum(
            _remove_selected_chain_generated(arm, bone_name)
            for bone_name in bone_names
        )
        if old_mode != "OBJECT":
            bpy.context.view_layer.objects.active = arm
            bpy.ops.object.mode_set(mode=old_mode)
        if not removed:
            self.report({"WARNING"}, "选中的骨骼没有找到刚体链生成内容")
            return {"CANCELLED"}
        self.report({"INFO"}, f"已删除 {len(bone_names)} 根选中骨骼的刚体绑定")
        return {"FINISHED"}


class RBS_OT_remove_colliders(bpy.types.Operator):
    bl_idname = "rbs.remove_colliders"
    bl_label = "删除骨骼碰撞体"
    bl_options = {"REGISTER", "UNDO"}

    def execute(self, context):
        arm = context.object if context.object and context.object.type == "ARMATURE" else None
        if arm is None:
            self.report({"WARNING"}, "请先选择一个骨架")
            return {"CANCELLED"}
        old_mode = arm.mode
        if old_mode != "OBJECT":
            bpy.ops.object.mode_set(mode="OBJECT")
        _remove_generated(arm.name, object_kinds=frozenset({"COLLIDER"}))
        if old_mode != "OBJECT":
            bpy.context.view_layer.objects.active = arm
            bpy.ops.object.mode_set(mode=old_mode)
        self.report({"INFO"}, "已删除当前骨架的骨骼碰撞体")
        return {"FINISHED"}


class RBS_OT_remove_colliders_selected(bpy.types.Operator):
    bl_idname = "rbs.remove_colliders_selected"
    bl_label = "删除当前骨骼碰撞体"
    bl_options = {"REGISTER", "UNDO"}

    def execute(self, context):
        arm = context.object if context.object and context.object.type == "ARMATURE" else None
        if arm is None:
            self.report({"WARNING"}, "请先选择一个骨架")
            return {"CANCELLED"}
        bone_name = _current_selected_bone_name(context, arm)
        if not bone_name:
            self.report({"WARNING"}, "请在姿态模式下选择一根骨骼")
            return {"CANCELLED"}
        old_mode = arm.mode
        if old_mode != "OBJECT":
            bpy.ops.object.mode_set(mode="OBJECT")
        removed = _remove_selected_colliders(arm, bone_name)
        if old_mode != "OBJECT":
            bpy.context.view_layer.objects.active = arm
            bpy.ops.object.mode_set(mode=old_mode)
        if not removed:
            self.report({"WARNING"}, f"骨骼 {bone_name} 没有找到碰撞体")
            return {"CANCELLED"}
        self.report({"INFO"}, f"已删除骨骼 {bone_name} 的碰撞体")
        return {"FINISHED"}


class RBS_OT_add_rotation_transfer(bpy.types.Operator):
    bl_idname = "rbs.add_rotation_transfer"
    bl_label = "添加旋转传递"
    bl_options = {"REGISTER", "UNDO"}

    @classmethod
    def poll(cls, context):
        return (
            context.object is not None
            and context.object.type == "ARMATURE"
            and context.object.mode == "POSE"
            and context.active_pose_bone is not None
        )

    def execute(self, context):
        armature = context.object
        active = context.active_pose_bone
        selected = list(context.selected_pose_bones or [])
        pairs = _rotation_transfer_pairs(armature, active, selected)
        if not pairs:
            self.report({"WARNING"}, "请选中活动骨骼及其连续的子骨骼")
            return {"CANCELLED"}

        created = 0
        for owner, source in pairs:
            # Re-adding a chain should replace only this module's generated
            # constraint and leave manually authored Copy Rotation constraints
            # such as the Finger_Inherit setup untouched.
            for old in list(owner.constraints):
                if old.name.startswith(ROTATION_TRANSFER_PREFIX):
                    owner.constraints.remove(old)
            constraint = owner.constraints.new("COPY_ROTATION")
            constraint.name = f"{ROTATION_TRANSFER_PREFIX}{owner.name}"
            constraint.target = armature
            constraint.subtarget = source.name
            constraint.mix_mode = "ADD"
            constraint.owner_space = "LOCAL"
            constraint.target_space = "LOCAL"
            constraint.use_x = True
            constraint.use_y = False
            constraint.use_z = False
            constraint.invert_x = False
            constraint.invert_y = False
            constraint.invert_z = False
            constraint.influence = 1.0
            created += 1

        self.report({"INFO"}, f"已添加 {created} 个旋转传递约束")
        return {"FINISHED"}


class RBS_OT_remove_rotation_transfer(bpy.types.Operator):
    bl_idname = "rbs.remove_rotation_transfer"
    bl_label = "删除旋转传递"
    bl_options = {"REGISTER", "UNDO"}

    @classmethod
    def poll(cls, context):
        return (
            context.object is not None
            and context.object.type == "ARMATURE"
            and context.object.mode == "POSE"
            and context.active_pose_bone is not None
        )

    def execute(self, context):
        armature = context.object
        selected = list(context.selected_pose_bones or [])
        if not selected:
            self.report({"WARNING"}, "请在姿态模式下选择需要删除旋转传递的骨骼")
            return {"CANCELLED"}
        removed = 0
        for pose_bone in selected:
            for constraint in list(pose_bone.constraints):
                if constraint.name.startswith(ROTATION_TRANSFER_PREFIX):
                    pose_bone.constraints.remove(constraint)
                    removed += 1
        if not removed:
            self.report({"WARNING"}, "选中骨骼没有找到旋转传递")
            return {"CANCELLED"}
        self.report({"INFO"}, f"已删除 {removed} 个旋转传递约束")
        return {"FINISHED"}


class RBS_OT_remove_all_rotation_transfer(bpy.types.Operator):
    """Remove every rotation-transfer constraint generated by this add-on."""

    bl_idname = "rbs.remove_all_rotation_transfer"
    bl_label = "删除所有旋转传递"
    bl_options = {"REGISTER", "UNDO"}

    @classmethod
    def poll(cls, context):
        return (
            context.object is not None
            and context.object.type == "ARMATURE"
            and context.object.mode == "POSE"
        )

    def execute(self, context):
        armature = context.object
        removed = 0
        for pose_bone in armature.pose.bones:
            for constraint in list(pose_bone.constraints):
                if constraint.name.startswith(ROTATION_TRANSFER_PREFIX):
                    pose_bone.constraints.remove(constraint)
                    removed += 1
        if not removed:
            self.report({"WARNING"}, "当前骨架没有找到旋转传递")
            return {"CANCELLED"}
        self.report({"INFO"}, f"已删除 {removed} 个旋转传递约束")
        return {"FINISHED"}


class RBS_OT_remove_ik(bpy.types.Operator):
    bl_idname = "rbs.remove_ik"
    bl_label = "删除所有IK骨骼"
    bl_options = {"REGISTER", "UNDO"}

    def execute(self, context):
        arm = context.object if context.object and context.object.type == "ARMATURE" else None
        if arm is None:
            self.report({"WARNING"}, "请先选择一个骨架")
            return {"CANCELLED"}
        old_mode = arm.mode
        if old_mode != "OBJECT":
            bpy.ops.object.mode_set(mode="OBJECT")
        _remove_ik_generated(arm)
        _restore_helpers_after_ik(arm)
        if old_mode != "OBJECT":
            bpy.context.view_layer.objects.active = arm
            bpy.ops.object.mode_set(mode=old_mode)
        self.report({"INFO"}, "已删除当前骨架的所有IK骨骼和IK约束")
        return {"FINISHED"}


class RBS_OT_remove_ik_selected(bpy.types.Operator):
    bl_idname = "rbs.remove_ik_selected"
    bl_label = "删除当前骨骼IK"
    bl_options = {"REGISTER", "UNDO"}

    def execute(self, context):
        arm = context.object if context.object and context.object.type == "ARMATURE" else None
        if arm is None:
            self.report({"WARNING"}, "请先选择一个骨架")
            return {"CANCELLED"}
        bone_name = _current_selected_bone_name(context, arm)
        if not bone_name:
            self.report({"WARNING"}, "请在姿态模式下选择一根骨骼")
            return {"CANCELLED"}
        old_mode = arm.mode
        if old_mode != "OBJECT":
            bpy.ops.object.mode_set(mode="OBJECT")
        removed = _remove_ik_generated_for_bone(arm, bone_name)
        if old_mode != "OBJECT":
            bpy.context.view_layer.objects.active = arm
            bpy.ops.object.mode_set(mode=old_mode)
        if not removed:
            self.report({"WARNING"}, f"骨骼 {bone_name} 没有找到IK骨骼")
            return {"CANCELLED"}
        self.report({"INFO"}, f"已删除骨骼 {bone_name} 对应的IK骨骼")
        return {"FINISHED"}


class RBS_PT_panel(bpy.types.Panel):
    bl_label = "Rigid Body Simulation"
    bl_idname = "RBS_PT_panel"
    bl_space_type = "VIEW_3D"
    bl_region_type = "UI"
    bl_category = "刚体模拟"

    @classmethod
    def poll(cls, context):
        obj = getattr(context, "object", None)
        return obj is not None and obj.type == "ARMATURE" and obj.mode == "POSE"

    def draw(self, context):
        layout = self.layout
        settings = context.scene.rbs_settings
        col = layout.column(align=True)
        col.label(text=_ui_text(context, "骨骼链刚体", "Rigid-Body Chain"))
        preset_box = col.box()
        preset_box.label(text=_ui_text(context, "刚体链参数预设", "Rigid-Body Chain Presets"))
        preset_box.prop(settings, "chain_preset", text=_ui_text(context, "选择预设", "Preset"))
        preset_row = preset_box.row(align=True)
        preset_row.prop(settings, "chain_preset_name", text=_ui_text(context, "名称", "Name"))
        preset_row.operator("rbs.add_chain_preset", text=_ui_text(context, "添加", "Add"), icon="ADD")
        delete_row = preset_box.row(align=True)
        delete_row.operator(
            "rbs.remove_chain_preset",
            text=_ui_text(context, "删除预设", "Delete Preset"),
            icon="TRASH",
        )
        delete_row.enabled = _selected_user_preset_index(settings) >= 0
        col.prop(settings, "chain_radius", text=_ui_text(context, "刚体半径", "Body Radius"))
        col.prop(settings, "body_length_scale", text=_ui_text(context, "刚体长度比例", "Body Length Scale"))
        col.prop(settings, "mass", text=_ui_text(context, "质量", "Mass"))
        col.prop(settings, "linear_damping", text=_ui_text(context, "线性阻尼", "Linear Damping"))
        col.prop(settings, "angular_damping", text=_ui_text(context, "角阻尼", "Angular Damping"))
        col.prop(settings, "follow_strength", text=_ui_text(context, "根骨骼跟随强度", "Root Follow Strength"))
        col.prop(settings, "spring_stiffness", text=_ui_text(context, "链条弹簧刚度", "Chain Spring Stiffness"))
        col.prop(settings, "spring_damping", text=_ui_text(context, "链条弹簧阻尼", "Chain Spring Damping"))
        row = col.row(align=True)
        row.operator("rbs.build_chain", text=_ui_text(context, "创建骨骼链刚体", "Create Rigid-Body Chain"), icon="PHYSICS")
        row.operator(
            "rbs.toggle_body_proxies",
            text=_ui_text(context, "代理显示/隐藏", "Show/Hide Proxies"),
            icon="HIDE_OFF" if settings.bodies_visible else "HIDE_ON",
        )
        col.operator(
            "rbs.remove_chain_selected",
            text=_ui_text(context, "删除选中骨骼刚体", "Delete Selected Bone Bodies"),
            icon="TRASH",
        )
        col.separator()
        col.label(text=_ui_text(context, "IK骨骼链", "IK Bone Chain"))
        col.operator("rbs.create_ik_chain", text=_ui_text(context, "创建IK骨骼链", "Create IK Bone Chain"), icon="CONSTRAINT_BONE")
        col.operator("rbs.remove_ik_selected", text=_ui_text(context, "删除当前骨骼IK", "Delete Selected Bone IK"), icon="TRASH")
        col.separator()
        col.label(text=_ui_text(context, "骨骼碰撞体", "Bone Colliders"))
        col.prop(settings, "collider_radius", text=_ui_text(context, "碰撞体半径", "Collider Radius"))
        col.prop(settings, "collider_shape", text=_ui_text(context, "碰撞形状", "Collider Shape"))
        row = col.row(align=True)
        row.operator("rbs.add_colliders", text=_ui_text(context, "添加碰撞体", "Add Colliders"), icon="MESH_UVSPHERE")
        row.operator("rbs.toggle_colliders", text=_ui_text(context, "显示/隐藏", "Show/Hide"), icon="HIDE_OFF" if settings.colliders_visible else "HIDE_ON")
        col.operator("rbs.remove_colliders_selected", text=_ui_text(context, "删除当前骨骼碰撞体", "Delete Selected Bone Colliders"), icon="TRASH")
        col.separator()
        col.label(text=_ui_text(context, "旋转传递", "Rotation Transfer"))
        row = col.row(align=True)
        row.operator("rbs.add_rotation_transfer", text=_ui_text(context, "添加", "Add"), icon="CONSTRAINT_BONE")
        row.operator("rbs.remove_rotation_transfer", text=_ui_text(context, "删除", "Delete"), icon="TRASH")
        col.separator()
        col.label(text=_ui_text(context, "删除所有", "Delete All"))
        row = col.row(align=True)
        row.operator("rbs.remove_chain", text=_ui_text(context, "删除刚体链", "Delete Rigid-Body Chains"), icon="TRASH")
        row.operator("rbs.remove_colliders", text=_ui_text(context, "删除碰撞体", "Delete Bone Colliders"), icon="TRASH")
        row = col.row(align=True)
        row.operator("rbs.remove_ik", text=_ui_text(context, "删除所有IK骨骼", "Delete All IK Bones"), icon="TRASH")
        row.operator("rbs.remove_all_rotation_transfer", text=_ui_text(context, "删除所有旋转传递", "Delete All Rotation Transfer"), icon="TRASH")


CLASSES = (
    RBS_ChainPreset,
    RBS_Settings,
    RBS_OT_create_ik_chain,
    RBS_OT_build_chain,
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
    try:
        bpy.app.translations.register(__name__, _PROPERTY_TOOLTIP_TRANSLATIONS)
    except (AttributeError, RuntimeError, TypeError, ValueError):
        # Older Blender builds may not expose the translation registry while
        # running in a restricted startup context; the English descriptions
        # remain usable in that case.
        pass
    for cls in CLASSES:
        bpy.utils.register_class(cls)
    bpy.types.Scene.rbs_settings = PointerProperty(type=RBS_Settings)
    if _rbs_load_post not in bpy.app.handlers.load_post:
        bpy.app.handlers.load_post.append(_rbs_load_post)
    if _rbs_frame_change_post not in bpy.app.handlers.frame_change_post:
        bpy.app.handlers.frame_change_post.append(_rbs_frame_change_post)
    if _update_static_ik_helpers not in bpy.app.handlers.depsgraph_update_post:
        bpy.app.handlers.depsgraph_update_post.append(_update_static_ik_helpers)
    # Enabling an add-on does not emit load_post for the file that is already
    # open. Repair that active scene immediately as well.
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
        bpy.app.translations.unregister(__name__)
    except (AttributeError, RuntimeError, TypeError, ValueError):
        pass


if __name__ == "__main__":
    register()
