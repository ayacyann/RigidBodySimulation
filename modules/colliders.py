"""v1.1.2 feature module; class bodies are kept behavior-compatible."""
from .core import *

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

