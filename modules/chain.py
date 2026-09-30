"""v1.1.2 feature module; class bodies are kept behavior-compatible."""
from .core import *

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
        anchor["rbs_initial_follow_pending"] = True
        anchor["rbs_initial_follow_frame"] = int(current_frame)
        anchor["rbs_sync_anchor_matrix"] = _flatten_matrix(anchor.matrix_world)
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
            body["rbs_initial_follow_pending"] = True
            body["rbs_initial_follow_frame"] = int(current_frame)
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
            # The proxy is centered on its source bone, so the root joint's
            # rest-frame offset is half of the first source-bone length.
            root_allowance = 0.5 * float(bodies[0].get("rbs_full_length", 0.0))
            constraint_obj = _make_constraint(
                f"{CONSTRAINT_PREFIX}{arm.name}_ROOT",
                anchor, bodies[0], driver_targets[0][0].matrix_world.translation,
                body_coll,
                settings.spring_stiffness * SPRING_PARAMETER_SCALE * follow,
                settings.spring_damping * SPRING_PARAMETER_SCALE / follow,
                True,
                max_distance=root_allowance,
            )
            # The constraint pivot follows the passive anchor. It therefore
            # follows the upstream animation parent without reading root_bone.
            root_constraint_world = constraint_obj.matrix_world.copy()
            constraint_obj.parent = anchor
            constraint_obj.matrix_world = root_constraint_world
            constraint_obj["rbs_chain_id"] = chain_id
            if not _bone_has_simulation_driver(root_bone):
                _ensure_damped_follow_constraint(
                    arm,
                    arm.pose.bones[root_bone.name],
                    bodies[0],
                    settings.follow_strength,
                    overwrite=False,
                    chain_id=chain_id,
                )
        for index in range(1, len(bodies)):
            parent = bodies[index - 1]
            child = bodies[index]
            constraint_obj = _make_constraint(
                f"{CONSTRAINT_PREFIX}{arm.name}_{chain[index - 1].name}_{chain[index].name}",
                parent, child, driver_targets[index][0].matrix_world.translation,
                body_coll,
                settings.spring_stiffness * SPRING_PARAMETER_SCALE,
                settings.spring_damping * SPRING_PARAMETER_SCALE,
                True,
                max_distance=0.0,
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
            # Hold the chain at the current pose until the first parent/pose
            # update has been consumed. The frame handler releases it after
            # leaving the build frame; this prevents a same-frame Center/Spine
            # edit from being ignored by Bullet.
            if not body.get("rbs_overlap_adjusted"):
                body.rigid_body.kinematic = True
        bpy.context.view_layer.update()
        self.report({"INFO"}, f"已生成 {len(chain)} 个动态刚体；根骨骼 {root_bone.name} 保持动画跟随")
        return {"FINISHED"}

class RBS_OT_reset_body_positions(bpy.types.Operator):
    bl_idname = "rbs.reset_body_positions"
    bl_label = "重置刚体位置"
    bl_description = "将当前骨架的刚体代理和关节重置到对应骨骼的当前姿态"
    bl_options = {"REGISTER", "UNDO"}

    @classmethod
    def poll(cls, context):
        return context.object is not None and context.object.type == "ARMATURE"

    def execute(self, context):
        arm = context.object
        scene = context.scene
        frame = int(scene.frame_current)
        bodies = [
            obj for obj in bpy.data.objects
            if obj.get("rbs_generated")
            and obj.get("rbs_kind") == "BODY"
            and obj.get("rbs_armature") == arm.name
            and obj.rigid_body is not None
        ]
        if not bodies:
            self.report({"WARNING"}, "当前骨架没有可重置的刚体代理")
            return {"CANCELLED"}

        # Temporarily mute generated bone drivers. Otherwise moving BODY_01
        # changes the root pose while BODY_02 is still being sampled, so the
        # reset target itself moves during the operation.
        muted = []
        for pose_bone in arm.pose.bones:
            for constraint in pose_bone.constraints:
                if constraint.name.startswith((f"{PREFIX}SIMULATION", DAMPED_FOLLOW_PREFIX)):
                    muted.append((constraint, bool(constraint.mute)))
                    constraint.mute = True
        bpy.context.view_layer.update()
        target_matrices = {}
        for body in bodies:
            pose_bone = arm.pose.bones.get(str(body.get("rbs_bone", "")))
            if pose_bone is None:
                continue
            head, tail, _delta, _length = _bone_points(arm, pose_bone)
            matrix = _bone_world_rotation(arm, pose_bone).to_matrix().to_4x4()
            matrix.translation = (head + tail) * 0.5
            target_matrices[body.name] = matrix

        anchors = [
            obj for obj in bpy.data.objects
            if obj.get("rbs_kind") == "ANCHOR"
            and obj.get("rbs_armature") == arm.name
            and obj.get("rbs_generated")
        ]
        for anchor in anchors:
            root_bone = arm.pose.bones.get(str(anchor.get("rbs_bone", "")))
            if root_bone is not None:
                world = anchor.matrix_world.copy()
                world.translation = _bone_points(arm, root_bone)[1]
                anchor.matrix_world = world

        for body in bodies:
            matrix = target_matrices.get(body.name)
            if matrix is None:
                continue
            body.rigid_body.kinematic = True
            body.matrix_world = matrix
            body["rbs_initial_follow_pending"] = True
            body["rbs_initial_follow_frame"] = frame

        # Put every unparented joint pivot back on the child bone head. The
        # root joint remains parented to its passive anchor.
        for joint in bpy.data.objects:
            if (
                not joint.get("rbs_generated")
                or joint.get("rbs_kind") != "CONSTRAINT"
                or joint.get("rbs_armature") != arm.name
                or joint.rigid_body_constraint is None
            ):
                continue
            child = joint.rigid_body_constraint.object2
            child_bone = (
                arm.pose.bones.get(str(child.get("rbs_bone", "")))
                if child is not None and child.get("rbs_kind") == "BODY"
                else None
            )
            if child_bone is None:
                continue
            if joint.name.endswith("_ROOT"):
                continue
            if joint.parent is not None:
                joint.parent = None
            joint.matrix_world.translation = _bone_points(arm, child_bone)[0]

        for anchor in anchors:
            anchor["rbs_sync_anchor_matrix"] = _flatten_matrix(anchor.matrix_world)
            anchor["rbs_initial_follow_pending"] = True
            anchor["rbs_initial_follow_frame"] = frame

        bpy.context.view_layer.update()
        for constraint, was_muted in muted:
            constraint.mute = was_muted
        bpy.context.view_layer.update()
        self.report({"INFO"}, f"已将 {len(bodies)} 个刚体代理重置到对应骨骼中心")
        return {"FINISHED"}

class RBS_OT_create_damped_follow(bpy.types.Operator):
    bl_idname = "rbs.create_damped_follow"
    bl_label = "创建阻尼跟随"
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
        settings = context.scene.rbs_settings
        selected = list(context.selected_pose_bones or [])
        if not selected:
            selected = [context.active_pose_bone]
        created = 0
        updated = 0
        skipped = []
        for pose_bone in selected:
            has_anchor_chain = any(
                obj.get("rbs_kind") == "ANCHOR"
                and obj.get("rbs_generated")
                and obj.get("rbs_armature") == arm.name
                and obj.get("rbs_bone") == pose_bone.name
                for obj in bpy.data.objects
            )
            if has_anchor_chain or _find_body_proxy(arm, pose_bone.name) is not None:
                skipped.append(pose_bone.name)
                continue
            child, target = _first_child_body_proxy(arm, pose_bone)
            if child is None or target is None:
                skipped.append(pose_bone.name)
                continue
            constraint, was_created = _ensure_damped_follow_constraint(
                arm,
                pose_bone,
                target,
                settings.damped_follow_strength,
                overwrite=True,
                chain_id=str(target.get("rbs_chain_id", "")),
            )
            if constraint is None:
                skipped.append(pose_bone.name)
            elif was_created:
                created += 1
            else:
                updated += 1
        if not created and not updated:
            self.report({"WARNING"}, "选中骨骼的第一根子骨骼没有刚体代理，未创建阻尼跟随")
            return {"CANCELLED"}
        message = f"已创建 {created} 个、覆盖 {updated} 个阻尼跟随"
        if skipped:
            message += f"；跳过 {len(skipped)} 根没有第一子骨骼刚体代理的骨骼"
        self.report({"INFO"}, message)
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
        chain_ids = {
            str(obj.get("rbs_chain_id", ""))
            for obj in bpy.data.objects
            if obj.get("rbs_generated")
            and obj.get("rbs_armature") == arm.name
            and obj.get("rbs_kind") == "ANCHOR"
            and obj.get("rbs_chain_id")
        }
        for chain_id in chain_ids:
            _remove_chain_damped_follow(arm, chain_id)
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
        _remove_chain_damped_follow(arm, bone_names=set(bone_names))
        if old_mode != "OBJECT":
            bpy.context.view_layer.objects.active = arm
            bpy.ops.object.mode_set(mode=old_mode)
        if not removed:
            self.report({"WARNING"}, "选中的骨骼没有找到刚体链生成内容")
            return {"CANCELLED"}
        self.report({"INFO"}, f"已删除 {len(bone_names)} 根选中骨骼的刚体绑定")
        return {"FINISHED"}

