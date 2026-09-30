"""v1.1.2 feature module; class bodies are kept behavior-compatible."""
from .core import *

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

    @staticmethod
    def _ik_shape(armature, target_name, length, collection):
        """Return the box display shape for a target bone, reusing it if present."""
        for obj in bpy.data.objects:
            if (
                obj.get("rbs_generated")
                and obj.get("rbs_kind") == "IK_SHAPE"
                and obj.get("rbs_armature") == armature.name
                and obj.get("rbs_bone") == target_name
            ):
                return obj
        return _make_ik_shape(armature, target_name, length * IK_SHAPE_SIZE, collection)

    def execute(self, context):
        arm = context.object
        active = context.active_pose_bone
        selected = list(context.selected_pose_bones or [])
        if active is None:
            self.report({"WARNING"}, "请在姿态模式下选择作为IK骨骼的骨骼作为活动项")
            return {"CANCELLED"}
        if len(selected) < 2:
            self.report({"WARNING"}, "请选中IK骨骼和至少一根需要IK的骨骼")
            return {"CANCELLED"}

        components = _selected_ik_components(selected)
        two_island = len(components) == 2
        if len(components) > 2:
            self.report({"WARNING"}, "创建IK只能选择一个连续骨骼链，或选择两个骨骼孤岛")
            return {"CANCELLED"}

        target_name = None
        generated_target = False
        if two_island:
            chain, reason = _selected_ik_two_island_chain(selected, active)
            if chain is None:
                self.report({"WARNING"}, reason or "两孤岛选择无效")
                return {"CANCELLED"}
            # In two-island mode the singleton active island is the existing
            # IK control; no generated control bone is added.
            target_name = active.name
        else:
            # Preserve the legacy one-island workflow: the active bone is the
            # chain tip and a new IK control is generated for it.
            chain = _selected_ik_chain(active)
            if len(chain) < 2:
                self.report({"WARNING"}, "请至少选择活动骨骼和一根连续的父骨骼作为IK链")
                return {"CANCELLED"}
            existing = [
                bone.name for bone in arm.data.bones
                if bone.get("rbs_ik_generated") and bone.get("rbs_ik_source") == active.name
            ]
            if existing:
                self.report({"WARNING"}, f"活动骨骼已经存在IK骨骼：{existing[0]}，请先删除IK骨骼")
                return {"CANCELLED"}

        chain_names = [pose_bone.name for pose_bone in chain]
        chain_length = len(chain)
        # The legacy mode keeps its original active-tip owner.  In
        # two-island mode the non-IK island's terminal bone owns the solver.
        solver_pose = chain[-1] if two_island else active
        solver_name = solver_pose.name

        selected_names = [pose_bone.name for pose_bone in selected]
        old_mode = arm.mode

        if target_name is None:
            generated_target = True
            bpy.ops.object.mode_set(mode="OBJECT")
            bpy.context.view_layer.objects.active = arm
            arm.select_set(True)
            bpy.ops.object.mode_set(mode="EDIT")
            source = arm.data.edit_bones.get(solver_name)
            if source is None:
                bpy.ops.object.mode_set(mode="POSE")
                self.report({"ERROR"}, "无法读取需要IK的骨骼编辑数据")
                return {"CANCELLED"}
            direction = source.tail - source.head
            length = max(direction.length, 0.1)
            direction.normalize()
            ik_bone = arm.data.edit_bones.new(f"{PREFIX}IK_{arm.name}_{solver_name}")
            ik_bone.head = source.tail
            ik_bone.tail = source.tail + direction * length
            ik_bone.roll = source.roll
            ik_bone.use_deform = False
            ik_bone.parent = None
            target_name = ik_bone.name
            bpy.ops.object.mode_set(mode="OBJECT")

        target_data = arm.data.bones.get(target_name)
        if target_data is not None:
            target_data["rbs_ik_source"] = solver_name
            target_data["rbs_ik_chain_length"] = chain_length
            if generated_target:
                target_data["rbs_ik_generated"] = True
            try:
                target_data.show_wire = True
            except AttributeError:
                pass

        if arm.mode != "POSE":
            bpy.ops.object.mode_set(mode="POSE")

        ik_pose = arm.pose.bones.get(target_name)
        if ik_pose is not None:
            # Give the IK bone a box display so it is easy to identify and
            # select, and keep it a pure translation handle.
            root_collection = bpy.data.collections.get(ROOT_COLLECTION) or _collection(ROOT_COLLECTION)
            ik_collection = _collection(IK_COLLECTION, root_collection)
            shape_length = max(
                (target_data.tail_local - target_data.head_local).length, 0.1
            )
            shape = self._ik_shape(arm, target_name, shape_length, ik_collection)
            ik_pose.custom_shape = shape
            settings = getattr(context.scene, "rbs_settings", None)
            shape_scale = float(getattr(settings, "ik_shape_size", 0.75))
            shape_scale = max(shape_scale, 0.05)
            ik_pose.lock_rotation = (True, True, True)
            ik_pose.lock_rotation_w = True
            ik_pose.lock_rotations_4d = True
            ik_pose.lock_scale = (True, True, True)
            ik_pose.lock_location = (False, False, False)
            ik_pose.rotation_mode = "QUATERNION"
            # IK controls are translation handles in the armature's edit
            # position; clear any pose-space offset before exposing them.
            ik_pose.location = (0.0, 0.0, 0.0)
            # Keep the IK custom-shape displacement at zero so the handle's
            # transform panel reports no positional offset.
            ik_pose.custom_shape_translation = (0.0, 0.0, 0.0)
            ik_pose.custom_shape_scale_xyz = (shape_scale, shape_scale, shape_scale)
            try:
                ik_pose.color.palette = "THEME_YELLOW"
            except (AttributeError, TypeError):
                pass

        # Re-creating IK for the same chain replaces only the solver generated
        # by this add-on and leaves other constraints untouched.
        for old in list(solver_pose.constraints):
            if old.type == "IK" and old.name.startswith(f"{PREFIX}IK_"):
                solver_pose.constraints.remove(old)

        # Bone-parented passive helpers are detached before the solver is
        # added. Otherwise an IK solver spanning a simulated chain can feed
        # the rigid-body world back through the root anchor/colliders.
        solved_names = [solver_name]
        parent_pose = solver_pose.parent
        for _index in range(max(chain_length - 1, 0)):
            if parent_pose is None:
                break
            solved_names.append(parent_pose.name)
            parent_pose = parent_pose.parent
        _freeze_helpers_on_ik_chain(arm, solved_names)
        constraint = solver_pose.constraints.new("IK")
        constraint.name = f"{PREFIX}IK_{target_name}"
        constraint.target = arm
        constraint.subtarget = target_name
        constraint.chain_count = chain_length
        constraint.iterations = 500
        constraint.use_tail = True
        constraint.use_stretch = True
        constraint.weight = 1.0
        constraint.orient_weight = 1.0
        constraint.influence = 1.0
        _restore_ik_colliders(arm)

        for bone in arm.data.bones:
            bone.select = bone.name == target_name or bone.name in selected_names
        if target_data is not None:
            arm.data.bones.active = target_data
        if old_mode != "POSE":
            bpy.ops.object.mode_set(mode=old_mode)
        self.report(
            {"INFO"},
            f"已在骨骼 {solver_name} 上添加IK，IK骨骼 {target_name}，链条长度为 {chain_length}",
        )
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

